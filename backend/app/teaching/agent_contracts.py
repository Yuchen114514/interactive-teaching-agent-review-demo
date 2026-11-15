from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from app.models import AgentTeachingStep


DEFAULT_FORBIDDEN_TEXT_POLICY_PATH = Path(__file__).with_name("forbidden_text_policy.json")


@dataclass(frozen=True)
class ForbiddenTextPolicy:
    markers: tuple[str, ...]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "ForbiddenTextPolicy":
        markers = parse_string_list(payload.get("markers"), min_items=1, max_items=128)
        if markers is None:
            raise ValueError("forbidden text policy requires non-empty unique markers")
        return cls(
            markers=tuple(markers),
            source=source,
            source_detail=source_detail,
        )

    def contains(self, value: str) -> bool:
        normalized = value.lower()
        for marker in self.markers:
            normalized_marker = marker.lower()
            if _is_code_keyword_marker(normalized_marker):
                pattern = _code_keyword_pattern(normalized_marker)
                if pattern and re.search(pattern, normalized):
                    return True
                continue
            if normalized_marker in normalized:
                return True
        return False


def _is_code_keyword_marker(marker: str) -> bool:
    return marker in {"for", "while", "def", "class"}


def _code_keyword_pattern(marker: str) -> str:
    if marker == "for":
        return r"(?<![a-z_])for\s+[a-z_][a-z0-9_]*\s+in\s+"
    if marker == "while":
        return r"(?<![a-z_])while\s+[^:\n]+:"
    if marker == "def":
        return r"(?<![a-z_])def\s+[a-z_][a-z0-9_]*\s*\("
    if marker == "class":
        return r"(?<![a-z_])class\s+[a-z_][a-z0-9_]*"
    return ""


def build_step_payload(current_step: AgentTeachingStep) -> dict[str, Any]:
    return {
        "step_id": current_step.id,
        "step_key": current_step.step_key,
        "title": current_step.title,
        "teaching_goal": current_step.teaching_goal,
        "expected_student_action": current_step.expected_student_action,
        "mastery_criteria": current_step.mastery_criteria,
        "repeat_count": current_step.repeat_count,
    }


def build_source_event_payloads(events: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event.id,
            "event_type": event.event_type,
            "actor": event.actor,
            "payload": event.payload,
        }
        for event in events
    ]


def parse_string_list(value: Any, *, min_items: int, max_items: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    parsed = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(parsed) != len(value) or not min_items <= len(parsed) <= max_items:
        return None
    if len(set(parsed)) != len(parsed):
        return None
    return parsed


def contains_forbidden_text(value: str, markers: tuple[str, ...] | None = None) -> bool:
    if markers is not None:
        return ForbiddenTextPolicy(tuple(markers)).contains(value)
    return load_default_forbidden_text_policy().contains(value)


def load_default_forbidden_text_policy() -> ForbiddenTextPolicy:
    return load_forbidden_text_policy("")


def load_forbidden_text_policy(path: str | None = None) -> ForbiddenTextPolicy:
    return _load_forbidden_text_policy(path or "")


@lru_cache(maxsize=16)
def _load_forbidden_text_policy(path: str) -> ForbiddenTextPolicy:
    if path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_FORBIDDEN_TEXT_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return ForbiddenTextPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
