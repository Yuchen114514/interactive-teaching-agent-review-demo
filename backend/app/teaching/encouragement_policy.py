from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from string import Formatter
from typing import Any

from app.teaching.agent_contracts import contains_forbidden_text


FORBIDDEN_ENCOURAGEMENT_POLICY_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "hard_coded_solution",
}

ALLOWED_ENCOURAGEMENT_TEMPLATE_FIELDS = {"current_step_title"}
DEFAULT_ENCOURAGEMENT_POLICY_PATH = Path(__file__).with_name("encouragement_fallback_policy.json")


@dataclass(frozen=True)
class EncouragementFallbackSelection:
    feedback_type: str
    visible_prefix: str
    rationale: str


@dataclass(frozen=True)
class EncouragementFallbackPolicy:
    normalize_stuck_prefix: str
    normalize_stuck_rationale: str
    positive_prefix_template: str
    positive_rationale: str
    neutral_rationale: str
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "EncouragementFallbackPolicy":
        forbidden = FORBIDDEN_ENCOURAGEMENT_POLICY_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"encouragement policy contains problem-specific runtime rules: {sorted(forbidden)}")
        return cls(
            normalize_stuck_prefix=_validate_text(payload["normalize_stuck_prefix"]),
            normalize_stuck_rationale=_validate_text(payload["normalize_stuck_rationale"]),
            positive_prefix_template=_validate_template(payload["positive_prefix_template"]),
            positive_rationale=_validate_text(payload["positive_rationale"]),
            neutral_rationale=_validate_text(payload["neutral_rationale"]),
            source=source,
            source_detail=source_detail,
        )

    def select(
        self,
        *,
        current_step_title: str,
        student_state: str,
        repeat_risk: str,
    ) -> EncouragementFallbackSelection:
        if repeat_risk == "high":
            return EncouragementFallbackSelection(
                feedback_type="normalize_stuck",
                visible_prefix=self.normalize_stuck_prefix,
                rationale=self.normalize_stuck_rationale,
            )
        if student_state == "progressing":
            return EncouragementFallbackSelection(
                feedback_type="positive_specific",
                visible_prefix=self.positive_prefix_template.format(current_step_title=current_step_title),
                rationale=self.positive_rationale,
            )
        return EncouragementFallbackSelection(
            feedback_type="neutral",
            visible_prefix="",
            rationale=self.neutral_rationale,
        )


def _validate_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("encouragement policy text must be a string")
    if contains_forbidden_text(value):
        raise ValueError("encouragement policy text contains solution-like content")
    return value.strip()


def _validate_template(value: Any) -> str:
    text = _validate_text(value)
    unknown_fields = {
        field_name
        for _, field_name, _, _ in Formatter().parse(text)
        if field_name and field_name not in ALLOWED_ENCOURAGEMENT_TEMPLATE_FIELDS
    }
    if unknown_fields:
        raise ValueError(f"encouragement policy template contains unknown template fields: {sorted(unknown_fields)}")
    return text


def load_default_encouragement_policy() -> EncouragementFallbackPolicy:
    return load_encouragement_policy("")


def load_encouragement_policy(path: str | None = None) -> EncouragementFallbackPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_ENCOURAGEMENT_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return EncouragementFallbackPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
