"""Ownership rules for private Agent sessions and the English demo example."""
from __future__ import annotations

from app.config import Settings
from app.models import AgentSession, AgentSessionStatus


def is_demo_showcase_session(session: AgentSession, settings: Settings) -> bool:
    return (
        settings.demo_mode
        and bool(settings.demo_showcase_session_uuid)
        and session.session_uuid == settings.demo_showcase_session_uuid
        and session.status != AgentSessionStatus.DELETED
    )


def can_read_agent_session(session: AgentSession, user_id: int, settings: Settings) -> bool:
    return session.status != AgentSessionStatus.DELETED and (
        session.user_id == user_id or is_demo_showcase_session(session, settings)
    )


def can_write_agent_session(session: AgentSession, user_id: int, settings: Settings) -> bool:
    return (
        session.status != AgentSessionStatus.DELETED
        and session.user_id == user_id
        and not is_demo_showcase_session(session, settings)
    )
