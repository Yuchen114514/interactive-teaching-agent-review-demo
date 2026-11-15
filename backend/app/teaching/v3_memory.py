from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import MemoryScope
from app.repositories.teaching import TeachingRepository


class MemoryAgent:
    def __init__(
        self,
        *,
        repo: TeachingRepository,
        provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        min_compacted_summary_chars: int = 480,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.min_compacted_summary_chars = min_compacted_summary_chars

    def run(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if self.provider is None:
            return [{"type": "memory_status", "status": "skipped", "written": 0}]
        compacted_summary_text = _compacted_problem_summary_text(context)
        if len(compacted_summary_text) < self.min_compacted_summary_chars:
            return [{"type": "memory_status", "status": "skipped", "reason": "below_summary_threshold", "written": 0}]

        raw_output = self.provider(
            {
                "capability": "memory_candidates",
                "context": context,
                "compacted_summary_text": compacted_summary_text,
                "message": compacted_summary_text,
                "prompt_contract": {
                    "goal": "只提炼跨题稳定的用户画像、偏好、薄弱点；不要记录单题步骤或临时结论。",
                    "candidate_schema": {
                        "memory_type": "preference | weakness | learning_style | recurring_mistake",
                        "content": "一句可长期复用的中文事实",
                        "scope": "user_profile",
                        "confidence": "0..1",
                        "importance": "0..1",
                    },
                },
            }
        )
        candidates = raw_output.get("candidates") if isinstance(raw_output, dict) else None
        if not isinstance(candidates, list):
            return [{"type": "memory_status", "status": "completed", "written": 0}]

        user_id = context.get("user_id")
        if not isinstance(user_id, int):
            raise ValueError("v3 memory context requires integer user_id")

        written = 0
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = str(candidate.get("content") or "").strip()
            memory_type = str(candidate.get("memory_type") or "").strip()
            if not content or not memory_type:
                continue
            scope = _parse_scope(candidate.get("scope"))
            if scope != MemoryScope.USER_PROFILE:
                continue
            self.repo.upsert_memory_item(
                user_id=user_id,
                memory_type=memory_type,
                content=content[:240],
                scope=scope,
                confidence=_optional_float(candidate.get("confidence")),
                importance=_optional_float(candidate.get("importance")),
            )
            written += 1
        return [{"type": "memory_status", "status": "completed", "written": written}]


def _parse_scope(value: Any) -> MemoryScope:
    try:
        return MemoryScope(str(value or MemoryScope.USER_PROFILE.value))
    except ValueError:
        return MemoryScope.USER_PROFILE


def _optional_float(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) else None


def _compacted_problem_summary_text(context: dict[str, Any]) -> str:
    summary = context.get("problem_thread_summary")
    if not isinstance(summary, dict):
        return ""
    layers = summary.get("layers") if isinstance(summary.get("layers"), dict) else {}
    parts = [
        layers.get("recent_summary"),
        layers.get("middle_summary"),
        layers.get("long_term_outline"),
        summary.get("summary"),
        "；".join(str(item) for item in summary.get("open_confusions", []) if str(item or "").strip())
        if isinstance(summary.get("open_confusions"), list)
        else "",
        "；".join(str(item) for item in summary.get("resolved_points", []) if str(item or "").strip())
        if isinstance(summary.get("resolved_points"), list)
        else "",
    ]
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())
