from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, select
from typing import List, Optional
from pathlib import Path
import asyncio
import traceback
import jwt
import uuid
import json
import logging
import re
from time import perf_counter
from urllib.parse import unquote, urlparse

from app.dependencies import get_agent_manager
from app.agents.manager import AgentManager
from app.agent_store import AgentStore
from app.boardwork_trace import build_trace_payload
from app.database import get_session, session_context
from app.config import get_settings
from app.api_errors import ApiError
from app.image_storage import (
    build_thumbnail_public_url,
    build_image_public_url,
    build_image_storage_path,
    build_thumbnail_storage_path,
    ensure_thumbnail_file,
    generate_image_id,
    validate_image_upload,
)
from app.jwt_auth import decode_java_compatible_jwt
from app.models import (
    AgentBoardNode,
    AgentBoardworkStatus,
    AgentMessageImage,
    AgentProblemWorkspace,
    AgentMode,
    AgentSession,
    AgentSessionSource,
    AgentSessionFactType,
    AgentSessionStatus,
    ImageAsset,
    ProblemWorkspaceStatus,
    User,
)
from app.rate_limits import rate_limit_dependency
from app.session_access import can_read_agent_session, can_write_agent_session
from app.schemas import (
    BoardCloseRequest,
    BoardCloseResponse,
    BoardNodeCreateRequest,
    BoardNodeResponse,
    BoardStateResponse,
    BoardStartRequest,
    BoardworkResponse,
    ChatRequest,
    CreateAgentSessionRequest,
    DeleteSessionResponse,
    ExamProblemTimingResponse,
    ExamProblemTimingStartRequest,
    DiagramSceneResponse,
    ImageUploadResponse,
    SessionSchema,
    UpdateSessionRequest,
    DeleteTurnsFromMessageResponse,
)
from fastapi import UploadFile, File

router = APIRouter()
logger = logging.getLogger("uvicorn.error")
settings = get_settings()

def _mask_token(token: str, prefix: int = 8, suffix: int = 4) -> str:
    if not token:
        return ""
    token = token.strip()
    if len(token) <= prefix + suffix:
        return token
    return f"{token[:prefix]}...{token[-suffix:]}"


def _coerce_positive_user_id(value: object) -> int | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        user_id = int(text)
    except (TypeError, ValueError):
        return None

    return user_id if user_id > 0 else None


def _extract_authenticated_user_id(payload: dict) -> int:
    raw_id = payload.get("id")
    if raw_id is not None and str(raw_id).strip():
        user_id = _coerce_positive_user_id(raw_id)
        if user_id is None:
            raise ApiError(
                status_code=401,
                code="AUTH_INVALID",
                message="Invalid token payload: id must be a positive integer",
            )
        return user_id

    user_id = _coerce_positive_user_id(payload.get("sub"))
    if user_id is not None:
        return user_id

    raise ApiError(
        status_code=401,
        code="AUTH_INVALID",
        message="Invalid token payload: missing numeric user id",
    )


def _ensure_user_exists(user_id: int) -> None:
    with session_context() as db:
        exists = db.exec(select(User.id).where(User.id == user_id)).first()
    if exists is None:
        raise ApiError(status_code=403, code="USER_NOT_FOUND", message="User not found")


def _get_owned_session(db: Session, session_uuid: str, user_id: str) -> AgentSession:
    session = db.exec(
        select(AgentSession).where(
            AgentSession.session_uuid == session_uuid,
            AgentSession.user_id == int(user_id),
        )
    ).first()
    if session is None or not can_write_agent_session(session, int(user_id), settings):
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found or access denied")
    return session


def _find_owned_session(db: Session, session_uuid: str, user_id: str) -> AgentSession | None:
    session = db.exec(
        select(AgentSession).where(
            AgentSession.session_uuid == session_uuid,
            AgentSession.user_id == int(user_id),
            AgentSession.status != AgentSessionStatus.DELETED,
        )
    ).first()
    return session if session is not None and can_write_agent_session(session, int(user_id), settings) else None


def _resolve_optional_agent_session_id(db: Session, session_uuid: str | None, user_id: str) -> int | None:
    if not session_uuid:
        return None
    session = _get_owned_session(db, session_uuid, user_id)
    return session.id


def _serialize_board_node(node: AgentBoardNode) -> BoardNodeResponse:
    return BoardNodeResponse(
        node_id=int(node.id),
        title=node.title,
        summary=node.summary,
        status=node.status,
        trace_payload=node.trace_payload,
        coach_note=node.coach_note,
        sort_order=node.sort_order,
        is_active=node.is_active,
        source_turn_index=node.source_turn_index,
        created_at=node.create_time,
        updated_at=node.update_time,
    )


def _serialize_boardwork(boardwork) -> BoardworkResponse:
    return BoardworkResponse(
        boardwork_id=int(boardwork.id),
        title=boardwork.title,
        status=boardwork.status,
        source_turn_index=boardwork.source_turn_index,
        closed_turn_index=boardwork.closed_turn_index,
        created_at=boardwork.create_time,
        updated_at=boardwork.update_time,
    )


def _extract_context_from_referer(referer: str | None) -> dict[str, int | str | None]:
    if not referer:
        return {
            "source": None,
            "problem_id": None,
            "submission_id": None,
            "course_id": None,
            "class_id": None,
            "exam_id": None,
        }

    path = urlparse(referer).path or ""
    matchers: tuple[tuple[str, str, str], ...] = (
        (r"/problem/(\d+)", "problem_id", "problem_help"),
        (r"/submission/(\d+)", "submission_id", "submission_debug"),
        (r"/course/(\d+)", "course_id", "course_tutor"),
        (r"/class/(\d+)", "class_id", "course_tutor"),
        (r"/exam/(\d+)", "exam_id", "exam_assistant"),
    )
    for pattern, field_name, source in matchers:
        match = re.search(pattern, path)
        if match:
            return {
                "source": source,
                "problem_id": int(match.group(1)) if field_name == "problem_id" else None,
                "submission_id": int(match.group(1)) if field_name == "submission_id" else None,
                "course_id": int(match.group(1)) if field_name == "course_id" else None,
                "class_id": int(match.group(1)) if field_name == "class_id" else None,
                "exam_id": int(match.group(1)) if field_name == "exam_id" else None,
            }
    return {
        "source": None,
        "problem_id": None,
        "submission_id": None,
        "course_id": None,
        "class_id": None,
        "exam_id": None,
    }


def _resolve_request_context(request: ChatRequest, http_request: Request) -> dict[str, int | str | None]:
    resolved = {
        "source": request.source,
        "problem_id": request.problem_id,
        "submission_id": request.submission_id,
        "course_id": request.course_id,
        "class_id": request.class_id,
        "exam_id": request.exam_id,
    }
    explicit_context = (
        resolved["source"] is not None
        or resolved["problem_id"] is not None
        or resolved["submission_id"] is not None
        or resolved["course_id"] is not None
        or resolved["class_id"] is not None
        or resolved["exam_id"] is not None
    )
    if explicit_context:
        return resolved

    inferred = _extract_context_from_referer(http_request.headers.get("referer"))
    for key, value in inferred.items():
        if resolved.get(key) is None and value is not None:
            resolved[key] = value
    return resolved

async def get_current_user_id_and_token(
    x_accoding_session: Optional[str] = Header(None, alias="X-Accoding-Session")
) -> tuple[str, str]:
    if not x_accoding_session:
        raise ApiError(status_code=401, code="AUTH_INVALID", message="X-Accoding-Session header missing")
    logger.info("Incoming X-Accoding-Session token: %s", _mask_token(x_accoding_session))
    try:
        payload = decode_java_compatible_jwt(x_accoding_session, settings.token_secret)
        user_id = _extract_authenticated_user_id(payload)
        _ensure_user_exists(user_id)
        return str(user_id), x_accoding_session
    except jwt.PyJWTError as e:
        raise ApiError(status_code=401, code="AUTH_INVALID", message=f"Invalid token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Token decode error: {e}")
        raise ApiError(status_code=401, code="AUTH_INVALID", message="Token decode failed")


chat_rate_limit = rate_limit_dependency("chat", get_current_user_id_and_token)
image_rate_limit = rate_limit_dependency("image", get_current_user_id_and_token)

@router.post("/chat", dependencies=[Depends(chat_rate_limit)])
async def chat(
    http_request: Request,
    request: ChatRequest,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
    db: Session = Depends(get_session),
):
    user_id, session_token = user_data
    try:
        # If session_id is provided, use it. Otherwise generate a new UUID.
        session_id = request.session_id if request.session_id else str(uuid.uuid4())
        resolved_context = _resolve_request_context(request, http_request)

        response = await manager.arun(
            request.message,
            session_id=session_id,
            user_id=user_id,
            session_token=session_token,
            socratic=request.socratic_mode,
            source=resolved_context["source"],
            problem_id=resolved_context["problem_id"],
            submission_id=resolved_context["submission_id"],
            course_id=resolved_context["course_id"],
            class_id=resolved_context["class_id"],
            exam_id=resolved_context["exam_id"],
            images=_owned_chat_image_references(db, request.images, user_id),
            audio=request.audio,
        )

        history = manager.get_session_history(session_id)

        return {
            "session_id": session_id,
            "response": response,
            "history": history
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))

async def sse_generator(generator, session_id, *, clock=perf_counter):
    started_at = clock()
    first_token_metric_sent = False
    try:
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"

        async for payload in generator:
            if isinstance(payload, dict):
                event_type = payload.get("type")
                if event_type != "text":
                    continue
                incoming = payload.get("content") or ""
            else:
                incoming = str(payload)

            if not incoming:
                continue

            if not first_token_metric_sent:
                first_token_metric_sent = True
                first_token_latency_ms = max(0, int(round((clock() - started_at) * 1000)))
                metric_payload = {
                    "session_id": session_id,
                    "metric": "stream_first_token_latency_ms",
                    "value_ms": first_token_latency_ms,
                }
                yield f"data: {json.dumps(metric_payload)}\n\n"

            yield f"data: {json.dumps({'text': incoming})}\n\n"

    except asyncio.CancelledError as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    yield "data: [DONE]\n\n"

@router.post("/chat/stream", dependencies=[Depends(chat_rate_limit)])
async def chat_stream(
    http_request: Request,
    request: ChatRequest,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
    db: Session = Depends(get_session),
):
    user_id, session_token = user_data
    try:
        session_id = request.session_id if request.session_id else str(uuid.uuid4())
        resolved_context = _resolve_request_context(request, http_request)
        logger.info("Starting streaming response for session_id: %s", session_id)
        return StreamingResponse(
            sse_generator(manager.astream(
                request.message,
                session_id=session_id,
                user_id=user_id,
                session_token=session_token,
                socratic=request.socratic_mode,
                source=resolved_context["source"],
                problem_id=resolved_context["problem_id"],
                submission_id=resolved_context["submission_id"],
                course_id=resolved_context["course_id"],
                class_id=resolved_context["class_id"],
                exam_id=resolved_context["exam_id"],
                images=_owned_chat_image_references(db, request.images, user_id),
                audio=request.audio,
            ), session_id),
            media_type="text/event-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/exam/timings/start", response_model=ExamProblemTimingResponse)
async def start_exam_problem_timing(
    request: ExamProblemTimingStartRequest,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        store = AgentStore(db)
        agent_session_id = _resolve_optional_agent_session_id(db, request.session_id, user_id)
        timing = store.record_exam_problem_answer_start(
            user_id=int(user_id),
            exam_id=request.exam_id,
            problem_id=request.problem_id,
            session_id=agent_session_id,
        )
        db.commit()
        db.refresh(timing)
        return timing
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.get("/history")
async def get_history_current_user(
    session_id: Optional[str] = None, # Allow querying specific session history
    page: int = 1,
    limit: int = 10,
    after_message_id: Optional[int] = None,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token)
):
    user_id, _ = user_data
    try:
        # Default to the main session if not specified, though UUID system encourages explicit session_id
        target_session_id = session_id if session_id else f"user_session_{user_id}"
        if session_id:
            with session_context() as db:
                session = db.exec(select(AgentSession).where(AgentSession.session_uuid == session_id)).first()
                if session is None or not can_read_agent_session(session, int(user_id), settings):
                    raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
        
        offset = 0 if after_message_id is not None else (page - 1) * limit
        history = manager.get_session_history(
            target_session_id,
            limit=limit,
            offset=offset,
            after_message_id=after_message_id,
        )
        return {
            "history": history,
            "meta": manager.get_session_history_meta(target_session_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))

# Session Management Endpoints

@router.get("/sessions", response_model=List[SessionSchema])
async def list_user_sessions(
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token)
):
    user_id, _ = user_data
    try:
        sessions = manager.get_user_sessions(user_id, showcase_uuid=settings.demo_showcase_session_uuid if settings.demo_mode else None)
        return sessions
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/sessions", response_model=SessionSchema)
async def create_user_session(
    request: CreateAgentSessionRequest,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        session = AgentStore(db).get_or_create_session(
            str(uuid.uuid4()),
            int(user_id),
            AgentMode.SOCRATIC,
            source=AgentSessionSource.WEB,
            problem_id=request.problem_id,
        )
        db.commit()
        return next(
            item
            for item in AgentStore(db).list_user_sessions(int(user_id))
            if item["session_id"] == session.session_uuid
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise ApiError(status_code=500, code="SESSION_CREATE_FAILED", message=str(e))


@router.get("/sessions/groups")
async def list_user_session_groups(
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        return manager.get_user_session_groups(user_id, showcase_uuid=settings.demo_showcase_session_uuid if settings.demo_mode else None)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))

@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
async def delete_user_session(
    session_id: str,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token)
):
    user_id, _ = user_data
    try:
        success = manager.delete_session(session_id, user_id)
        if success:
            return {"success": True, "message": "Session deleted"}
        else:
            # Could be not found or not owned by user
            return {"success": False, "message": "Session not found or access denied"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/sessions/{session_id}", response_model=SessionSchema)
async def update_user_session(
    session_id: str,
    request: UpdateSessionRequest,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token)
):
    user_id, _ = user_data
    try:
        success = manager.rename_session(session_id, request.title, user_id)
        if success:
            sessions = manager.get_user_sessions(user_id)
            for session in sessions:
                if session.get("session_id") == session_id:
                    return session
            return {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": 0,
                "updated_at": 0,
                "title": request.title,
                "preview": "",
                "source": None,
                "problem_id": None,
                "submission_id": None,
                "course_id": None,
                "class_id": None,
                "exam_id": None,
            }
        else:
             raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found or access denied")
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/chat/{session_id}/undo")
async def undo_last_turn(
    session_id: str,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token)
):
    user_id, _ = user_data
    try:
        # Ideally verify ownership first
        success = manager.delete_last_turn(session_id, user_id)
        if success:
             # Fetch updated history
             history = manager.get_session_history(session_id)
             return {"success": True, "message": "Last turn undone", "history": history}
        else:
             return {"success": False, "message": "Nothing to undo or session not found"}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/chat/{session_id}/stop")
async def stop_generation(
    session_id: str,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        success = manager.request_stop(session_id, user_id)
        if success:
            return {"success": True, "message": "Generation stopped"}
        return {"success": False, "message": "Session not found or access denied"}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        success = manager.delete_message(message_id, user_id)
        if success:
            return {"success": True, "message": "Message deleted"}
        return {"success": False, "message": "Message not found or access denied"}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.delete("/messages/{message_id}/response-group")
async def delete_response_group(
    message_id: int,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        result = manager.delete_response_group(message_id, user_id)
        if not result.get("deleted"):
            raise ApiError(status_code=404, code="MESSAGE_NOT_FOUND", message="Response not found or access denied")
        return {
            "success": True,
            "deleted_message_ids": result.get("message_ids") or [],
            "deleted_scene_ids": result.get("scene_ids") or [],
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.delete("/messages/{message_id}/turns-from", response_model=DeleteTurnsFromMessageResponse)
async def delete_turns_from_message(
    message_id: int,
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        result = manager.delete_turns_from_message(message_id, user_id)
        if result.get("deleted"):
            return {
                "success": True,
                "message": "Turns deleted",
                "session_id": result.get("session_uuid"),
                "start_turn_index": result.get("start_turn_index"),
            }
        return {
            "success": False,
            "message": "Message not found or access denied",
            "session_id": None,
            "start_turn_index": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.get("/diagram/{session_id}/scenes/{scene_id}", response_model=DiagramSceneResponse)
async def get_diagram_scene(
    session_id: str,
    scene_id: str,
    db: Session = Depends(get_session),
    manager: AgentManager = Depends(get_agent_manager),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        session = db.exec(select(AgentSession).where(AgentSession.session_uuid == session_id)).first()
        if session is None or not can_read_agent_session(session, int(user_id), settings):
            raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
        scene = manager.get_diagram_scene(session_id, scene_id)
        if scene is None:
            raise ApiError(status_code=404, code="DIAGRAM_SCENE_NOT_FOUND", message="Diagram scene not found")
        return scene
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/board/{session_id}/nodes", response_model=BoardNodeResponse)
async def create_board_node(
    session_id: str,
    request: BoardNodeCreateRequest,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    session = _find_owned_session(db, session_id, user_id)
    if session is None:
        return BoardStateResponse(
            session_id=session_id,
            session_summary=None,
            learning_state=None,
            boardwork=None,
            nodes=[],
        )
    store = AgentStore(db)
    try:
        active_boardwork = store.get_or_create_active_boardwork(
            session_id=session.id,
            title=request.title,
            source_turn_index=request.source_turn_index,
        )
        node = store.create_board_node(
            session_id=session.id,
            title=request.title,
            summary=request.summary,
            source_turn_index=request.source_turn_index,
            trace_payload=build_trace_payload(
                summary=request.summary,
                source_turn_index=request.source_turn_index,
                coach_note=f"先围绕“{request.title.strip()}”确认当前关键判断。",
                promotion_reason="用户手动创建了当前板书节点。",
            ),
        )
        if active_boardwork.title == "当前题目白板":
            active_boardwork.title = request.title.strip()
            db.add(active_boardwork)
        db.commit()
        db.refresh(node)
        return _serialize_board_node(node)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.get("/board/{session_id}", response_model=BoardStateResponse)
async def get_board_state(
    session_id: str,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    session = db.exec(select(AgentSession).where(AgentSession.session_uuid == session_id)).first()
    if session is not None and not can_read_agent_session(session, int(user_id), settings):
        session = None
    if session is None:
        return BoardStateResponse(
            session_id=session_id,
            session_summary=None,
            learning_state=None,
            boardwork=None,
            nodes=[],
        )
    store = AgentStore(db)
    active_boardwork = store.get_active_boardwork(session.id)
    active_workspace = db.exec(
        select(AgentProblemWorkspace)
        .where(
            AgentProblemWorkspace.session_id == session.id,
            AgentProblemWorkspace.status == ProblemWorkspaceStatus.ACTIVE,
        )
        .order_by(AgentProblemWorkspace.last_active_turn_index.desc(), AgentProblemWorkspace.id.desc())
        .limit(1)
    ).first()
    if (
        active_workspace is not None
        and active_boardwork is not None
        and active_boardwork.workspace_id != active_workspace.id
    ):
        active_boardwork = None
        nodes = []
    else:
        nodes = [_serialize_board_node(node) for node in store.list_board_nodes(session_id)]
    learning_state_fact = next(
        iter(
            store.list_session_facts(
                session.id,
                fact_type=AgentSessionFactType.LEARNING_STATE,
                limit=1,
            )
        ),
        None,
    )
    return BoardStateResponse(
        session_id=session_id,
        session_summary=session.summary,
        learning_state=learning_state_fact.content if learning_state_fact is not None else None,
        boardwork=_serialize_boardwork(active_boardwork) if active_boardwork is not None else None,
        nodes=nodes,
    )


@router.post("/board/{session_id}/close", response_model=BoardCloseResponse)
async def close_boardwork(
    session_id: str,
    request: BoardCloseRequest,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    session = _get_owned_session(db, session_id, user_id)
    if request.outcome not in {AgentBoardworkStatus.COMPLETED, AgentBoardworkStatus.ABANDONED}:
        raise ApiError(status_code=400, code="BOARDWORK_CLOSE_INVALID", message="Invalid boardwork close outcome")
    store = AgentStore(db)
    try:
        boardwork = store.close_active_boardwork(
            session_id=session.id,
            status=request.outcome,
            closed_turn_index=store.next_turn_index(session.id),
        )
        if boardwork is None or boardwork.id is None:
            raise ApiError(status_code=404, code="BOARDWORK_NOT_FOUND", message="No active boardwork to close")
        db.commit()
        db.refresh(boardwork)
        return BoardCloseResponse(
            session_id=session_id,
            boardwork_id=int(boardwork.id),
            status=boardwork.status,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/board/{session_id}/start", response_model=BoardworkResponse)
async def start_boardwork(
    session_id: str,
    request: BoardStartRequest,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    session = _get_owned_session(db, session_id, user_id)
    store = AgentStore(db)
    try:
        boardwork = store.start_new_boardwork(
            session_id=session.id,
            title=request.title,
            source_turn_index=request.source_turn_index,
        )
        db.commit()
        db.refresh(boardwork)
        return _serialize_boardwork(boardwork)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


@router.post("/media/images", response_model=ImageUploadResponse, dependencies=[Depends(image_rate_limit)])
async def upload_image(
    request: Request,
    image: UploadFile = File(...),
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    try:
        content = await image.read()
        validate_image_upload(
            filename=image.filename or "",
            content_type=image.content_type or "",
            size=len(content),
            max_bytes=settings.media_max_upload_bytes,
        )

        image_id = generate_image_id()
        # 图片文件落盘，数据库只保存元信息与访问地址，便于后续迁移到正式服务器或对象存储。
        storage_path = build_image_storage_path(Path(settings.media_upload_dir), image_id, image.filename or "upload.bin")
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)

        public_base_url = (settings.media_public_base_url or "").strip().rstrip("/")
        request_origin = str(request.base_url).rstrip("/")
        base_origin = public_base_url or request_origin
        public_url = build_image_public_url(base_origin, image_id)
        thumbnail_url = build_thumbnail_public_url(base_origin, image_id)
        asset = ImageAsset(
            id=image_id,
            filename=image.filename or storage_path.name,
            storage_path=str(storage_path),
            public_url=public_url,
            content_type=image.content_type or "application/octet-stream",
            size_bytes=len(content),
            uploader_user_id=int(user_id),
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

        return ImageUploadResponse(
            image_id=asset.id,
            url=asset.public_url,
            thumbnail_url=thumbnail_url,
            filename=asset.filename,
            mime_type=asset.content_type,
            size=asset.size_bytes,
            created_at=asset.created_at,
        )
    except ValueError as e:
        raise ApiError(status_code=400, code="IMAGE_INVALID", message=str(e))
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise ApiError(status_code=500, code="INTERNAL_ERROR", message=str(e))


PRIVATE_MEDIA_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Vary": "X-Accoding-Session, Authorization",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _get_owned_image(db: Session, image_id: str, user_id: str) -> ImageAsset:
    asset = db.get(ImageAsset, image_id)
    if asset is None or str(asset.uploader_user_id) != str(user_id):
        # Do not disclose whether another user's private attachment exists.
        raise ApiError(status_code=404, code="IMAGE_NOT_FOUND", message="Image not found")
    return asset


def _owned_chat_image_references(db: Session, references: list[str] | None, user_id: str) -> list[str] | None:
    if not references:
        return references
    resolved: list[str] = []
    prefix = "/api/agents/media/images/"
    for reference in references:
        # Decode before classifying local references so escaped paths and a
        # forged host cannot turn a private asset into an unchecked URL.
        path = unquote(urlparse(reference).path).rstrip("/")
        if not path.startswith(prefix):
            resolved.append(reference)
            continue
        image_id = path.removeprefix(prefix).removesuffix("/thumbnail")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", image_id):
            raise ApiError(status_code=400, code="IMAGE_INVALID", message="Invalid image reference")
        _get_owned_image(db, image_id, user_id)
        resolved.append(f"{prefix}{image_id}")
    return resolved


@router.get("/media/images/{image_id}")
async def get_uploaded_image(
    image_id: str,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    asset = _get_owned_image(db, image_id, user_data[0])

    image_path = Path(asset.storage_path)
    if not image_path.exists():
        raise ApiError(status_code=404, code="IMAGE_NOT_FOUND", message="Image file missing")

    return FileResponse(
        path=image_path,
        media_type=asset.content_type,
        filename=asset.filename,
        content_disposition_type="inline",
        headers=PRIVATE_MEDIA_HEADERS,
    )


@router.get("/media/images/{image_id}/thumbnail")
async def get_uploaded_image_thumbnail(
    image_id: str,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    asset = _get_owned_image(db, image_id, user_data[0])

    image_path = Path(asset.storage_path)
    if not image_path.exists():
        raise ApiError(status_code=404, code="IMAGE_NOT_FOUND", message="Image file missing")

    try:
        thumbnail_path = ensure_thumbnail_file(image_path)
    except Exception as e:
        raise ApiError(status_code=500, code="THUMBNAIL_FAILED", message=f"Thumbnail generation failed: {e}")

    media_type = asset.content_type
    if not thumbnail_path.exists():
        raise ApiError(status_code=404, code="IMAGE_NOT_FOUND", message="Thumbnail file missing")
    return FileResponse(
        path=thumbnail_path,
        media_type=media_type,
        filename=thumbnail_path.name,
        content_disposition_type="inline",
        headers=PRIVATE_MEDIA_HEADERS,
    )


@router.delete("/media/images/{image_id}")
async def delete_uploaded_image(
    image_id: str,
    db: Session = Depends(get_session),
    user_data: tuple[str, str] = Depends(get_current_user_id_and_token),
):
    user_id, _ = user_data
    asset = db.get(ImageAsset, image_id)
    if asset is None:
        raise ApiError(status_code=404, code="IMAGE_NOT_FOUND", message="Image not found")
    if str(asset.uploader_user_id) != str(user_id):
        raise ApiError(status_code=403, code="IMAGE_FORBIDDEN", message="You can only delete your own uploaded images")

    image_path = Path(asset.storage_path)
    if image_path.exists():
        image_path.unlink()
    thumbnail_path = build_thumbnail_storage_path(image_path)
    if thumbnail_path.exists():
        thumbnail_path.unlink()

    # 中文注释：用户手动删除图片时采用强制删除策略，即使这张图曾被会话使用过，
    # 也直接删除文件、元数据和残留引用，后续模型输入会自动忽略失效的本地图片 URL。
    dangling_refs = list(db.exec(select(AgentMessageImage).where(AgentMessageImage.image_id == image_id)))
    for ref in dangling_refs:
        db.delete(ref)
    db.delete(asset)
    db.commit()
    return {"success": True, "message": "Image deleted"}
