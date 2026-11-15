from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
import re
from threading import Thread
from typing import Any

from pydantic import ValidationError

from app.models import TeachingStepStatus
from app.repositories.teaching import TeachingRepository
from app.teaching.v3_contracts import ChainDeltaBlock, PlanDeltaBlock


_INTERNAL_STEP_KEY_TOKENS = (
    "agent",
    "blackboard",
    "boardwork",
    "deposit",
    "handoff",
    "sidecar",
    "whiteboard",
)
_INTERNAL_STEP_TEXT_PATTERNS = (
    re.compile(r"(?:更新|刷新|沉淀|写入|记录|同步|整理|生成|维护).*(?:板书|白板)"),
    re.compile(r"(?:板书|白板).*(?:更新|刷新|沉淀|写入|记录|同步|整理|生成|维护)"),
    re.compile(r"沉淀.*(?:关键结论|结论|知识|内容)"),
    re.compile(r"(?:BoardworkAgent|PlanStateAgent|WhiteboardAgent|智能体|sidecar|handoff)", re.IGNORECASE),
    re.compile(r"(?:知识沉淀|长期记忆|记忆压缩|MemoryConsolidatorAgent)", re.IGNORECASE),
)


@dataclass(frozen=True)
class _ProviderAttempt:
    output: dict[str, Any] | None
    status: str


class PlanStateAgent:
    def __init__(
        self,
        *,
        repo: TeachingRepository,
        provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        provider_timeout_seconds: float = 8.0,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.provider_timeout_seconds = max(0.0, provider_timeout_seconds)

    def run(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        workspace_id = _required_int(context, "workspace_id")
        session_id = _required_int(context, "session_id")
        provider_attempt = self._provider_output(context)
        raw_output = provider_attempt.output

        if raw_output is None:
            raw_output = _existing_plan_fallback_output(context) or _persisted_plan_fallback_output({**context, "_repo": self.repo})
        if raw_output is None:
            return [
                {
                    "type": "plan_status",
                    "status": "skipped",
                    "reason": "plan_provider_unavailable",
                    "provider_status": provider_attempt.status,
                }
            ]
        diagnostics = _plan_diagnostics(
            raw_output,
            provider_status=provider_attempt.status,
        )
        plan_delta, chain_delta = _parse_provider_output(raw_output, context)
        persisted_chain_delta = self._persist_active_workspace_plan(
            workspace_id=workspace_id,
            plan_delta=plan_delta,
            chain_delta=chain_delta,
        ) or chain_delta

        payload = {
            **plan_delta.model_dump(mode="json", exclude={"block_type"}),
            **diagnostics,
            "step_chain": [step.model_dump(mode="json") for step in persisted_chain_delta.steps],
        }
        self.repo.record_blackboard_entry(
            workspace_id=workspace_id,
            session_id=session_id,
            entry_type="plan_state",
            content=plan_delta.plan_summary,
            writer_agent="PlanStateAgent",
            payload=payload,
        )

        return [
            {
                "type": "plan_delta",
                "workspace_id": workspace_id,
                **plan_delta.model_dump(mode="json", exclude={"block_type"}),
                **diagnostics,
            },
            {
                "type": "chain_delta",
                "workspace_id": workspace_id,
                **persisted_chain_delta.model_dump(mode="json", exclude={"block_type"}),
            },
        ]

    def _provider_output(self, context: dict[str, Any]) -> _ProviderAttempt:
        if self.provider is None:
            return _ProviderAttempt(output=None, status="not_configured")
        if self.provider_timeout_seconds <= 0:
            try:
                value = self.provider(_provider_payload(context))
            except Exception:
                return _ProviderAttempt(output=None, status="error")
            return _ProviderAttempt(output=value if isinstance(value, dict) else None, status="completed" if isinstance(value, dict) else "invalid_output")

        results: Queue[tuple[str, dict[str, Any] | None | BaseException]] = Queue(maxsize=1)

        def run_provider() -> None:
            try:
                results.put(("ok", self.provider(_provider_payload(context))))
            except BaseException as exc:
                results.put(("error", exc))

        Thread(target=run_provider, daemon=True).start()
        try:
            status, value = results.get(timeout=self.provider_timeout_seconds)
        except Empty:
            return _ProviderAttempt(output=None, status="timeout")
        if status != "ok":
            return _ProviderAttempt(output=None, status="error")
        return _ProviderAttempt(output=value if isinstance(value, dict) else None, status="completed" if isinstance(value, dict) else "invalid_output")

    def _persist_active_workspace_plan(
        self,
        *,
        workspace_id: int,
        plan_delta: PlanDeltaBlock,
        chain_delta: ChainDeltaBlock,
    ) -> ChainDeltaBlock | None:
        if not chain_delta.steps:
            return None
        workspace = self.repo.get_workspace(workspace_id)
        if workspace is None:
            return None
        active_plan = self.repo.get_active_plan(workspace_id)
        should_create_plan = (
            active_plan is None
            or plan_delta.operation in {"create", "branch", "repair", "reset"}
        )
        if not should_create_plan:
            active_steps = self.repo.list_steps(int(active_plan.id)) if active_plan.id is not None else []
            current_step = self.repo.get_current_step(workspace_id)
            if plan_delta.operation == "update" and active_steps and active_plan.id is not None:
                focused_step = self.repo.focus_plan_step(
                    workspace_id=workspace_id,
                    plan_id=int(active_plan.id),
                    step_key=plan_delta.current_step_key,
                )
                self.repo.record_event(
                    workspace_id=workspace_id,
                    session_id=workspace.session_id,
                    turn_index=workspace.last_active_turn_index,
                    event_type="plan_refocused" if focused_step is not None else "plan_updated",
                    actor="PlanStateAgent",
                    step_id=int(focused_step.id) if focused_step is not None and focused_step.id is not None else None,
                    payload={
                        "plan_id": active_plan.id,
                        "current_step_key": plan_delta.current_step_key,
                        "reason": plan_delta.reason,
                    },
                    visible_to_user=False,
                )
                return _chain_delta_from_persisted_plan(self.repo, workspace_id)
            if plan_delta.operation == "advance" and "complete_solution" in plan_delta.reason and active_steps and active_plan.id is not None:
                completed_step = self.repo.complete_plan_steps(
                    workspace_id=workspace_id,
                    plan_id=int(active_plan.id),
                    current_step_key=plan_delta.current_step_key,
                )
                self.repo.record_event(
                    workspace_id=workspace_id,
                    session_id=workspace.session_id,
                    turn_index=workspace.last_active_turn_index,
                    event_type="plan_completed",
                    actor="PlanStateAgent",
                    step_id=int(completed_step.id) if completed_step is not None and completed_step.id is not None else None,
                    payload={
                        "plan_id": active_plan.id,
                        "current_step_key": plan_delta.current_step_key,
                        "reason": plan_delta.reason,
                    },
                    visible_to_user=False,
                )
                return _chain_delta_from_persisted_plan(self.repo, workspace_id)
            if plan_delta.operation == "advance" and current_step is not None and current_step.id is not None:
                next_step = self.repo.advance_step(workspace_id, int(current_step.id))
                if next_step is not None and next_step.id is not None:
                    completed = next_step.id == current_step.id
                    self.repo.record_event(
                        workspace_id=workspace_id,
                        session_id=workspace.session_id,
                        turn_index=workspace.last_active_turn_index,
                        event_type="plan_completed" if completed else "plan_advanced",
                        actor="PlanStateAgent",
                        step_id=int(next_step.id),
                        payload={
                            "plan_id": active_plan.id,
                            "from_step_key": current_step.step_key,
                            "to_step_key": None if completed else next_step.step_key,
                            "reason": plan_delta.reason,
                        },
                        visible_to_user=False,
                    )
                return _chain_delta_from_persisted_plan(self.repo, workspace_id)
            if plan_delta.operation in {"keep", "update"} and active_steps:
                self.repo.record_event(
                    workspace_id=workspace_id,
                    session_id=workspace.session_id,
                    turn_index=workspace.last_active_turn_index,
                    event_type="plan_kept" if plan_delta.operation == "keep" else "plan_updated",
                    actor="PlanStateAgent",
                    step_id=int(current_step.id) if current_step is not None and current_step.id is not None else None,
                    payload={
                        "plan_id": active_plan.id,
                        "current_step_key": plan_delta.current_step_key,
                        "reason": plan_delta.reason,
                    },
                    visible_to_user=False,
                )
            return _chain_delta_from_persisted_plan(self.repo, workspace_id)
        if active_plan is not None and should_create_plan:
            self.repo.supersede_plan(int(active_plan.id))
        plan = (
            self.repo.create_plan(
                workspace_id=workspace_id,
                planner_agent="PlanStateAgent",
                plan_summary=plan_delta.plan_summary,
                branch_from_plan_id=int(active_plan.id) if active_plan is not None and active_plan.id is not None else None,
            )
            if should_create_plan
            else active_plan
        )
        if plan is None or plan.id is None:
            return None
        steps = self.repo.create_steps(
            plan_id=int(plan.id),
            workspace_id=workspace_id,
            step_specs=[
                {
                    "step_key": step.step_key,
                    "title": step.title,
                    "teaching_goal": step.teaching_goal,
                }
                for step in chain_delta.steps
            ],
        )
        if not steps:
            return None
        current_step = next(
            (step for step in steps if step.step_key == plan_delta.current_step_key),
            steps[0],
        )
        if current_step.id is not None:
            self.repo.activate_plan(workspace_id, int(plan.id), int(current_step.id))
            if plan_delta.operation == "advance" and "complete_solution" in plan_delta.reason:
                current_step = self.repo.complete_plan_steps(
                    workspace_id=workspace_id,
                    plan_id=int(plan.id),
                    current_step_key=plan_delta.current_step_key,
                ) or current_step
            self.repo.record_event(
                workspace_id=workspace_id,
                session_id=workspace.session_id,
                turn_index=workspace.last_active_turn_index,
                event_type="plan_completed" if plan_delta.operation == "advance" and "complete_solution" in plan_delta.reason else "plan_created",
                actor="PlanStateAgent",
                step_id=int(current_step.id),
                payload={
                    "plan_id": plan.id,
                    "current_step_key": current_step.step_key,
                    "reason": plan_delta.reason,
                },
                visible_to_user=False,
            )
        return _chain_delta_from_persisted_plan(self.repo, workspace_id)


def _provider_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "capability": "plan",
        "context": context,
        "contract": {
            "plan_summary": "short string",
            "steps": [
                {
                    "step_key": "stable lowercase identifier",
                    "title": "student-visible current-step title",
                    "teaching_goal": "what this step teaches",
                    "expected_student_action": "what the student should do next",
                    "mastery_criteria": "how the tutor knows this step is done",
                }
            ],
        },
    }


def _plan_diagnostics(raw_output: dict[str, Any], *, provider_status: str) -> dict[str, Any]:
    fallback_reason = raw_output.get("_fallback_reason")
    if isinstance(fallback_reason, str) and fallback_reason:
        return {
            "source": "fallback",
            "provider_status": provider_status,
            "fallback_reason": fallback_reason,
        }
    return {
        "source": "agent",
        "provider_status": provider_status,
    }


def _parse_provider_output(raw_output: dict[str, Any], context: dict[str, Any]) -> tuple[PlanDeltaBlock, ChainDeltaBlock]:
    if "plan_summary" in raw_output and "steps" in raw_output:
        return _parse_plan_decomposer_output(raw_output, context)
    legacy_output = _normalize_legacy_teaching_steps_output(raw_output)
    if legacy_output is not None:
        return _parse_plan_decomposer_output(legacy_output, context)
    try:
        plan_delta = PlanDeltaBlock.model_validate(raw_output.get("plan_delta"))
        chain_delta = _filter_student_facing_chain_delta(ChainDeltaBlock.model_validate(raw_output.get("chain_delta")))
        if not chain_delta.steps:
            raise ValueError("PlanStateAgent provider output rejected")
        if not any(step.step_key == plan_delta.current_step_key for step in chain_delta.steps):
            plan_delta = plan_delta.model_copy(update={"current_step_key": chain_delta.steps[0].step_key})
        return (plan_delta, chain_delta)
    except (TypeError, ValidationError, ValueError) as exc:
        raise ValueError("PlanStateAgent provider output rejected") from exc


def _normalize_legacy_teaching_steps_output(raw_output: dict[str, Any]) -> dict[str, Any] | None:
    raw_steps = raw_output.get("teaching_steps")
    if not isinstance(raw_steps, list):
        return None

    steps: list[dict[str, str]] = []
    used_keys: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            continue
        title = _first_non_empty_string(raw_step, ("title", "visible", "summary", "description"))
        if title is None:
            continue
        step_key = _unique_step_key(
            _legacy_step_key(raw_step, index=index),
            used_keys=used_keys,
        )
        teaching_goal = _first_non_empty_string(raw_step, ("teaching_goal", "goal", "rationale", "visible")) or title
        steps.append(
            {
                "step_key": step_key,
                "title": title,
                "teaching_goal": teaching_goal,
            }
        )
    if not steps:
        return None

    plan_summary = _first_non_empty_string(raw_output, ("plan_summary", "knowledge_deposit", "summary"))
    return {
        "plan_summary": plan_summary or steps[0]["title"],
        "steps": steps,
    }


def _first_non_empty_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _legacy_step_key(raw_step: dict[str, Any], *, index: int) -> str:
    for key in ("step_key", "rationale"):
        value = raw_step.get(key)
        if isinstance(value, str) and value.strip():
            normalized = _normalize_step_key(value)
            if normalized:
                return normalized
    step_id = raw_step.get("step_id")
    if isinstance(step_id, int) and step_id > 0:
        return f"step_{step_id}"
    if isinstance(step_id, str) and step_id.strip():
        normalized = _normalize_step_key(step_id)
        if normalized:
            return normalized
    return f"step_{index + 1}"


def _normalize_step_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:64].strip("_")


def _unique_step_key(step_key: str, *, used_keys: set[str]) -> str:
    base = step_key or f"step_{len(used_keys) + 1}"
    candidate = base
    suffix = 2
    while candidate in used_keys:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_keys.add(candidate)
    return candidate


def _required_int(context: dict[str, Any], key: str) -> int:
    value = context.get(key)
    if not isinstance(value, int):
        raise ValueError(f"v3 plan context requires integer {key}")
    return value


def _parse_plan_decomposer_output(raw_output: dict[str, Any], context: dict[str, Any]) -> tuple[PlanDeltaBlock, ChainDeltaBlock]:
    raw_steps = raw_output.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("PlanStateAgent provider output rejected")
    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            continue
        step_key = raw_step.get("step_key")
        title = raw_step.get("title")
        if not isinstance(step_key, str) or not step_key.strip():
            step_key = f"step_{index + 1}"
        if not isinstance(title, str) or not title.strip():
            continue
        teaching_goal = raw_step.get("teaching_goal")
        step_key = step_key.strip()
        title = title.strip()
        teaching_goal = teaching_goal.strip() if isinstance(teaching_goal, str) else ""
        if _is_internal_workflow_step(
            step_key=step_key,
            title=title,
            teaching_goal=teaching_goal,
        ):
            continue
        steps.append(
            {
                "step_key": step_key,
                "title": title,
                "status": "active" if not steps else "pending",
                "teaching_goal": teaching_goal,
            }
        )
    if not steps:
        raise ValueError("PlanStateAgent provider output rejected")
    plan_summary = raw_output.get("plan_summary")
    plan_state = context.get("plan_state") if isinstance(context.get("plan_state"), dict) else {}
    main_blocks = context.get("main_blocks") if isinstance(context.get("main_blocks"), dict) else {}
    intent_state = main_blocks.get("intent_state") if isinstance(main_blocks.get("intent_state"), dict) else {}
    existing_version = plan_state.get("plan_version")
    if not isinstance(existing_version, int) or existing_version < 0:
        existing_version = 0
    current_step_key = plan_state.get("current_step_key")
    plan_signal = str(intent_state.get("plan_signal") or "").strip()
    intent = str(intent_state.get("intent") or "").strip()
    fallback_reason = raw_output.get("_fallback_reason")
    has_existing_plan = existing_version > 0 and isinstance(current_step_key, str) and bool(current_step_key)

    operation = "create"
    target_step_key = steps[0]["step_key"]
    target_step_from_intent = intent_state.get("target_step_key")
    if plan_signal == "complete_solution":
        operation = "advance"
        target_step_key = steps[-1]["step_key"]
    elif has_existing_plan and intent != "new_problem" and plan_signal != "reset_plan":
        if isinstance(target_step_from_intent, str) and any(step.get("step_key") == target_step_from_intent for step in steps):
            operation = "update"
            target_step_key = target_step_from_intent
        elif plan_signal == "advance_step":
            operation = "advance"
            target_step_key = _next_step_key(steps, current_step_key) or current_step_key
        elif plan_signal in {"branch", "repair_plan"}:
            operation = "branch" if plan_signal == "branch" else "repair"
            target_step_key = current_step_key
        else:
            operation = "keep"
            target_step_key = current_step_key

    return (
        PlanDeltaBlock.model_validate(
            {
                "operation": operation,
                "plan_version": max(1, existing_version if has_existing_plan else 1),
                "plan_summary": plan_summary.strip() if isinstance(plan_summary, str) and plan_summary.strip() else steps[0]["title"],
                "current_step_key": target_step_key,
                "reason": fallback_reason if isinstance(fallback_reason, str) and fallback_reason else f"plan_decomposer_provider:{plan_signal or 'default'}",
            }
        ),
        ChainDeltaBlock.model_validate({"steps": steps}),
    )


def _is_internal_workflow_step(*, step_key: str, title: str, teaching_goal: str) -> bool:
    normalized_key = _normalize_step_key(step_key)
    if any(token in normalized_key for token in _INTERNAL_STEP_KEY_TOKENS):
        return True
    text = " ".join(part for part in (title, teaching_goal) if part).strip()
    return any(pattern.search(text) for pattern in _INTERNAL_STEP_TEXT_PATTERNS)


def _filter_student_facing_chain_delta(chain_delta: ChainDeltaBlock) -> ChainDeltaBlock:
    steps = [
        step.model_dump(mode="json")
        for step in chain_delta.steps
        if not _is_internal_workflow_step(
            step_key=step.step_key,
            title=step.title,
            teaching_goal=step.teaching_goal,
        )
    ]
    if steps:
        steps[0]["status"] = "active"
        for step in steps[1:]:
            if step.get("status") == "active":
                step["status"] = "pending"
    return ChainDeltaBlock.model_validate({"steps": steps})


def _existing_plan_fallback_output(context: dict[str, Any]) -> dict[str, Any] | None:
    plan_state = context.get("plan_state") if isinstance(context.get("plan_state"), dict) else {}
    existing_steps = plan_state.get("step_chain") if isinstance(plan_state.get("step_chain"), list) else []
    steps: list[dict[str, str]] = []
    for index, raw_step in enumerate(existing_steps):
        if not isinstance(raw_step, dict):
            continue
        title = raw_step.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        step_key = raw_step.get("step_key")
        steps.append(
            {
                "step_key": str(step_key or f"step_{index + 1}").strip(),
                "title": title.strip(),
                "teaching_goal": str(raw_step.get("teaching_goal") or title).strip(),
            }
        )
    if not steps:
        return None
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    title = str(problem_context.get("title") or "").strip()
    summary = str(problem_context.get("problem_summary") or "").strip()
    plan_summary = title or summary[:80] or steps[0]["title"]
    return {
        "plan_summary": plan_summary,
        "steps": steps,
        "_fallback_reason": "context_fallback_plan",
    }


def _persisted_plan_fallback_output(context: dict[str, Any]) -> dict[str, Any] | None:
    repo = context.get("_repo")
    workspace_id = context.get("workspace_id")
    if not isinstance(repo, TeachingRepository) or not isinstance(workspace_id, int):
        return None
    active_plan = repo.get_active_plan(workspace_id)
    if active_plan is None or active_plan.id is None:
        return None
    persisted_steps = repo.list_steps(int(active_plan.id))
    steps: list[dict[str, str]] = []
    for index, step in enumerate(persisted_steps):
        title = str(step.title or "").strip()
        if not title:
            continue
        step_key = str(step.step_key or f"step_{index + 1}").strip()
        steps.append(
            {
                "step_key": step_key,
                "title": title,
                "teaching_goal": str(step.teaching_goal or title).strip(),
            }
        )
    if not steps:
        return None
    plan_summary = str(active_plan.plan_summary or "").strip() or steps[0]["title"]
    return {
        "plan_summary": plan_summary,
        "steps": steps,
        "_fallback_reason": "persisted_plan_fallback",
    }


def _chain_delta_from_persisted_plan(repo: TeachingRepository, workspace_id: int) -> ChainDeltaBlock | None:
    active_plan = repo.get_active_plan(workspace_id)
    if active_plan is None or active_plan.id is None:
        return None
    current_step = repo.get_current_step(workspace_id)
    current_step_id = current_step.id if current_step is not None else None
    steps = repo.list_steps(int(active_plan.id))
    if not steps:
        return None
    return ChainDeltaBlock.model_validate(
        {
            "steps": [
                {
                    "step_key": step.step_key,
                    "title": step.title,
                    "status": _v3_status_from_persisted_step(step.status, is_current=step.id == current_step_id),
                    "teaching_goal": step.teaching_goal,
                }
                for step in steps
            ]
        }
    )


def _v3_status_from_persisted_step(status: TeachingStepStatus, *, is_current: bool) -> str:
    if is_current and status != TeachingStepStatus.CONFIRMED:
        return "active"
    if status in {TeachingStepStatus.CONFIRMED, TeachingStepStatus.SKIPPED}:
        return "done"
    if status in {TeachingStepStatus.IN_PROGRESS, TeachingStepStatus.NEEDS_REVISIT}:
        return "active"
    return "pending"


def _next_step_key(steps: list[dict[str, Any]], current_step_key: str) -> str | None:
    for index, step in enumerate(steps):
        if step.get("step_key") == current_step_key:
            if index + 1 < len(steps):
                next_step_key = steps[index + 1].get("step_key")
                return next_step_key if isinstance(next_step_key, str) and next_step_key else None
            return current_step_key
    return None
