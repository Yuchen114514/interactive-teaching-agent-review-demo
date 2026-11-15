from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.repositories.teaching import TeachingRepository

DEFAULT_COMPACTION_USER_TURN_THRESHOLD = 10
# Backward-compatible export for older tests/imports. The trigger is now user turns.
DEFAULT_COMPACTION_VISIBLE_TURN_THRESHOLD = DEFAULT_COMPACTION_USER_TURN_THRESHOLD
DEFAULT_COMPACTION_RETAIN_RECENT = 8


class ProblemCompactionAgent:
    def __init__(
        self,
        *,
        repo: TeachingRepository,
        provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        user_turn_threshold: int = DEFAULT_COMPACTION_USER_TURN_THRESHOLD,
        retain_recent: int = DEFAULT_COMPACTION_RETAIN_RECENT,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.user_turn_threshold = user_turn_threshold
        self.retain_recent = retain_recent

    def run(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        workspace_id = _required_int(context, "workspace_id")
        session_id = _required_int(context, "session_id")
        user_turn_count = self.repo.count_visible_user_messages_for_workspace(workspace_id)
        if user_turn_count < self.user_turn_threshold:
            return [{"type": "compaction_status", "status": "skipped", "reason": "below_threshold"}]

        compactable = self.repo.list_compactable_visible_messages(
            workspace_id,
            retain_recent=self.retain_recent,
        )
        if not compactable:
            return [{"type": "compaction_status", "status": "skipped", "reason": "nothing_to_compact"}]

        summary_text = self._summarize(context, compactable)
        previous_summary = context.get("problem_thread_summary")
        previous_version = (
            int(previous_summary.get("summary_version") or 0)
            if isinstance(previous_summary, dict)
            else 0
        )
        payload = {
            "summary_version": previous_version + 1,
            "summary": summary_text,
            "layers": self._layered_summary(context, compactable, summary_text),
            "student_state_summary": summary_text,
            "open_confusions": _string_list_from_provider(raw_layer_value(context, "open_confusions")),
            "resolved_points": _string_list_from_provider(raw_layer_value(context, "resolved_points")),
            "important_turn_refs": [item.get("turn_index") for item in compactable[-4:]],
            "compacted_visible_turn_count": len(compactable),
        }
        self.repo.record_blackboard_entry(
            workspace_id=workspace_id,
            session_id=session_id,
            entry_type="problem_thread_summary",
            content=summary_text,
            writer_agent="ProblemCompactionAgent",
            payload=payload,
        )
        return [
            {
                "type": "compaction_status",
                "status": "completed",
                "summary_version": payload["summary_version"],
                "compacted_visible_turn_count": len(compactable),
            }
        ]

    def _summarize(self, context: dict[str, Any], compactable: list[dict[str, Any]]) -> str:
        if self.provider is not None:
            raw_output = self.provider(
                {
                    "capability": "workspace_summary",
                    "context": context,
                    "compactable_messages": compactable,
                    "turns_to_compact": compactable,
                    "prior_summary": _prior_summary_text(context),
                }
            )
            if isinstance(raw_output, dict):
                context["_compaction_provider_output"] = raw_output
                if isinstance(raw_output.get("workspace_summary"), str):
                    return raw_output["workspace_summary"][:600]
                if isinstance(raw_output.get("summary"), str):
                    return raw_output["summary"][:600]
                layered = _normalize_layered_summary(raw_output, fallback="")
                joined = "；".join(value for value in layered.values() if value)
                if joined:
                    return joined[:600]
        joined = "；".join(
            f"{item.get('role')}:{str(item.get('content') or '')[:80]}"
            for item in compactable[-6:]
        )
        return joined[:600] or "已压缩较早题内对话。"

    def _layered_summary(
        self,
        context: dict[str, Any],
        compactable: list[dict[str, Any]],
        summary_text: str,
    ) -> dict[str, str]:
        provider_output = context.get("_compaction_provider_output")
        if isinstance(provider_output, dict):
            return _normalize_layered_summary(provider_output, fallback=summary_text)

        recent_messages = compactable[-4:]
        middle_messages = compactable[-12:-4] if len(compactable) > 4 else []
        return {
            "recent_summary": _messages_summary(recent_messages, fallback=summary_text),
            "middle_summary": _messages_summary(middle_messages, fallback=""),
            "long_term_outline": summary_text[:240],
        }


def _required_int(context: dict[str, Any], key: str) -> int:
    value = context.get(key)
    if not isinstance(value, int):
        raise ValueError(f"v3 compaction context requires integer {key}")
    return value


def _normalize_layered_summary(raw_output: dict[str, Any], *, fallback: str) -> dict[str, str]:
    layers = raw_output.get("layers") if isinstance(raw_output.get("layers"), dict) else raw_output
    return {
        "recent_summary": _bounded_text(layers.get("recent_summary"), fallback=fallback, limit=360),
        "middle_summary": _bounded_text(layers.get("middle_summary"), fallback="", limit=360),
        "long_term_outline": _bounded_text(layers.get("long_term_outline"), fallback=fallback, limit=360),
    }


def _messages_summary(messages: list[dict[str, Any]], *, fallback: str) -> str:
    joined = "；".join(
        f"{item.get('role')}:{str(item.get('content') or '')[:72]}"
        for item in messages
    ).strip("；")
    return (joined or fallback)[:360]


def _bounded_text(value: Any, *, fallback: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    return text[:limit]


def _string_list_from_provider(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:160] for item in value if str(item or "").strip()][:8]


def raw_layer_value(context: dict[str, Any], key: str) -> Any:
    provider_output = context.get("_compaction_provider_output")
    if isinstance(provider_output, dict):
        return provider_output.get(key)
    return None


def _prior_summary_text(context: dict[str, Any]) -> str | None:
    previous_summary = context.get("problem_thread_summary")
    if not isinstance(previous_summary, dict):
        return None
    summary = str(previous_summary.get("summary") or "").strip()
    return summary or None
