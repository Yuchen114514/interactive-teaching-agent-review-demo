from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import re

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    AgentAcceptanceRun,
    AgentBlackboardEntry,
    AgentExecutionTrace,
    AgentMessage,
    AgentMessageRole,
    AgentMessageImage,
    AgentMemoryItem,
    AgentProblemWorkspace,
    AgentProviderCallTrace,
    AgentTeachingEvent,
    AgentTeachingPlan,
    AgentTeachingStep,
    AgentTurnRun,
    AgentTurnRunEvent,
    AgentSession,
    AgentToolCallTrace,
    AgentUserMemorySettings,
    AgentUserPreferences,
    GuidanceDepthValue,
    MemoryScope,
    ProblemWorkspaceStatus,
    TeachingPlanStatus,
    TeachingStepStatus,
)
from app.session_titles import generate_session_title


PROVIDER_AUDIT_PAYLOAD_ALLOWLIST = {
    "compact_payload_keys",
    "json_keys",
    "prompt_size_bytes",
    "payload_size_bytes",
    "content_length",
    "timeout_seconds",
    "cache_scope",
    "cache_context_key",
    "cache_bypass_reason",
    "cache_bypass_agent",
    "decision_phase",
    "primary_interface",
    "compatibility_capability",
    "interface_mode",
    "topology_reason",
    "route_reason",
    "rejection_reason",
    "fallback_reason",
    "error_type",
}
MEMORY_DEDUPLICATION_SIMILARITY_THRESHOLD = 0.86

TOOL_AUDIT_PAYLOAD_ALLOWLIST = {
    "mcp_mode",
    "mcp_transport",
    "tool_namespace",
    "resource",
    "problem_id",
    "submission_id",
    "argument_keys",
    "result_keys",
    "content_length",
    "result_type",
    "step_count",
    "timeout_ms",
    "error_type",
}


def _sanitize_provider_audit_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    sanitized = {
        key: value
        for key, value in payload.items()
        if key in PROVIDER_AUDIT_PAYLOAD_ALLOWLIST
    }
    return sanitized


def _sanitize_tool_audit_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key in TOOL_AUDIT_PAYLOAD_ALLOWLIST
    }


TURN_RUN_EVENT_PAYLOAD_ALLOWLIST = {
    "type",
    "delta",
    "status",
    "stage",
    "agent",
    "message",
    "model",
    "provider_backend",
    "session_id",
    "run_id",
    "workspace_id",
    "problem_id",
    "resource",
    "tool_name",
    "latency_ms",
    "error",
    "code",
    "reason",
    "need_diagram",
    "diagram_goal",
    "diagram_scene_id",
    "visual_focus",
    "response",
}


def _sanitize_turn_run_event_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    sanitized: dict = {}
    for key, value in payload.items():
        if key not in TURN_RUN_EVENT_PAYLOAD_ALLOWLIST:
            continue
        if key == "response":
            if isinstance(value, str):
                sanitized[key] = value[:500]
            elif isinstance(value, dict):
                sanitized[key] = {
                    "workspace_id": value.get("workspace_id"),
                    "response": str(value.get("response") or "")[:500],
                    "sidecar_statuses": value.get("sidecar_statuses"),
                }
            continue
        if isinstance(value, str):
            sanitized[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key] = value
        elif isinstance(value, dict):
            sanitized[key] = {str(k): str(v)[:200] for k, v in list(value.items())[:12]}
    return sanitized


def _normalize_memory_content(content: str) -> str:
    return re.sub(r"[\W_]+", "", content.lower())


def _memory_content_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


class TeachingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_memory_settings(self, user_id: int) -> AgentUserMemorySettings:
        settings = self.db.exec(
            select(AgentUserMemorySettings).where(AgentUserMemorySettings.user_id == user_id)
        ).first()
        if settings is not None:
            return settings

        settings = AgentUserMemorySettings(user_id=user_id)
        self.db.add(settings)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            settings = self.db.exec(
                select(AgentUserMemorySettings).where(AgentUserMemorySettings.user_id == user_id)
            ).first()
            if settings is None:
                raise
        return settings

    def get_or_create_user_preferences(self, user_id: int) -> AgentUserPreferences:
        preferences = self.db.exec(
            select(AgentUserPreferences).where(AgentUserPreferences.user_id == user_id)
        ).first()
        if preferences is not None:
            return preferences

        preferences = AgentUserPreferences(user_id=user_id)
        self.db.add(preferences)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            preferences = self.db.exec(
                select(AgentUserPreferences).where(AgentUserPreferences.user_id == user_id)
            ).first()
            if preferences is None:
                raise
        return preferences

    def update_user_preferences(
        self,
        user_id: int,
        *,
        mascot_skin: str | None = None,
        font_size: str | None = None,
    ) -> AgentUserPreferences:
        preferences = self.get_or_create_user_preferences(user_id)
        if mascot_skin is not None:
            preferences.mascot_skin = mascot_skin
        if font_size is not None:
            preferences.font_size = font_size
        preferences.update_time = datetime.utcnow()
        self.db.add(preferences)
        self.db.flush()
        return preferences

    def set_long_term_memory_enabled(self, user_id: int, enabled: bool) -> AgentUserMemorySettings:
        settings = self.get_or_create_memory_settings(user_id)
        settings.long_term_memory_enabled = enabled
        settings.update_time = datetime.utcnow()
        self.db.add(settings)
        self.db.flush()
        return settings

    def create_memory_item(
        self,
        *,
        user_id: int,
        memory_type: str,
        content: str,
        scope: MemoryScope = MemoryScope.USER_PROFILE,
        entity_type: str | None = None,
        entity_id: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
    ) -> AgentMemoryItem:
        item = AgentMemoryItem(
            user_id=user_id,
            scope=scope,
            entity_type=entity_type,
            entity_id=entity_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def upsert_memory_item(
        self,
        *,
        user_id: int,
        memory_type: str,
        content: str,
        scope: MemoryScope = MemoryScope.USER_PROFILE,
        entity_type: str | None = None,
        entity_id: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
    ) -> AgentMemoryItem:
        existing_items = list(self.db.exec(
            select(AgentMemoryItem)
            .where(
                AgentMemoryItem.user_id == user_id,
                AgentMemoryItem.is_active.is_(True),
                AgentMemoryItem.memory_type == memory_type,
                AgentMemoryItem.scope == scope,
                AgentMemoryItem.entity_type == entity_type,
                AgentMemoryItem.entity_id == entity_id,
            )
            .order_by(AgentMemoryItem.update_time.desc(), AgentMemoryItem.id.desc())
        ))
        existing = self._match_existing_memory_item(existing_items, content)
        if existing is None:
            return self.create_memory_item(
                user_id=user_id,
                memory_type=memory_type,
                content=content,
                scope=scope,
                entity_type=entity_type,
                entity_id=entity_id,
                confidence=confidence,
                importance=importance,
            )

        existing.content = content
        existing.confidence = confidence
        existing.importance = importance
        existing.update_time = datetime.utcnow()
        self.db.add(existing)
        self.db.flush()
        return existing

    def _match_existing_memory_item(
        self,
        items: list[AgentMemoryItem],
        content: str,
    ) -> AgentMemoryItem | None:
        normalized_content = _normalize_memory_content(content)
        for item in items:
            if item.content == content:
                return item
            if _memory_content_similarity(normalized_content, _normalize_memory_content(item.content)) >= (
                MEMORY_DEDUPLICATION_SIMILARITY_THRESHOLD
            ):
                return item
        return None

    def list_active_long_term_memory(self, user_id: int) -> list[AgentMemoryItem]:
        settings = self.get_or_create_memory_settings(user_id)
        if not settings.long_term_memory_enabled:
            return []

        return list(
            self.db.exec(
                select(AgentMemoryItem)
                .where(
                    AgentMemoryItem.user_id == user_id,
                    AgentMemoryItem.is_active.is_(True),
                    AgentMemoryItem.scope == MemoryScope.USER_PROFILE,
                )
                .order_by(AgentMemoryItem.update_time.desc(), AgentMemoryItem.id.desc())
            )
        )

    def list_context_memory_for_workspace(
        self,
        *,
        user_id: int,
        workspace: AgentProblemWorkspace,
    ) -> list[AgentMemoryItem]:
        settings = self.get_or_create_memory_settings(user_id)
        if not settings.long_term_memory_enabled:
            return []

        scoped: list[AgentMemoryItem] = []
        if workspace.problem_id is not None:
            scoped.extend(
                self.db.exec(
                    select(AgentMemoryItem)
                    .where(
                        AgentMemoryItem.user_id == user_id,
                        AgentMemoryItem.is_active.is_(True),
                        AgentMemoryItem.scope == MemoryScope.PROBLEM,
                        AgentMemoryItem.entity_type == "problem",
                        AgentMemoryItem.entity_id == str(workspace.problem_id),
                    )
                    .order_by(AgentMemoryItem.importance.desc(), AgentMemoryItem.update_time.desc())
                    .limit(5)
                )
            )

        scoped.extend(
            self.db.exec(
                select(AgentMemoryItem)
                .where(
                    AgentMemoryItem.user_id == user_id,
                    AgentMemoryItem.is_active.is_(True),
                    AgentMemoryItem.scope == MemoryScope.USER_PROFILE,
                )
                .order_by(AgentMemoryItem.importance.desc(), AgentMemoryItem.update_time.desc())
                .limit(5)
            )
        )
        return scoped

    def delete_memory_item(self, user_id: int, memory_id: int) -> bool:
        item = self.db.get(AgentMemoryItem, memory_id)
        if item is None or item.user_id != user_id:
            return False
        item.is_active = False
        item.update_time = datetime.utcnow()
        self.db.add(item)
        self.db.flush()
        return True

    def clear_long_term_memory(self, user_id: int) -> int:
        items = self.list_active_long_term_memory(user_id)
        for item in items:
            item.is_active = False
            item.update_time = datetime.utcnow()
            self.db.add(item)
        self.db.flush()
        return len(items)

    def create_workspace(
        self,
        *,
        session_id: int,
        user_id: int,
        title: str,
        guidance_depth: GuidanceDepthValue = GuidanceDepthValue.DIRECT,
        problem_id: int | None = None,
        problem_summary: str | None = None,
        source: str | None = None,
    ) -> AgentProblemWorkspace:
        workspace = AgentProblemWorkspace(
            session_id=session_id,
            user_id=user_id,
            title=title,
            guidance_depth=guidance_depth,
            problem_id=problem_id,
            problem_summary=problem_summary,
            source=source,
        )
        self.db.add(workspace)
        self.db.flush()
        return workspace

    def record_visible_message(
        self,
        *,
        session_id: int,
        workspace_id: int | None,
        role: AgentMessageRole | str,
        content: str,
        turn_index: int,
    ) -> AgentMessage:
        normalized_role = role if isinstance(role, AgentMessageRole) else AgentMessageRole(role)
        message = AgentMessage(
            session_id=session_id,
            workspace_id=workspace_id,
            role=normalized_role,
            content=content,
            turn_index=turn_index,
            visible_to_user=True,
        )
        self.db.add(message)
        session = self.db.get(AgentSession, session_id)
        if session is not None:
            now = datetime.utcnow()
            if normalized_role == AgentMessageRole.USER and not session.title:
                session.title = generate_session_title(content)
            session.last_message_at = now
            session.update_time = now
            self.db.add(session)
        self.db.flush()
        return message

    def assign_visible_message_workspace(self, message_id: int, workspace_id: int | None) -> AgentMessage:
        message = self.db.get(AgentMessage, message_id)
        if message is None:
            raise ValueError(f"message not found: {message_id}")
        message.workspace_id = workspace_id
        self.db.add(message)
        self.db.flush()
        return message

    def list_recent_visible_messages_for_session(self, session_id: int, *, limit: int = 8) -> list[dict]:
        messages = list(
            self.db.exec(
                select(AgentMessage)
                .where(
                    AgentMessage.session_id == session_id,
                    AgentMessage.visible_to_user.is_(True),
                )
                .order_by(AgentMessage.turn_index.desc(), AgentMessage.id.desc())
                .limit(limit)
            )
        )
        messages.reverse()
        return [
            {
                "role": message.role.value,
                "content": message.content,
                "turn_index": message.turn_index,
            }
            for message in messages
        ]

    def list_recent_visible_messages(self, workspace_id: int, *, limit: int = 8) -> list[dict]:
        messages = list(
            self.db.exec(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.visible_to_user.is_(True),
                )
                .order_by(AgentMessage.turn_index.desc(), AgentMessage.id.desc())
                .limit(limit)
            )
        )
        messages.reverse()
        return [
            {
                "role": message.role.value,
                "content": message.content,
                "turn_index": message.turn_index,
            }
            for message in messages
        ]

    def list_compactable_visible_messages(self, workspace_id: int, *, retain_recent: int = 8) -> list[dict]:
        messages = list(
            self.db.exec(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == workspace_id,
                    AgentMessage.visible_to_user.is_(True),
                )
                .order_by(AgentMessage.turn_index.asc(), AgentMessage.id.asc())
            )
        )
        compactable = messages[: max(0, len(messages) - retain_recent)]
        return [
            {
                "role": message.role.value,
                "content": message.content,
                "turn_index": message.turn_index,
            }
            for message in compactable
        ]

    def count_visible_messages_for_workspace(self, workspace_id: int) -> int:
        return len(
            list(
                self.db.exec(
                    select(AgentMessage.id).where(
                        AgentMessage.workspace_id == workspace_id,
                        AgentMessage.visible_to_user.is_(True),
                    )
                )
            )
        )

    def count_visible_user_messages_for_workspace(self, workspace_id: int) -> int:
        return len(
            list(
                self.db.exec(
                    select(AgentMessage.id).where(
                        AgentMessage.workspace_id == workspace_id,
                        AgentMessage.visible_to_user.is_(True),
                        AgentMessage.role == AgentMessageRole.USER,
                    )
                )
            )
        )

    def get_workspace_summary(self, workspace_id: int) -> AgentMemoryItem | None:
        return self.db.exec(
            select(AgentMemoryItem)
            .where(
                AgentMemoryItem.scope == MemoryScope.WORKSPACE,
                AgentMemoryItem.entity_type == "workspace",
                AgentMemoryItem.entity_id == str(workspace_id),
                AgentMemoryItem.memory_type == "workspace_summary",
                AgentMemoryItem.is_active.is_(True),
            )
            .order_by(AgentMemoryItem.update_time.desc(), AgentMemoryItem.id.desc())
            .limit(1)
        ).first()

    def upsert_workspace_summary(
        self,
        *,
        user_id: int,
        workspace_id: int,
        content: str,
        confidence: float | None = None,
        importance: float | None = None,
    ) -> AgentMemoryItem:
        normalized_content = content.strip()
        existing = self.get_workspace_summary(workspace_id)
        if existing is None:
            item = AgentMemoryItem(
                user_id=user_id,
                scope=MemoryScope.WORKSPACE,
                entity_type="workspace",
                entity_id=str(workspace_id),
                memory_type="workspace_summary",
                content=normalized_content,
                summary=normalized_content,
                confidence=confidence,
                importance=importance,
            )
            self.db.add(item)
            self.db.flush()
            return item

        existing.content = normalized_content
        existing.summary = normalized_content
        existing.confidence = confidence
        existing.importance = importance
        existing.update_time = datetime.utcnow()
        self.db.add(existing)
        self.db.flush()
        return existing

    def get_active_workspace(self, session_id: int) -> AgentProblemWorkspace | None:
        return self.db.exec(
            select(AgentProblemWorkspace)
            .where(
                AgentProblemWorkspace.session_id == session_id,
                AgentProblemWorkspace.status == ProblemWorkspaceStatus.ACTIVE,
            )
            .order_by(AgentProblemWorkspace.update_time.desc(), AgentProblemWorkspace.id.desc())
            .limit(1)
        ).first()

    def find_workspace_by_problem_id(
        self,
        *,
        session_id: int,
        user_id: int,
        problem_id: int,
    ) -> AgentProblemWorkspace | None:
        return self.db.exec(
            select(AgentProblemWorkspace)
            .where(
                AgentProblemWorkspace.session_id == session_id,
                AgentProblemWorkspace.user_id == user_id,
                AgentProblemWorkspace.problem_id == problem_id,
                AgentProblemWorkspace.status != ProblemWorkspaceStatus.ABANDONED,
            )
            .order_by(AgentProblemWorkspace.update_time.desc(), AgentProblemWorkspace.id.desc())
            .limit(1)
        ).first()

    def list_workspaces_for_session(self, session_id: int) -> list[AgentProblemWorkspace]:
        return list(
            self.db.exec(
                select(AgentProblemWorkspace)
                .where(AgentProblemWorkspace.session_id == session_id)
                .order_by(AgentProblemWorkspace.update_time.desc(), AgentProblemWorkspace.id.desc())
            )
        )

    def get_workspace(self, workspace_id: int) -> AgentProblemWorkspace | None:
        return self.db.get(AgentProblemWorkspace, workspace_id)

    def update_workspace_guidance_depth(
        self,
        workspace_id: int,
        guidance_depth: GuidanceDepthValue,
        depth_override: bool,
    ) -> AgentProblemWorkspace:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            raise ValueError(f"workspace not found: {workspace_id}")
        workspace.guidance_depth = guidance_depth
        workspace.depth_override = depth_override
        workspace.update_time = datetime.utcnow()
        self.db.add(workspace)
        self.db.flush()
        return workspace

    def set_workspace_status(
        self,
        workspace_id: int,
        status: ProblemWorkspaceStatus,
    ) -> AgentProblemWorkspace:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            raise ValueError(f"workspace not found: {workspace_id}")
        workspace.status = status
        workspace.update_time = datetime.utcnow()
        self.db.add(workspace)
        self.db.flush()
        return workspace

    def activate_workspace_for_session(self, workspace_id: int) -> AgentProblemWorkspace:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            raise ValueError(f"workspace not found: {workspace_id}")

        now = datetime.utcnow()
        active_siblings = list(
            self.db.exec(
                select(AgentProblemWorkspace).where(
                    AgentProblemWorkspace.session_id == workspace.session_id,
                    AgentProblemWorkspace.status == ProblemWorkspaceStatus.ACTIVE,
                    AgentProblemWorkspace.id != workspace_id,
                )
            )
        )
        for sibling in active_siblings:
            sibling.status = ProblemWorkspaceStatus.PAUSED
            sibling.update_time = now
            self.db.add(sibling)

        workspace.status = ProblemWorkspaceStatus.ACTIVE
        workspace.update_time = now
        self.db.add(workspace)
        self.db.flush()
        return workspace

    def create_plan(
        self,
        *,
        workspace_id: int,
        planner_agent: str,
        plan_summary: str | None = None,
        branch_from_plan_id: int | None = None,
    ) -> AgentTeachingPlan:
        existing_count = len(
            list(
                self.db.exec(
                    select(AgentTeachingPlan).where(AgentTeachingPlan.workspace_id == workspace_id)
                )
            )
        )
        plan = AgentTeachingPlan(
            workspace_id=workspace_id,
            version=existing_count + 1,
            planner_agent=planner_agent,
            plan_summary=plan_summary,
            branch_from_plan_id=branch_from_plan_id,
        )
        self.db.add(plan)
        self.db.flush()
        return plan

    def supersede_plan(self, plan_id: int) -> AgentTeachingPlan:
        plan = self.db.get(AgentTeachingPlan, plan_id)
        if plan is None:
            raise ValueError(f"plan not found: {plan_id}")
        plan.status = TeachingPlanStatus.SUPERSEDED
        plan.update_time = datetime.utcnow()
        self.db.add(plan)
        self.db.flush()
        return plan

    def create_steps(
        self,
        *,
        plan_id: int,
        workspace_id: int,
        step_specs: list[dict[str, str]],
    ) -> list[AgentTeachingStep]:
        steps: list[AgentTeachingStep] = []
        for index, spec in enumerate(step_specs):
            step = AgentTeachingStep(
                plan_id=plan_id,
                workspace_id=workspace_id,
                order_index=index,
                step_key=spec["step_key"],
                title=spec["title"],
                teaching_goal=spec.get("teaching_goal", ""),
                expected_student_action=spec.get("expected_student_action"),
                mastery_criteria=spec.get("mastery_criteria"),
            )
            self.db.add(step)
            steps.append(step)
        self.db.flush()
        return steps

    def activate_plan(self, workspace_id: int, plan_id: int, current_step_id: int) -> AgentProblemWorkspace:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            raise ValueError(f"workspace not found: {workspace_id}")

        workspace.active_plan_id = plan_id
        workspace.current_step_id = current_step_id
        workspace.update_time = datetime.utcnow()
        self.db.add(workspace)

        current_step = self.db.get(AgentTeachingStep, current_step_id)
        if current_step is not None:
            current_step.status = TeachingStepStatus.IN_PROGRESS
            current_step.update_time = datetime.utcnow()
            self.db.add(current_step)
        self.db.flush()
        return workspace

    def get_active_plan(self, workspace_id: int) -> AgentTeachingPlan | None:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is not None and workspace.active_plan_id is not None:
            return self.db.get(AgentTeachingPlan, workspace.active_plan_id)
        return self.db.exec(
            select(AgentTeachingPlan)
            .where(
                AgentTeachingPlan.workspace_id == workspace_id,
                AgentTeachingPlan.status == TeachingPlanStatus.ACTIVE,
            )
            .order_by(AgentTeachingPlan.version.desc(), AgentTeachingPlan.id.desc())
            .limit(1)
        ).first()

    def list_steps(self, plan_id: int) -> list[AgentTeachingStep]:
        return list(
            self.db.exec(
                select(AgentTeachingStep)
                .where(AgentTeachingStep.plan_id == plan_id)
                .order_by(AgentTeachingStep.order_index.asc(), AgentTeachingStep.id.asc())
            )
        )

    def get_current_step(self, workspace_id: int) -> AgentTeachingStep | None:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None or workspace.current_step_id is None:
            return None
        return self.db.get(AgentTeachingStep, workspace.current_step_id)

    def record_step_prompt(self, step_id: int, turn_index: int) -> AgentTeachingStep:
        step = self.db.get(AgentTeachingStep, step_id)
        if step is None:
            raise ValueError(f"step not found: {step_id}")
        step.repeat_count += 1
        step.last_prompt_turn_index = turn_index
        step.update_time = datetime.utcnow()
        self.db.add(step)
        self.db.flush()
        return step

    def record_tutor_step_delta(self, step_id: int, step_delta: dict) -> AgentTeachingStep:
        step = self.db.get(AgentTeachingStep, step_id)
        if step is None:
            raise ValueError(f"step not found: {step_id}")

        delta_status = step_delta.get("status") if isinstance(step_delta, dict) else None
        status_map = {
            "in_progress": TeachingStepStatus.IN_PROGRESS,
            "blocked": TeachingStepStatus.NEEDS_REVISIT,
            "ready_for_code": TeachingStepStatus.IN_PROGRESS,
            "ready_for_debug": TeachingStepStatus.IN_PROGRESS,
        }
        if delta_status not in status_map:
            raise ValueError(f"unsupported tutor step delta status: {delta_status}")

        step.status = status_map[delta_status]
        step.mastery_state = delta_status
        step.update_time = datetime.utcnow()
        self.db.add(step)
        self.db.flush()
        return step

    def record_event(
        self,
        *,
        workspace_id: int,
        session_id: int,
        turn_index: int,
        event_type: str,
        actor: str,
        step_id: int | None = None,
        payload: dict | None = None,
        confidence: float | None = None,
        visible_to_user: bool = False,
    ) -> AgentTeachingEvent:
        event = AgentTeachingEvent(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=turn_index,
            event_type=event_type,
            actor=actor,
            step_id=step_id,
            payload=payload or {},
            confidence=confidence,
            visible_to_user=visible_to_user,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_events(self, workspace_id: int) -> list[AgentTeachingEvent]:
        return list(
            self.db.exec(
                select(AgentTeachingEvent)
                .where(AgentTeachingEvent.workspace_id == workspace_id)
                .order_by(AgentTeachingEvent.id.asc())
            )
        )

    def create_turn_run(
        self,
        *,
        run_uuid: str,
        client_request_id: str | None = None,
        session_id: int,
        workspace_id: int | None,
        turn_index: int,
        user_message_id: int | None,
        metadata: dict | None = None,
    ) -> AgentTurnRun:
        run = AgentTurnRun(
            run_uuid=run_uuid,
            session_id=session_id,
            client_request_id=client_request_id or run_uuid,
            workspace_id=workspace_id,
            turn_index=turn_index,
            user_message_id=user_message_id,
            status="running",
            metadata_json=metadata or {},
        )
        self.db.add(run)
        self.db.flush()
        return run

    def get_turn_run_by_client_request_id(self, session_id: int, client_request_id: str) -> AgentTurnRun | None:
        return self.db.exec(
            select(AgentTurnRun).where(
                AgentTurnRun.session_id == session_id,
                AgentTurnRun.client_request_id == client_request_id,
            )
        ).first()

    def delete_turn_run(self, run_uuid: str) -> None:
        run = self.db.exec(select(AgentTurnRun).where(AgentTurnRun.run_uuid == run_uuid)).first()
        if run is None:
            return
        self.db.delete(run)
        self.db.flush()

    def delete_message(self, message_id: int) -> None:
        message = self.db.exec(select(AgentMessage).where(AgentMessage.id == message_id)).first()
        if message is None:
            return
        self.db.delete(message)
        self.db.flush()

    def _delete_message_image_links(self, message_id: int) -> None:
        links = list(self.db.exec(select(AgentMessageImage).where(AgentMessageImage.message_id == message_id)))
        for link in links:
            self.db.delete(link)
        if links:
            self.db.flush()

    def discard_turn_run(self, run_uuid: str, *, restore_session_metadata: bool = True) -> None:
        run = self.db.exec(select(AgentTurnRun).where(AgentTurnRun.run_uuid == run_uuid)).first()
        if run is None:
            return

        session = self.db.get(AgentSession, run.session_id)
        metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
        if restore_session_metadata and session is not None:
            before = metadata.get("session_before_turn") if isinstance(metadata.get("session_before_turn"), dict) else {}
            for field_name in ("last_message_at", "update_time"):
                value = before.get(field_name)
                if value:
                    try:
                        setattr(session, field_name, datetime.fromisoformat(str(value)))
                    except ValueError:
                        pass
            if "title" in before:
                session.title = before.get("title") or None
            self.db.add(session)

        event_rows = list(self.db.exec(select(AgentTurnRunEvent).where(AgentTurnRunEvent.run_id == run.id)))
        for event in event_rows:
            self.db.delete(event)
        user_message_id = run.user_message_id
        assistant_message_id = run.assistant_message_id
        self.db.delete(run)
        self.db.flush()
        if user_message_id is not None:
            self._delete_message_image_links(int(user_message_id))
            self.delete_message(int(user_message_id))
        if assistant_message_id is not None:
            self._delete_message_image_links(int(assistant_message_id))
            self.delete_message(int(assistant_message_id))

    def lock_session_for_turn_admission(self, session_id: int) -> None:
        self.db.exec(
            select(AgentSession)
            .where(AgentSession.id == session_id)
            .with_for_update()
        ).first()

    def get_active_turn_run(self, session_id: int) -> AgentTurnRun | None:
        return self.db.exec(
            select(AgentTurnRun)
            .where(
                AgentTurnRun.session_id == session_id,
                AgentTurnRun.status.in_(("running", "cancel_requested")),
            )
            .order_by(AgentTurnRun.id.desc())
        ).first()

    def request_turn_run_cancellation(self, session_id: int) -> list[AgentTurnRun]:
        runs = list(
            self.db.exec(
                select(AgentTurnRun).where(
                    AgentTurnRun.session_id == session_id,
                    AgentTurnRun.status == "running",
                )
            )
        )
        now = datetime.utcnow()
        for run in runs:
            run.status = "cancel_requested"
            run.update_time = now
            self.db.add(run)
        self.db.flush()
        return runs

    def is_turn_run_cancel_requested(self, run_id: int) -> bool:
        run = self.db.get(AgentTurnRun, run_id)
        if run is None:
            return False
        self.db.refresh(run)
        return run.status == "cancel_requested"

    def interrupt_stale_turn_runs(self) -> int:
        runs = list(
            self.db.exec(
                select(AgentTurnRun).where(AgentTurnRun.status.in_(("running", "cancel_requested")))
            )
        )
        now = datetime.utcnow()
        for run in runs:
            run.status = "interrupted"
            run.error_code = "PROCESS_RESTARTED"
            run.error_message = "The server restarted before this teaching turn completed."
            run.update_time = now
            self.db.add(run)
        self.db.flush()
        return len(runs)

    def append_turn_run_event(self, run_id: int, payload: dict) -> AgentTurnRunEvent:
        run = self.db.get(AgentTurnRun, run_id)
        if run is None:
            raise ValueError(f"turn run not found: {run_id}")
        event_type = str(payload.get("type") or "unknown")
        run.event_count += 1
        run.update_time = datetime.utcnow()
        event = AgentTurnRunEvent(
            run_id=run_id,
            event_index=run.event_count,
            event_type=event_type,
            payload=_sanitize_turn_run_event_payload(payload),
        )
        self.db.add(run)
        self.db.add(event)
        self.db.flush()
        return event

    def complete_turn_run(
        self,
        run_id: int,
        *,
        workspace_id: int | None = None,
        assistant_message_id: int | None = None,
        response_preview: str | None = None,
    ) -> AgentTurnRun:
        run = self.db.get(AgentTurnRun, run_id)
        if run is None:
            raise ValueError(f"turn run not found: {run_id}")
        run.status = "completed"
        if workspace_id is not None:
            run.workspace_id = workspace_id
        run.assistant_message_id = assistant_message_id
        run.response_preview = response_preview
        run.update_time = datetime.utcnow()
        self.db.add(run)
        self.db.flush()
        return run

    def fail_turn_run(
        self,
        run_id: int,
        *,
        error_code: str,
        error_message: str,
        workspace_id: int | None = None,
    ) -> AgentTurnRun:
        run = self.db.get(AgentTurnRun, run_id)
        if run is None:
            raise ValueError(f"turn run not found: {run_id}")
        run.status = "failed"
        run.error_code = error_code
        run.error_message = error_message
        if workspace_id is not None:
            run.workspace_id = workspace_id
        run.update_time = datetime.utcnow()
        self.db.add(run)
        self.db.flush()
        return run

    def stop_turn_run(
        self,
        run_id: int,
        *,
        workspace_id: int | None = None,
        assistant_message_id: int | None = None,
        response_preview: str | None = None,
    ) -> AgentTurnRun:
        run = self.db.get(AgentTurnRun, run_id)
        if run is None:
            raise ValueError(f"turn run not found: {run_id}")
        run.status = "stopped"
        if workspace_id is not None:
            run.workspace_id = workspace_id
        run.assistant_message_id = assistant_message_id
        run.response_preview = response_preview
        run.error_code = None
        run.error_message = None
        run.update_time = datetime.utcnow()
        self.db.add(run)
        self.db.flush()
        return run

    def list_turn_runs_for_session(self, session_id: int, *, limit: int = 20, include_events: bool = True) -> list[dict]:
        runs = list(
            self.db.exec(
                select(AgentTurnRun)
                .where(AgentTurnRun.session_id == session_id)
                .order_by(AgentTurnRun.id.desc())
                .limit(limit)
            )
        )
        event_map: dict[int, list[AgentTurnRunEvent]] = {}
        if include_events and runs:
            run_ids = [int(run.id) for run in runs if run.id is not None]
            events = list(
                self.db.exec(
                    select(AgentTurnRunEvent)
                    .where(AgentTurnRunEvent.run_id.in_(run_ids))
                    .order_by(AgentTurnRunEvent.run_id.asc(), AgentTurnRunEvent.event_index.asc())
                )
            )
            for event in events:
                event_map.setdefault(event.run_id, []).append(event)
        return [
            {
                "run_id": run.run_uuid,
                "status": run.status,
                "session_id": run.session_id,
                "workspace_id": run.workspace_id,
                "turn_index": run.turn_index,
                "user_message_id": run.user_message_id,
                "assistant_message_id": run.assistant_message_id,
                "response_preview": run.response_preview,
                "error_code": run.error_code,
                "error_message": run.error_message,
                "event_count": run.event_count,
                "created_at": run.create_time.isoformat(),
                "updated_at": run.update_time.isoformat(),
                "events": [
                    {
                        "type": event.event_type,
                        "event_index": event.event_index,
                        "payload": event.payload,
                    }
                    for event in event_map.get(int(run.id), [])
                ],
            }
            for run in runs
        ]

    def get_turn_run_events(self, run_uuid: str, *, after: int = 0, limit: int = 200) -> dict | None:
        run = self.db.exec(select(AgentTurnRun).where(AgentTurnRun.run_uuid == run_uuid)).first()
        if run is None or run.id is None:
            return None
        safe_after = max(0, int(after))
        safe_limit = min(max(int(limit), 1), 500)
        events = list(
            self.db.exec(
                select(AgentTurnRunEvent)
                .where(
                    AgentTurnRunEvent.run_id == int(run.id),
                    AgentTurnRunEvent.event_index > safe_after,
                )
                .order_by(AgentTurnRunEvent.event_index.asc())
                .limit(safe_limit)
            )
        )
        return {
            "run_id": run.run_uuid,
            "status": run.status,
            "session_id": run.session_id,
            "workspace_id": run.workspace_id,
            "turn_index": run.turn_index,
            "user_message_id": run.user_message_id,
            "assistant_message_id": run.assistant_message_id,
            "response_preview": run.response_preview,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "event_count": run.event_count,
            "events": [
                {
                    "type": event.event_type,
                    "event_index": event.event_index,
                    "payload": event.payload,
                }
                for event in events
            ],
        }

    def record_blackboard_entry(
        self,
        *,
        workspace_id: int,
        session_id: int,
        entry_type: str,
        content: str,
        writer_agent: str,
        step_id: int | None = None,
        source_event_id: int | None = None,
        source_message_id: int | None = None,
        payload: dict | None = None,
        confidence: float | None = None,
        visibility: str = "internal",
    ) -> AgentBlackboardEntry:
        entry = AgentBlackboardEntry(
            workspace_id=workspace_id,
            session_id=session_id,
            step_id=step_id,
            entry_type=entry_type,
            content=content,
            writer_agent=writer_agent,
            source_event_id=source_event_id,
            source_message_id=source_message_id,
            payload=payload or {},
            confidence=confidence,
            visibility=visibility,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def list_blackboard_entries(self, workspace_id: int) -> list[AgentBlackboardEntry]:
        return list(
            self.db.exec(
                select(AgentBlackboardEntry)
                .where(
                    AgentBlackboardEntry.workspace_id == workspace_id,
                    AgentBlackboardEntry.status == "active",
                )
                .order_by(AgentBlackboardEntry.id.asc())
            )
        )

    def advance_step(self, workspace_id: int, current_step_id: int) -> AgentTeachingStep | None:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        current_step = self.db.get(AgentTeachingStep, current_step_id)
        if workspace is None or current_step is None or workspace.active_plan_id is None:
            return None
        if workspace.current_step_id != current_step_id:
            return None

        current_step.status = TeachingStepStatus.CONFIRMED
        current_step.confirmed_turn_index = workspace.last_active_turn_index
        current_step.update_time = datetime.utcnow()
        self.db.add(current_step)

        next_step = self.db.exec(
            select(AgentTeachingStep)
            .where(
                AgentTeachingStep.plan_id == workspace.active_plan_id,
                AgentTeachingStep.order_index > current_step.order_index,
            )
            .order_by(AgentTeachingStep.order_index.asc(), AgentTeachingStep.id.asc())
            .limit(1)
        ).first()
        if next_step is None:
            workspace.status = ProblemWorkspaceStatus.COMPLETED
            workspace.update_time = datetime.utcnow()
            self.db.add(workspace)
            self.db.flush()
            return current_step

        next_step.status = TeachingStepStatus.IN_PROGRESS
        next_step.update_time = datetime.utcnow()
        workspace.current_step_id = next_step.id
        workspace.update_time = datetime.utcnow()
        self.db.add(next_step)
        self.db.add(workspace)
        self.db.flush()
        return next_step

    def complete_plan_steps(
        self,
        *,
        workspace_id: int,
        plan_id: int,
        current_step_key: str | None = None,
    ) -> AgentTeachingStep | None:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            return None
        steps = list(
            self.db.exec(
                select(AgentTeachingStep)
                .where(AgentTeachingStep.plan_id == plan_id)
                .order_by(AgentTeachingStep.order_index.asc(), AgentTeachingStep.id.asc())
            )
        )
        if not steps:
            return None

        selected_step = None
        for step in steps:
            if current_step_key and step.step_key == current_step_key:
                selected_step = step
            step.status = TeachingStepStatus.CONFIRMED
            step.confirmed_turn_index = workspace.last_active_turn_index
            step.update_time = datetime.utcnow()
            self.db.add(step)
        if selected_step is None:
            selected_step = steps[-1]

        workspace.active_plan_id = plan_id
        workspace.current_step_id = selected_step.id
        workspace.status = ProblemWorkspaceStatus.COMPLETED
        workspace.update_time = datetime.utcnow()
        self.db.add(workspace)
        self.db.flush()
        return selected_step

    def focus_plan_step(
        self,
        *,
        workspace_id: int,
        plan_id: int,
        step_key: str,
    ) -> AgentTeachingStep | None:
        workspace = self.db.get(AgentProblemWorkspace, workspace_id)
        if workspace is None:
            return None
        target_step = self.db.exec(
            select(AgentTeachingStep)
            .where(
                AgentTeachingStep.plan_id == plan_id,
                AgentTeachingStep.step_key == step_key,
            )
            .limit(1)
        ).first()
        if target_step is None:
            return None
        target_step.status = TeachingStepStatus.IN_PROGRESS
        target_step.update_time = datetime.utcnow()
        workspace.active_plan_id = plan_id
        workspace.current_step_id = target_step.id
        workspace.status = ProblemWorkspaceStatus.ACTIVE
        workspace.update_time = datetime.utcnow()
        self.db.add(target_step)
        self.db.add(workspace)
        self.db.flush()
        return target_step

    def record_trace(
        self,
        *,
        session_id: int,
        agent_name: str,
        status: str,
        input_summary: str = "",
        output_summary: str = "",
        workspace_id: int | None = None,
        turn_index: int = 0,
        latency_ms: int = 0,
        blackboard_write_count: int = 0,
        memory_write_count: int = 0,
        error_message: str | None = None,
        payload: dict | None = None,
    ) -> AgentExecutionTrace:
        trace = AgentExecutionTrace(
            session_id=session_id,
            workspace_id=workspace_id,
            turn_index=turn_index,
            agent_name=agent_name,
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            latency_ms=latency_ms,
            blackboard_write_count=blackboard_write_count,
            memory_write_count=memory_write_count,
            error_message=error_message,
            payload=payload or {},
        )
        self.db.add(trace)
        self.db.flush()
        return trace

    def list_traces(self, session_id: int) -> list[AgentExecutionTrace]:
        return list(
            self.db.exec(
                select(AgentExecutionTrace)
                .where(AgentExecutionTrace.session_id == session_id)
                .order_by(AgentExecutionTrace.id.asc())
            )
        )

    def record_provider_call_trace(
        self,
        *,
        session_id: int,
        agent_name: str,
        capability: str,
        provider_backend: str,
        outcome: str,
        accepted: bool,
        workspace_id: int | None = None,
        turn_index: int = 0,
        latency_ms: int = 0,
        prompt_size_bytes: int | None = None,
        payload_size_bytes: int | None = None,
        fallback_reason: str | None = None,
        error_type: str | None = None,
        payload: dict | None = None,
    ) -> AgentProviderCallTrace:
        sanitized_payload = _sanitize_provider_audit_payload(payload)
        trace = AgentProviderCallTrace(
            session_id=session_id,
            workspace_id=workspace_id,
            turn_index=turn_index,
            agent_name=agent_name,
            capability=capability,
            provider_backend=provider_backend,
            outcome=outcome,
            accepted=accepted,
            latency_ms=latency_ms,
            prompt_size_bytes=prompt_size_bytes,
            payload_size_bytes=payload_size_bytes,
            fallback_reason=fallback_reason,
            error_type=error_type,
            payload=sanitized_payload,
        )
        self.db.add(trace)
        self.db.flush()
        return trace

    def list_provider_call_traces(self, session_id: int, *, limit: int = 50) -> list[AgentProviderCallTrace]:
        bounded_limit = min(200, max(1, limit))
        return list(
            self.db.exec(
                select(AgentProviderCallTrace)
                .where(AgentProviderCallTrace.session_id == session_id)
                .order_by(AgentProviderCallTrace.id.desc())
                .limit(bounded_limit)
            )
        )

    def record_tool_call_trace(
        self,
        *,
        session_id: int,
        owner_agent: str,
        tool_backend: str,
        tool_name: str,
        status: str,
        workspace_id: int | None = None,
        turn_index: int = 0,
        latency_ms: int = 0,
        request_summary: str | None = None,
        response_summary: str | None = None,
        error_type: str | None = None,
        payload: dict | None = None,
    ) -> AgentToolCallTrace:
        trace = AgentToolCallTrace(
            session_id=session_id,
            workspace_id=workspace_id,
            turn_index=turn_index,
            owner_agent=owner_agent,
            tool_backend=tool_backend,
            tool_name=tool_name,
            status=status,
            latency_ms=latency_ms,
            request_summary=request_summary,
            response_summary=response_summary,
            error_type=error_type,
            payload=_sanitize_tool_audit_payload(payload),
        )
        self.db.add(trace)
        self.db.flush()
        return trace

    def list_tool_call_traces(self, session_id: int, *, limit: int = 50) -> list[AgentToolCallTrace]:
        bounded_limit = min(200, max(1, limit))
        return list(
            self.db.exec(
                select(AgentToolCallTrace)
                .where(AgentToolCallTrace.session_id == session_id)
                .order_by(AgentToolCallTrace.id.desc())
                .limit(bounded_limit)
            )
        )

    def record_acceptance_run(
        self,
        *,
        scenario_id: str,
        status: str,
        mode: str,
        model: str | None = None,
        runtime_equivalent: bool | None = None,
        checks: dict | None = None,
        failed_checks: list[str] | None = None,
        performance: dict | None = None,
        real_provider: dict | None = None,
        turn_count: int = 0,
    ) -> AgentAcceptanceRun:
        run = AgentAcceptanceRun(
            scenario_id=scenario_id,
            status=status,
            mode=mode,
            model=model,
            runtime_equivalent=runtime_equivalent,
            checks=checks or {},
            failed_checks=failed_checks or [],
            performance=performance or {},
            real_provider=real_provider,
            turn_count=turn_count,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def list_acceptance_runs(self, *, limit: int = 20) -> list[AgentAcceptanceRun]:
        bounded_limit = min(100, max(1, limit))
        return list(
            self.db.exec(
                select(AgentAcceptanceRun)
                .order_by(AgentAcceptanceRun.create_time.desc(), AgentAcceptanceRun.id.desc())
                .limit(bounded_limit)
            )
        )

    def delete_acceptance_run(self, run_id: int) -> bool:
        run = self.db.get(AgentAcceptanceRun, run_id)
        if run is None:
            return False
        self.db.delete(run)
        self.db.flush()
        return True

    def clear_acceptance_runs(self) -> int:
        runs = list(self.db.exec(select(AgentAcceptanceRun)))
        for run in runs:
            self.db.delete(run)
        self.db.flush()
        return len(runs)
