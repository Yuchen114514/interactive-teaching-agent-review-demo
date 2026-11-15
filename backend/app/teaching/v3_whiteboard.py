from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter, sleep
from typing import Any

from sqlmodel import select

from app.agent_store import AgentStore
from app.models import AgentBoardNode
from app.repositories.teaching import TeachingRepository
from app.teaching.agent_nodes import BoardworkNode

DEFAULT_CURRENT_STEP_WAIT_SECONDS = 2.0
CURRENT_STEP_POLL_INTERVAL_SECONDS = 0.05
SOURCE_QUESTION_SUMMARY_LIMIT = 120


class WhiteboardAgent:
    """v3 compatibility sidecar that restores the v2 BoardworkAgent path."""

    def __init__(
        self,
        *,
        repo: TeachingRepository,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        current_step_wait_seconds: float = DEFAULT_CURRENT_STEP_WAIT_SECONDS,
    ) -> None:
        self.repo = repo
        self.handoff_provider = handoff_provider
        self.current_step_wait_seconds = max(0.0, current_step_wait_seconds)

    def run(
        self,
        context: dict[str, Any],
        *,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> list[dict[str, Any]]:
        workspace_id = _required_int(context, "workspace_id")
        session_id = _required_int(context, "session_id")
        source_turn_index = _optional_int(context.get("source_turn_index")) or 0
        source_message_id = _optional_int(context.get("source_message_id"))
        if not _deposit_hint_allows_whiteboard(context):
            return [{"type": "whiteboard_status", "status": "skipped", "reason": "deposit_not_allowed"}]
        current_step = self._wait_for_current_step(workspace_id)
        if current_step is None:
            return [{"type": "whiteboard_status", "status": "skipped", "reason": "current_step_missing"}]

        provider = handoff_provider or self.handoff_provider
        recent_events = self.repo.list_events(workspace_id)[-8:]
        request_context = _request_context(context)
        visible_entries = [
            entry for entry in self.repo.list_blackboard_entries(workspace_id)
            if entry.entry_type == "whiteboard_entry" and entry.visibility == "user"
        ]
        request_context["previous_whiteboard"] = _previous_whiteboard_context(visible_entries)
        boardwork_node = BoardworkNode(handoff_provider=provider)
        result, payload = boardwork_node.run(
            current_step,
            source_events=recent_events,
            collaboration_context=_collaboration_context(context),
            request_context=request_context,
        )
        payload_request_context = payload.get("request_context") if isinstance(payload.get("request_context"), dict) else {}
        payload["request_context"] = {**request_context, **payload_request_context}
        payload["student_feedback"] = _visible_reply(context)
        takeaway = _incremental_takeaway(payload)
        prior_takeaways = [item for entry in visible_entries for item in _entry_takeaways(entry)]
        if (visible_entries and payload.get("continuity_action") == "repeat") or (
            takeaway and _normalize_takeaway(takeaway) in {_normalize_takeaway(item) for item in prior_takeaways}
        ):
            return [{"type": "whiteboard_status", "status": "skipped", "reason": "duplicate_takeaway"}]
        if not takeaway:
            return [{"type": "whiteboard_status", "status": "skipped", "reason": "empty_takeaway"}]
        source_event = self.repo.record_event(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=source_turn_index,
            event_type="boardwork_refresh_requested",
            actor=result.agent_name,
            step_id=int(current_step.id) if current_step.id is not None else None,
            payload=payload,
            visible_to_user=False,
        )

        boardwork_entry = self.repo.record_blackboard_entry(
            workspace_id=workspace_id,
            session_id=session_id,
            entry_type="boardwork_request",
            content="refresh_current_step",
            writer_agent=result.agent_name,
            payload=payload,
            step_id=int(current_step.id) if current_step.id is not None else None,
            source_event_id=int(source_event.id) if source_event.id is not None else None,
            source_message_id=source_message_id,
        )
        board_node = _materialize_boardwork_artifact(
            repo=self.repo,
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=source_turn_index,
            current_step=current_step,
            boardwork_payload=payload,
        )
        compat_entry = _record_compat_whiteboard_entry(
            repo=self.repo,
            workspace_id=workspace_id,
            session_id=session_id,
            source_message_id=source_message_id,
            source_turn_index=source_turn_index,
            current_step=current_step,
            boardwork_payload=payload,
            previous_entries=visible_entries,
        )
        compat_payload = compat_entry.payload if isinstance(compat_entry.payload, dict) else {}
        event_entry = {
            "entry_type": "boardwork_request",
            "entry_id": boardwork_entry.id,
            "title": compat_payload.get("title") or (board_node.title if board_node is not None else current_step.title),
            "summary": compat_payload.get("summary") or "",
            "payload": payload,
            "source_message_id": source_message_id,
            "source_turn_index": source_turn_index,
            "compat_whiteboard_entry_id": compat_entry.id if compat_entry.id is not None else None,
            "board_node_id": board_node.id if board_node is not None else None,
        }
        return [
            {
                "type": "whiteboard_ready",
                "agent": "BoardworkAgent",
                "entry": event_entry,
            }
        ]

    def _wait_for_current_step(self, workspace_id: int):
        deadline = perf_counter() + self.current_step_wait_seconds
        while True:
            current_step = self.repo.get_current_step(workspace_id)
            if current_step is not None:
                return current_step
            if perf_counter() >= deadline:
                return None
            # MySQL's default repeatable-read snapshot can hide the plan sidecar's
            # later commit unless the whiteboard poll releases its read transaction.
            _release_stale_read_snapshot(self.repo)
            sleep(min(CURRENT_STEP_POLL_INTERVAL_SECONDS, max(0.0, deadline - perf_counter())))


def _release_stale_read_snapshot(repo: TeachingRepository) -> None:
    db = repo.db
    get_bind = getattr(db, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    dialect_name = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    if dialect_name == "sqlite":
        return
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()


def _materialize_boardwork_artifact(
    *,
    repo: TeachingRepository,
    workspace_id: int,
    session_id: int,
    turn_index: int,
    current_step,
    boardwork_payload: dict[str, Any],
) -> AgentBoardNode | None:
    if current_step.id is None:
        return None
    agent_store = AgentStore(repo.db)
    request_context = boardwork_payload.get("request_context")
    english = isinstance(request_context, dict) and request_context.get("response_language") == "en"
    boardwork = agent_store.get_or_create_active_boardwork(
        session_id=session_id,
        title="Problem whiteboard" if english else "当前题目白板",
        source_turn_index=turn_index,
        workspace_id=workspace_id,
    )
    existing_node = repo.db.exec(
        select(AgentBoardNode).where(
            AgentBoardNode.session_id == session_id,
            AgentBoardNode.boardwork_id == boardwork.id,
            AgentBoardNode.teaching_step_id == current_step.id,
        )
    ).first()
    trace_payload = {
        "workspace_id": workspace_id,
        "teaching_step_id": current_step.id,
        "handoff_type": boardwork_payload.get("handoff_type"),
        "boardwork_source": boardwork_payload.get("boardwork_source"),
        "continuity_action": boardwork_payload.get("continuity_action"),
        "incremental_takeaway": boardwork_payload.get("incremental_takeaway"),
        "focus": boardwork_payload.get("focus"),
        "boardwork_goal": boardwork_payload.get("boardwork_goal"),
        "source_event_ids": boardwork_payload.get("source_event_ids", []),
        "source_event_types": boardwork_payload.get("source_event_types", []),
        "student_feedback": boardwork_payload.get("student_feedback"),
    }
    summary = _boardwork_display_summary(
        current_step=current_step,
        boardwork_payload=boardwork_payload,
    )
    coach_note = (
        boardwork_payload.get("focus")
        or boardwork_payload.get("student_feedback")
        or boardwork_payload.get("handoff_type")
    )
    if existing_node is not None:
        return agent_store.update_board_node(
            existing_node,
            summary=summary,
            trace_payload=trace_payload,
            coach_note=str(coach_note) if coach_note else None,
            is_active=True,
        )
    return agent_store.create_board_node(
        session_id=session_id,
        title=current_step.title,
        summary=summary,
        source_turn_index=turn_index,
        trace_payload=trace_payload,
        coach_note=str(coach_note) if coach_note else None,
        teaching_step_id=current_step.id,
    )


def _record_compat_whiteboard_entry(
    *,
    repo: TeachingRepository,
    workspace_id: int,
    session_id: int,
    source_message_id: int | None,
    source_turn_index: int,
    current_step,
    boardwork_payload: dict[str, Any],
    previous_entries: list[Any],
):
    takeaway = _incremental_takeaway(boardwork_payload)
    step_id = int(current_step.id) if current_step.id is not None else None
    existing = next((entry for entry in reversed(previous_entries) if entry.step_id == step_id), None)
    action = boardwork_payload.get("continuity_action")
    if existing is not None:
        takeaways = _entry_takeaways(existing)
        anchor_message_id = source_message_id if source_message_id is not None else existing.source_message_id
        if action == "refine" and takeaways:
            takeaways[-1] = takeaway
        else:
            takeaways.append(takeaway)
        previous_payload = existing.payload if isinstance(existing.payload, dict) else {}
        existing.content = _format_takeaways(takeaways)
        existing.payload = {
            **previous_payload,
            "display_version": 2,
            "title": str(previous_payload.get("source_step_title") or current_step.title or "").strip(),
            "summary": takeaways[-1],
            "takeaways": takeaways,
            "source_step_title": str(current_step.title or "").strip(),
            "source_message_id": anchor_message_id,
            "source_turn_index": source_turn_index,
            "boardwork_payload": boardwork_payload,
        }
        existing.source_message_id = anchor_message_id
        existing.update_time = datetime.utcnow()
        repo.db.add(existing)
        repo.db.flush()
        return existing

    title = str(current_step.title or "").strip() or _whiteboard_card_title(
        current_step=current_step, boardwork_payload=boardwork_payload
    )
    previous_entry = previous_entries[-1] if previous_entries else None
    payload = {
        "display_version": 2,
        "title": title,
        "summary": takeaway,
        "takeaways": [takeaway],
        "deposit_type": "boardwork",
        "source_step_key": current_step.step_key,
        "source_step_title": str(current_step.title or "").strip(),
        "source_question": _source_question_summary(boardwork_payload),
        "previous_entry_id": previous_entry.id if previous_entry is not None else None,
        "source_session_id": session_id,
        "source_workspace_id": workspace_id,
        "source_message_id": source_message_id,
        "source_turn_index": source_turn_index,
        "boardwork_payload": boardwork_payload,
    }
    return repo.record_blackboard_entry(
        workspace_id=workspace_id,
        session_id=session_id,
        entry_type="whiteboard_entry",
        content=takeaway,
        writer_agent="BoardworkAgent",
        payload=payload,
        step_id=step_id,
        source_message_id=source_message_id,
        visibility="user",
    )


def _previous_whiteboard_context(entries: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in entries[-6:]:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        takeaways = _entry_takeaways(entry)
        if takeaways:
            result.append({
                "step_id": entry.step_id,
                "step_title": str(payload.get("source_step_title") or payload.get("title") or "").strip(),
                "takeaways": takeaways[-4:],
            })
    return result


def _entry_takeaways(entry: Any) -> list[str]:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    values = payload.get("takeaways")
    if isinstance(values, list):
        return [item.strip() for item in values if isinstance(item, str) and item.strip()]
    legacy_title = payload.get("title")
    return [legacy_title.strip()] if isinstance(legacy_title, str) and legacy_title.strip() else []


def _incremental_takeaway(payload: dict[str, Any]) -> str:
    model_takeaway = payload.get("incremental_takeaway")
    if isinstance(model_takeaway, str) and model_takeaway.strip():
        return _compact_student_visible_summary(model_takeaway, limit=500)
    return _candidate_summary_from_boardwork_payload(payload)


def _normalize_takeaway(value: str) -> str:
    return "".join(value.casefold().split()).strip("，,；;。.!！?？")


def _format_takeaways(takeaways: list[str]) -> str:
    if len(takeaways) == 1:
        return takeaways[0]
    return "\n".join(f"{index}. {item}" for index, item in enumerate(takeaways, start=1))


def _whiteboard_card_title(
    *,
    current_step,
    boardwork_payload: dict[str, Any],
) -> str:
    return (
        _candidate_summary_from_boardwork_payload(boardwork_payload)
        or str(boardwork_payload.get("focus") or current_step.title or "当前白板").strip()
        or "当前白板"
    )


def _source_question_summary(boardwork_payload: dict[str, Any]) -> str:
    request_context = boardwork_payload.get("request_context")
    if not isinstance(request_context, dict):
        return ""
    latest_user_message = str(request_context.get("latest_user_message") or "").strip()
    if not latest_user_message:
        return ""
    return _compact_source_question_summary(latest_user_message)


def _compact_source_question_summary(value: str, *, limit: int = SOURCE_QUESTION_SUMMARY_LIMIT) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rstrip(" ，,；;。")
    return f"{clipped}..."


def _boardwork_display_summary(
    *,
    current_step,
    boardwork_payload: dict[str, Any],
) -> str:
    lines: list[str] = []
    request_context = boardwork_payload.get("request_context")
    english = isinstance(request_context, dict) and request_context.get("response_language") == "en"
    step_title = str(current_step.title or "").strip()
    if step_title:
        lines.append(f"Current step: {step_title}" if english else f"当前步骤：{step_title}")
    candidate = _incremental_takeaway(boardwork_payload)
    if candidate:
        lines.append(f"Key takeaway: {candidate}" if english else f"关键结论：{candidate}")
    if not lines:
        fallback = str(
            boardwork_payload.get("focus")
            or boardwork_payload.get("boardwork_goal")
            or boardwork_payload.get("handoff_type")
            or step_title
            or "当前白板"
        ).strip()
        return fallback
    return "\n".join(lines)


def _candidate_summary_from_boardwork_payload(boardwork_payload: dict[str, Any]) -> str:
    request_context = boardwork_payload.get("request_context")
    if isinstance(request_context, dict):
        candidate_summary = str(request_context.get("knowledge_candidate_summary") or "").strip()
        if candidate_summary:
            return _compact_student_visible_summary(candidate_summary)
        visible_text = str(request_context.get("tutor_visible_text") or "").strip()
        if visible_text:
            return _compact_student_visible_summary(visible_text)
    student_feedback = str(boardwork_payload.get("student_feedback") or "").strip()
    if student_feedback:
        return _compact_student_visible_summary(student_feedback)
    return ""


def _compact_student_visible_summary(value: str, *, limit: int = 220) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[:limit].rstrip(" ，,；;。")
    return f"{clipped}..."


def _collaboration_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem_context": context.get("problem_context"),
        "plan_state": context.get("plan_state"),
        "problem_thread_summary": context.get("problem_thread_summary"),
        "memory_snapshot": context.get("memory_snapshot"),
    }


def _request_context(context: dict[str, Any]) -> dict[str, Any]:
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    hint = main_blocks.get("knowledge_deposit_hint") if isinstance(main_blocks.get("knowledge_deposit_hint"), dict) else {}
    latest_user_message = str(context.get("source_quote") or context.get("user_message") or "").strip()
    payload = {
        "latest_user_message": latest_user_message,
        "tutor_visible_text": _visible_reply(context),
        "tutor_needs_diagram": _tutor_needs_diagram(context),
        "recent_visible_messages": context.get("recent_messages"),
        "response_language": "en" if context.get("response_language") == "en" else "zh",
    }
    candidate_summary = str(hint.get("candidate_summary") or "").strip()
    if candidate_summary:
        payload["knowledge_candidate_summary"] = candidate_summary
    deposit_type = hint.get("deposit_type")
    if isinstance(deposit_type, str) and deposit_type.strip():
        payload["knowledge_deposit_type"] = deposit_type.strip()
    return payload


def _visible_reply(context: dict[str, Any]) -> str:
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    return str(main_blocks.get("visible_reply") or "").strip()


def _tutor_needs_diagram(context: dict[str, Any]) -> bool:
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    visual_need = main_blocks.get("visual_need") if isinstance(main_blocks.get("visual_need"), dict) else {}
    return bool(visual_need.get("need_diagram"))


def _deposit_hint_allows_whiteboard(context: dict[str, Any]) -> bool:
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    hint = main_blocks.get("knowledge_deposit_hint") if isinstance(main_blocks.get("knowledge_deposit_hint"), dict) else {}
    if "can_deposit" not in hint:
        return True
    return bool(hint.get("can_deposit"))


def _required_int(context: dict[str, Any], key: str) -> int:
    value = context.get(key)
    if not isinstance(value, int):
        raise ValueError(f"v3 whiteboard context requires integer {key}")
    return value


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
