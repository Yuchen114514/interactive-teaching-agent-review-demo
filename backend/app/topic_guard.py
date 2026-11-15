from __future__ import annotations


def is_message_in_scope(message: str, socratic: bool, visible_history_context: str | None = None) -> bool:
    del message, socratic, visible_history_context
    return True


def build_scope_guidance(socratic: bool) -> str:
    del socratic
    return ""
