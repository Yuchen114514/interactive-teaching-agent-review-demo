from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


FORBIDDEN_INTENT_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
DEFAULT_INTENT_POLICY_PATH = Path(__file__).with_name("problem_intent_policy.json")


@dataclass(frozen=True)
class ProblemIntentPolicy:
    new_problem_markers: tuple[str, ...]
    explicit_workspace_followup_markers: tuple[str, ...]
    workspace_reference_markers: dict[str, tuple[str, ...]]
    problem_statement_markers: tuple[str, ...]
    problem_statement_min_chars: int
    problem_statement_min_marker_hits: int
    problem_statement_min_lines: int
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "ProblemIntentPolicy":
        forbidden = FORBIDDEN_INTENT_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"intent policy contains problem-specific runtime rules: {sorted(forbidden)}")
        return cls(
            new_problem_markers=tuple(str(marker) for marker in payload.get("new_problem_markers", [])),
            explicit_workspace_followup_markers=tuple(
                str(marker) for marker in payload.get("explicit_workspace_followup_markers", [])
            ),
            workspace_reference_markers=_parse_reference_markers(payload.get("workspace_reference_markers")),
            problem_statement_markers=tuple(str(marker) for marker in payload.get("problem_statement_markers", [])),
            problem_statement_min_chars=_positive_int(payload.get("problem_statement_min_chars"), default=80),
            problem_statement_min_marker_hits=_positive_int(
                payload.get("problem_statement_min_marker_hits"),
                default=2,
            ),
            problem_statement_min_lines=_positive_int(payload.get("problem_statement_min_lines"), default=3),
            source=source,
            source_detail=source_detail,
        )

    def detect_new_problem_intent(self, message: str) -> bool:
        normalized = message.strip().lower()
        return any(marker.lower() in normalized for marker in self.new_problem_markers)

    def detect_workspace_reference(self, message: str) -> str | None:
        normalized = message.strip().lower()
        for target, markers in self.workspace_reference_markers.items():
            if any(marker.lower() in normalized for marker in markers):
                return target
        return None

    def detect_explicit_workspace_followup(self, message: str) -> bool:
        normalized = message.strip().lower()
        return any(marker.lower() in normalized for marker in self.explicit_workspace_followup_markers)

    def detect_problem_statement_candidate(self, message: str) -> bool:
        normalized = message.strip().lower()
        if len(normalized) < self.problem_statement_min_chars:
            return False
        lines = [line for line in normalized.splitlines() if line.strip()]
        if len(lines) < self.problem_statement_min_lines:
            return False
        marker_hits = sum(1 for marker in self.problem_statement_markers if marker.lower() in normalized)
        return marker_hits >= self.problem_statement_min_marker_hits


def _parse_reference_markers(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, tuple[str, ...]] = {}
    for target, markers in value.items():
        if not isinstance(target, str) or target not in {"previous", "first", "latest"}:
            continue
        if not isinstance(markers, list):
            continue
        parsed[target] = tuple(str(marker) for marker in markers if str(marker).strip())
    return parsed


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_default_intent_policy() -> ProblemIntentPolicy:
    return load_intent_policy("")


def load_intent_policy(path: str | None = None) -> ProblemIntentPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_INTENT_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return ProblemIntentPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
