from __future__ import annotations

from typing import Any

from app.models import AgentBlackboardEntry, AgentMemoryItem, AgentProblemWorkspace
from app.repositories.teaching import TeachingRepository


class ContextAssembler:
    """Build the compact v3 turn context from persisted state only."""

    def __init__(self, repo: TeachingRepository):
        self.repo = repo

    def build(
        self,
        *,
        user_id: int,
        session_id: int,
        workspace: AgentProblemWorkspace | None,
        user_message: str,
        turn_images: list[dict[str, Any]] | None = None,
        recent_limit: int = 6,
        guidance_depth: str = "direct",
        session_problem_id: int | None = None,
    ) -> dict[str, Any]:
        if workspace is None:
            return self._build_without_workspace(
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                turn_images=turn_images,
                recent_limit=recent_limit,
                guidance_depth=guidance_depth,
                session_problem_id=session_problem_id,
            )

        workspace_id = int(workspace.id) if workspace.id is not None else None
        if workspace_id is None:
            raise ValueError("workspace must be persisted before assembling v3 context")

        entries = self.repo.list_blackboard_entries(workspace_id)
        problem_context_entry = _latest_entry(entries, "problem_context")
        if problem_context_entry is None:
            problem_context = self._create_problem_context(
                session_id=session_id,
                workspace=workspace,
                user_message=user_message,
            )
            entries = self.repo.list_blackboard_entries(workspace_id)
        else:
            problem_context = dict(problem_context_entry.payload or {})
        _attach_session_problem_id(problem_context, session_problem_id)

        plan_state_entry = _latest_entry(entries, "plan_state")
        summary_entry = _latest_entry(entries, "problem_thread_summary")

        return {
            "schema_version": "teaching_v3_context.v1",
            "user_id": user_id,
            "session_id": session_id,
            "workspace_id": workspace_id,
            "workspace": {
                "id": workspace_id,
                "title": workspace.title,
                "guidance_depth": workspace.guidance_depth.value,
                "problem_id": workspace.problem_id,
                "source": workspace.source,
            },
            "guidance_contract": _guidance_contract(workspace.guidance_depth.value),
            "user_message": user_message,
            "turn_images": _normalize_turn_images(turn_images),
            "problem_context": problem_context,
            "plan_state": dict(plan_state_entry.payload or {}) if plan_state_entry else _empty_plan_state(),
            "problem_thread_summary": (
                dict(summary_entry.payload or {}) if summary_entry else _empty_problem_thread_summary()
            ),
            "recent_messages": self.repo.list_recent_visible_messages(workspace_id, limit=recent_limit),
            "learning_progress": _learning_progress_from_messages(
                self.repo.list_recent_visible_messages(workspace_id, limit=40),
            ),
            "memory_snapshot": {
                "version": 1,
                "items": [_serialize_memory_item(item) for item in self.repo.list_active_long_term_memory(user_id)],
            },
            "whiteboard_refs": _compact_whiteboard_refs(entries),
        }

    def _build_without_workspace(
        self,
        *,
        user_id: int,
        session_id: int,
        user_message: str,
        turn_images: list[dict[str, Any]] | None,
        recent_limit: int,
        guidance_depth: str,
        session_problem_id: int | None,
    ) -> dict[str, Any]:
        recent_messages = self.repo.list_recent_visible_messages_for_session(session_id, limit=recent_limit)
        problem_context = _empty_problem_context()
        _attach_session_problem_id(problem_context, session_problem_id)
        return {
            "schema_version": "teaching_v3_context.v1",
            "user_id": user_id,
            "session_id": session_id,
            "workspace_id": None,
            "workspace": None,
            "guidance_contract": _guidance_contract(guidance_depth),
            "user_message": user_message,
            "turn_images": _normalize_turn_images(turn_images),
            "problem_context": problem_context,
            "plan_state": _empty_plan_state(),
            "problem_thread_summary": _empty_problem_thread_summary(),
            "recent_messages": recent_messages,
            "learning_progress": _learning_progress_from_messages(
                self.repo.list_recent_visible_messages_for_session(session_id, limit=40),
            ),
            "memory_snapshot": {
                "version": 1,
                "items": [_serialize_memory_item(item) for item in self.repo.list_active_long_term_memory(user_id)],
            },
            "whiteboard_refs": [],
        }

    def _create_problem_context(
        self,
        *,
        session_id: int,
        workspace: AgentProblemWorkspace,
        user_message: str,
    ) -> dict[str, Any]:
        workspace_id = int(workspace.id) if workspace.id is not None else None
        if workspace_id is None:
            raise ValueError("workspace must be persisted before creating problem context")

        summary = (workspace.problem_summary or workspace.title or user_message or "当前题目").strip()
        payload = {
            "problem_context_id": f"workspace:{workspace_id}:problem:v1",
            "version": 1,
            "problem_id": workspace.problem_id,
            "problem_title": workspace.title,
            "problem_summary": summary,
            "source": workspace.source,
            "raw_problem_text_available": bool((workspace.problem_summary or user_message or "").strip()),
            "examples": [],
            "constraints": [],
        }
        self.repo.record_blackboard_entry(
            workspace_id=workspace_id,
            session_id=session_id,
            entry_type="problem_context",
            content=payload["problem_context_id"],
            writer_agent="ContextAssembler",
            payload=payload,
        )
        return payload


def _attach_session_problem_id(problem_context: dict[str, Any], session_problem_id: int | None) -> None:
    if isinstance(session_problem_id, int) and session_problem_id > 0 and not problem_context.get("problem_id"):
        problem_context["problem_id"] = session_problem_id
        problem_context["problem_title"] = problem_context.get("problem_title") or f"题目 {session_problem_id}"

def _latest_entry(entries: list[AgentBlackboardEntry], entry_type: str) -> AgentBlackboardEntry | None:
    for entry in reversed(entries):
        if entry.entry_type == entry_type:
            return entry
    return None


def _empty_plan_state() -> dict[str, Any]:
    return {
        "plan_version": 0,
        "current_step_key": None,
        "current_step_status": None,
        "step_chain": [],
    }


def _empty_problem_thread_summary() -> dict[str, Any]:
    return {
        "summary_version": 0,
        "summary": "",
        "open_confusions": [],
        "resolved_points": [],
    }


def _empty_problem_context(problem_summary: str = "") -> dict[str, Any]:
    return {
        "problem_context_id": "session:candidate_problem:v1" if problem_summary else None,
        "version": 1,
        "problem_id": None,
        "problem_title": _problem_title_from_summary(problem_summary),
        "problem_summary": problem_summary,
        "source": None,
        "raw_problem_text_available": bool(problem_summary),
        "examples": [],
        "constraints": [],
    }


def _normalize_turn_images(images: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for image in images or []:
        if not isinstance(image, dict):
            continue
        image_id = str(image.get("image_id") or "").strip()
        url = str(image.get("url") or "").strip()
        if not image_id or not url:
            continue
        normalized.append(
            {
                "image_id": image_id,
                "url": url,
                "thumbnail_url": str(image.get("thumbnail_url") or "").strip() or None,
                "filename": str(image.get("filename") or "").strip() or image_id,
                "mime_type": str(image.get("mime_type") or "").strip() or None,
                "size_bytes": image.get("size_bytes") if isinstance(image.get("size_bytes"), int) else None,
                "storage_path": str(image.get("storage_path") or "").strip() or None,
            }
        )
    return normalized


def _problem_title_from_summary(summary: str) -> str | None:
    if not summary:
        return None
    first_line = summary.splitlines()[0].strip()
    if len(first_line) <= 32:
        return first_line
    return f"{first_line[:31]}..."


def _learning_progress_from_messages(
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    # A neighboring user turn is not necessarily an answer. Preserve the turns
    # as evidence and let the main model decide whether it answered a question.
    recent_turns = [
        {
            "role": message.get("role"),
            "content": str(message.get("content") or "")[:220],
            "turn_index": message.get("turn_index"),
        }
        for message in messages
        if message.get("role") in {"user", "assistant"} and str(message.get("content") or "").strip()
    ]
    return {
        "recent_turns": recent_turns[-20:],
    }


def _guidance_contract(guidance_depth: str) -> dict[str, Any]:
    base = {
        "guidance_depth": guidance_depth,
        "code_policy": "complete_code_only_when_user_explicitly_asks",
        "visible_reply_policy": "answer_requested_scope_like_normal_model",
        "interaction_policy": "direct_answers_question_without_checkpoint_gating",
        "solution_scope_policy": "when_user_requests_solution_or_full_explanation_cover_problem_understanding_algorithm_derivation_steps_complexity_and_implementation_notes",
        "plan_completion_policy": "if_visible_reply_covers_complete_solution_emit_complete_solution_plan_signal",
    }
    if guidance_depth == "coached":
        return {
            **base,
            "code_policy": "no_complete_code",
            "visible_reply_policy": "acknowledge_user_turn_then_give_key_observation_and_one_checkpoint",
            "interaction_policy": "respond_to_user_specific_content_before_any_question",
            "max_checkpoint_questions": 1,
        }
    if guidance_depth in {"socratic", "minimal"}:
        return {
            **base,
            "code_policy": "no_complete_code",
            "visible_reply_policy": "acknowledge_user_turn_then_give_small_hint_or_one_question",
            "interaction_policy": "do_not_only_ask_questions_first_anchor_to_the_user_message",
            "max_checkpoint_questions": 1,
        }
    return base


def _serialize_memory_item(item: AgentMemoryItem) -> dict[str, Any]:
    return {
        "memory_id": item.id,
        "memory_type": item.memory_type,
        "content": item.content,
        "scope": item.scope.value,
        "confidence": item.confidence,
        "importance": item.importance,
    }


def _compact_whiteboard_refs(entries: list[AgentBlackboardEntry]) -> list[dict[str, Any]]:
    visible = [
        entry for entry in entries
        if entry.entry_type == "whiteboard_entry" and entry.visibility == "user"
    ]
    refs: list[dict[str, Any]] = []
    for entry in visible[-8:]:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        raw_takeaways = payload.get("takeaways")
        if isinstance(raw_takeaways, list):
            takeaways = [item.strip()[:350] for item in raw_takeaways[-4:] if isinstance(item, str) and item.strip()]
        else:
            title = str(payload.get("title") or "").strip()
            takeaways = [title[:350]] if title else []
        refs.append({
            "entry_id": entry.id,
            "step_id": entry.step_id,
            "title": str(payload.get("title") or "").strip()[:120],
            "takeaways": takeaways,
            "source_message_id": entry.source_message_id or payload.get("source_message_id"),
        })
    return refs
