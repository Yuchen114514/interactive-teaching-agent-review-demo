from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.teaching.agent_contracts import contains_forbidden_text, parse_string_list


FORBIDDEN_HINT_SIGNAL_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "hard_coded_solution",
}

DEFAULT_HINT_SIGNAL_POLICY_PATH = Path(__file__).with_name("hint_signal_policy.json")


@dataclass(frozen=True)
class HintSignalPolicy:
    progress_markers: tuple[str, ...]
    code_request_markers: tuple[str, ...]
    debug_request_markers: tuple[str, ...]
    confirmation_markers: tuple[str, ...]
    blocker_markers: tuple[str, ...]
    progress_encouragement: str
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "HintSignalPolicy":
        forbidden = FORBIDDEN_HINT_SIGNAL_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"hint signal policy contains problem-specific runtime rules: {sorted(forbidden)}")
        progress_encouragement = payload.get("progress_encouragement")
        if not isinstance(progress_encouragement, str) or not progress_encouragement.strip():
            raise ValueError("hint signal policy requires progress encouragement text")
        if contains_forbidden_text(progress_encouragement):
            raise ValueError("hint signal policy encouragement contains solution-like content")
        return cls(
            progress_markers=_parse_markers(payload, "progress_markers"),
            code_request_markers=_parse_markers(payload, "code_request_markers"),
            debug_request_markers=_parse_markers(payload, "debug_request_markers"),
            confirmation_markers=_parse_markers(payload, "confirmation_markers"),
            blocker_markers=_parse_markers(payload, "blocker_markers"),
            progress_encouragement=progress_encouragement.strip(),
            source=source,
            source_detail=source_detail,
        )

    def shows_progress(self, message: str) -> bool:
        return _contains_any_marker(message, self.progress_markers)

    def requests_code(self, message: str) -> bool:
        return _contains_any_marker(message, self.code_request_markers)

    def requests_debug(self, message: str) -> bool:
        return _contains_any_marker(message, self.debug_request_markers)

    def confirms_current_step(self, message: str) -> bool:
        return _contains_any_marker(message, self.confirmation_markers) and not _contains_any_marker(
            message,
            self.blocker_markers,
        )


def _parse_markers(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    markers = parse_string_list(payload.get(key), min_items=1, max_items=64)
    if markers is None:
        raise ValueError(f"hint signal policy requires non-empty unique markers for {key}")
    if any(contains_forbidden_text(marker) for marker in markers):
        raise ValueError(f"hint signal policy marker list contains solution-like content: {key}")
    return tuple(markers)


def _contains_any_marker(message: str, markers: tuple[str, ...]) -> bool:
    normalized = message.strip().lower()
    return any(marker.lower() in normalized for marker in markers)


def load_default_hint_signal_policy() -> HintSignalPolicy:
    return load_hint_signal_policy("")


def load_hint_signal_policy(path: str | None = None) -> HintSignalPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_HINT_SIGNAL_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return HintSignalPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
