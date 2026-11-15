from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from string import Formatter
from typing import Any

from app.teaching.agent_contracts import contains_forbidden_text


FORBIDDEN_TUTOR_POLICY_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "hard_coded_solution",
}

ALLOWED_TUTOR_TEMPLATE_FIELDS = {
    "action",
    "current_step_title",
    "depth",
    "prefix",
    "primer",
}

DEFAULT_TUTOR_POLICY_PATH = Path(__file__).with_name("tutor_fallback_policy.json")


@dataclass(frozen=True)
class TutorFallbackPolicy:
    minimal_template: str
    action_templates: dict[str, str]
    depth_templates: dict[str, str]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "TutorFallbackPolicy":
        forbidden = FORBIDDEN_TUTOR_POLICY_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"tutor fallback policy contains problem-specific runtime rules: {sorted(forbidden)}")

        minimal_template = _validate_template_text(payload["minimal_template"])
        action_templates = _validate_template_map(payload.get("action_templates") or {})
        depth_templates = _validate_template_map(payload.get("depth_templates") or {})
        if "default" not in depth_templates:
            raise ValueError("tutor fallback policy requires a default depth template")
        return cls(
            minimal_template=minimal_template,
            action_templates=action_templates,
            depth_templates=depth_templates,
            source=source,
            source_detail=source_detail,
        )

    def render(
        self,
        *,
        depth: str,
        action: str,
        current_step_title: str,
        primer: str,
        encouragement: str | None = None,
    ) -> str:
        template = self._select_template(depth=depth, action=action)
        prefix = f"{encouragement}\n" if encouragement else ""
        return template.format(
            action=action,
            current_step_title=current_step_title,
            depth=depth,
            prefix=prefix,
            primer=primer,
        )

    def _select_template(self, *, depth: str, action: str) -> str:
        if depth == "minimal":
            return self.minimal_template
        return self.action_templates.get(action) or self.depth_templates.get(depth) or self.depth_templates["default"]


def _validate_template_map(payload: dict[str, Any]) -> dict[str, str]:
    return {str(key): _validate_template_text(value) for key, value in payload.items()}


def _validate_template_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("tutor fallback template must be a string")
    if contains_forbidden_text(value):
        raise ValueError("tutor fallback template contains solution-like content")
    unknown_fields = {
        field_name
        for _, field_name, _, _ in Formatter().parse(value)
        if field_name and field_name not in ALLOWED_TUTOR_TEMPLATE_FIELDS
    }
    if unknown_fields:
        raise ValueError(f"tutor fallback template contains unknown template fields: {sorted(unknown_fields)}")
    return value


def load_default_tutor_policy() -> TutorFallbackPolicy:
    return load_tutor_policy("")


def load_tutor_policy(path: str | None = None) -> TutorFallbackPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_TUTOR_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return TutorFallbackPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
