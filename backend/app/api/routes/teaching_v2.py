from __future__ import annotations

from datetime import datetime
import json
from queue import Empty, Queue
from threading import Thread
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api_errors import ApiError
from app.agent_store import AgentStore
from app.agents.router import get_current_user_id_and_token
from app.database import get_session
from app.models import (
    AgentAcceptanceRun,
    AgentBlackboardEntry,
    AgentDiagramScene,
    AgentDiagramStep,
    AgentExecutionTrace,
    AgentMessageRole,
    AgentMode,
    AgentProblemWorkspace,
    AgentProviderCallTrace,
    AgentSession,
    AgentSessionSource,
    AgentSessionStatus,
    AgentTeachingEvent,
    AgentTeachingStep,
    AgentToolCallTrace,
    GuidanceDepthValue,
    ProblemWorkspaceStatus,
    User,
)
from app.repositories.teaching import TeachingRepository
from app.schemas import (
    MemoryItemResponse,
    MemorySettingsRequest,
    MemorySettingsResponse,
    TeachingTurnRequest,
    TeachingTurnResponse,
)
from app.config import Settings, get_settings
from app.session_access import can_read_agent_session, can_write_agent_session
from app.teaching.agent_providers import (
    TeachingAgentProviderUnavailableError,
    build_teaching_agent_providers,
    enabled_teaching_agent_names,
    teaching_agent_provider_capabilities,
    teaching_agent_provider_contracts,
    teaching_agent_provider_fields,
    teaching_agent_primary_capability,
)
from app.teaching.acceptance_scenarios import (
    build_selective_real_provider_config,
    run_runtime_equivalent_acceptance_scenario,
    run_mock_student_acceptance_scenario,
    run_mock_student_collaboration_acceptance_scenario,
)
from app.teaching.provider_acceptance import (
    DEFAULT_ACCEPTANCE_MESSAGE,
    DEFAULT_ACCEPTANCE_JSON_MODE,
    DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS,
    DEFAULT_ACCEPTANCE_RECOMMENDED_MODEL,
    DEFAULT_ACCEPTANCE_TARGET_AGENT,
    DEFAULT_ACCEPTANCE_TARGET_CAPABILITY,
    DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS,
    run_algorithm_identifier_acceptance,
)
from app.teaching.orchestrator import OPTIONAL_SPECIALIST_EXECUTION_ORDER, TeachingOrchestrator
from app.teaching.runtime_contracts import (
    teaching_acceptance_runtime_contract,
    teaching_cache_runtime_contract,
    teaching_policy_registry,
    teaching_policy_surfaces_with_provider_status,
    teaching_streaming_runtime_contract,
    teaching_supervisor_runtime_contract,
    teaching_tools_runtime_contract,
)
from app.teaching.supervisor import TeachingSupervisor
from app.teaching.taxonomy import load_taxonomy
from app.teaching.diagram_production import render_verified_frame


router = APIRouter()
V2_STREAM_HEARTBEAT_SECONDS = 5.0

REQUIRED_V2_MODEL_PROVIDER_AGENTS = (
    "ProblemIdentifierAgent",
    "AlgorithmIdentifierAgent",
    "HintPolicyAgent",
    "TutorAgent",
    "MemoryConsolidatorAgent",
)


class GuidanceDepthUpdateRequest(BaseModel):
    guidance_depth: GuidanceDepthValue
    depth_override: bool = True


class MockStudentAcceptanceRequest(BaseModel):
    scenario_id: Literal[
        "mock_student_guidance_depth_control",
        "mock_student_collaboration_breadth",
    ] = "mock_student_guidance_depth_control"
    real_algorithm_provider: bool = False
    runtime_provider: bool = False
    message: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS, ge=1.0, le=120.0)
    provider_timeout_seconds: float = Field(default=DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS, ge=1.0, le=120.0)
    json_mode: bool | None = None
    model: str | None = None
    base_url: str | None = None


class ProviderAcceptanceRequest(BaseModel):
    message: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS, ge=1.0, le=120.0)
    provider_timeout_seconds: float = Field(default=DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS, ge=1.0, le=120.0)
    json_mode: bool | None = None


async def get_v2_user_id(
    x_test_user_id: int | None = Header(default=None, alias="X-Test-User-Id"),
    x_accoding_session: str | None = Header(default=None, alias="X-Accoding-Session"),
    settings: Settings = Depends(get_settings),
) -> int:
    if x_accoding_session:
        user_id, _ = await get_current_user_id_and_token(x_accoding_session)
        return int(user_id)
    if settings.app_env in {"dev", "test"}:
        return x_test_user_id or 19
    raise ApiError(status_code=401, code="AUTH_INVALID", message="X-Accoding-Session header missing")


def _get_or_create_session(db: Session, request: TeachingTurnRequest, user_id: int) -> AgentSession:
    if request.session_id:
        session = db.exec(
            select(AgentSession).where(
                AgentSession.session_uuid == request.session_id,
            )
        ).first()
        if session is None:
            session = AgentSession(
                session_uuid=request.session_id,
                user_id=user_id,
                mode=AgentMode.SOCRATIC,
                status=AgentSessionStatus.ACTIVE,
                source=AgentSessionSource.WEB,
            )
            db.add(session)
            db.flush()
            return session
        if not can_write_agent_session(session, user_id, get_settings()):
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


def _ensure_v2_user(db: Session, user_id: int, settings: Settings) -> None:
    if db.get(User, user_id) is not None:
        return
    if settings.app_env not in {"dev", "test"}:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    now = datetime.utcnow()
    db.add(
        User(
            id=user_id,
            username=f"test-user-{user_id}@oj.local",
            nickname=f"测试用户{user_id}",
            create_time=now,
            update_time=now,
        )
    )
    db.flush()


def _get_session_by_uuid(db: Session, session_id: str) -> AgentSession:
    session = db.exec(
        select(AgentSession).where(
            AgentSession.session_uuid == session_id,
            AgentSession.status != AgentSessionStatus.DELETED,
        )
    ).first()
    if session is None or session.id is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    return session


def _find_session_by_uuid(db: Session, session_id: str) -> AgentSession | None:
    return db.exec(
        select(AgentSession).where(
            AgentSession.session_uuid == session_id,
            AgentSession.status != AgentSessionStatus.DELETED,
        )
    ).first()


def _get_owned_session_by_uuid(db: Session, session_id: str, user_id: int) -> AgentSession:
    session = _find_session_by_uuid(db, session_id)
    if session is None or session.id is None or not can_write_agent_session(session, user_id, get_settings()):
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    return session


def _get_readable_session_by_uuid(db: Session, session_id: str, user_id: int, settings: Settings) -> AgentSession:
    session = _find_session_by_uuid(db, session_id)
    if session is None or session.id is None or not can_read_agent_session(session, user_id, settings):
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    return session


def _get_workspace_or_404(repo: TeachingRepository, workspace_id: int) -> AgentProblemWorkspace:
    workspace = repo.get_workspace(workspace_id)
    if workspace is None:
        raise ApiError(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
    return workspace


def _get_owned_workspace_or_404(repo: TeachingRepository, workspace_id: int, user_id: int) -> AgentProblemWorkspace:
    workspace = _get_workspace_or_404(repo, workspace_id)
    session = repo.db.get(AgentSession, workspace.session_id)
    if session is None or not can_write_agent_session(session, user_id, get_settings()):
        raise ApiError(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
    return workspace


def _get_readable_workspace_or_404(repo: TeachingRepository, workspace_id: int, user_id: int, settings: Settings) -> AgentProblemWorkspace:
    workspace = _get_workspace_or_404(repo, workspace_id)
    session = repo.db.get(AgentSession, workspace.session_id)
    if session is None or not can_read_agent_session(session, user_id, settings):
        raise ApiError(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
    return workspace


def _serialize_step(step: AgentTeachingStep | None) -> dict | None:
    if step is None or step.id is None:
        return None
    return {
        "step_id": step.id,
        "step_key": step.step_key,
        "title": step.title,
        "status": step.status.value,
        "mastery_state": step.mastery_state,
        "order_index": step.order_index,
        "teaching_goal": step.teaching_goal,
    }


def _serialize_step_chain_item(step: AgentTeachingStep, *, current_order_index: int | None) -> dict:
    return {
        "step_id": step.id,
        "step_key": step.step_key,
        "title": step.title,
        "status": step.status.value,
        "mastery_state": step.mastery_state,
        "order_index": step.order_index,
        "is_current": current_order_index is not None and step.order_index == current_order_index,
        "is_reached": current_order_index is not None and step.order_index <= current_order_index,
    }


def _serialize_workspace(
    repo: TeachingRepository,
    workspace: AgentProblemWorkspace,
    *,
    include_future_steps: bool = True,
) -> dict:
    active_plan = repo.get_active_plan(int(workspace.id)) if workspace.id is not None else None
    current_step = repo.get_current_step(int(workspace.id)) if workspace.id is not None else None
    plan_steps = repo.list_steps(int(active_plan.id)) if active_plan is not None and active_plan.id is not None else []
    current_order_index = current_step.order_index if current_step is not None else None
    reached_steps = [
        step
        for step in plan_steps
        if current_order_index is not None and step.order_index <= current_order_index
    ]
    visible_steps = plan_steps if include_future_steps else reached_steps
    return {
        "workspace_id": workspace.id,
        "session_id": workspace.session_id,
        "problem_id": workspace.problem_id,
        "title": workspace.title,
        "problem_summary": workspace.problem_summary,
        "status": workspace.status.value,
        "guidance_depth": workspace.guidance_depth.value,
        "depth_override": workspace.depth_override,
        "algorithm_family": workspace.algorithm_family,
        "active_plan_id": workspace.active_plan_id,
        "current_step": _serialize_step(current_step),
        "steps": [
            _serialize_step_chain_item(step, current_order_index=current_order_index)
            for step in visible_steps
        ],
        "total_reached_steps": max(1, len(reached_steps)) if current_step is not None else 0,
        "total_plan_steps": len(plan_steps) if current_step is not None else 0,
    }


def _serialize_trace(trace: AgentExecutionTrace) -> dict:
    return {
        "agent": trace.agent_name,
        "status": trace.status,
        "input_summary": trace.input_summary,
        "output_summary": trace.output_summary,
        "latency_ms": trace.latency_ms,
        "blackboard_writes": trace.blackboard_write_count,
        "memory_writes": trace.memory_write_count,
        "error_message": trace.error_message,
        "payload": trace.payload or {},
    }


def _serialize_provider_call_trace(trace: AgentProviderCallTrace) -> dict:
    return {
        "trace_id": trace.id,
        "session_id": trace.session_id,
        "workspace_id": trace.workspace_id,
        "turn_index": trace.turn_index,
        "agent": trace.agent_name,
        "capability": trace.capability,
        "provider_backend": trace.provider_backend,
        "outcome": trace.outcome,
        "accepted": trace.accepted,
        "latency_ms": trace.latency_ms,
        "prompt_size_bytes": trace.prompt_size_bytes,
        "payload_size_bytes": trace.payload_size_bytes,
        "fallback_reason": trace.fallback_reason,
        "error_type": trace.error_type,
        "redaction_policy": trace.redaction_policy,
        "sanitized": trace.sanitized,
        "payload": trace.payload or {},
        "created_at": trace.create_time,
    }


def _serialize_tool_call_trace(trace: AgentToolCallTrace) -> dict:
    return {
        "trace_id": trace.id,
        "session_id": trace.session_id,
        "workspace_id": trace.workspace_id,
        "turn_index": trace.turn_index,
        "owner_agent": trace.owner_agent,
        "tool_backend": trace.tool_backend,
        "tool_name": trace.tool_name,
        "status": trace.status,
        "latency_ms": trace.latency_ms,
        "request_summary": trace.request_summary,
        "response_summary": trace.response_summary,
        "error_type": trace.error_type,
        "redaction_policy": trace.redaction_policy,
        "sanitized": trace.sanitized,
        "payload": trace.payload or {},
        "created_at": trace.create_time,
    }


def _serialize_acceptance_run(run: AgentAcceptanceRun) -> dict:
    return {
        "run_id": run.id,
        "scenario_id": run.scenario_id,
        "status": run.status,
        "mode": run.mode,
        "model": run.model,
        "runtime_equivalent": run.runtime_equivalent,
        "turn_count": run.turn_count,
        "checks": run.checks,
        "failed_checks": run.failed_checks,
        "performance": run.performance,
        "real_provider": run.real_provider,
        "created_at": run.create_time,
    }


def _serialize_blackboard_entry(entry: AgentBlackboardEntry) -> dict:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    display = _whiteboard_display_fields(entry, payload)
    return {
        "entry_id": entry.id,
        "workspace_id": entry.workspace_id,
        "step_id": entry.step_id,
        "entry_type": entry.entry_type,
        "title": display["title"],
        "summary": display["summary"],
        "content": display["content"],
        "payload": entry.payload,
        "writer_agent": entry.writer_agent,
        "source_event_id": entry.source_event_id,
        "source_message_id": entry.source_message_id,
        "confidence": entry.confidence,
        "visibility": entry.visibility,
        "status": entry.status,
        "created_at": entry.create_time,
    }


def _whiteboard_display_fields(entry: AgentBlackboardEntry, payload: dict[str, Any]) -> dict[str, str | None]:
    title = payload.get("title") if isinstance(payload.get("title"), str) else None
    summary = payload.get("summary") if isinstance(payload.get("summary"), str) else None
    content = entry.content
    if entry.entry_type != "whiteboard_entry" or payload.get("deposit_type") != "boardwork" or payload.get("display_version") == 2:
        return {"title": title, "summary": summary, "content": content}

    boardwork_payload = payload.get("boardwork_payload")
    request_context = (
        boardwork_payload.get("request_context")
        if isinstance(boardwork_payload, dict) and isinstance(boardwork_payload.get("request_context"), dict)
        else {}
    )
    candidate = _first_non_empty_text(
        request_context.get("knowledge_candidate_summary"),
        request_context.get("tutor_visible_text"),
        boardwork_payload.get("student_feedback") if isinstance(boardwork_payload, dict) else None,
    )
    source_question = _first_non_empty_text(request_context.get("latest_user_message"))
    if candidate:
        title = _compact_whiteboard_display_text(candidate)
    if source_question:
        summary = _compact_whiteboard_source_question(source_question)
        content = summary
    return {"title": title, "summary": summary, "content": content}


def _first_non_empty_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _compact_whiteboard_display_text(value: str, *, limit: int = 220) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rstrip(" ，,；;。")
    return f"{clipped}..."


def _compact_whiteboard_source_question(value: str, *, limit: int = 120) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rstrip(" ，,；;。")
    return f"{clipped}..."


def _serialize_event(event: AgentTeachingEvent) -> dict:
    return {
        "event_id": event.id,
        "workspace_id": event.workspace_id,
        "session_id": event.session_id,
        "turn_index": event.turn_index,
        "event_type": event.event_type,
        "actor": event.actor,
        "step_id": event.step_id,
        "payload": event.payload,
        "confidence": event.confidence,
        "visible_to_user": event.visible_to_user,
        "created_at": event.create_time,
    }


def _serialize_diagram_scene(scene: AgentDiagramScene, *, step_count: int = 0) -> dict:
    return {
        "scene_id": scene.scene_uuid,
        "workspace_id": scene.workspace_id,
        "teaching_step_id": scene.teaching_step_id,
        "title": scene.title,
        "diagram_kind": scene.diagram_kind,
        "layout": scene.layout,
        "status": scene.status,
        "default_step_id": scene.default_step_id,
        "source_turn_index": scene.source_turn_index,
        "step_count": step_count,
    }


def _serialize_runtime(settings: Settings) -> dict:
    provider_fields = teaching_agent_provider_fields()
    provider_capabilities = teaching_agent_provider_capabilities()
    provider_contracts = teaching_agent_provider_contracts()
    enabled_agents = enabled_teaching_agent_names(settings)
    provider_enabled = (
        settings.teaching_agent_providers_enabled
        and settings.teaching_agent_provider_backend != "disabled"
    )
    if not provider_enabled:
        activation_status = "disabled"
        activation_reason = "provider backend disabled"
    elif not enabled_agents:
        activation_status = "no_agents_enabled"
        activation_reason = "TEACHING_AGENT_PROVIDER_AGENTS is empty"
    else:
        activation_status = "active"
        activation_reason = None
    acceptance_probe_runtime_agent_enabled = DEFAULT_ACCEPTANCE_TARGET_AGENT in enabled_agents
    acceptance_probe_provider_credentials_ready = bool(settings.external_llm_api_key.strip())
    acceptance_probe_runtime_ready = (
        provider_enabled
        and acceptance_probe_runtime_agent_enabled
        and acceptance_probe_provider_credentials_ready
    )
    if acceptance_probe_runtime_ready:
        acceptance_probe_runtime_readiness_reason = None
    elif not provider_enabled:
        acceptance_probe_runtime_readiness_reason = "provider_backend_disabled"
    elif not acceptance_probe_runtime_agent_enabled:
        acceptance_probe_runtime_readiness_reason = "target_agent_not_enabled"
    else:
        acceptance_probe_runtime_readiness_reason = "missing_api_key"
    primary_capabilities = {
        agent_name: capability
        for agent_name in provider_capabilities
        if (capability := teaching_agent_primary_capability(agent_name)) is not None
    }
    acceptance_contract = teaching_acceptance_runtime_contract()
    taxonomy = load_taxonomy(settings.teaching_algorithm_taxonomy_path)
    family_ids = taxonomy.family_ids()
    return {
        "provider": {
            "enabled": provider_enabled,
            "backend": settings.teaching_agent_provider_backend,
            "model": settings.external_llm_model,
            "activation_status": activation_status,
            "activation_reason": activation_reason,
            "v2_model_provider_required": settings.teaching_v2_require_model_provider,
            "v2_required_agents": list(REQUIRED_V2_MODEL_PROVIDER_AGENTS),
            "timeout_seconds": settings.teaching_agent_provider_timeout_seconds,
            "json_mode": settings.teaching_agent_provider_json_mode,
            "available_agents": sorted(provider_fields),
            "enabled_agents": list(enabled_agents),
            "agent_fields": provider_fields,
            "primary_capabilities": primary_capabilities,
            "agent_capabilities": provider_capabilities,
            "agent_contracts": provider_contracts,
        },
        "policies": teaching_policy_registry(),
        "policy_surfaces": teaching_policy_surfaces_with_provider_status(
            provider_fields=provider_fields,
            enabled_agents=enabled_agents,
            provider_enabled=provider_enabled,
        ),
        "supervisor": teaching_supervisor_runtime_contract(
            provider_timeout_seconds=settings.teaching_supervisor_provider_timeout_seconds,
            allowed_agents=tuple(TeachingSupervisor.allowed_agents),
            optional_agents=tuple(TeachingSupervisor.optional_agents),
            optional_agents_parallelizable=tuple(OPTIONAL_SPECIALIST_EXECUTION_ORDER),
        ),
        "budgets": {
            "supervisor_provider_timeout_seconds": settings.teaching_supervisor_provider_timeout_seconds,
            "optional_specialist_result_timeout_seconds": (
                settings.teaching_optional_specialist_result_timeout_seconds
            ),
            "memory_consolidation_timeout_seconds": settings.teaching_memory_consolidation_timeout_seconds,
        },
        "acceptance": {
            **acceptance_contract,
            "provider_probe": {
                "target_agent": DEFAULT_ACCEPTANCE_TARGET_AGENT,
                "target_capability": DEFAULT_ACCEPTANCE_TARGET_CAPABILITY,
                "recommended_model": DEFAULT_ACCEPTANCE_RECOMMENDED_MODEL,
                "message": DEFAULT_ACCEPTANCE_MESSAGE,
                "timeout_seconds": DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS,
                "provider_timeout_seconds": DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS,
                "json_mode": DEFAULT_ACCEPTANCE_JSON_MODE,
                "enabled_agent_selection_required": True,
                "runtime_agent_enabled": acceptance_probe_runtime_agent_enabled,
                "provider_credentials_ready": acceptance_probe_provider_credentials_ready,
                "runtime_ready": acceptance_probe_runtime_ready,
                "runtime_readiness_reason": acceptance_probe_runtime_readiness_reason,
            },
        },
        "streaming": teaching_streaming_runtime_contract(),
        "cache": teaching_cache_runtime_contract(),
        "tools": teaching_tools_runtime_contract(mcp_enabled=settings.mcp_enabled, mcp_mode=settings.mcp_mode),
        "taxonomy": {
            "source": taxonomy.source,
            "source_detail": taxonomy.source_detail,
            "family_ids": family_ids,
            "family_count": len(family_ids),
        },
    }


@router.get("/runtime")
def get_runtime_registry(
    settings: Settings = Depends(get_settings),
) -> dict:
    return _serialize_runtime(settings)


def _ensure_v2_model_provider_ready(settings: Settings) -> None:
    if not settings.teaching_v2_require_model_provider:
        return
    if not settings.teaching_agent_providers_enabled or settings.teaching_agent_provider_backend == "disabled":
        raise ApiError(
            status_code=503,
            code="TEACHING_MODEL_UNAVAILABLE",
            message="Teaching model provider is required for v2 chat but is not enabled.",
            details={
                "agent": "TeachingV2",
                "capability": "v2_chat",
                "provider_backend": settings.teaching_agent_provider_backend,
                "outcome": "provider_disabled",
            },
        )
    enabled_agents = set(enabled_teaching_agent_names(settings))
    missing_agents = [
        agent_name
        for agent_name in REQUIRED_V2_MODEL_PROVIDER_AGENTS
        if agent_name not in enabled_agents
    ]
    if missing_agents:
        raise ApiError(
            status_code=503,
            code="TEACHING_MODEL_UNAVAILABLE",
            message="Teaching model provider is missing required v2 agents.",
            details={
                "agent": "TeachingV2",
                "capability": "v2_chat",
                "provider_backend": settings.teaching_agent_provider_backend,
                "outcome": "provider_agents_missing",
                "missing_agents": missing_agents,
            },
        )


@router.post("/acceptance/mock-student")
def run_mock_student_acceptance(
    request: MockStudentAcceptanceRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if request.real_algorithm_provider and request.runtime_provider:
        raise ApiError(
            status_code=400,
            code="ACCEPTANCE_PROVIDER_MODE_CONFLICT",
            message="Choose either selective real provider or runtime provider mode",
        )
    real_provider_config = None
    if request.real_algorithm_provider:
        real_provider_config = build_selective_real_provider_config(
            api_key=settings.external_llm_api_key,
            model=(request.model or settings.external_llm_model),
            base_url=(request.base_url or settings.external_llm_base_url),
            timeout_seconds=request.timeout_seconds,
            provider_timeout_seconds=request.provider_timeout_seconds,
            json_mode=(
                request.json_mode
                if request.json_mode is not None
                else settings.teaching_agent_provider_json_mode
            ),
        )
    if request.runtime_provider:
        result = run_runtime_equivalent_acceptance_scenario(
            scenario_id=request.scenario_id,
            problem_message=request.message,
            providers=build_teaching_agent_providers(settings),
            enabled_agents=enabled_teaching_agent_names(settings),
            model=settings.external_llm_model,
        )
    else:
        scenario_runner = (
            run_mock_student_collaboration_acceptance_scenario
            if request.scenario_id == "mock_student_collaboration_breadth"
            else run_mock_student_acceptance_scenario
        )
        result = scenario_runner(
            problem_message=request.message,
            real_provider_config=real_provider_config,
        )
    payload = result.to_dict()
    TeachingRepository(db).record_acceptance_run(
        scenario_id=result.scenario_id,
        status=result.status,
        mode=(
            "runtime_provider"
            if request.runtime_provider
            else "real_algorithm_provider"
            if request.real_algorithm_provider
            else (
                "mock_student_collaboration"
                if request.scenario_id == "mock_student_collaboration_breadth"
                else "mock_student"
            )
        ),
        model=payload.get("real_provider", {}).get("model") if payload.get("real_provider") else request.model,
        runtime_equivalent=(
            payload.get("real_provider", {}).get("runtime_equivalent") if payload.get("real_provider") else None
        ),
        checks=result.checks,
        failed_checks=result.failed_checks,
        performance=result.performance,
        real_provider=result.real_provider,
        turn_count=result.turn_count,
    )
    db.commit()
    return payload


@router.post("/acceptance/provider")
def run_algorithm_provider_acceptance(
    request: ProviderAcceptanceRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = run_algorithm_identifier_acceptance(
        api_key=settings.external_llm_api_key,
        model=request.model or settings.external_llm_model,
        base_url=request.base_url or settings.external_llm_base_url,
        timeout_seconds=request.timeout_seconds,
        provider_timeout_seconds=request.provider_timeout_seconds,
        json_mode=(
            request.json_mode
            if request.json_mode is not None
            else settings.teaching_agent_provider_json_mode
        ),
        message=request.message or DEFAULT_ACCEPTANCE_MESSAGE,
    )
    payload = result.to_dict()
    TeachingRepository(db).record_acceptance_run(
        scenario_id="provider_algorithm_identifier_probe",
        status=result.status,
        mode="provider_probe",
        model=result.model,
        runtime_equivalent=False,
        checks={"provider_accepted": result.accepted},
        failed_checks=[] if result.accepted else ["provider_accepted"],
        performance={
            "total_trace_latency_ms": result.latency_ms,
            "max_turn_trace_latency_ms": result.latency_ms,
            "p95_turn_trace_latency_ms": result.latency_ms,
            "slowest_turn_index": 0,
            "slowest_agent": DEFAULT_ACCEPTANCE_TARGET_AGENT,
            "slowest_agent_latency_ms": result.latency_ms,
            "turn_latency_budget_ms": int(request.provider_timeout_seconds * 1000),
            "over_budget_turn_count": 0 if result.latency_ms <= int(request.provider_timeout_seconds * 1000) else 1,
            "within_turn_latency_budget": result.latency_ms <= int(request.provider_timeout_seconds * 1000),
            "total_provider_payload_count": 1 if result.prompt_size_bytes or result.payload_size_bytes else 0,
            "total_provider_prompt_tokens": result.prompt_tokens,
            "total_provider_completion_tokens": result.completion_tokens,
            "total_provider_tokens": result.total_tokens,
        },
        real_provider=payload,
        turn_count=1,
    )
    db.commit()
    return payload


@router.get("/acceptance/runs")
def list_acceptance_runs(
    limit: int = 20,
    db: Session = Depends(get_session),
) -> list[dict]:
    return [_serialize_acceptance_run(run) for run in TeachingRepository(db).list_acceptance_runs(limit=limit)]


@router.delete("/acceptance/runs/{run_id}")
def delete_acceptance_run(
    run_id: int,
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    deleted = TeachingRepository(db).delete_acceptance_run(run_id)
    db.commit()
    return {"deleted": deleted}


@router.delete("/acceptance/runs")
def clear_acceptance_runs(
    db: Session = Depends(get_session),
) -> dict[str, int]:
    deleted_count = TeachingRepository(db).clear_acceptance_runs()
    db.commit()
    return {"deleted_count": deleted_count}


def _record_v2_visible_messages(
    *,
    db: Session,
    session: AgentSession,
    request: TeachingTurnRequest,
    response: TeachingTurnResponse,
    turn_index: int,
) -> None:
    repo = TeachingRepository(db)
    repo.record_visible_message(
        session_id=int(session.id),
        workspace_id=response.workspace_id,
        role=AgentMessageRole.USER,
        content=request.message,
        turn_index=turn_index,
    )
    repo.record_visible_message(
        session_id=int(session.id),
        workspace_id=response.workspace_id,
        role=AgentMessageRole.ASSISTANT,
        content=response.response,
        turn_index=turn_index,
    )


def _build_v2_orchestrator(db: Session, settings: Settings) -> TeachingOrchestrator:
    return TeachingOrchestrator(
        db,
        agent_providers=build_teaching_agent_providers(settings),
        taxonomy=load_taxonomy(settings.teaching_algorithm_taxonomy_path),
        supervisor_provider_timeout_seconds=settings.teaching_supervisor_provider_timeout_seconds,
        optional_specialist_result_timeout_seconds=settings.teaching_optional_specialist_result_timeout_seconds,
        memory_consolidation_timeout_seconds=settings.teaching_memory_consolidation_timeout_seconds,
        require_model_provider=settings.teaching_v2_require_model_provider,
    )


@router.post("/chat", response_model=TeachingTurnResponse)
def chat_v2(
    request: TeachingTurnRequest,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> TeachingTurnResponse:
    _ensure_v2_user(db, user_id, settings)
    _ensure_v2_model_provider_ready(settings)
    session = _get_or_create_session(db, request, user_id)
    if session.id is None:
        raise ApiError(status_code=500, code="SESSION_CREATE_FAILED", message="Failed to create session")
    request = request.model_copy(update={"session_id": session.session_uuid})

    orchestrator = _build_v2_orchestrator(db, settings)
    turn_index = AgentStore(db).next_turn_index(int(session.id))
    try:
        response = orchestrator.run_turn(
            request,
            session_numeric_id=session.id,
            user_id=user_id,
            turn_index=turn_index,
        )
    except TeachingAgentProviderUnavailableError as exc:
        db.rollback()
        raise ApiError(
            status_code=503,
            code="TEACHING_MODEL_UNAVAILABLE",
            message="Teaching model provider is unavailable for this turn.",
            details={
                "agent": exc.agent_name,
                "capability": exc.capability,
                "provider_backend": exc.provider_backend,
                "outcome": exc.outcome,
                "provider_details": exc.details,
            },
        ) from exc
    _record_v2_visible_messages(
        db=db,
        session=session,
        request=request,
        response=response,
        turn_index=turn_index,
    )
    db.commit()
    return response


@router.post("/chat/stream")
def chat_v2_stream(
    request: TeachingTurnRequest,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    _ensure_v2_user(db, user_id, settings)
    _ensure_v2_model_provider_ready(settings)
    session = _get_or_create_session(db, request, user_id)
    if session.id is None:
        raise ApiError(status_code=500, code="SESSION_CREATE_FAILED", message="Failed to create session")
    request = request.model_copy(update={"session_id": session.session_uuid})
    turn_index = AgentStore(db).next_turn_index(int(session.id))

    def event_payload(payload: dict | str) -> str:
        if isinstance(payload, str):
            return f"data: {payload}\n\n"
        return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def stream_events():
        stream_started_at = perf_counter()
        events: Queue[dict | str] = Queue()
        partial_response_metric_emitted = False
        last_progress: dict = {
            "type": "progress",
            "stage": "starting",
            "agent": None,
            "model": settings.external_llm_model,
            "provider_backend": settings.teaching_agent_provider_backend,
            "message": "正在启动本轮教学分析。",
        }

        def enqueue_progress(payload: dict) -> None:
            nonlocal partial_response_metric_emitted
            if payload.get("type") == "progress":
                payload = {
                    "model": settings.external_llm_model,
                    "provider_backend": settings.teaching_agent_provider_backend,
                    **payload,
                }
            if payload.get("type") == "progress":
                last_progress.update(payload)
            if payload.get("type") == "partial_response" and not partial_response_metric_emitted:
                partial_response_metric_emitted = True
                events.put(
                    {
                        "metric": "v2_stream_partial_response_latency_ms",
                        "value_ms": max(0, int((perf_counter() - stream_started_at) * 1000)),
                    }
                )
            events.put(payload)

        def worker() -> None:
            try:
                orchestrator = _build_v2_orchestrator(db, settings)
                response = orchestrator.run_turn(
                    request,
                    session_numeric_id=int(session.id),
                    user_id=user_id,
                    turn_index=turn_index,
                    progress_callback=enqueue_progress,
                )
                _record_v2_visible_messages(
                    db=db,
                    session=session,
                    request=request,
                    response=response,
                    turn_index=turn_index,
                )
                db.commit()
                events.put({"type": "final", "response": response.model_dump(mode="json")})
            except TeachingAgentProviderUnavailableError as exc:
                db.rollback()
                events.put(
                    {
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
                )
            except Exception as exc:
                db.rollback()
                events.put({"type": "error", "error": str(exc), "code": type(exc).__name__})
            finally:
                events.put("[DONE]")

        thread = Thread(target=worker, daemon=True)
        thread.start()
        while True:
            try:
                payload = events.get(timeout=V2_STREAM_HEARTBEAT_SECONDS)
            except Empty:
                payload = {
                    **last_progress,
                    "heartbeat": True,
                    "message": str(last_progress.get("message") or "仍在处理本轮教学。"),
                }
            yield event_payload(payload)
            if payload == "[DONE]":
                break
        thread.join(timeout=0)

    return StreamingResponse(stream_events(), media_type="text/event-stream")


@router.delete("/sessions/{session_id}")
def delete_session_v2(
    session_id: str,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
) -> dict[str, bool | str]:
    session = _get_owned_session_by_uuid(db, session_id, user_id)
    session.status = AgentSessionStatus.DELETED
    session.update_time = datetime.utcnow()
    db.add(session)
    db.commit()
    return {"success": True, "message": "Session deleted"}


@router.get("/sessions/{session_id}/workspaces")
def list_session_workspaces(
    session_id: str,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    if _find_session_by_uuid(db, session_id) is None:
        return []
    session = _get_readable_session_by_uuid(db, session_id, user_id, settings)
    repo = TeachingRepository(db)
    return [_serialize_workspace(repo, workspace) for workspace in repo.list_workspaces_for_session(int(session.id))]


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> dict:
    repo = TeachingRepository(db)
    workspace = _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    return _serialize_workspace(repo, workspace, include_future_steps=False)


@router.get("/workspaces/{workspace_id}/blackboard")
def get_workspace_blackboard(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    repo = TeachingRepository(db)
    _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    return [_serialize_blackboard_entry(entry) for entry in repo.list_blackboard_entries(workspace_id)]


@router.get("/workspaces/{workspace_id}/events")
def get_workspace_events(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    repo = TeachingRepository(db)
    _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    return [_serialize_event(event) for event in repo.list_events(workspace_id)]


@router.get("/workspaces/{workspace_id}/diagram-scenes")
def get_workspace_diagram_scenes(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    repo = TeachingRepository(db)
    _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    scenes = db.exec(
        select(AgentDiagramScene)
        .where(AgentDiagramScene.workspace_id == workspace_id)
        .order_by(AgentDiagramScene.update_time.desc(), AgentDiagramScene.id.desc())
    ).all()
    serialized = []
    for scene in scenes:
        step_count = len(
            db.exec(
                select(AgentDiagramStep).where(AgentDiagramStep.diagram_scene_id == scene.id)
            ).all()
        )
        serialized.append(_serialize_diagram_scene(scene, step_count=step_count))
    return serialized


@router.get("/workspaces/{workspace_id}/diagram-scenes/{scene_id}")
def get_workspace_diagram_scene_detail(
    workspace_id: int,
    scene_id: str,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> dict:
    repo = TeachingRepository(db)
    workspace = _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    scene = db.exec(
        select(AgentDiagramScene).where(
            AgentDiagramScene.workspace_id == workspace_id,
            AgentDiagramScene.scene_uuid == scene_id,
        )
    ).first()
    if scene is None:
        raise ApiError(status_code=404, code="DIAGRAM_SCENE_NOT_FOUND", message="Diagram scene not found")
    session = db.get(AgentSession, workspace.session_id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", message="Session not found")
    payload = AgentStore(db).get_diagram_scene_payload(session.session_uuid, scene_id)
    if payload is None:
        raise ApiError(status_code=404, code="DIAGRAM_SCENE_NOT_FOUND", message="Diagram scene not found")
    return payload


@router.get("/workspaces/{workspace_id}/diagram-scenes/{scene_id}/frames/{frame_index}.svg")
def get_workspace_diagram_frame(
    workspace_id: int,
    scene_id: str,
    frame_index: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> Response:
    repo = TeachingRepository(db)
    workspace = _get_readable_workspace_or_404(repo, workspace_id, user_id, settings)
    scene = db.exec(select(AgentDiagramScene).where(
        AgentDiagramScene.workspace_id == workspace_id,
        AgentDiagramScene.scene_uuid == scene_id,
    )).first()
    session = db.get(AgentSession, workspace.session_id)
    if scene is None or session is None:
        raise ApiError(status_code=404, code="DIAGRAM_SCENE_NOT_FOUND", message="Diagram scene not found")
    payload = AgentStore(db).get_diagram_scene_payload(session.session_uuid, scene_id)
    if payload is None:
        raise ApiError(status_code=404, code="DIAGRAM_SCENE_NOT_FOUND", message="Diagram scene not found")
    try:
        svg = render_verified_frame(payload, frame_index)
    except (IndexError, ValueError):
        raise ApiError(status_code=404, code="DIAGRAM_FRAME_NOT_FOUND", message="Diagram frame not found") from None
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; script-src 'none'"},
    )


@router.patch("/workspaces/{workspace_id}/guidance-depth")
def update_workspace_guidance_depth(
    workspace_id: int,
    request: GuidanceDepthUpdateRequest,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
) -> dict:
    repo = TeachingRepository(db)
    _get_owned_workspace_or_404(repo, workspace_id, user_id)
    workspace = repo.update_workspace_guidance_depth(
        workspace_id,
        request.guidance_depth,
        request.depth_override,
    )
    db.commit()
    return _serialize_workspace(repo, workspace)


@router.post("/workspaces/{workspace_id}/pause")
def pause_workspace(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
) -> dict:
    repo = TeachingRepository(db)
    _get_owned_workspace_or_404(repo, workspace_id, user_id)
    workspace = repo.set_workspace_status(workspace_id, ProblemWorkspaceStatus.PAUSED)
    db.commit()
    return _serialize_workspace(repo, workspace)


@router.post("/workspaces/{workspace_id}/resume")
def resume_workspace(
    workspace_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
) -> dict:
    repo = TeachingRepository(db)
    _get_owned_workspace_or_404(repo, workspace_id, user_id)
    workspace = repo.activate_workspace_for_session(workspace_id)
    AgentStore(db).get_or_create_active_boardwork(
        session_id=workspace.session_id,
        title=workspace.title or "当前题目白板",
        source_turn_index=workspace.last_active_turn_index,
        workspace_id=workspace.id,
    )
    db.commit()
    return _serialize_workspace(repo, workspace)


@router.post("/workspaces/{workspace_id}/steps/{step_id}/advance")
def advance_workspace_step(
    workspace_id: int,
    step_id: int,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
) -> dict:
    repo = TeachingRepository(db)
    _get_owned_workspace_or_404(repo, workspace_id, user_id)
    next_step = repo.advance_step(workspace_id, step_id)
    if next_step is None:
        raise ApiError(status_code=409, code="STEP_ADVANCE_FAILED", message="Current step cannot advance")
    workspace = _get_workspace_or_404(repo, workspace_id)
    db.commit()
    return _serialize_workspace(repo, workspace)


@router.get("/sessions/{session_id}/traces")
def list_session_traces(
    session_id: str,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    if _find_session_by_uuid(db, session_id) is None:
        return []
    session = _get_readable_session_by_uuid(db, session_id, user_id, settings)
    return [_serialize_trace(trace) for trace in TeachingRepository(db).list_traces(int(session.id))]


@router.get("/sessions/{session_id}/provider-traces")
def list_session_provider_call_traces(
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    if _find_session_by_uuid(db, session_id) is None:
        return []
    session = _get_readable_session_by_uuid(db, session_id, user_id, settings)
    return [
        _serialize_provider_call_trace(trace)
        for trace in TeachingRepository(db).list_provider_call_traces(int(session.id), limit=limit)
    ]


@router.get("/sessions/{session_id}/tool-traces")
def list_session_tool_call_traces(
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_session),
    user_id: int = Depends(get_v2_user_id),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    if _find_session_by_uuid(db, session_id) is None:
        return []
    session = _get_readable_session_by_uuid(db, session_id, user_id, settings)
    return [
        _serialize_tool_call_trace(trace)
        for trace in TeachingRepository(db).list_tool_call_traces(int(session.id), limit=limit)
    ]


@router.get("/users/{user_id}/memory/settings", response_model=MemorySettingsResponse)
def get_memory_settings(
    user_id: int,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    requester_id: int = Depends(get_v2_user_id),
) -> MemorySettingsResponse:
    if requester_id != user_id:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    _ensure_v2_user(db, user_id, settings)
    settings = TeachingRepository(db).get_or_create_memory_settings(user_id)
    db.commit()
    return MemorySettingsResponse(
        user_id=user_id,
        long_term_memory_enabled=settings.long_term_memory_enabled,
        personalization_enabled=settings.personalization_enabled,
        updated_at=settings.update_time,
    )


@router.patch("/users/{user_id}/memory/settings", response_model=MemorySettingsResponse)
def update_memory_settings(
    user_id: int,
    request: MemorySettingsRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    requester_id: int = Depends(get_v2_user_id),
) -> MemorySettingsResponse:
    if requester_id != user_id:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    _ensure_v2_user(db, user_id, settings)
    repo = TeachingRepository(db)
    settings = repo.set_long_term_memory_enabled(user_id, request.long_term_memory_enabled)
    if request.personalization_enabled is not None:
        settings.personalization_enabled = request.personalization_enabled
        settings.update_time = datetime.utcnow()
        db.add(settings)
        db.flush()
    db.commit()
    return MemorySettingsResponse(
        user_id=user_id,
        long_term_memory_enabled=settings.long_term_memory_enabled,
        personalization_enabled=settings.personalization_enabled,
        updated_at=settings.update_time,
    )


@router.get("/users/{user_id}/memory", response_model=list[MemoryItemResponse])
def list_memory_items(
    user_id: int,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    requester_id: int = Depends(get_v2_user_id),
) -> list[MemoryItemResponse]:
    if requester_id != user_id:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    _ensure_v2_user(db, user_id, settings)
    items = TeachingRepository(db).list_active_long_term_memory(user_id)
    db.commit()
    return [
        MemoryItemResponse(
            memory_id=int(item.id),
            scope=item.scope.value,
            memory_type=item.memory_type,
            content=item.content,
            confidence=item.confidence,
            importance=item.importance,
            updated_at=item.update_time,
        )
        for item in items
        if item.id is not None
    ]


@router.delete("/users/{user_id}/memory/{memory_id}")
def delete_memory_item(
    user_id: int,
    memory_id: int,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    requester_id: int = Depends(get_v2_user_id),
) -> dict[str, bool]:
    if requester_id != user_id:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    _ensure_v2_user(db, user_id, settings)
    deleted = TeachingRepository(db).delete_memory_item(user_id, memory_id)
    db.commit()
    return {"deleted": deleted}


@router.delete("/users/{user_id}/memory")
def clear_memory_items(
    user_id: int,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    requester_id: int = Depends(get_v2_user_id),
) -> dict[str, int]:
    if requester_id != user_id:
        raise ApiError(status_code=404, code="USER_NOT_FOUND", message="User not found")
    _ensure_v2_user(db, user_id, settings)
    deleted_count = TeachingRepository(db).clear_long_term_memory(user_id)
    db.commit()
    return {"deleted_count": deleted_count}
