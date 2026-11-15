from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from queue import Empty, Queue
from random import uniform
from threading import Thread
from time import perf_counter, sleep
from typing import Any

from app.models import AgentProblemWorkspace, GuidanceDepthValue, ProblemWorkspaceStatus
from app.repositories.teaching import TeachingRepository
from app.teaching.v3_context import ContextAssembler
from app.teaching.v3_contracts import (
    MCPRequestBlock,
    TeachingV3TurnResponse,
)
from app.teaching.v3_main_dialog import MainDialogAgent
from app.teaching.v3_followup import followup_input, stream_followup

SidecarFn = Callable[[dict[str, Any], Callable[[dict[str, Any]], None]], None]
VisibleStreamFn = Callable[[dict[str, Any]], Iterator[str]]
FollowupFn = Callable[[dict[str, Any]], Any]
MCPExecutorFn = Callable[[MCPRequestBlock, dict[str, Any], Callable[[dict[str, Any]], None]], str | None]

REQUIRED_SIDECAR_COLLECT_TIMEOUT_SECONDS = 10.0


class TeachingV3Orchestrator:
    def __init__(
        self,
        *,
        repo: TeachingRepository,
        main_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
        visible_stream_provider: VisibleStreamFn | None = None,
        plan_sidecar: SidecarFn | None = None,
        diagram_sidecar: SidecarFn | None = None,
        followup_provider: FollowupFn | None = None,
        whiteboard_sidecar: SidecarFn | None = None,
        memory_sidecar: SidecarFn | None = None,
        compaction_sidecar: SidecarFn | None = None,
        mcp_executor: MCPExecutorFn | None = None,
        submission_demo_mode: bool = False,
        response_language: str = "zh",
        sidecar_collect_timeout_seconds: float = 0.05,
        required_sidecar_collect_timeout_seconds: float = REQUIRED_SIDECAR_COLLECT_TIMEOUT_SECONDS,
    ) -> None:
        self.repo = repo
        self.main_provider = main_provider
        self.visible_stream_provider = visible_stream_provider
        self.plan_sidecar = plan_sidecar
        self.diagram_sidecar = diagram_sidecar
        self.followup_provider = followup_provider
        self.whiteboard_sidecar = whiteboard_sidecar
        self.memory_sidecar = memory_sidecar
        self.compaction_sidecar = compaction_sidecar
        self.mcp_executor = mcp_executor
        self.submission_demo_mode = submission_demo_mode
        self.response_language = "en" if response_language == "en" else "zh"
        self.sidecar_collect_timeout_seconds = max(0.0, sidecar_collect_timeout_seconds)
        self.required_sidecar_collect_timeout_seconds = max(
            self.sidecar_collect_timeout_seconds,
            required_sidecar_collect_timeout_seconds,
        )

    def run_stream(
        self,
        *,
        user_id: int,
        session_id: int,
        session_uuid: str,
        workspace: AgentProblemWorkspace | None,
        user_message: str,
        turn_images: list[dict[str, Any]] | None = None,
        source_message_id: int | None = None,
        source_turn_index: int | None = None,
        requested_guidance_depth: str = "direct",
        problem_id: int | None = None,
        session_problem_id: int | None = None,
        source: str | None = None,
        serialize_sidecars: bool = False,
        force_diagram: bool = False,
        forced_diagram_goal: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        started_at = perf_counter()
        sidecar_events: Queue[dict[str, Any]] = Queue()
        sidecar_statuses = {
            "plan": "skipped",
            "diagram": "skipped",
            "whiteboard": "skipped",
            "memory": "skipped",
            "compaction": "skipped",
        }
        emitted_events: list[dict[str, Any]] = []
        metrics: dict[str, int] = {}
        mcp_request_event_emitted = False

        def elapsed_ms() -> int:
            return max(0, int((perf_counter() - started_at) * 1000))

        context = ContextAssembler(self.repo).build(
            user_id=user_id,
            session_id=session_id,
            workspace=workspace,
            user_message=user_message,
            turn_images=turn_images,
            guidance_depth=requested_guidance_depth,
            session_problem_id=session_problem_id,
        )
        context = {**context, "response_language": self.response_language}
        first_visible_delta_latency_ms: int | None = None
        final_visible_reply = ""

        main_result = MainDialogAgent(provider=self.main_provider).run(context)
        metrics["intent_state_latency_ms"] = elapsed_ms()
        final_visible_reply = main_result.visible_reply

        intent_event = {
            "type": "main_block.intent_state",
            **main_result.intent_state.model_dump(mode="json"),
        }
        emitted_events.append(intent_event)
        yield intent_event

        if main_result.mcp_request.need_mcp:
            mcp_request_event = {
                "type": "main_block.mcp_request",
                **main_result.mcp_request.model_dump(mode="json"),
            }
            emitted_events.append(mcp_request_event)
            yield mcp_request_event
            mcp_request_event_emitted = True
            if _mcp_request_needs_runtime_execution(main_result.mcp_request, context):
                mcp_reply = None
                for mcp_event in self._execute_mcp_request_events(
                    main_result.mcp_request,
                    context={
                        **context,
                        "session_uuid": session_uuid,
                        "source_message_id": source_message_id,
                        "source_turn_index": source_turn_index,
                    },
                ):
                    if isinstance(mcp_event.get("visible_reply"), str):
                        mcp_reply = str(mcp_event.get("visible_reply") or "").strip() or mcp_reply
                    emitted_events.append(mcp_event)
                    yield mcp_event
                if mcp_reply:
                    final_visible_reply = mcp_reply

        for chunk in _chunk_visible_reply(final_visible_reply):
            if first_visible_delta_latency_ms is None:
                first_visible_delta_latency_ms = elapsed_ms()
                metrics["first_visible_delta_latency_ms"] = first_visible_delta_latency_ms
            delta_event = {"type": "main_delta", "delta": chunk}
            emitted_events.append(delta_event)
            yield delta_event

        final_visible_reply = final_visible_reply.strip()
        metrics.setdefault(
            "first_visible_delta_latency_ms",
            first_visible_delta_latency_ms if first_visible_delta_latency_ms is not None else elapsed_ms(),
        )
        metrics["visible_reply_complete_latency_ms"] = elapsed_ms()
        visible_complete_event = {
            "type": "main_block.visible_complete",
            "response": final_visible_reply,
        }
        emitted_events.append(visible_complete_event)
        yield visible_complete_event

        if force_diagram and not main_result.visual_need.need_diagram:
            forced_visual_need = main_result.visual_need.model_copy(update={
                "need_diagram": True,
                "diagram_goal": forced_diagram_goal or main_result.visual_need.diagram_goal or "用户请求的图示讲解",
                "visual_focus": "当前题目的关键结构与变化",
                "priority": "normal",
            })
            main_result = replace(main_result, visual_need=forced_visual_need)

        if main_result.intent_state.intent == "non_problem" and not main_result.visual_need.need_diagram:
            if not mcp_request_event_emitted:
                mcp_event = {
                    "type": "main_block.mcp_request",
                    **main_result.mcp_request.model_dump(mode="json"),
                }
                emitted_events.append(mcp_event)
                yield mcp_event
            response = TeachingV3TurnResponse(
                session_id=session_uuid,
                workspace_id=int(workspace.id) if workspace is not None and workspace.id is not None else None,
                response=final_visible_reply,
                intent_state=main_result.intent_state,
                visual_need=None,
                knowledge_deposit_hint=None,
                mcp_request=main_result.mcp_request,
                metrics={**metrics, "final_contract_latency_ms": elapsed_ms()},
                sidecar_statuses=sidecar_statuses,
                events=emitted_events,
            )
            yield {"type": "final", "response": response.model_dump(mode="json")}
            return

        if (
            (
                (workspace is not None and workspace.problem_id is not None)
                or session_problem_id is not None
            )
            and problem_id is None
            and main_result.intent_state.intent == "new_problem"
        ):
            yield {
                "type": "problem_redirect",
                "problem_id": 0,
                "problem_title": "新题目",
                "reason": "model_identified_new_problem",
            }
            return

        workspace = self._ensure_problem_workspace(
            user_id=user_id,
            session_id=session_id,
            workspace=workspace,
            user_message=user_message,
            intent=main_result.intent_state.intent,
            should_start_new_workspace=main_result.intent_state.should_start_new_workspace,
            requested_guidance_depth=requested_guidance_depth,
            problem_id=(
                problem_id
                or (
                    main_result.mcp_request.problem_id
                    if main_result.mcp_request.need_mcp and main_result.mcp_request.resource == "problem_detail"
                    else None
                )
            ),
            source=source,
            source_message_id=source_message_id,
            candidate_problem_summary=(
                main_result.intent_state.problem_summary
                or (_candidate_problem_summary(context) if main_result.intent_state.intent == "continue_problem" else "")
            ),
        )
        context = ContextAssembler(self.repo).build(
            user_id=user_id,
            session_id=session_id,
            workspace=workspace,
            user_message=user_message,
            turn_images=turn_images,
            guidance_depth=requested_guidance_depth,
            session_problem_id=session_problem_id,
        )
        context = {**context, "response_language": self.response_language}
        self.repo.db.commit()

        visual_event = {
            "type": "main_block.visual_need",
            **main_result.visual_need.model_dump(mode="json"),
        }
        emitted_events.append(visual_event)
        yield visual_event

        early_diagram_running_emitted = False
        if main_result.visual_need.need_diagram and self.diagram_sidecar is not None:
            early_diagram_running_event = {
                "type": "diagram_status",
                "status": "running",
                "agent": "DiagramAgent",
                "message": "正在调用DiagramAgent。",
            }
            emitted_events.append(early_diagram_running_event)
            early_diagram_running_emitted = True
            yield early_diagram_running_event

        deposit_event = {
            "type": "main_block.knowledge_deposit_hint",
            **main_result.knowledge_deposit_hint.model_dump(mode="json"),
        }
        emitted_events.append(deposit_event)
        yield deposit_event

        started_sidecars = self._start_sidecars(
            context={
                **context,
                "_serialize_sidecars": serialize_sidecars,
                "session_uuid": session_uuid,
                "source_message_id": source_message_id,
                "source_turn_index": source_turn_index,
                "source_quote": user_message,
                "main_blocks": {
                    "intent_state": main_result.intent_state.model_dump(mode="json"),
                    "visible_reply": final_visible_reply,
                    "visual_need": main_result.visual_need.model_dump(mode="json"),
                    "knowledge_deposit_hint": main_result.knowledge_deposit_hint.model_dump(mode="json"),
                    "mcp_request": main_result.mcp_request.model_dump(mode="json"),
                },
            },
            sidecar_events=sidecar_events,
            sidecar_statuses=sidecar_statuses,
            intent=main_result.intent_state.intent,
            need_diagram=main_result.visual_need.need_diagram,
            can_deposit=main_result.knowledge_deposit_hint.can_deposit,
        )

        for status_event in _sidecar_status_events(started_sidecars):
            if early_diagram_running_emitted and status_event.get("type") == "diagram_status":
                continue
            emitted_events.append(status_event)
            yield status_event

        if not mcp_request_event_emitted:
            mcp_event = {
                "type": "main_block.mcp_request",
                **main_result.mcp_request.model_dump(mode="json"),
            }
            emitted_events.append(mcp_event)
            yield mcp_event

        diagram_started = any(key == "diagram" for key, _agent_name in started_sidecars)
        wait_for_keys = _sidecar_terminal_wait_keys(
            started_sidecars,
            diagram_started=diagram_started,
            wait_for_all=serialize_sidecars,
        )
        sidecar_collect_timeout = _sidecar_collect_timeout_seconds(
            started_sidecars,
            base_timeout_seconds=self.sidecar_collect_timeout_seconds,
            required_timeout_seconds=self.required_sidecar_collect_timeout_seconds,
            diagram_started=diagram_started,
            wait_for_all=serialize_sidecars,
        )
        for sidecar_event in self._collect_ready_sidecar_events(
            sidecar_events,
            timeout_seconds=sidecar_collect_timeout,
            wait_for_keys=wait_for_keys,
            wait_for_diagram=diagram_started,
        ):
            _apply_sidecar_completion_status(sidecar_event, sidecar_statuses)
            _record_sidecar_ready_metric(sidecar_event, metrics, elapsed_ms())
            emitted_events.append(sidecar_event)
            yield sidecar_event

        _finalize_uncollected_sidecar_statuses(sidecar_statuses)
        diagram_scene_id = _latest_diagram_scene_id(emitted_events)
        if diagram_scene_id is not None:
            ready_scene = next(
                (event.get("scene") for event in reversed(emitted_events)
                 if event.get("type") == "diagram_ready" and isinstance(event.get("scene"), dict)),
                None,
            )
            if isinstance(ready_scene, dict):
                followup_payload = followup_input(
                    guidance_depth=requested_guidance_depth,
                    first_answer=final_visible_reply,
                    diagram_goal=str(main_result.visual_need.diagram_goal or ""),
                    scene=ready_scene,
                    language=self.response_language,
                )
                followup_start = {"type": "main_followup.start", "source_turn_index": source_turn_index}
                emitted_events.append(followup_start)
                yield followup_start
                followup_parts: list[str] = []
                followup_length = 0
                for chunk in stream_followup(followup_payload, self.followup_provider):
                    visible_chunk = chunk[:max(0, 1600 - followup_length)]
                    if not visible_chunk:
                        break
                    followup_parts.append(visible_chunk)
                    followup_length += len(visible_chunk)
                    delta_event = {"type": "main_followup.delta", "delta": visible_chunk}
                    emitted_events.append(delta_event)
                    yield delta_event
                followup_text = "".join(followup_parts).strip()
                followup_complete = {"type": "main_followup.visible_complete", "response": followup_text}
                emitted_events.append(followup_complete)
                yield followup_complete
        metrics["final_contract_latency_ms"] = elapsed_ms()
        response = TeachingV3TurnResponse(
            session_id=session_uuid,
            workspace_id=int(workspace.id) if workspace.id is not None else None,
            response=final_visible_reply,
            intent_state=main_result.intent_state,
            visual_need=main_result.visual_need,
            knowledge_deposit_hint=main_result.knowledge_deposit_hint,
            mcp_request=main_result.mcp_request,
            diagram_scene_id=diagram_scene_id,
            metrics=metrics,
            sidecar_statuses=sidecar_statuses,
            events=emitted_events,
        )
        yield {"type": "final", "response": response.model_dump(mode="json")}

    def _ensure_problem_workspace(
        self,
        *,
        user_id: int,
        session_id: int,
        workspace: AgentProblemWorkspace | None,
        user_message: str,
        intent: str,
        should_start_new_workspace: bool,
        requested_guidance_depth: str,
        problem_id: int | None,
        source: str | None,
        source_message_id: int | None,
        candidate_problem_summary: str,
    ) -> AgentProblemWorkspace:
        if problem_id is not None:
            anchored_workspace = self.repo.find_workspace_by_problem_id(
                session_id=session_id,
                user_id=user_id,
                problem_id=problem_id,
            )
            if anchored_workspace is not None and anchored_workspace.id is not None:
                anchored_workspace = self.repo.activate_workspace_for_session(int(anchored_workspace.id))
                if source_message_id is not None:
                    self.repo.assign_visible_message_workspace(source_message_id, int(anchored_workspace.id))
                self.repo.update_workspace_guidance_depth(
                    int(anchored_workspace.id),
                    _guidance_depth_value(requested_guidance_depth),
                    True,
                )
                return anchored_workspace

        if intent in {"continue_problem", "non_problem"} and workspace is not None and not should_start_new_workspace:
            return workspace
        if workspace is not None and workspace.id is not None:
            self.repo.set_workspace_status(int(workspace.id), ProblemWorkspaceStatus.PAUSED)
        summary = candidate_problem_summary if candidate_problem_summary and (intent == "continue_problem" or should_start_new_workspace) else _workspace_summary_from_message(user_message)
        created = self.repo.create_workspace(
            session_id=session_id,
            user_id=user_id,
            title=_workspace_title(summary),
            guidance_depth=_guidance_depth_value(requested_guidance_depth),
            problem_id=problem_id,
            problem_summary=summary or None,
            source=source,
        )
        if source_message_id is not None and created.id is not None:
            self.repo.assign_visible_message_workspace(source_message_id, int(created.id))
        return created

    def _start_sidecars(
        self,
        *,
        context: dict[str, Any],
        sidecar_events: Queue[dict[str, Any]],
        sidecar_statuses: dict[str, str],
        intent: str,
        need_diagram: bool,
        can_deposit: bool,
    ) -> list[tuple[str, str]]:
        if intent == "non_problem" and not need_diagram:
            return []

        sidecars: list[tuple[str, str, SidecarFn | None, bool]]
        if intent == "non_problem":
            sidecars = [("diagram", "DiagramAgent", self.diagram_sidecar, True)]
        else:
            sidecars = [
                ("plan", "PlanStateAgent", self.plan_sidecar, True),
                ("diagram", "DiagramAgent", self.diagram_sidecar, need_diagram),
                ("whiteboard", "WhiteboardAgent", self.whiteboard_sidecar, can_deposit),
                ("memory", "MemoryAgent", self.memory_sidecar, True),
                ("compaction", "ProblemCompactionAgent", self.compaction_sidecar, True),
            ]
        started: list[tuple[str, str]] = []
        runnable_sidecars: list[tuple[str, SidecarFn]] = []
        for key, agent_name, sidecar, should_start in sidecars:
            if not should_start:
                continue
            if sidecar is None:
                sidecar_statuses[key] = "not_configured"
                continue
            sidecar_statuses[key] = "running"
            started.append((key, agent_name))
            runnable_sidecars.append((key, sidecar))
        if runnable_sidecars:
            if context.get("_serialize_sidecars"):
                _run_sidecar_batch(runnable_sidecars, context, sidecar_events, sidecar_statuses)
            else:
                Thread(
                    target=_run_sidecar_batch,
                    args=(runnable_sidecars, context, sidecar_events, sidecar_statuses),
                    daemon=True,
                ).start()
        return started

    def _collect_ready_sidecar_events(
        self,
        sidecar_events: Queue[dict[str, Any]],
        *,
        timeout_seconds: float | None = None,
        wait_for_keys: set[str] | None = None,
        wait_for_diagram: bool = False,
    ) -> Iterator[dict[str, Any]]:
        required_keys = set(wait_for_keys or set())
        seen_required_keys: set[str] = set()
        terminal_diagram_seen = False
        deadline = perf_counter() + (self.sidecar_collect_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds))
        while True:
            timeout = max(0.0, deadline - perf_counter())
            if required_keys and (not wait_for_diagram or terminal_diagram_seen) and required_keys.issubset(seen_required_keys):
                break
            if timeout <= 0:
                break
            try:
                event = sidecar_events.get(timeout=timeout)
                yield event
                terminal_key = _terminal_sidecar_key(event)
                if terminal_key in required_keys:
                    seen_required_keys.add(terminal_key)
                if _is_terminal_diagram_event(event):
                    terminal_diagram_seen = True
                    if required_keys.issubset(seen_required_keys):
                        break
            except Empty:
                break

    def _execute_mcp_request_events(
        self,
        request: MCPRequestBlock,
        *,
        context: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        if not request.need_mcp:
            return
        running_event = {
            "type": "mcp_status",
            "status": "running",
            "resource": request.resource,
            "problem_id": request.problem_id,
            "submission_id": request.submission_id,
            "agent": "MCPToolExecutor",
            "message": (
                "Simulating code submission."
                if self.submission_demo_mode and request.resource == "submit_code_feedback"
                else "正在调用平台 MCP 工具。"
            ),
        }
        yield running_event
        if self.submission_demo_mode and request.resource == "submit_code_feedback":
            sleep(uniform(5.0, 8.0))
            yield {
                "type": "mcp_status",
                "status": "completed",
                "resource": request.resource,
                "agent": "MCPToolExecutor",
                "message": "Demo submission simulation complete.",
                "visible_reply": "Demo simulation: AC (sample verdict only; no judge ran).",
            }
            return
        if self.mcp_executor is None:
            yield {
                "type": "mcp_status",
                "status": "failed",
                "resource": request.resource,
                "agent": "MCPToolExecutor",
                "error": "not_configured",
                "message": "MCP 工具执行器未配置。",
            }
            return
        emitted: list[dict[str, Any]] = []

        def emit(payload: dict[str, Any]) -> None:
            emitted.append(payload)

        try:
            reply = self.mcp_executor(request, context, emit)
        except Exception as exc:
            for payload in emitted:
                yield payload
            yield {
                "type": "mcp_status",
                "status": "failed",
                "resource": request.resource,
                "problem_id": request.problem_id,
                "submission_id": request.submission_id,
                "agent": "MCPToolExecutor",
                "error": type(exc).__name__,
                "message": f"平台 MCP 工具调用失败：{type(exc).__name__}",
            }
            return
        for payload in emitted:
            yield payload
        yield {
            "type": "mcp_status",
            "status": "completed" if reply else "failed",
            "resource": request.resource,
            "problem_id": request.problem_id,
            "submission_id": request.submission_id,
            "agent": "MCPToolExecutor",
            "message": "平台 MCP 工具调用完成。" if reply else "平台 MCP 工具未返回可用回复。",
            **({"visible_reply": reply} if reply else {}),
        }


def _run_sidecar(
    key: str,
    sidecar: SidecarFn,
    context: dict[str, Any],
    sidecar_events: Queue[dict[str, Any]],
    sidecar_statuses: dict[str, str],
) -> None:
    terminal_status: str | None = None

    def emit(payload: dict[str, Any]) -> None:
        nonlocal terminal_status
        if payload.get("type") == f"{key}_status":
            status = str(payload.get("status") or "")
            if status and status != "running":
                terminal_status = status
        sidecar_events.put(payload)

    try:
        sidecar(context, emit)
        if terminal_status is not None:
            sidecar_statuses[key] = terminal_status
        elif sidecar_statuses.get(key) == "running":
            sidecar_statuses[key] = "completed"
            sidecar_events.put(_sidecar_completed_status_event(key))
    except Exception as exc:  # pragma: no cover - defensive status surface
        sidecar_statuses[key] = "failed"
        sidecar_events.put(
            {
                "type": f"{key}_status",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )


def _is_terminal_diagram_event(event: dict[str, Any]) -> bool:
    if event.get("type") == "diagram_ready":
        return True
    return event.get("type") == "diagram_status" and event.get("status") != "running"


def _terminal_sidecar_key(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {"whiteboard_ready", "diagram_ready"}:
        return event_type.split("_", 1)[0]
    if event_type.endswith("_status") and event.get("status") != "running":
        return event_type.split("_", 1)[0]
    return None


def _latest_diagram_scene_id(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") != "diagram_ready":
            continue
        scene = event.get("scene")
        if isinstance(scene, dict) and isinstance(scene.get("scene_id"), str):
            return scene["scene_id"]
    return None


def _finalize_uncollected_sidecar_statuses(sidecar_statuses: dict[str, str]) -> None:
    for key, status in list(sidecar_statuses.items()):
        if status == "running":
            sidecar_statuses[key] = "pending"


def _run_sidecar_batch(
    sidecars: list[tuple[str, SidecarFn]],
    context: dict[str, Any],
    sidecar_events: Queue[dict[str, Any]],
    sidecar_statuses: dict[str, str],
) -> None:
    if context.get("_serialize_sidecars"):
        for key, sidecar in sidecars:
            _run_sidecar(key, sidecar, context, sidecar_events, sidecar_statuses)
        return
    for key, sidecar in sidecars:
        Thread(
            target=_run_sidecar,
            args=(key, sidecar, context, sidecar_events, sidecar_statuses),
            daemon=True,
        ).start()


def _chunk_visible_reply(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    current = ""
    for char in stripped:
        current += char
        if len(current) >= 12 or char in "。！？；，、,.!?;":
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _mcp_request_needs_runtime_execution(request: MCPRequestBlock, context: dict[str, Any]) -> bool:
    if not request.need_mcp:
        return False
    if request.resource != "problem_detail":
        return True
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    context_problem_id = problem_context.get("problem_id")
    if isinstance(context_problem_id, str) and context_problem_id.isdigit():
        context_problem_id = int(context_problem_id)
    return not (
        isinstance(context_problem_id, int)
        and context_problem_id == request.problem_id
        and bool(problem_context.get("raw_problem_text_available"))
        and bool(str(problem_context.get("problem_summary") or "").strip())
    )


def _workspace_summary_from_message(message: str) -> str:
    compact = "\n".join(line.strip() for line in message.strip().splitlines() if line.strip())
    if len(compact) <= 600:
        return compact
    return f"{compact[:597].rstrip()}..."


def _candidate_problem_summary(context: dict[str, Any]) -> str:
    problem_context = context.get("problem_context") if isinstance(context.get("problem_context"), dict) else {}
    summary = str(problem_context.get("problem_summary") or "").strip()
    return summary[:600].rstrip()


def _workspace_title(summary: str) -> str:
    if not summary:
        return "当前题目"
    first_line = summary.splitlines()[0].strip()
    if len(first_line) <= 32:
        return first_line
    return f"{first_line[:31]}..."


def _guidance_depth_value(value: str) -> GuidanceDepthValue:
    normalized = (value or "direct").strip().lower()
    try:
        return GuidanceDepthValue(normalized)
    except ValueError:
        return GuidanceDepthValue.DIRECT


def _sidecar_status_events(started_sidecars: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "type": f"{key}_status",
            "status": "running",
            "agent": agent_name,
            "message": f"正在调用{agent_name}。",
        }
        for key, agent_name in started_sidecars
    ]


def _sidecar_completed_status_event(key: str) -> dict[str, Any]:
    agent_name = {
        "plan": "PlanStateAgent",
        "diagram": "DiagramAgent",
        "whiteboard": "WhiteboardAgent",
        "memory": "MemoryAgent",
        "compaction": "ProblemCompactionAgent",
    }.get(key, key)
    message = "题目规划已更新。" if key == "plan" else f"{agent_name} 调用完成。"
    return {
        "type": f"{key}_status",
        "status": "completed",
        "agent": agent_name,
        "message": message,
    }


def _sidecar_collect_timeout_seconds(
    started_sidecars: list[tuple[str, str]],
    *,
    base_timeout_seconds: float,
    required_timeout_seconds: float,
    diagram_started: bool,
    wait_for_all: bool,
) -> float:
    if not started_sidecars:
        return 0.0
    started_keys = {key for key, _agent_name in started_sidecars}
    if wait_for_all:
        return max(15.0, required_timeout_seconds)
    if "plan" in started_keys and "whiteboard" in started_keys:
        return max(15.0 if diagram_started else base_timeout_seconds, required_timeout_seconds * 2)
    if diagram_started:
        return max(15.0, required_timeout_seconds)
    if "plan" in started_keys:
        return max(base_timeout_seconds, required_timeout_seconds)
    return base_timeout_seconds


def _sidecar_terminal_wait_keys(
    started_sidecars: list[tuple[str, str]],
    *,
    diagram_started: bool,
    wait_for_all: bool = False,
) -> set[str]:
    if wait_for_all:
        return {key for key, _agent_name in started_sidecars}
    started_keys = {key for key, _agent_name in started_sidecars}
    if not diagram_started:
        return {
            key
            for key in ("plan", "whiteboard")
            if key in started_keys
        }
    return {
        key
        for key in ("plan", "whiteboard", "memory", "compaction")
        if key in started_keys
    }


def _apply_sidecar_completion_status(event: dict[str, Any], sidecar_statuses: dict[str, str]) -> None:
    event_type = str(event.get("type") or "")
    prefix = event_type.split("_", 1)[0]
    if prefix not in sidecar_statuses:
        return
    if event_type == f"{prefix}_status":
        status = str(event.get("status") or "")
        if status in {"completed", "skipped", "failed"}:
            sidecar_statuses[prefix] = status
        return
    sidecar_statuses[prefix] = "completed"


def _record_sidecar_ready_metric(event: dict[str, Any], metrics: dict[str, int], elapsed_ms: int) -> None:
    event_type = str(event.get("type") or "")
    prefix = event_type.split("_", 1)[0]
    metric_name = {
        "plan": "plan_ready_latency_ms",
        "diagram": "diagram_ready_latency_ms",
        "whiteboard": "whiteboard_ready_latency_ms",
        "memory": "memory_ready_latency_ms",
        "compaction": "compaction_ready_latency_ms",
    }.get(prefix)
    if metric_name is None:
        return
    if event_type.endswith("_status") and event.get("status") == "running":
        return
    metrics.setdefault(metric_name, elapsed_ms)
