from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.models import AgentTeachingStep
from app.teaching.agent_contracts import contains_forbidden_text, parse_string_list


FORBIDDEN_SPECIALIST_HANDOFF_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "patch_code",
    "hard_coded_solution",
}

DEFAULT_SPECIALIST_HANDOFF_POLICY_PATH = Path(__file__).with_name("specialist_handoff_policy.json")


@dataclass(frozen=True)
class SpecialistHandoffPolicy:
    boardwork_requirements: tuple[str, ...]
    coding_checkpoints: tuple[str, ...]
    debug_checkpoints: tuple[str, ...]
    diagram_requirements: tuple[str, ...]
    boardwork_agent_required_requirements: frozenset[str]
    diagram_agent_required_requirements: frozenset[str]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "SpecialistHandoffPolicy":
        forbidden = FORBIDDEN_SPECIALIST_HANDOFF_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"specialist handoff policy contains problem-specific runtime rules: {sorted(forbidden)}")
        agent_required = payload.get("agent_required_requirements") or {}
        if not isinstance(agent_required, dict):
            raise ValueError("specialist handoff policy requires agent requirement map")
        return cls(
            boardwork_requirements=tuple(_parse_policy_list(payload, "boardwork_requirements", min_items=3)),
            coding_checkpoints=tuple(_parse_policy_list(payload, "coding_checkpoints", min_items=3)),
            debug_checkpoints=tuple(_parse_policy_list(payload, "debug_checkpoints", min_items=3)),
            diagram_requirements=tuple(_parse_policy_list(payload, "diagram_requirements", min_items=3)),
            boardwork_agent_required_requirements=frozenset(
                _parse_policy_list(agent_required, "boardwork", min_items=1)
            ),
            diagram_agent_required_requirements=frozenset(
                _parse_policy_list(agent_required, "diagram", min_items=1)
            ),
            source=source,
            source_detail=source_detail,
        )

    def build_boardwork_handoff(self, current_step: AgentTeachingStep, events: list[Any]) -> dict[str, Any]:
        return {
            "handoff_type": "boardwork_refresh",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "focus": current_step.title,
            "boardwork_goal": current_step.teaching_goal or current_step.title,
            "requirements": list(self.boardwork_requirements),
            "source_event_ids": [event.id for event in events if event.id is not None],
            "source_event_types": [event.event_type for event in events],
        }

    def build_coding_handoff(self, current_step: AgentTeachingStep) -> dict[str, Any]:
        return {
            "handoff_type": "implementation_plan",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "checkpoints": list(self.coding_checkpoints),
        }

    def build_debug_handoff(self, current_step: AgentTeachingStep) -> dict[str, Any]:
        return {
            "handoff_type": "debug_plan",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "checkpoints": list(self.debug_checkpoints),
        }

    def build_diagram_handoff(self, current_step: AgentTeachingStep, events: list[Any]) -> dict[str, Any]:
        return {
            "handoff_type": "diagram_request",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "diagram_goal": current_step.teaching_goal or current_step.title,
            "visual_focus": current_step.title,
            "requirements": list(self.diagram_requirements),
            "source_event_ids": [event.id for event in events if event.id is not None],
            "source_event_types": [event.event_type for event in events],
        }


def _parse_policy_list(payload: dict[str, Any], key: str, *, min_items: int) -> list[str]:
    values = parse_string_list(payload.get(key), min_items=min_items, max_items=12)
    if values is None:
        raise ValueError(f"specialist handoff policy requires {key} list")
    if any(contains_forbidden_text(value) for value in values):
        raise ValueError(f"specialist handoff policy {key} contains solution-like content")
    return values


def load_default_specialist_handoff_policy() -> SpecialistHandoffPolicy:
    return load_specialist_handoff_policy("")


def load_specialist_handoff_policy(path: str | None = None) -> SpecialistHandoffPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_SPECIALIST_HANDOFF_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return SpecialistHandoffPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
