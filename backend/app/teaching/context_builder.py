from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import (
    AgentBlackboardEntry,
    AgentMemoryItem,
    AgentProblemWorkspace,
    AgentTeachingEvent,
    AgentTeachingPlan,
    AgentTeachingStep,
)
from app.repositories.teaching import TeachingRepository


@dataclass(frozen=True)
class TeachingContextBundle:
    system_policy: dict[str, Any]
    workspace: dict[str, Any]
    workspace_summary: dict[str, Any]
    current_plan: dict[str, Any]
    current_step: dict[str, Any]
    relevant_blackboard: list[dict[str, Any]]
    recent_turns: list[dict[str, Any]]
    retrieved_memory: list[dict[str, Any]]
    context_snapshot: dict[str, Any]


class TeachingContextBuilder:
    raw_recent_turn_limit = 8

    def __init__(self, repo: TeachingRepository):
        self.repo = repo

    def build(
        self,
        *,
        user_id: int,
        workspace: AgentProblemWorkspace,
        recent_turns: list[dict[str, Any]] | None = None,
    ) -> TeachingContextBundle:
        active_plan = self.repo.get_active_plan(int(workspace.id)) if workspace.id is not None else None
        current_step = self.repo.get_current_step(int(workspace.id)) if workspace.id is not None else None
        blackboard = (
            self.repo.list_blackboard_entries(int(workspace.id))
            if workspace.id is not None
            else []
        )
        events = self.repo.list_events(int(workspace.id)) if workspace.id is not None else []
        memories = self.repo.list_context_memory_for_workspace(user_id=user_id, workspace=workspace)
        workspace_summary = self.repo.get_workspace_summary(int(workspace.id)) if workspace.id is not None else None
        memory_settings = self.repo.get_or_create_memory_settings(user_id)
        if recent_turns is None and workspace.id is not None:
            normalized_recent_turns = self.repo.list_recent_visible_messages(
                int(workspace.id),
                limit=self.raw_recent_turn_limit,
            )
            total_visible_turn_count = self.repo.count_visible_messages_for_workspace(int(workspace.id))
        else:
            normalized_recent_turns = recent_turns or []
            total_visible_turn_count = len(normalized_recent_turns)

        return TeachingContextBundle(
            system_policy={
                "future_steps_visible": False,
                "problem_specific_runtime_rules_allowed": False,
            },
            workspace=self._serialize_workspace(workspace),
            workspace_summary=self._serialize_workspace_summary(workspace_summary),
            current_plan=self._serialize_plan(active_plan),
            current_step=self._serialize_step(current_step),
            relevant_blackboard=[self._serialize_blackboard_entry(entry) for entry in blackboard],
            recent_turns=normalized_recent_turns,
            retrieved_memory=[self._serialize_memory_item(item) for item in memories],
            context_snapshot=self._build_context_snapshot(
                workspace=workspace,
                current_step=current_step,
                blackboard=blackboard,
                events=events,
                recent_turns=normalized_recent_turns,
                total_visible_turn_count=total_visible_turn_count,
                long_term_enabled=memory_settings.long_term_memory_enabled,
                retrieved_memory_count=len(memories),
                workspace_summary=workspace_summary,
            ),
        )

    def _serialize_workspace(self, workspace: AgentProblemWorkspace) -> dict[str, Any]:
        return {
            "workspace_id": workspace.id,
            "session_id": workspace.session_id,
            "user_id": workspace.user_id,
            "problem_key": workspace.problem_key,
            "problem_id": workspace.problem_id,
            "source": workspace.source,
            "title": workspace.title,
            "problem_summary": workspace.problem_summary,
            "status": workspace.status.value,
            "guidance_depth": workspace.guidance_depth.value,
            "algorithm_family": workspace.algorithm_family,
            "active_plan_id": workspace.active_plan_id,
            "current_step_id": workspace.current_step_id,
        }

    def _serialize_plan(self, plan: AgentTeachingPlan | None) -> dict[str, Any]:
        if plan is None:
            return {}
        return {
            "plan_id": plan.id,
            "workspace_id": plan.workspace_id,
            "version": plan.version,
            "status": plan.status.value,
            "planner_agent": plan.planner_agent,
            "plan_summary": plan.plan_summary,
            "branch_from_plan_id": plan.branch_from_plan_id,
        }

    def _serialize_step(self, step: AgentTeachingStep | None) -> dict[str, Any]:
        if step is None:
            return {}
        return {
            "step_id": step.id,
            "plan_id": step.plan_id,
            "workspace_id": step.workspace_id,
            "step_key": step.step_key,
            "title": step.title,
            "teaching_goal": step.teaching_goal,
            "expected_student_action": step.expected_student_action,
            "mastery_criteria": step.mastery_criteria,
            "status": step.status.value,
            "mastery_state": step.mastery_state,
            "repeat_count": step.repeat_count,
            "last_prompt_turn_index": step.last_prompt_turn_index,
        }

    def _serialize_blackboard_entry(self, entry: AgentBlackboardEntry) -> dict[str, Any]:
        return {
            "entry_id": entry.id,
            "step_id": entry.step_id,
            "entry_type": entry.entry_type,
            "content": entry.content,
            "writer_agent": entry.writer_agent,
            "source_event_id": entry.source_event_id,
            "confidence": entry.confidence,
            "visibility": entry.visibility,
            "status": entry.status,
        }

    def _serialize_memory_item(self, item: AgentMemoryItem) -> dict[str, Any]:
        return {
            "memory_id": item.id,
            "scope": item.scope.value,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "memory_type": item.memory_type,
            "content": item.content,
            "summary": item.summary,
            "confidence": item.confidence,
            "importance": item.importance,
        }

    def _serialize_workspace_summary(self, item: AgentMemoryItem | None) -> dict[str, Any]:
        if item is None:
            return {}
        return {
            "memory_id": item.id,
            "content": item.content,
            "confidence": item.confidence,
            "importance": item.importance,
            "updated_at": item.update_time,
        }

    def _serialize_event_window_item(self, event: AgentTeachingEvent) -> dict[str, Any]:
        return {
            "event_type": event.event_type,
            "actor": event.actor,
            "step_id": event.step_id,
            "turn_index": event.turn_index,
        }

    def _build_context_snapshot(
        self,
        *,
        workspace: AgentProblemWorkspace,
        current_step: AgentTeachingStep | None,
        blackboard: list[AgentBlackboardEntry],
        events: list[AgentTeachingEvent],
        recent_turns: list[dict[str, Any]],
        total_visible_turn_count: int,
        long_term_enabled: bool,
        retrieved_memory_count: int,
        workspace_summary: AgentMemoryItem | None,
    ) -> dict[str, Any]:
        if current_step is None:
            current_step_snapshot: dict[str, Any] = {}
        else:
            current_step_snapshot = {
                "step_id": current_step.id,
                "step_key": current_step.step_key,
                "title": current_step.title,
                "repeat_count": current_step.repeat_count,
                "status": current_step.status.value,
                "mastery_state": current_step.mastery_state,
            }

        recent_events = events[-8:]
        recent_blackboard = blackboard[-8:]

        return {
            "workspace_id": workspace.id,
            "session_id": workspace.session_id,
            "problem_key": workspace.problem_key,
            "problem_id": workspace.problem_id,
            "problem_title": workspace.title,
            "problem_summary": workspace.problem_summary[:240] if workspace.problem_summary else None,
            "guidance_depth": workspace.guidance_depth.value,
            "algorithm_family": workspace.algorithm_family,
            "current_step": current_step_snapshot,
            "event_window": [self._serialize_event_window_item(event) for event in recent_events],
            "blackboard_summary": [
                f"{entry.entry_type}: {entry.content}" for entry in recent_blackboard
            ],
            "recent_turn_count": len(recent_turns),
            "memory_policy": {
                "long_term_enabled": long_term_enabled,
                "retrieved_memory_count": retrieved_memory_count,
            },
            "workspace_summary": {
                "available": workspace_summary is not None,
                "preview": workspace_summary.content[:160] if workspace_summary is not None else "",
            },
            "compaction_policy": {
                "current_step_only": True,
                "event_window_limit": 8,
                "blackboard_summary_limit": 8,
                "raw_recent_turn_limit": self.raw_recent_turn_limit,
                "compacted_turn_count": max(0, total_visible_turn_count - len(recent_turns)),
            },
        }
