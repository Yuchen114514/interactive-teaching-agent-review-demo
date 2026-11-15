from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.teaching.agent_contracts import contains_forbidden_text, parse_string_list


FORBIDDEN_DIAGRAM_REQUEST_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "diagram_layout",
    "hard_coded_solution",
}

DEFAULT_DIAGRAM_REQUEST_POLICY_PATH = Path(__file__).with_name("diagram_request_policy.json")


@dataclass(frozen=True)
class DiagramRequestPolicy:
    request_markers: tuple[str, ...]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "DiagramRequestPolicy":
        forbidden = FORBIDDEN_DIAGRAM_REQUEST_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"diagram request policy contains problem-specific runtime rules: {sorted(forbidden)}")
        markers = parse_string_list(payload.get("request_markers"), min_items=1, max_items=32)
        if markers is None:
            raise ValueError("diagram request policy requires non-empty unique request markers")
        if any(contains_forbidden_text(marker) for marker in markers):
            raise ValueError("diagram request policy marker contains solution-like content")
        return cls(
            request_markers=tuple(markers),
            source=source,
            source_detail=source_detail,
        )

    def requests_diagram(self, message: str) -> bool:
        normalized = message.strip().lower()
        return any(marker.lower() in normalized for marker in self.request_markers)


def load_default_diagram_request_policy() -> DiagramRequestPolicy:
    return load_diagram_request_policy("")


def load_diagram_request_policy(path: str | None = None) -> DiagramRequestPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_DIAGRAM_REQUEST_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return DiagramRequestPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
