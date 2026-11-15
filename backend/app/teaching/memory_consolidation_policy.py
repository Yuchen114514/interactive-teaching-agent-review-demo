from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.teaching.agent_contracts import parse_string_list


FORBIDDEN_MEMORY_CONSOLIDATION_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "hard_coded_solution",
}

DEFAULT_MEMORY_CONSOLIDATION_POLICY_PATH = Path(__file__).with_name("memory_consolidation_policy.json")


@dataclass(frozen=True)
class MemoryConsolidationPolicy:
    preference_markers: tuple[str, ...]
    weakness_markers: tuple[str, ...]
    solution_like_markers: tuple[str, ...]
    preference_confidence: float
    preference_importance: float
    weakness_confidence: float
    weakness_importance: float
    max_content_length: int
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "MemoryConsolidationPolicy":
        forbidden = FORBIDDEN_MEMORY_CONSOLIDATION_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"memory consolidation policy contains problem-specific runtime rules: {sorted(forbidden)}")
        max_content_length = payload.get("max_content_length")
        if not isinstance(max_content_length, int) or isinstance(max_content_length, bool) or max_content_length <= 0:
            raise ValueError("memory consolidation policy requires positive max content length")
        return cls(
            preference_markers=tuple(_parse_markers(payload, "preference_markers")),
            weakness_markers=tuple(_parse_markers(payload, "weakness_markers")),
            solution_like_markers=tuple(_parse_markers(payload, "solution_like_markers")),
            preference_confidence=_parse_score(payload, "preference_confidence"),
            preference_importance=_parse_score(payload, "preference_importance"),
            weakness_confidence=_parse_score(payload, "weakness_confidence"),
            weakness_importance=_parse_score(payload, "weakness_importance"),
            max_content_length=max_content_length,
            source=source,
            source_detail=source_detail,
        )

    def has_preference_signal(self, normalized: str) -> bool:
        return _contains_any_marker(normalized, self.preference_markers)

    def has_weakness_signal(self, normalized: str) -> bool:
        return _contains_any_marker(normalized, self.weakness_markers)

    def is_solution_like(self, normalized: str) -> bool:
        return _contains_any_marker(normalized, self.solution_like_markers)

    def trim_content(self, content: str) -> str:
        return content[: self.max_content_length]


def _parse_markers(payload: dict[str, Any], key: str) -> list[str]:
    markers = parse_string_list(payload.get(key), min_items=1, max_items=64)
    if markers is None:
        raise ValueError(f"memory consolidation policy requires non-empty unique markers for {key}")
    return markers


def _parse_score(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"memory consolidation policy requires score from 0.0 to 1.0 for {key}")
    return float(value)


def _contains_any_marker(normalized: str, markers: tuple[str, ...]) -> bool:
    return any(marker.lower() in normalized for marker in markers)


def load_default_memory_consolidation_policy() -> MemoryConsolidationPolicy:
    return load_memory_consolidation_policy("")


def load_memory_consolidation_policy(path: str | None = None) -> MemoryConsolidationPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_MEMORY_CONSOLIDATION_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return MemoryConsolidationPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
