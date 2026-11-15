from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


VISIBLE_HISTORY_KEY = "visible_history"
SESSION_SUMMARY_KEY = "session_summary"
MEMORY_FACTS_KEY = "memory_facts"
DEFAULT_VISIBLE_HISTORY_LIMIT = 24


def get_visible_history(session_data: Dict[str, Any]) -> List[Dict[str, str]]:
    raw_history = session_data.get(VISIBLE_HISTORY_KEY, [])
    if not isinstance(raw_history, list):
        return []

    visible_history: List[Dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        visible_history.append(item)
    return visible_history


def append_visible_message(
    session_data: Dict[str, Any],
    role: str,
    content: str,
    mode: str,
    created_at: str,
    max_entries: int = DEFAULT_VISIBLE_HISTORY_LIMIT,
) -> Dict[str, Any]:
    updated = deepcopy(session_data)
    history = get_visible_history(updated)
    history.append(
        {
            "role": role,
            "content": content.strip(),
            "mode": mode,
            "created_at": created_at,
        }
    )
    updated[VISIBLE_HISTORY_KEY] = history[-max_entries:]
    return updated


def get_session_summary(session_data: Dict[str, Any]) -> str | None:
    summary = session_data.get(SESSION_SUMMARY_KEY)
    if not isinstance(summary, str) or not summary.strip():
        return None
    return summary.strip()


def set_session_summary(session_data: Dict[str, Any], summary: str | None) -> Dict[str, Any]:
    updated = deepcopy(session_data)
    if summary and summary.strip():
        updated[SESSION_SUMMARY_KEY] = summary.strip()
    else:
        updated.pop(SESSION_SUMMARY_KEY, None)
    return updated


def get_memory_facts(session_data: Dict[str, Any]) -> List[str]:
    raw_facts = session_data.get(MEMORY_FACTS_KEY, [])
    if not isinstance(raw_facts, list):
        return []
    facts: List[str] = []
    for item in raw_facts:
        if isinstance(item, str) and item.strip():
            facts.append(item.strip())
    return facts


def set_memory_facts(session_data: Dict[str, Any], memory_facts: List[str]) -> Dict[str, Any]:
    updated = deepcopy(session_data)
    cleaned_facts = [item.strip() for item in memory_facts if isinstance(item, str) and item.strip()]
    if cleaned_facts:
        updated[MEMORY_FACTS_KEY] = cleaned_facts
    else:
        updated.pop(MEMORY_FACTS_KEY, None)
    return updated


def prune_context_layers_for_visible_history(
    session_data: Dict[str, Any],
    visible_history_count: int,
) -> Dict[str, Any]:
    updated = deepcopy(session_data)
    visible_history = get_visible_history(updated)
    updated[VISIBLE_HISTORY_KEY] = visible_history[: max(0, visible_history_count)]
    return updated


def render_visible_history_context(visible_history: List[Dict[str, str]], limit: int = 6) -> str:
    recent_history = visible_history[-limit:]
    if not recent_history:
        return ""

    # 中文注释：普通模式只继承用户可见的对话，不继承苏格拉底运行时约束。
    lines = ["以下是当前会话中用户可见的最近对话，请仅把它们当作连续对话背景："]
    for item in recent_history:
        lines.append(f"{item['role']}: {item['content']}")
    lines.append("你现在处于普通对话模式，请按普通问答方式直接回答当前用户消息。")
    return "\n".join(lines)
