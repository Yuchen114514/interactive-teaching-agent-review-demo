from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import logging
from queue import Empty, Queue
from threading import Thread
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api_errors import ApiError
from app.agent_store import AgentStore
from app.agents.router import get_current_user_id_and_token
from app.config import Settings, get_settings
from app.session_access import can_read_agent_session, can_write_agent_session
from app.en_demo import demo_agent_message, ensure_demo_agent_ready, limit_demo_chat, require_demo_problem_id
from app.database import get_session
from app.dependencies import get_redis
from app.image_references import extract_local_image_id
from app.models import (
    AgentDiagramJob,
    AgentMessageRole,
    AgentMode,
    AgentProblemWorkspace,
    AgentSession,
    AgentSessionSource,
    AgentSessionStatus,
    GuidanceDepthValue,
    ImageAsset,
    AgentTurnRun,
    ProblemWorkspaceStatus,
    User,
)
from app.repositories.teaching import TeachingRepository
from app.rate_limits import enforce_scope_rate_limits
from app.schemas import (
    AgentUserPreferencesRequest,
    AgentUserPreferencesResponse,
    GuidanceDepth,
    TeachingTurnRequest,
)
from app.teaching.agent_providers import (
    TeachingAgentProviderUnavailableError,
    build_teaching_agent_providers,
)
from app.teaching.v3_orchestrator import TeachingV3Orchestrator
from app.teaching.v3_compaction import ProblemCompactionAgent
from app.teaching.v3_diagram import DiagramAgent
from app.teaching.diagram_production import create_semantic_diagram_provider, production_diagram_events
from app.teaching.diagram_jobs import finish_diagram_job, start_diagram_job
from app.teaching.v3_followup import create_followup_provider
from app.teaching.v3_memory import MemoryAgent
from app.teaching.v3_contracts import MCPRequestBlock
from app.teaching.v3_mcp import V3MCPContextProvider, render_mcp_tool_reply
from app.teaching.v3_plan_state import PlanStateAgent
from app.teaching.v3_visible_stream import create_openai_compatible_visible_stream_provider
from app.teaching.v3_whiteboard import WhiteboardAgent


router = APIRouter()
logger = logging.getLogger(__name__)
DEFAULT_DIAGRAM_PROVIDER_TIMEOUT_SECONDS = 155.0
V3_DELTA_EVENT_COMMIT_BATCH_SIZE = 8
V3_STREAM_DONE_SENTINEL = object()


async def get_v3_user_id(
    x_test_user_id: int | None = Header(default=None, alias="X-Test-User-Id"),
    x_accoding_session: str | None = Header(default=None, alias="X-Accoding-Session"),
    settings: Settings = Depends(get_settings),
) -> int:
    if x_accoding_session:
        user_id, _ = await get_current_user_id_and_token(x_accoding_session)
        return int(user_id)
    if not settings.demo_mode and settings.app_env in {"dev", "test"}:
        return x_test_user_id or 19
    raise ApiError(status_code=401, code="AUTH_INVALID", message="X-Accoding-Session header missing")


async def get_v3_rate_limited_user_id(
    user_id: int = Depends(get_v3_user_id),
    redis: Redis = Depends(get_redis),
) -> int:
    if get_settings().demo_mode:
        limit_demo_chat(user_id)
        return user_id
    await enforce_scope_rate_limits(redis, user_id=str(user_id), scope="chat")
    return user_id


@router.get("/users/me/preferences", response_model=AgentUserPreferencesResponse)
def get_v3_user_preferences(
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
) -> AgentUserPreferencesResponse:
    preferences = TeachingRepository(db).get_or_create_user_preferences(user_id)
    db.commit()
    return AgentUserPreferencesResponse(
        mascot_skin=preferences.mascot_skin,
        font_size=preferences.font_size,
    )


@router.patch("/users/me/preferences", response_model=AgentUserPreferencesResponse)
def update_v3_user_preferences(
    request: AgentUserPreferencesRequest,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
) -> AgentUserPreferencesResponse:
    preferences = TeachingRepository(db).update_user_preferences(
        user_id,
        mascot_skin=request.mascot_skin,
        font_size=request.font_size,
    )
    db.commit()
    return AgentUserPreferencesResponse(
        mascot_skin=preferences.mascot_skin,
        font_size=preferences.font_size,
    )


@router.post("/chat/stream")
def chat_v3_stream(
    request: TeachingTurnRequest,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_rate_limited_user_id),
    x_accoding_session: str | None = Header(default=None, alias="X-Accoding-Session"),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    ensure_demo_agent_ready(settings)
    _ensure_v3_user(db, user_id, settings)
    session = _get_or_create_session(db, request, user_id)
    if session.id is None:
        raise ApiError(status_code=500, code="SESSION_CREATE_FAILED", message="Failed to create session")
    repo = TeachingRepository(db)
    session_db_id = int(session.id)
    repo.lock_session_for_turn_admission(session_db_id)
    existing_run = repo.get_turn_run_by_client_request_id(session_db_id, request.client_request_id)
    if existing_run is not None:
        return _replay_v3_run_stream(repo, session.session_uuid, existing_run)
    active_run = repo.get_active_turn_run(session_db_id)
    if active_run is not None:
        raise ApiError(
            status_code=409,
            code="SESSION_RUN_ACTIVE",
            message="A teaching turn is already running for this session",
            details={"run_id": active_run.run_uuid, "status": active_run.status},
        )
    explicit_problem_id = request.problem_id
    if settings.demo_mode and explicit_problem_id is not None:
        require_demo_problem_id(explicit_problem_id)
    auto_bound_problem = False
    if explicit_problem_id is not None:
        request = request.model_copy(update={"problem_id": explicit_problem_id})
    if request.problem_id is not None:
        store = AgentStore(db)
        if session.problem_id is None:
            if not store.bind_session_problem(session.session_uuid, user_id, request.problem_id):
                return _problem_redirect_stream(
                    identified=False,
                    problem_id=request.problem_id,
                    problem_title=store._problem_title(request.problem_id),
                    message=request.message,
                )
            auto_bound_problem = True
        elif session.problem_id != request.problem_id:
            return _problem_redirect_stream(
                identified=False,
                problem_id=request.problem_id,
                problem_title=store._problem_title(request.problem_id),
                message=request.message,
            )
    request = request.model_copy(update={"session_id": session.session_uuid})
    workspace = _resolve_workspace(repo=repo, request=request, session_id=int(session.id), user_id=user_id)
    turn_index = AgentStore(db).next_turn_index(int(session.id))
    session_before_turn = session.model_copy()
    if auto_bound_problem:
        repo.record_visible_message(
            session_id=int(session.id),
            workspace_id=None,
            role=AgentMessageRole.SYSTEM,
            content="已将会话自动收进题目袋内",
            turn_index=turn_index,
        )
    user_message = repo.record_visible_message(
        session_id=int(session.id),
        workspace_id=int(workspace.id) if workspace is not None and workspace.id is not None else None,
        role=AgentMessageRole.USER,
        content=request.message,
        turn_index=turn_index,
    )
    try:
        run = repo.create_turn_run(
            run_uuid=str(uuid4()),
            client_request_id=request.client_request_id,
            session_id=int(session.id),
            workspace_id=int(workspace.id) if workspace is not None and workspace.id is not None else None,
            turn_index=turn_index,
            user_message_id=int(user_message.id) if user_message.id is not None else None,
            metadata={
                "source": request.source,
                "guidance_depth": (request.guidance_depth or GuidanceDepth.DIRECT).value,
                "problem_id": request.problem_id,
                "original_message": request.message,
                "session_before_turn": {
                    "title": session_before_turn.title,
                    "last_message_at": session_before_turn.last_message_at.isoformat() if session_before_turn.last_message_at else None,
                    "update_time": session_before_turn.update_time.isoformat() if session_before_turn.update_time else None,
                },
            },
        )
    except IntegrityError:
        db.rollback()
        duplicate_run = repo.get_turn_run_by_client_request_id(session_db_id, request.client_request_id)
        if duplicate_run is None:
            raise
        return _replay_v3_run_stream(repo, session.session_uuid, duplicate_run)
    session_uuid = session.session_uuid
    workspace_db_id = int(workspace.id) if workspace is not None and workspace.id is not None else None
    user_message_db_id = int(user_message.id) if user_message.id is not None else None
    run_db_id = int(run.id) if run.id is not None else None
    turn_images = _attach_v3_turn_images(
        db=db,
        message_id=user_message_db_id,
        image_refs=request.images,
        user_id=user_id,
    )
    db.commit()

    session_factory = _sidecar_session_factory(db)
    live_events: Queue[str | object] = Queue()

    def emit_live(payload: dict | str) -> None:
        live_events.put(_format_v3_sse_payload(payload))

    def run_worker() -> None:
        try:
            _run_v3_turn_background(
                session_factory=session_factory,
                request=request,
                user_id=user_id,
                x_accoding_session=x_accoding_session,
                settings=settings,
                session_id=session_db_id,
                session_uuid=session_uuid,
                session_problem_id=session.problem_id,
                initial_workspace_id=workspace_db_id,
                user_message_id=user_message_db_id,
                run_id=run_db_id,
                turn_index=turn_index,
                turn_images=turn_images,
                emit_live=emit_live,
            )
        finally:
            live_events.put(V3_STREAM_DONE_SENTINEL)

    Thread(target=run_worker, daemon=True).start()

    return StreamingResponse(_stream_v3_live_events(live_events), media_type="text/event-stream")


def _problem_redirect_stream(
    *,
    identified: bool,
    problem_id: int,
    problem_title: str,
    message: str,
) -> StreamingResponse:
    def stream_events():
        yield _format_v3_sse_payload(
            {
                "type": "problem_redirect",
                "problem_id": problem_id,
                "problem_title": problem_title,
                "message": message,
                "action_label": "携带会话进入该题目",
                "identified": identified,
            }
        )
        yield _format_v3_sse_payload("[DONE]")

    return StreamingResponse(stream_events(), media_type="text/event-stream")


def _replay_v3_run_stream(repo: TeachingRepository, session_uuid: str, run: AgentTurnRun) -> StreamingResponse:
    replay = repo.get_turn_run_events(run.run_uuid, limit=500)
    events = replay.get("events", []) if isinstance(replay, dict) else []

    def stream_events():
        if not events:
            yield _format_v3_sse_payload({"type": "session", "session_id": session_uuid, "run_id": run.run_uuid})
        for event in events:
            payload = event.get("payload") if isinstance(event, dict) else None
            if isinstance(payload, dict):
                yield _format_v3_sse_payload(payload)
        yield _format_v3_sse_payload("[DONE]")

    return StreamingResponse(stream_events(), media_type="text/event-stream")


def _format_v3_sse_payload(payload: dict | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _stream_v3_live_events(live_events: Queue[str | object], *, heartbeat_seconds: float = 15.0):
    while True:
        try:
            item = live_events.get(timeout=max(0.0, heartbeat_seconds))
        except Empty:
            yield ": heartbeat\n\n"
            continue
        if item is V3_STREAM_DONE_SENTINEL:
            return
        yield item


def _run_v3_turn_background(
    *,
    session_factory: SessionFactory,
    request: TeachingTurnRequest,
    user_id: int,
    x_accoding_session: str | None,
    settings: Settings,
    session_id: int,
    session_uuid: str,
    session_problem_id: int | None = None,
    initial_workspace_id: int | None,
    user_message_id: int | None,
    run_id: int | None,
    turn_index: int,
    turn_images: list[dict],
    emit_live: Callable[[dict | str], None],
) -> None:
    with session_factory() as worker_db:
        repo = TeachingRepository(worker_db)
        run_record = worker_db.get(AgentTurnRun, run_id) if run_id is not None else None
        workspace = repo.get_workspace(initial_workspace_id) if initial_workspace_id is not None else None
        final_workspace_id: int | None = int(workspace.id) if workspace is not None and workspace.id is not None else None
        response_text = ""
        first_assistant_message_id: int | None = None
        followup_text = ""
        followup_message_id: int | None = None
        diagram_job: AgentDiagramJob | None = None
        pending_delta_events = 0

        def is_cancelled() -> bool:
            return run_id is not None and repo.is_turn_run_cancel_requested(run_id)

        def stop_current_run() -> None:
            if run_id is None:
                return
            assistant_message_id: int | None = first_assistant_message_id
            clean_response = response_text.strip()
            if clean_response and assistant_message_id is None:
                assistant_message = repo.record_visible_message(
                    session_id=session_id,
                    workspace_id=final_workspace_id,
                    role=AgentMessageRole.ASSISTANT,
                    content=clean_response,
                    turn_index=turn_index,
                )
                assistant_message_id = int(assistant_message.id) if assistant_message.id is not None else None
            if followup_text.strip() and followup_message_id is None:
                repo.record_visible_message(
                    session_id=session_id,
                    workspace_id=final_workspace_id,
                    role=AgentMessageRole.ASSISTANT,
                    content=followup_text.strip(),
                    turn_index=turn_index,
                )
            if diagram_job is not None and diagram_job.status == "running":
                finish_diagram_job(worker_db, diagram_job, status="stopped")
            repo.stop_turn_run(
                run_id,
                workspace_id=final_workspace_id,
                assistant_message_id=assistant_message_id,
                response_preview=clean_response[:500] if clean_response else None,
            )
            worker_db.commit()

        def emit(payload: dict | str) -> None:
            nonlocal pending_delta_events
            if isinstance(payload, dict) and run_id is not None:
                repo.append_turn_run_event(run_id, payload)
                if payload.get("type") in {"main_delta", "main_followup.delta"}:
                    pending_delta_events += 1
                    if pending_delta_events < V3_DELTA_EVENT_COMMIT_BATCH_SIZE:
                        emit_live(payload)
                        return
                worker_db.commit()
                pending_delta_events = 0
            emit_live(payload)

        try:
            emit({"type": "session", "session_id": session_uuid, "run_id": run_record.run_uuid if run_record is not None else None})
            if is_cancelled():
                emit({"type": "run_status", "status": "stopped", "message": "已停止本轮生成。"})
                stop_current_run()
                return
            provider = _main_provider_for_settings(settings)
            sidecar_providers = _providers_for_settings(settings)
            mcp_provider = _mcp_context_provider_for_settings(settings, x_accoding_session)
            mcp_prefetch_failed_reply: str | None = None
            if (
                request.problem_id is not None
                and mcp_provider is not None
            ):
                emit(
                    {
                        "type": "mcp_status",
                        "status": "running",
                        "resource": "problem_detail",
                        "problem_id": request.problem_id,
                        "agent": "MCPContextProvider",
                        "message": "正在查询平台题面。",
                    }
                )
                workspace = _prefetch_mcp_problem_workspace(
                    repo=repo,
                    request=request,
                    session_id=session_id,
                    user_id=user_id,
                    source_message_id=user_message_id,
                    source_turn_index=turn_index,
                    mcp_provider=mcp_provider,
                )
                if workspace is not None:
                    worker_db.commit()
                    final_workspace_id = int(workspace.id) if workspace.id is not None else None
                    emit(
                        {
                            "type": "mcp_status",
                            "status": "completed",
                            "resource": "problem_detail",
                            "problem_id": request.problem_id,
                            "workspace_id": final_workspace_id,
                            "agent": "MCPContextProvider",
                            "message": "已读取平台题面。",
                        }
                    )
                else:
                    mcp_prefetch_failed_reply = _problem_prefetch_failure_reply(
                        repo=repo,
                        mcp_provider=mcp_provider,
                        request=request,
                        session_id=session_id,
                        workspace_id=None,
                        turn_index=turn_index,
                        user_id=user_id,
                    )
                    emit(
                        {
                            "type": "mcp_status",
                            "status": "failed",
                            "resource": "problem_detail",
                            "problem_id": request.problem_id,
                            "agent": "MCPContextProvider",
                            "message": mcp_prefetch_failed_reply or "平台题面查询失败。",
                        }
                    )
            emit(
                {
                    "type": "progress",
                    "stage": "main_dialog",
                    "agent": "MainDialogAgent",
                    "message": "正在调用MainDialogAgent生成实时回复。",
                    "model": settings.external_llm_model,
                    "provider_backend": settings.teaching_agent_provider_backend,
                }
            )
            final_workspace_id = int(workspace.id) if workspace is not None and workspace.id is not None else None
            orchestrator = TeachingV3Orchestrator(
                repo=repo,
                main_provider=provider,
                visible_stream_provider=_visible_stream_provider_for_settings(settings),
                plan_sidecar=_plan_sidecar(
                    session_factory=session_factory,
                    provider=sidecar_providers.plan,
                    provider_timeout_seconds=settings.teaching_agent_provider_timeout_seconds,
                ),
                diagram_sidecar=_persisting_diagram_sidecar(
                    session_factory=session_factory,
                    provider=sidecar_providers.diagram_handoff,
                    semantic_provider=create_semantic_diagram_provider(settings),
                    provider_timeout_seconds=DEFAULT_DIAGRAM_PROVIDER_TIMEOUT_SECONDS,
                ),
                followup_provider=_followup_provider_for_settings(settings),
                whiteboard_sidecar=_whiteboard_sidecar(
                    session_factory=session_factory,
                    session_id=session_id,
                    workspace_id=final_workspace_id,
                    provider=sidecar_providers.boardwork_handoff,
                    current_step_wait_seconds=settings.teaching_agent_provider_timeout_seconds + 5.0,
                ),
                memory_sidecar=_memory_sidecar(
                    session_factory=session_factory,
                    provider=sidecar_providers.memory_candidates,
                ),
                compaction_sidecar=_compaction_sidecar(
                    session_factory=session_factory,
                    provider=sidecar_providers.workspace_summary,
                ),
                mcp_executor=_mcp_tool_executor(
                    repo=repo,
                    mcp_provider=mcp_provider,
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=final_workspace_id,
                    turn_index=turn_index,
                ),
                submission_demo_mode=settings.submission_demo_mode,
                response_language="en" if settings.demo_mode else "zh",
                required_sidecar_collect_timeout_seconds=max(
                    settings.teaching_agent_provider_timeout_seconds + 5.0,
                    DEFAULT_DIAGRAM_PROVIDER_TIMEOUT_SECONDS + 5.0,
                ),
            )
            for event in orchestrator.run_stream(
                user_id=user_id,
                session_id=session_id,
                session_uuid=session_uuid,
                workspace=workspace,
                user_message=demo_agent_message(request.problem_id, request.message) if settings.demo_mode else request.message,
                turn_images=turn_images,
                source_message_id=user_message_id,
                source_turn_index=turn_index,
                requested_guidance_depth=(request.guidance_depth or GuidanceDepth.DIRECT).value,
                problem_id=request.problem_id,
                session_problem_id=session_problem_id,
                source=request.source,
                serialize_sidecars=settings.app_env == "test",
            ):
                if is_cancelled():
                    emit({"type": "run_status", "status": "stopped", "message": "已停止本轮生成。"})
                    stop_current_run()
                    return
                event_type = event.get("type")
                if event_type == "main_delta":
                    response_text += str(event.get("delta") or "")
                    emit(event)
                    continue
                if event_type == "main_followup.delta":
                    followup_text += str(event.get("delta") or "")
                    emit(event)
                    continue
                if event_type == "problem_redirect":
                    redirect_event = {
                        **event,
                        "problem_id": int(event.get("problem_id") or 0),
                        "problem_title": str(event.get("problem_title") or "新题目"),
                        "message": request.message,
                        "action_label": "携带会话进入该题目",
                    }
                    emit(redirect_event)
                    if run_record is not None:
                        repo.discard_turn_run(run_record.run_uuid)
                        worker_db.commit()
                    return
                if event_type == "main_block.visible_complete":
                    visible_response = str(event.get("response") or "").strip()
                    event = {**event, "response": visible_response}
                    if visible_response and first_assistant_message_id is None:
                        active_workspace = repo.get_active_workspace(session_id)
                        if active_workspace is not None and active_workspace.id is not None:
                            final_workspace_id = int(active_workspace.id)
                        assistant_message = repo.record_visible_message(
                            session_id=session_id,
                            workspace_id=final_workspace_id,
                            role=AgentMessageRole.ASSISTANT,
                            content=visible_response,
                            turn_index=turn_index,
                        )
                        first_assistant_message_id = int(assistant_message.id) if assistant_message.id is not None else None
                        if run_record is not None:
                            run_record.assistant_message_id = first_assistant_message_id
                            worker_db.add(run_record)
                        worker_db.commit()
                        event["message_id"] = first_assistant_message_id
                if event_type == "main_followup.visible_complete":
                    followup_text = str(event.get("response") or "").strip()
                    if followup_text and followup_message_id is None:
                        followup_message = repo.record_visible_message(
                            session_id=session_id,
                            workspace_id=final_workspace_id,
                            role=AgentMessageRole.ASSISTANT,
                            content=followup_text,
                            turn_index=turn_index,
                        )
                        followup_message_id = int(followup_message.id) if followup_message.id is not None else None
                        worker_db.commit()
                        event = {**event, "message_id": followup_message_id}
                if event_type == "main_block.visual_need" and event.get("need_diagram"):
                    diagram_job = start_diagram_job(
                        worker_db, session_uuid=session_uuid, session_id=session_id, user_id=user_id,
                        run_uuid=run_record.run_uuid if run_record is not None else None,
                        workspace_id=final_workspace_id, source_message_id=first_assistant_message_id,
                        source_turn_index=turn_index, diagram_goal=str(event.get("diagram_goal") or ""),
                    )
                    worker_db.commit()
                    event = {**event, "job_id": diagram_job.job_uuid}
                if event_type == "diagram_status" and diagram_job is not None:
                    event = {**event, "job_id": diagram_job.job_uuid}
                    if event.get("status") == "failed":
                        finish_diagram_job(worker_db, diagram_job, status="failed", error_code="DIAGRAM_FAILED")
                        worker_db.commit()
                if event_type == "diagram_ready" and diagram_job is not None:
                    scene = event.get("scene") if isinstance(event.get("scene"), dict) else {}
                    metadata = scene.get("metadata_json") if isinstance(scene.get("metadata_json"), dict) else {}
                    finish_diagram_job(
                        worker_db, diagram_job, status="ready",
                        scene_uuid=str(event.get("diagram_scene_id") or scene.get("scene_id") or ""),
                        producer_version=str(metadata.get("producer_version") or "legacy"),
                        renderer_version=str(metadata.get("renderer_version") or "legacy"),
                    )
                    worker_db.commit()
                    event = {**event, "job_id": diagram_job.job_uuid}
                if event.get("type") == "final":
                    final_response = event.get("response")
                    if isinstance(final_response, dict):
                        clean_response = str(final_response.get("response") or "").strip()
                        final_workspace_id = final_response.get("workspace_id") if isinstance(final_response.get("workspace_id"), int) else final_workspace_id
                        final_response = {**final_response, "response": clean_response}
                        event = {**event, "response": final_response}
                        response_text = clean_response
                    else:
                        response_text = ""
                emit(event)
                if is_cancelled():
                    emit({"type": "run_status", "status": "stopped", "message": "已停止本轮生成。"})
                    stop_current_run()
                    return


            if diagram_job is not None and diagram_job.status == "running":
                finish_diagram_job(worker_db, diagram_job, status="failed", error_code="DIAGRAM_NOT_READY")
            if response_text and first_assistant_message_id is None:
                assistant_message = repo.record_visible_message(
                    session_id=session_id,
                    workspace_id=final_workspace_id,
                    role=AgentMessageRole.ASSISTANT,
                    content=response_text,
                    turn_index=turn_index,
                )
                first_assistant_message_id = int(assistant_message.id) if assistant_message.id is not None else None
            if run_id is not None:
                repo.complete_turn_run(
                    run_id, workspace_id=final_workspace_id,
                    assistant_message_id=first_assistant_message_id,
                    response_preview=response_text[:500] if response_text else None,
                )
            worker_db.commit()
        except TeachingAgentProviderUnavailableError as exc:
            worker_db.rollback()
            if diagram_job is not None and diagram_job.status == "running":
                finish_diagram_job(worker_db, diagram_job, status="failed", error_code="TEACHING_MODEL_UNAVAILABLE")
            error_payload = {
                "type": "error",
                "error": "Teaching model provider is unavailable for this turn.",
                "code": "TEACHING_MODEL_UNAVAILABLE",
                "details": {
                    "agent": exc.agent_name,
                    "capability": exc.capability,
                    "provider_backend": exc.provider_backend,
                    "outcome": exc.outcome,
                    "provider_details": exc.details,
                },
            }
            if run_id is not None:
                repo.fail_turn_run(
                    run_id,
                    error_code="TEACHING_MODEL_UNAVAILABLE",
                    error_message=error_payload["error"],
                    workspace_id=final_workspace_id,
                )
                worker_db.commit()
            emit(error_payload)
        except Exception as exc:
            worker_db.rollback()
            if diagram_job is not None and diagram_job.status == "running":
                finish_diagram_job(worker_db, diagram_job, status="failed", error_code=type(exc).__name__[:64])
            error_payload = {"type": "error", "error": str(exc), "code": type(exc).__name__}
            if run_id is not None:
                repo.fail_turn_run(
                    run_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    workspace_id=final_workspace_id,
                )
                worker_db.commit()
            emit(error_payload)
        finally:
            emit("[DONE]")


@router.post("/sessions/{session_id}/runs/stop")
def stop_v3_session_runs(
    session_id: str,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
) -> dict:
    session = db.exec(
        select(AgentSession).where(AgentSession.session_uuid == session_id)
    ).first()
    if session is None or session.id is None or session.user_id != user_id or session.status == AgentSessionStatus.DELETED:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    repo = TeachingRepository(db)
    requested = len(repo.request_turn_run_cancellation(int(session.id)))
    db.commit()
    return {"session_id": session_id, "requested": requested}


@router.get("/sessions/{session_id}/runs")
def list_v3_session_runs(
    session_id: str,
    limit: int = 20,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    session = db.exec(
        select(AgentSession).where(AgentSession.session_uuid == session_id)
    ).first()
    if session is None or session.id is None or not can_read_agent_session(session, user_id, settings):
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    safe_limit = min(max(limit, 1), 50)
    return TeachingRepository(db).list_turn_runs_for_session(int(session.id), limit=safe_limit)


@router.get("/sessions/{session_id}/diagram-jobs")
def list_v3_diagram_jobs(
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    session = db.exec(select(AgentSession).where(AgentSession.session_uuid == session_id)).first()
    if session is None or session.id is None or not can_read_agent_session(session, user_id, settings):
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    jobs = db.exec(
        select(AgentDiagramJob)
        .where(AgentDiagramJob.session_id == int(session.id), AgentDiagramJob.user_id == session.user_id)
        .order_by(AgentDiagramJob.source_turn_index.desc(), AgentDiagramJob.id.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return [
        {
            "job_id": job.job_uuid, "status": job.status, "scene_id": job.scene_uuid,
            "workspace_id": job.workspace_id, "source_message_id": job.source_message_id,
            "source_turn_index": job.source_turn_index, "error_code": job.error_code,
            "updated_at": job.update_time.isoformat(),
        }
        for job in jobs
    ]


@router.get("/runs/{run_id}/events")
def list_v3_run_events(
    run_id: str,
    after: int = 0,
    limit: int = 200,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v3_user_id),
    settings: Settings = Depends(get_settings),
) -> dict:
    repo = TeachingRepository(db)
    result = repo.get_turn_run_events(run_id, after=after, limit=limit)
    if result is None:
        raise ApiError(status_code=404, code="RUN_NOT_FOUND", message="Run not found")
    session = db.get(AgentSession, result["session_id"])
    if session is None or not can_read_agent_session(session, user_id, settings):
        raise ApiError(status_code=404, code="RUN_NOT_FOUND", message="Run not found")
    return result


def _attach_v3_turn_images(
    *,
    db: Session,
    message_id: int | None,
    image_refs: list[str] | None,
    user_id: int,
) -> list[dict]:
    if message_id is None or not image_refs:
        return []
    image_ids: list[str] = []
    for image_ref in image_refs:
        image_id = extract_local_image_id(str(image_ref or ""))
        if image_id and image_id not in image_ids:
            image_ids.append(image_id)
    if not image_ids:
        return []

    assets = {
        asset.id: asset
        for asset in db.exec(
            select(ImageAsset).where(
                ImageAsset.id.in_(image_ids),
                ImageAsset.uploader_user_id == user_id,
            )
        )
    }
    ordered_assets = [assets[image_id] for image_id in image_ids if image_id in assets]
    if not ordered_assets:
        return []

    AgentStore(db).attach_images(message_id, [asset.id for asset in ordered_assets], user_id)
    return [
        {
            "image_id": asset.id,
            "url": asset.public_url,
            "thumbnail_url": f"{asset.public_url}/thumbnail",
            "filename": asset.filename,
            "mime_type": asset.content_type,
            "size_bytes": asset.size_bytes,
            "storage_path": asset.storage_path,
        }
        for asset in ordered_assets
    ]


def _main_provider_for_settings(settings: Settings):
    providers = _providers_for_settings(settings)
    if providers.main_dialog_turn is None:
        raise TeachingAgentProviderUnavailableError(
            agent_name="MainDialogAgent",
            capability="main_dialog_turn",
            outcome="provider_agent_missing",
            provider_backend=settings.teaching_agent_provider_backend,
            details={"required_for": "v3_chat"},
        )
    return providers.main_dialog_turn


def _fallback_v3_diagram_goal(workspace: AgentProblemWorkspace | None, message: str = "", *, english: bool = False) -> str:
    summary = (
        (workspace.problem_summary or workspace.title)
        if workspace is not None
        else message
    )
    summary = (summary or ("the current problem" if english else "当前题目")).strip()
    if english:
        return f"Use a diagram to explain the key state changes in this problem: {summary[:80]}"
    return f"用图解释当前题目的关键状态变化：{summary[:80]}"


def _chunk_v3_visible_response(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in text.strip():
        current += char
        if len(current) >= 12 or char in "。！？；，、,.!?;":
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _providers_for_settings(settings: Settings):
    return build_teaching_agent_providers(_settings_with_required_v3_sidecar_agents(settings))


def _settings_with_required_v3_sidecar_agents(settings: Settings) -> Settings:
    if not settings.teaching_agent_providers_enabled:
        return settings
    if settings.teaching_agent_provider_backend != "agno":
        return settings
    enabled_agents = [agent.strip() for agent in settings.teaching_agent_provider_agents.split(",") if agent.strip()]
    required_agents = (
        "PlanDecomposerAgent",
        "BoardworkAgent",
        "DiagramAgent",
        "MemoryManagerAgent",
        "MemoryConsolidatorAgent",
    )
    missing_agents = [agent for agent in required_agents if agent not in enabled_agents]
    if not missing_agents:
        return settings
    return settings.model_copy(
        update={
            "teaching_agent_provider_agents": ",".join([*enabled_agents, *missing_agents]),
        }
    )


def _visible_stream_provider_for_settings(settings: Settings):
    if not settings.teaching_agent_providers_enabled:
        return None
    if settings.teaching_agent_provider_backend != "agno":
        return None
    if "MainDialogAgent" not in {agent.strip() for agent in settings.teaching_agent_provider_agents.split(",")}:
        return None
    return create_openai_compatible_visible_stream_provider(settings)


def _followup_provider_for_settings(settings: Settings):
    return create_followup_provider(settings)


def _mcp_context_provider_for_settings(
    settings: Settings,
    session_token: str | None,
) -> V3MCPContextProvider | None:
    if not settings.mcp_enabled or not session_token:
        return None
    if settings.mcp_mode != "http":
        return None
    return V3MCPContextProvider(
        url=settings.mcp_http_url,
        session_token=session_token,
        proxy_url=settings.mcp_http_proxy,
        timeout_seconds=8.0,
    )


def _problem_prefetch_failure_reply(
    *,
    repo: TeachingRepository,
    mcp_provider: V3MCPContextProvider,
    request: TeachingTurnRequest,
    session_id: int,
    workspace_id: int | None,
    turn_index: int,
    user_id: int,
) -> str | None:
    if request.problem_id is None:
        return None
    mcp_request = MCPRequestBlock(
        need_mcp=True,
        resource="problem_detail",
        problem_id=request.problem_id,
        reason="explicit_problem_id",
    )
    result = mcp_provider.execute_request(mcp_request, user_message=request.message)
    repo.record_tool_call_trace(
        session_id=session_id,
        workspace_id=workspace_id,
        turn_index=turn_index,
        owner_agent="MCPContextProvider",
        tool_backend="mcp_http",
        tool_name=result.tool_name,
        status=result.status,
        latency_ms=result.latency_ms,
        request_summary=f"problem_detail:{result.request_arguments}",
        response_summary=result.error_message or result.raw_text[:300],
        error_type=result.error_type,
        payload={
            "resource": "problem_detail",
            "problem_id": request.problem_id,
            "request_arguments": result.request_arguments,
            "parsed_payload": result.parsed_payload,
        },
    )
    if result.status == "completed":
        return None
    return render_mcp_tool_reply(mcp_request, result, authenticated_user_id=user_id)


def _prefetch_mcp_problem_workspace(
    *,
    repo: TeachingRepository,
    request: TeachingTurnRequest,
    session_id: int,
    user_id: int,
    source_message_id: int | None,
    source_turn_index: int,
    mcp_provider: V3MCPContextProvider,
) -> AgentProblemWorkspace | None:
    if request.problem_id is None:
        return None
    cached_workspace = repo.find_workspace_by_problem_id(
        session_id=session_id,
        user_id=user_id,
        problem_id=request.problem_id,
    )
    if cached_workspace is not None and (cached_workspace.problem_summary or "").strip():
        return repo.activate_workspace_for_session(int(cached_workspace.id))
    try:
        problem = mcp_provider.fetch_problem(request.problem_id)
    except Exception:
        return None
    if not isinstance(problem, dict):
        return None
    content = str(problem.get("content") or problem.get("problem_summary") or "").strip()
    if not content:
        return None
    title = str(problem.get("title") or problem.get("problem_title") or f"题目 {request.problem_id}").strip()
    existing = repo.find_workspace_by_problem_id(
        session_id=session_id,
        user_id=user_id,
        problem_id=request.problem_id,
    )
    if existing is None:
        workspace = repo.create_workspace(
            session_id=session_id,
            user_id=user_id,
            title=title[:255],
            guidance_depth=_to_model_depth(request.guidance_depth or GuidanceDepth.DIRECT),
            problem_id=request.problem_id,
            problem_summary=content,
            source=request.source or "problem_help",
        )
    else:
        workspace = repo.activate_workspace_for_session(int(existing.id))
        workspace.title = title[:255]
        workspace.problem_summary = content
        workspace.source = request.source or workspace.source or "problem_help"
        workspace.last_active_turn_index = source_turn_index
        workspace.update_time = datetime.utcnow()
        repo.db.add(workspace)
        repo.db.flush()
    if source_message_id is not None and workspace.id is not None:
        repo.assign_visible_message_workspace(source_message_id, int(workspace.id))
    if workspace.id is not None:
        repo.record_tool_call_trace(
            session_id=session_id,
            workspace_id=int(workspace.id),
            turn_index=source_turn_index,
            owner_agent="MCPContextProvider",
            tool_backend="mcp_http",
            tool_name="get_problem_info",
            status="completed",
            request_summary=f"problem_detail:{{'problemId': '{request.problem_id}'}}",
            response_summary=title[:300],
            payload={
                "resource": "problem_detail",
                "problem_id": request.problem_id,
                "result_keys": sorted(str(key) for key in problem.keys()),
                "content_length": len(content),
            },
        )
        _record_mcp_problem_context(
            repo=repo,
            workspace=workspace,
            problem=problem,
            title=title,
            content=content,
        )
    return workspace


def _mcp_tool_executor(
    *,
    repo: TeachingRepository,
    mcp_provider: V3MCPContextProvider | None,
    session_id: int,
    user_id: int,
    workspace_id: int | None,
    turn_index: int,
):
    if mcp_provider is None:
        return None

    def execute(request: MCPRequestBlock, context: dict, emit) -> str | None:
        result = mcp_provider.execute_request(
            request,
            user_message=str(context.get("user_message") or ""),
        )
        response_summary = result.error_message or result.raw_text[:300]
        safe_arguments = {
            key: "[redacted]" if key.lower() in {"code", "content", "sourcecode", "source_code"} else value
            for key, value in result.request_arguments.items()
        }
        repo.record_tool_call_trace(
            session_id=session_id,
            workspace_id=workspace_id if workspace_id is not None else context.get("workspace_id"),
            turn_index=turn_index,
            owner_agent="MCPToolExecutor",
            tool_backend="mcp_http",
            tool_name=result.tool_name,
            status=result.status,
            latency_ms=result.latency_ms,
            request_summary=f"{request.resource}:{safe_arguments}",
            response_summary=response_summary,
            error_type=result.error_type,
            payload={
                "resource": request.resource,
                "problem_id": request.problem_id,
                "submission_id": request.submission_id,
                "request_arguments": safe_arguments,
                "parsed_payload": result.parsed_payload,
            },
        )
        emit(
            {
                "type": "mcp_tool_result",
                "status": result.status,
                "resource": request.resource,
                "tool_name": result.tool_name,
                "latency_ms": result.latency_ms,
                "error_type": result.error_type,
                "error_message": result.error_message,
            }
        )
        return render_mcp_tool_reply(request, result, authenticated_user_id=user_id)

    return execute


def _record_mcp_problem_context(
    *,
    repo: TeachingRepository,
    workspace: AgentProblemWorkspace,
    problem: dict,
    title: str,
    content: str,
) -> None:
    if workspace.id is None:
        return
    payload = {
        "problem_context_id": f"workspace:{workspace.id}:problem:mcp:{workspace.problem_id}",
        "version": 1,
        "problem_id": workspace.problem_id,
        "problem_title": title,
        "problem_summary": content,
        "source": problem.get("source") or "mcp",
        "raw_problem_text_available": True,
        "examples": problem.get("examples") if isinstance(problem.get("examples"), list) else [],
        "constraints": problem.get("constraints") if isinstance(problem.get("constraints"), list) else [],
    }
    repo.record_blackboard_entry(
        workspace_id=int(workspace.id),
        session_id=workspace.session_id,
        entry_type="problem_context",
        content=payload["problem_context_id"],
        writer_agent="MCPContextProvider",
        source_message_id=None,
        payload=payload,
        visibility="internal",
    )


def _resolve_workspace(
    *,
    repo: TeachingRepository,
    request: TeachingTurnRequest,
    session_id: int,
    user_id: int,
) -> AgentProblemWorkspace | None:
    session = repo.db.get(AgentSession, session_id)
    if session is not None and session.problem_id is not None:
        if request.workspace_id is not None:
            workspace = repo.get_workspace(request.workspace_id)
            if (
                workspace is None
                or workspace.session_id != session_id
                or workspace.user_id != user_id
                or workspace.problem_id != session.problem_id
            ):
                raise ApiError(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
            repo.activate_workspace_for_session(int(workspace.id))
            return _workspace_with_requested_guidance(repo, workspace, request)

        owner_workspace = repo.find_workspace_by_problem_id(
            session_id=session_id,
            user_id=user_id,
            problem_id=session.problem_id,
        )
        if owner_workspace is not None:
            repo.activate_workspace_for_session(int(owner_workspace.id))
            return _workspace_with_requested_guidance(repo, owner_workspace, request)
        return None

    if request.workspace_id is not None:
        workspace = repo.get_workspace(request.workspace_id)
        if workspace is None or workspace.session_id != session_id or workspace.user_id != user_id:
            raise ApiError(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
        repo.activate_workspace_for_session(int(workspace.id))
        return _workspace_with_requested_guidance(repo, workspace, request)

    active_workspace = repo.get_active_workspace(session_id)
    if active_workspace is not None:
        return _workspace_with_requested_guidance(repo, active_workspace, request)

    return None


def _workspace_with_requested_guidance(
    repo: TeachingRepository,
    workspace: AgentProblemWorkspace,
    request: TeachingTurnRequest,
) -> AgentProblemWorkspace:
    if workspace.id is None or request.guidance_depth is None:
        return workspace
    requested_depth = _to_model_depth(request.guidance_depth)
    if workspace.guidance_depth == requested_depth and workspace.depth_override == request.depth_override:
        return workspace
    return repo.update_workspace_guidance_depth(
        int(workspace.id),
        requested_depth,
        request.depth_override,
    )


def _get_or_create_session(db: Session, request: TeachingTurnRequest, user_id: int) -> AgentSession:
    if request.session_id:
        session = db.exec(
            select(AgentSession).where(AgentSession.session_uuid == request.session_id)
        ).first()
        if session is None or session.user_id != user_id or session.status == AgentSessionStatus.DELETED:
            raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
        return session

    session = AgentSession(
        session_uuid=request.session_id or str(uuid4()),
        user_id=user_id,
        mode=AgentMode.SOCRATIC,
        status=AgentSessionStatus.ACTIVE,
        source=AgentSessionSource.WEB,
    )
    db.add(session)
    db.flush()
    return session


def _ensure_v3_user(db: Session, user_id: int, settings: Settings) -> None:
    if db.get(User, user_id) is not None:
        return
    if settings.app_env not in {"dev", "test"}:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    now = datetime.utcnow()
    db.add(User(id=user_id, username=f"test-user-{user_id}@oj.local", nickname=f"测试用户{user_id}", create_time=now, update_time=now))
    db.flush()


SessionFactory = Callable[[], Session]


def _sidecar_session_factory(db: Session) -> SessionFactory:
    bind = db.get_bind()
    if not isinstance(bind, Engine):
        raise RuntimeError("v3 sidecar session factory requires an Engine bind")

    def factory() -> Session:
        return Session(bind=bind)

    return factory


def _run_repo_sidecar(
    *,
    session_factory: SessionFactory,
    run: Callable[[TeachingRepository], list[dict]],
    emit,
) -> None:
    with session_factory() as sidecar_db:
        try:
            events = run(TeachingRepository(sidecar_db))
            sidecar_db.commit()
        except Exception:
            sidecar_db.rollback()
            raise
    for event in events:
        emit(event)


def _plan_sidecar(
    *,
    session_factory: SessionFactory,
    provider: Callable[[dict], dict | None] | None = None,
    provider_timeout_seconds: float = 8.0,
):
    def sidecar(context: dict, emit) -> None:
        _run_repo_sidecar(
            session_factory=session_factory,
            run=lambda repo: PlanStateAgent(
                repo=repo,
                provider=provider,
                provider_timeout_seconds=provider_timeout_seconds,
            ).run(context),
            emit=emit,
        )

    return sidecar


def _status_only_sidecar(event_type: str):
    def sidecar(_context: dict, emit) -> None:
        emit({"type": event_type, "status": "completed"})

    return sidecar


def _diagram_sidecar(*, provider: Callable[[dict], dict | None] | None = None):
    def sidecar(context: dict, emit) -> None:
        for event in DiagramAgent(provider=provider).run(context):
            emit(event)

    return sidecar


def _persisting_diagram_sidecar(
    *,
    session_factory: SessionFactory,
    provider: Callable[[dict], dict | None] | None = None,
    semantic_provider: Callable[[dict], dict | None] | None = None,
    provider_timeout_seconds: float = DEFAULT_DIAGRAM_PROVIDER_TIMEOUT_SECONDS,
):
    def sidecar(context: dict, emit) -> None:
        events = _diagram_events_with_model_or_fallback(
            context,
            provider=provider,
            semantic_provider=semantic_provider,
            timeout_seconds=provider_timeout_seconds,
        )
        ready_events: list[tuple[dict, dict]] = []
        completed_events: list[dict] = []
        session_uuid = str(context.get("session_uuid") or "")
        for event in events:
            if event.get("type") != "diagram_ready":
                if event.get("type") == "diagram_status" and event.get("status") == "completed":
                    completed_events.append(event)
                else:
                    emit(event)
                continue
            scene = event.get("scene")
            if isinstance(scene, dict):
                ready_events.append((event, scene))
        if not ready_events:
            for event in completed_events:
                emit(event)
            return
        with session_factory() as sidecar_db:
            try:
                store = AgentStore(sidecar_db)
                runtime_session = store.get_session(session_uuid)
                if runtime_session is None:
                    raise ValueError("diagram session was not found")
                persisted_events: list[dict] = []
                for event, scene in ready_events:
                    scene_record = store.upsert_diagram_scene(runtime_session, scene)
                    store.get_diagram_scene_payload(session_uuid, scene_record.scene_uuid)
                    persisted_events.append({**event, "diagram_scene_id": scene_record.scene_uuid})
                sidecar_db.commit()
            except Exception as exc:
                sidecar_db.rollback()
                emit({"type": "diagram_status", "status": "failed", "message": f"图示保存失败：{exc}"})
                return
        for event in persisted_events:
            emit(event)
        for event in completed_events:
            emit(event)

    return sidecar


def _diagram_events_with_model_or_fallback(
    context: dict,
    *,
    provider: Callable[[dict], dict | None] | None,
    semantic_provider: Callable[[dict], dict | None] | None = None,
    timeout_seconds: float,
) -> list[dict]:
    if provider is None and semantic_provider is None:
        return production_diagram_events(context, None)

    results: Queue[tuple[str, list[dict] | None]] = Queue(maxsize=1)

    def run_provider() -> None:
        try:
            if semantic_provider is not None:
                results.put(("ok", production_diagram_events(context, semantic_provider)))
                return
            results.put(("ok", production_diagram_events(context, provider)))
        except Exception as exc:
            logger.warning("diagram provider failed: %s", str(exc)[:180])
            results.put(("error", None))

    Thread(target=run_provider, daemon=True).start()
    try:
        status, events = results.get(timeout=max(0.0, timeout_seconds))
    except Empty:
        if semantic_provider is not None:
            return _failed_semantic_diagram_events(context)
        return _fallback_diagram_events(context)
    if status != "ok" or not events or not _contains_ready_diagram(events):
        if semantic_provider is not None:
            return events if status == "ok" and events else _failed_semantic_diagram_events(context)
        return _fallback_diagram_events(context)
    return events


def _failed_semantic_diagram_events(context: dict) -> list[dict]:
    message = "The diagram could not be verified. Please try again." if context.get("response_language") == "en" else "图示未通过校验，请重试。"
    return [{"type": "diagram_status", "status": "failed", "message": message}]


def _fallback_diagram_events(context: dict) -> list[dict]:
    return DiagramAgent(provider=None).run(context)


def _contains_ready_diagram(events: list[dict]) -> bool:
    return any(event.get("type") == "diagram_ready" for event in events)


def _memory_sidecar(
    *,
    session_factory: SessionFactory,
    provider: Callable[[dict], dict | None] | None = None,
):
    def sidecar(context: dict, emit) -> None:
        _run_repo_sidecar(
            session_factory=session_factory,
            run=lambda repo: MemoryAgent(repo=repo, provider=provider).run(context),
            emit=emit,
        )

    return sidecar


def _compaction_sidecar(
    *,
    session_factory: SessionFactory,
    provider: Callable[[dict], dict | None] | None = None,
):
    def sidecar(context: dict, emit) -> None:
        _run_repo_sidecar(
            session_factory=session_factory,
            run=lambda repo: ProblemCompactionAgent(repo=repo, provider=provider).run(context),
            emit=emit,
        )

    return sidecar


def _whiteboard_sidecar(
    *,
    session_factory: SessionFactory,
    session_id: int,
    workspace_id: int | None,
    provider: Callable[[dict], dict | None] | None = None,
    current_step_wait_seconds: float = 2.0,
):
    def sidecar(context: dict, emit) -> None:
        resolved_workspace_id = context.get("workspace_id") if isinstance(context.get("workspace_id"), int) else workspace_id
        sidecar_context = {**context, "session_id": session_id, "workspace_id": resolved_workspace_id}
        _run_repo_sidecar(
            session_factory=session_factory,
            run=lambda repo: WhiteboardAgent(
                repo=repo,
                handoff_provider=provider,
                current_step_wait_seconds=current_step_wait_seconds,
            ).run(sidecar_context),
            emit=emit,
        )

    return sidecar


def _problem_summary_from_request(request: TeachingTurnRequest) -> str:
    return _problem_summary_from_text(request.message)


def _problem_summary_from_text(text: str) -> str:
    compact = "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if len(compact) <= 600:
        return compact
    return f"{compact[:597].rstrip()}..."


def _workspace_title(summary: str) -> str:
    if not summary:
        return "当前题目"
    first_line = summary.splitlines()[0].strip()
    if len(first_line) <= 32:
        return first_line
    return f"{first_line[:31]}..."


def _to_model_depth(depth: GuidanceDepth) -> GuidanceDepthValue:
    return GuidanceDepthValue(depth.value)


