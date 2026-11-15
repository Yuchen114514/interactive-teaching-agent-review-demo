from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, TimeoutError as FutureTimeoutError, wait
from dataclasses import dataclass, replace
import inspect
import re
from time import perf_counter
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.agent_store import AgentStore
from app.diagram_protocol import DiagramInlinePayloadModel, DiagramSceneModel, DiagramTeachingBundleModel
from app.diagram_tools import diagram_verify_tool
from app.models import (
    AgentBoardNode,
    AgentMode,
    AgentSession,
    AgentSessionSource,
    AgentSessionStatus,
    GuidanceDepthValue,
    ProblemWorkspaceStatus,
    TeachingStepStatus,
)
from app.repositories.teaching import TeachingRepository
from app.schemas import (
    AgentTraceResponse,
    GuidanceDepth,
    TeachingStateResponse,
    TeachingTurnRequest,
    TeachingTurnResponse,
)
from app.teaching.agent_providers import (
    TeachingAgentProviderEvent,
    TeachingAgentProviderUnavailableError,
    TeachingAgentProviderTelemetry,
    TeachingAgentProviders,
    teaching_agent_primary_capability,
)
from app.teaching.agent_nodes import (
    AgentNodeResult,
    AlgorithmIdentifierNode,
    AlgorithmPrimer,
    AlgorithmPrimerNode,
    BoardworkNode,
    CodingNode,
    DebugNode,
    DiagramNode,
    EncouragementNode,
    HintPolicyNode,
    MemoryManagerNode,
    MemoryRetrieverNode,
    PlanRevisionNode,
    PlanDecomposerNode,
    ProblemDecision,
    ProblemIdentifierNode,
    TutorOutput,
    TutorNode,
)
from app.teaching.context_builder import TeachingContextBuilder
from app.teaching.memory_consolidator import MemoryConsolidator
from app.teaching.plan_revision_policy import PlanRevisionDecision
from app.teaching.supervisor import TeachingSupervisor
from app.teaching.taxonomy import AlgorithmFamily, AlgorithmTaxonomy, load_default_taxonomy


OPTIONAL_SPECIALIST_EXECUTION_ORDER = ("DiagramAgent", "CodingAgent", "DebugAgent", "BoardworkAgent")
OPTIONAL_SPECIALIST_MAX_WORKERS = 4
DEFAULT_OPTIONAL_SPECIALIST_RESULT_TIMEOUT_SECONDS = 0.25
REFERENCE_TOPIC_STOPWORDS = frozenset(
    {
        "上面",
        "前面",
        "那道",
        "那题",
        "这道",
        "这题",
        "题目",
        "如果",
        "只有",
        "个人",
        "怎么",
        "处理",
        "一下",
        "状态",
        "当前",
        "样例",
        "输入",
        "输出",
    }
)


@dataclass(frozen=True)
class OptionalSpecialistJobResult:
    result: Any
    payload: dict
    latency_ms: int
    completed_at: float


@dataclass(frozen=True)
class TimedNodeResult:
    value: Any
    latency_ms: int


@dataclass(frozen=True)
class AlgorithmFamilyCacheLookup:
    family: AlgorithmFamily | None
    context_key: str
    quality_challenge: dict[str, str] | None = None


@dataclass(frozen=True)
class AlgorithmPrimerCacheLookup:
    primer: AlgorithmPrimer | None = None
    original_source: str | None = None
    quality_challenge: dict[str, str] | None = None


def _to_model_depth(depth: GuidanceDepth) -> GuidanceDepthValue:
    return GuidanceDepthValue(depth.value)


class TeachingOrchestrator:
    """Minimal v2 teaching orchestrator skeleton.

    This first slice creates explicit state and trace without invoking LLMs.
    Specialist agents will be attached behind this interface in later tasks.
    """

    def __init__(
        self,
        db: Session,
        *,
        agent_providers: TeachingAgentProviders | None = None,
        taxonomy: AlgorithmTaxonomy | None = None,
        supervisor_provider_timeout_seconds: float = 0.25,
        optional_specialist_result_timeout_seconds: float = DEFAULT_OPTIONAL_SPECIALIST_RESULT_TIMEOUT_SECONDS,
        memory_consolidation_timeout_seconds: float = 2.0,
        require_model_provider: bool = False,
    ):
        providers = agent_providers or TeachingAgentProviders()
        self.require_model_provider = require_model_provider
        self.optional_specialist_result_timeout_seconds = max(0.001, optional_specialist_result_timeout_seconds)
        self.optional_specialist_result_timeout_ms = int(self.optional_specialist_result_timeout_seconds * 1000)
        self.memory_consolidation_timeout_seconds = max(0.001, memory_consolidation_timeout_seconds)
        self.memory_consolidation_timeout_ms = int(self.memory_consolidation_timeout_seconds * 1000)
        self.provider_telemetry = providers.telemetry or TeachingAgentProviderTelemetry()
        self.provider_capabilities = {
            "TeachingSupervisor": providers.supervisor_schedule is not None
            or providers.supervisor_topology is not None
            or providers.supervisor_route is not None,
            "ProblemIdentifierAgent": providers.problem_decision is not None,
            "AlgorithmIdentifierAgent": providers.algorithm_family is not None,
            "AlgorithmPrimerAgent": providers.algorithm_primer is not None,
            "PlanRevisionAgent": providers.plan_revision is not None,
            "MemoryManagerAgent": providers.workspace_summary is not None,
            "PlanDecomposerAgent": providers.plan is not None,
            "HintPolicyAgent": providers.hint_policy is not None,
            "EncouragementAgent": providers.encouragement_strategy is not None,
            "TutorAgent": providers.tutor_response is not None,
            "MemoryConsolidatorAgent": providers.memory_candidates is not None,
            "BoardworkAgent": providers.boardwork_handoff is not None,
            "DiagramAgent": providers.diagram_handoff is not None,
            "CodingAgent": providers.coding_handoff is not None,
            "DebugAgent": providers.debug_handoff is not None,
        }
        self.repo = TeachingRepository(db)
        self.taxonomy = taxonomy or load_default_taxonomy()
        self.problem_node = ProblemIdentifierNode(decision_provider=providers.problem_decision)
        self.memory_manager_node = MemoryManagerNode(summary_provider=providers.workspace_summary)
        self.memory_node = MemoryRetrieverNode()
        self.algorithm_node = AlgorithmIdentifierNode(self.taxonomy, family_provider=providers.algorithm_family)
        self.primer_node = AlgorithmPrimerNode(primer_provider=providers.algorithm_primer)
        self.plan_revision_node = PlanRevisionNode(decision_provider=providers.plan_revision)
        self.plan_node = PlanDecomposerNode(plan_provider=providers.plan)
        self.hint_policy_node = HintPolicyNode(decision_provider=providers.hint_policy)
        self.encouragement_node = EncouragementNode(strategy_provider=providers.encouragement_strategy)
        self.tutor_node = TutorNode(response_provider=providers.tutor_response)
        self.boardwork_node = BoardworkNode(handoff_provider=providers.boardwork_handoff)
        self.coding_node = CodingNode(handoff_provider=providers.coding_handoff)
        self.debug_node = DebugNode(handoff_provider=providers.debug_handoff)
        self.diagram_node = DiagramNode(handoff_provider=providers.diagram_handoff)
        self.context_builder = TeachingContextBuilder(self.repo)
        self.memory_consolidator = MemoryConsolidator(candidate_provider=providers.memory_candidates)
        self.supervisor = TeachingSupervisor(
            schedule_provider=providers.supervisor_schedule,
            route_provider=providers.supervisor_route,
            topology_provider=providers.supervisor_topology,
            provider_timeout_seconds=supervisor_provider_timeout_seconds,
            deterministic_common_path=True,
            deterministic_common_path_overrides_provider=require_model_provider,
        )
        self.agent_store = AgentStore(db)
        self.agent_nodes = [
            self.problem_node,
            self.algorithm_node,
        ]

    def _provider_enabled_for(self, agent_name: str) -> bool:
        return self.provider_capabilities.get(agent_name, False)

    def _raise_provider_unavailable(
        self,
        agent_name: str,
        *,
        capability: str | None = None,
        outcome: str,
        provider_backend: str = "agno",
        details: dict[str, Any] | None = None,
    ) -> None:
        raise TeachingAgentProviderUnavailableError(
            agent_name=agent_name,
            capability=capability or teaching_agent_primary_capability(agent_name) or "",
            outcome=outcome,
            provider_backend=provider_backend,
            details=details,
        )

    def _require_provider_agent_ready(self, agent_name: str) -> None:
        if self.require_model_provider and not self._provider_enabled_for(agent_name):
            self._raise_provider_unavailable(
                agent_name,
                outcome="provider_agent_missing",
                provider_backend="disabled",
            )

    def _provider_rejection_event_for(self, agent_name: str) -> TeachingAgentProviderEvent | None:
        event = self.provider_telemetry.latest_for(agent_name)
        if event is not None and event.accepted is True:
            return event
        return None

    def _raise_rejected_provider_output(
        self,
        agent_name: str,
        *,
        outcome: str = "provider_output_rejected",
        details: dict[str, Any] | None = None,
    ) -> None:
        event = self._provider_rejection_event_for(agent_name)
        if event is None:
            self._raise_provider_unavailable(
                agent_name,
                outcome=outcome,
                details=details,
            )
        self._raise_provider_unavailable(
            agent_name,
            capability=event.capability,
            outcome=outcome,
            provider_backend=event.provider_backend,
            details={
                "provider_outcome": event.outcome,
                "accepted": event.accepted,
                "latency_ms": event.latency_ms,
                **event.payload,
                **(details or {}),
            },
        )

    def _enforce_strict_provider_source(
        self,
        agent_name: str,
        *,
        source: str | None,
    ) -> None:
        if not self.require_model_provider:
            return
        if source in {"agent", "cache", "structured", "skipped"}:
            return
        self._require_provider_agent_ready(agent_name)
        details: dict[str, Any] = {}
        if agent_name == self.memory_consolidator.agent_name and self.memory_consolidator.last_rejection_reason:
            details["rejection_reason"] = self.memory_consolidator.last_rejection_reason
        if agent_name == self.problem_node.agent_name and self.problem_node.last_rejection_reason:
            details["rejection_reason"] = self.problem_node.last_rejection_reason
        if agent_name == self.tutor_node.agent_name and self.tutor_node.last_rejection_reason:
            details["rejection_reason"] = self.tutor_node.last_rejection_reason
            if self.tutor_node.last_rejection_details:
                details["rejection_details"] = self.tutor_node.last_rejection_details
        if self._provider_rejection_event_for(agent_name) is not None:
            self._raise_rejected_provider_output(agent_name, details=details or None)
        self._raise_provider_unavailable(
            agent_name,
            outcome="provider_output_unavailable",
            details=details or None,
        )

    def _enforce_strict_supervisor_decision(
        self,
        graph: SupervisorGraph,
        *,
        phase: str,
    ) -> None:
        if not self.require_model_provider:
            return
        decision = graph.topology_decision if phase == "topology" else graph.routing_decisions
        source_key = "topology_source" if phase == "topology" else "route_source"
        if decision.get(source_key) in {"agent", "deterministic"}:
            return
        self._require_provider_agent_ready("TeachingSupervisor")
        trace_provider = graph.trace_payload.get("provider") if isinstance(graph.trace_payload, dict) else None
        capability = (
            str(trace_provider.get("capability"))
            if isinstance(trace_provider, dict) and isinstance(trace_provider.get("capability"), str)
            else teaching_agent_primary_capability("TeachingSupervisor")
        )
        reason_key = "topology_reason" if phase == "topology" else "route_reason"
        outcome = (
            "timeout"
            if isinstance(trace_provider, dict)
            and trace_provider.get("rejection_reason") == "provider_timeout"
            else "provider_output_rejected"
            if isinstance(trace_provider, dict)
            and trace_provider.get("outcome") in {"candidate_rejected", "route_rejected"}
            else "provider_output_unavailable"
        )
        event = self.provider_telemetry.latest_for("TeachingSupervisor", capability=capability)
        self._raise_provider_unavailable(
            "TeachingSupervisor",
            capability=capability,
            outcome=outcome,
            provider_backend=event.provider_backend if event is not None else "agno",
            details={
                "phase": phase,
                "reason": decision.get(reason_key),
                "trace_provider": trace_provider if isinstance(trace_provider, dict) else {},
            },
        )

    def _run_optional_specialist_job(self, runner, *args, **kwargs) -> OptionalSpecialistJobResult:
        started_at = perf_counter()
        result, payload = runner(*args, **kwargs)
        completed_at = perf_counter()
        latency_ms = max(0, int((completed_at - started_at) * 1000))
        return OptionalSpecialistJobResult(
            result=result,
            payload=payload,
            latency_ms=latency_ms,
            completed_at=completed_at,
        )

    def _run_timed_node(self, runner, *args, **kwargs) -> TimedNodeResult:
        started_at = perf_counter()
        value = runner(*args, **kwargs)
        latency_ms = max(0, int((perf_counter() - started_at) * 1000))
        return TimedNodeResult(value=value, latency_ms=latency_ms)

    def _trace_payload_for(
        self,
        agent_name: str,
        *,
        source: str | None = None,
        allow_rejected_provider_fallback: bool = False,
        provider_rejection_reason: str | None = None,
        policy_decision_reason: str | None = None,
    ) -> dict:
        if source == "cache":
            return {
                "provider": {
                    "capability": teaching_agent_primary_capability(agent_name),
                    "backend": "cache",
                    "outcome": "cache_hit",
                    "accepted": True,
                    "latency_ms": 0,
                }
            }
        if source == "skipped":
            return {
                "provider": {
                    "capability": teaching_agent_primary_capability(agent_name),
                    "backend": "skipped",
                    "outcome": "not_applicable",
                    "accepted": True,
                    "latency_ms": 0,
                }
            }
        if source == "structured":
            return {
                "provider": {
                    "capability": teaching_agent_primary_capability(agent_name),
                    "backend": "structured",
                    "outcome": "local_teaching_contract",
                    "accepted": True,
                    "latency_ms": 0,
                }
            }
        payload = self.provider_telemetry.trace_payload_for(agent_name)
        if provider_rejection_reason:
            provider = payload.get("provider") if isinstance(payload, dict) else None
            provider_details = (
                {
                    key: value
                    for key, value in provider.items()
                    if key
                    in {
                        "capability",
                        "backend",
                        "latency_ms",
                        "json_keys",
                        "prompt_size_bytes",
                        "payload_size_bytes",
                        "compact_payload_keys",
                    }
                }
                if isinstance(provider, dict)
                else {}
            )
            return {
                "provider": {
                    "capability": provider_details.get(
                        "capability",
                        teaching_agent_primary_capability(agent_name),
                    ),
                    "backend": provider_details.get("backend", "custom"),
                    **{
                        key: value
                        for key, value in provider_details.items()
                        if key not in {"capability", "backend"}
                    },
                    "outcome": "provider_output_rejected",
                    "accepted": False,
                    "fallback_reason": "provider_output_rejected",
                    "rejection_reason": provider_rejection_reason,
                }
            }
        if payload:
            provider = payload.get("provider")
            if source != "agent" and isinstance(provider, dict) and provider.get("accepted") is True:
                if allow_rejected_provider_fallback:
                    return {
                        "provider": {
                            **{
                                key: value
                                for key, value in provider.items()
                                if key
                                in {
                                    "capability",
                                    "backend",
                                    "latency_ms",
                                    "json_keys",
                                    "prompt_size_bytes",
                                    "payload_size_bytes",
                                    "compact_payload_keys",
                                }
                            },
                            "outcome": "provider_output_rejected",
                            "accepted": False,
                            "fallback_reason": "provider_output_rejected",
                        }
                    }
                raise TeachingAgentProviderUnavailableError(
                    agent_name=agent_name,
                    capability=str(provider.get("capability") or teaching_agent_primary_capability(agent_name) or ""),
                    outcome="provider_output_rejected",
                    provider_backend=str(provider.get("backend") or "unknown"),
                    details={
                        key: value
                        for key, value in provider.items()
                        if key
                        in {
                            "capability",
                            "backend",
                            "outcome",
                            "accepted",
                            "latency_ms",
                            "json_keys",
                            "prompt_size_bytes",
                            "payload_size_bytes",
                            "compact_payload_keys",
                        }
                    },
                )
            return payload
        if source == "agent":
            return {
                "provider": {
                    "capability": teaching_agent_primary_capability(agent_name),
                    "backend": "custom",
                    "outcome": "custom_provider_result",
                    "accepted": True,
                    "latency_ms": 0,
                }
            }
        capability = teaching_agent_primary_capability(agent_name)
        if source == "policy" and policy_decision_reason and capability is not None:
            return {
                "provider": {
                    "capability": capability,
                    "backend": "policy",
                    "outcome": "policy_override",
                    "accepted": True,
                    "latency_ms": 0,
                    "reason": policy_decision_reason,
                }
            }
        if source in {"policy", "fallback", "taxonomy"} and capability is not None:
            return {
                "provider": {
                    "capability": capability,
                    "backend": "disabled",
                    "outcome": "fallback_applied",
                    "accepted": False,
                    "fallback_reason": "provider_disabled",
                }
            }
        return {}

    def _tutor_trace_payload(self, agent_name: str, *, source: str | None) -> dict:
        payload = self._trace_payload_for(
            agent_name,
            source=source,
            allow_rejected_provider_fallback=True,
        )
        provider = payload.get("provider") if isinstance(payload, dict) else None
        if isinstance(provider, dict) and self.tutor_node.last_provider_call_count > 0:
            provider["call_count"] = self.tutor_node.last_provider_call_count
            provider["repair_attempt_count"] = self.tutor_node.last_repair_attempt_count
            provider["last_rejection_reason"] = self.tutor_node.last_rejection_reason
        return payload

    def _policy_tutor_output(
        self,
        request: TeachingTurnRequest,
        *,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
        primer: str,
        show_primer_before_plan: bool = True,
    ) -> tuple[AgentNodeResult, TutorOutput]:
        depth = request.guidance_depth.value if request.guidance_depth is not None else "socratic"
        prefix = f"{policy.encouragement.strip()}\n" if policy.encouragement else ""
        primer_text = primer.strip() if show_primer_before_plan else ""
        if depth == "minimal":
            visible_text = f"{prefix}最小提示：{primer_text or current_step.title}"
        elif policy.action == "give_hint":
            visible_text = f"{prefix}{primer_text + chr(10) if primer_text else ''}换个角度看当前步骤：{current_step.title}。"
        elif policy.action == "summarize":
            visible_text = f"{prefix}{primer_text + chr(10) if primer_text else ''}先小结：{current_step.title}。"
        elif policy.action == "confirm_and_advance":
            visible_text = f"{prefix}继续推进到当前步骤：{current_step.title}。"
        elif policy.action == "enter_code":
            visible_text = f"{prefix}实现计划：先把当前步骤收束成变量、状态和边界检查。"
        elif policy.action == "enter_debug":
            visible_text = f"{prefix}debug 计划：先定位当前步骤的输入、状态变化和边界现象。"
        elif primer_text:
            visible_text = f"{prefix}{primer_text}\n我们先聚焦当前步骤：{current_step.title}。"
        else:
            visible_text = f"{prefix}我们先聚焦当前步骤：{current_step.title}。"
        if policy.student_state == "progressing" and "关键点" not in visible_text:
            visible_text = f"{visible_text} 继续围绕这个关键点推进。"
        if depth != "minimal" and policy.action in {"ask", "give_hint"} and policy.max_question_count > 0:
            visible_text = f"{visible_text} 你可以先说说这里最不确定的一点。"
        step_status = (
            "ready_for_code"
            if policy.action == "enter_code"
            else "ready_for_debug"
            if policy.action == "enter_debug"
            else "in_progress"
        )
        output = TutorOutput(
            visible_text=visible_text.strip(),
            step_delta={"status": step_status},
            student_feedback=policy.student_state,
            needs_boardwork=True,
            needs_diagram=self.tutor_node._requests_diagram(request.message),
            upstream_quality_flags=[],
        )
        result = AgentNodeResult(
            agent_name=self.tutor_node.agent_name,
            status="fallback",
            input_summary="Generated visible tutor response from local teaching policy.",
            output_summary=(
                f"{depth}: current step response generated; "
                f"needs_boardwork={output.needs_boardwork}; needs_diagram={output.needs_diagram}; "
                "quality_flags=0; source=policy."
            ),
        )
        return result, output

    def _merge_provider_telemetry(
        self,
        trace_payload: dict,
        *,
        agent_name: str,
        capability: str,
    ) -> dict:
        telemetry_payload = self.provider_telemetry.trace_payload_for(agent_name, capability=capability)
        if not telemetry_payload or "provider" not in trace_payload:
            return trace_payload
        provider = dict(telemetry_payload["provider"])
        decision_provider = trace_payload["provider"]
        if not isinstance(decision_provider, dict):
            return trace_payload
        if "outcome" in provider:
            provider["provider_outcome"] = provider.pop("outcome")
        if "accepted" in provider:
            provider["provider_accepted"] = provider.pop("accepted")
        if "latency_ms" in provider:
            provider["provider_latency_ms"] = provider.pop("latency_ms")
        provider.update(decision_provider)
        return {"provider": provider}

    def _record_provider_call_audits(
        self,
        *,
        session_id: int,
        workspace_id: int | None,
        turn_index: int,
    ) -> None:
        for event in self.provider_telemetry.events():
            payload = event.payload if isinstance(event.payload, dict) else {}
            fallback_reason = payload.get("fallback_reason")
            error_type = payload.get("error_type")
            self.repo.record_provider_call_trace(
                session_id=session_id,
                workspace_id=workspace_id,
                turn_index=turn_index,
                agent_name=event.agent_name,
                capability=event.capability,
                provider_backend=event.provider_backend,
                outcome=event.outcome,
                accepted=event.accepted,
                latency_ms=event.latency_ms,
                prompt_size_bytes=payload.get("prompt_size_bytes")
                if isinstance(payload.get("prompt_size_bytes"), int)
                else None,
                payload_size_bytes=payload.get("payload_size_bytes")
                if isinstance(payload.get("payload_size_bytes"), int)
                else None,
                fallback_reason=fallback_reason if isinstance(fallback_reason, str) else None,
                error_type=error_type if isinstance(error_type, str) else None,
                payload=payload,
            )

    def _write_blackboard(
        self,
        *,
        workspace_id: int,
        session_id: int,
        turn_index: int,
        event_type: str,
        actor: str,
        entry_type: str,
        content: str,
        payload: dict,
        step_id: int | None = None,
    ) -> None:
        event = self.repo.record_event(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=turn_index,
            event_type=event_type,
            actor=actor,
            step_id=step_id,
            payload=payload,
        )
        self.repo.record_blackboard_entry(
            workspace_id=workspace_id,
            session_id=session_id,
            step_id=step_id,
            entry_type=entry_type,
            content=content,
            writer_agent=actor,
            source_event_id=event.id,
            payload=payload,
        )

    def _serialize_event(self, event) -> dict:
        return {
            "event_id": event.id,
            "workspace_id": event.workspace_id,
            "session_id": event.session_id,
            "turn_index": event.turn_index,
            "event_type": event.event_type,
            "actor": event.actor,
            "step_id": event.step_id,
            "payload": event.payload,
            "confidence": event.confidence,
            "visible_to_user": event.visible_to_user,
            "created_at": event.create_time,
        }

    def _build_collaboration_context(
        self,
        *,
        upstream_results: list,
        context_snapshot: dict,
    ) -> dict:
        upstream_agents = []
        for result in upstream_results:
            agent_name = getattr(result, "agent_name", None)
            output_summary = getattr(result, "output_summary", None)
            if isinstance(agent_name, str) and isinstance(output_summary, str) and output_summary.strip():
                upstream_agents.append(
                    {
                        "agent": agent_name,
                        "summary": output_summary.strip()[:240],
                    }
                )
        blackboard_summary = context_snapshot.get("blackboard_summary") if isinstance(context_snapshot, dict) else []
        if not isinstance(blackboard_summary, list):
            blackboard_summary = []
        upstream_quality_flags = (
            context_snapshot.get("upstream_quality_flags") if isinstance(context_snapshot, dict) else []
        )
        if not isinstance(upstream_quality_flags, list):
            upstream_quality_flags = []
        return {
            "upstream_agents": upstream_agents,
            "context_snapshot": self._sanitize_collaboration_snapshot(context_snapshot),
            "blackboard_summary": [
                item.strip()[:240] for item in blackboard_summary if isinstance(item, str) and item.strip()
            ],
            "upstream_quality_flags": [
                flag for flag in upstream_quality_flags if isinstance(flag, dict)
            ],
        }

    def _sanitize_collaboration_snapshot(self, context_snapshot: dict) -> dict:
        if not isinstance(context_snapshot, dict):
            return {}

        current_step = context_snapshot.get("current_step")
        if not isinstance(current_step, dict):
            current_step = {}
        memory_policy = context_snapshot.get("memory_policy")
        if not isinstance(memory_policy, dict):
            memory_policy = {}
        workspace_summary = context_snapshot.get("workspace_summary")
        if not isinstance(workspace_summary, dict):
            workspace_summary = {}
        compaction_policy = context_snapshot.get("compaction_policy")
        if not isinstance(compaction_policy, dict):
            compaction_policy = {}

        return {
            "workspace_id": context_snapshot.get("workspace_id"),
            "problem_key": context_snapshot.get("problem_key"),
            "problem_id": context_snapshot.get("problem_id"),
            "problem_title": context_snapshot.get("problem_title"),
            "problem_summary": context_snapshot.get("problem_summary"),
            "guidance_depth": context_snapshot.get("guidance_depth"),
            "algorithm_family": context_snapshot.get("algorithm_family"),
            "current_step": {
                "step_id": current_step.get("step_id"),
                "step_key": current_step.get("step_key"),
                "title": current_step.get("title"),
                "repeat_count": current_step.get("repeat_count"),
                "status": current_step.get("status"),
                "mastery_state": current_step.get("mastery_state"),
            },
            "recent_turn_count": context_snapshot.get("recent_turn_count"),
            "memory_policy": {
                "long_term_enabled": memory_policy.get("long_term_enabled"),
                "retrieved_memory_count": memory_policy.get("retrieved_memory_count"),
            },
            "workspace_summary": {
                "available": workspace_summary.get("available"),
                "preview": workspace_summary.get("preview"),
            },
            "compaction_policy": {
                "current_step_only": compaction_policy.get("current_step_only"),
                "event_window_limit": compaction_policy.get("event_window_limit"),
                "blackboard_summary_limit": compaction_policy.get("blackboard_summary_limit"),
                "raw_recent_turn_limit": compaction_policy.get("raw_recent_turn_limit"),
                "compacted_turn_count": compaction_policy.get("compacted_turn_count"),
            },
        }

    def _current_upstream_quality_flags(self, workspace_id: int, *, limit: int = 8) -> list[dict[str, str]]:
        entries = self.repo.list_blackboard_entries(workspace_id)
        flags: list[dict[str, str]] = []
        for entry in reversed(entries):
            if entry.entry_type != "agent_quality_flag":
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            flag = {
                key: payload[key]
                for key in ("flagged_agent", "severity", "reason", "suggested_action")
                if isinstance(payload.get(key), str)
            }
            if len(flag) == 4:
                flags.append(flag)
            if len(flags) >= limit:
                break
        flags.reverse()
        return flags

    def _write_quality_flags_from_payload(
        self,
        *,
        workspace_id: int,
        session_id: int,
        turn_index: int,
        reviewing_agent: str,
        payload: dict,
        step_id: int | None,
    ) -> None:
        flags = payload.get("upstream_quality_flags")
        if not isinstance(flags, list):
            return
        for quality_flag in flags:
            if not isinstance(quality_flag, dict):
                continue
            flagged_agent = quality_flag.get("flagged_agent")
            severity = quality_flag.get("severity")
            if not isinstance(flagged_agent, str) or not isinstance(severity, str):
                continue
            quality_payload = {
                **quality_flag,
                "reviewing_agent": reviewing_agent,
            }
            self._write_blackboard(
                workspace_id=workspace_id,
                session_id=session_id,
                turn_index=turn_index,
                event_type="agent_quality_flagged",
                actor=reviewing_agent,
                entry_type="agent_quality_flag",
                content=f"{severity}:{flagged_agent}",
                payload=quality_payload,
                step_id=step_id,
            )

    def _current_blackboard_summary(self, workspace_id: int, *, limit: int = 8) -> list[str]:
        entries = self.repo.list_blackboard_entries(workspace_id)
        return [
            f"{entry.entry_type}: {entry.content}"
            for entry in entries[-limit:]
            if isinstance(entry.entry_type, str) and isinstance(entry.content, str)
        ]

    def _algorithm_candidate_space_key(self) -> str:
        return ",".join(self.taxonomy.family_ids())

    def _cache_problem_key(self, problem_key: str | None) -> str:
        return problem_key or "unstructured"

    def _workspace_title_from_request(self, request: TeachingTurnRequest) -> str:
        problem_title, problem_summary = self._problem_summary_from_problem_id(request.problem_id)
        if problem_title:
            if len(problem_title) <= 32:
                return problem_title
            return f"{problem_title[:31]}…"
        summary = problem_summary or self._problem_summary_from_request(request)
        if not summary:
            return "当前题目"
        first_line = summary.splitlines()[0].strip()
        if not first_line:
            return "当前题目"
        if len(first_line) <= 32:
            return first_line
        return f"{first_line[:31]}…"

    def _problem_summary_from_request(self, request: TeachingTurnRequest) -> str:
        compact = "\n".join(line.strip() for line in request.message.strip().splitlines() if line.strip())
        if not compact:
            return ""
        if len(compact) <= 600:
            return compact
        return f"{compact[:597].rstrip()}..."

    def _problem_summary_from_problem_id(self, problem_id: int | None) -> tuple[str | None, str | None]:
        if problem_id is None:
            return None, None
        try:
            row = self.repo.db.exec(
                text("SELECT title, content FROM problem WHERE id = :problem_id LIMIT 1"),
                params={"problem_id": problem_id},
            ).first()
        except SQLAlchemyError:
            return None, None
        if row is None:
            return None, None
        title = (str(row[0]) if row[0] is not None else "").strip() or None
        content = "\n".join(line.strip() for line in (str(row[1]) if row[1] is not None else "").splitlines() if line.strip())
        if not content:
            return title, title
        if len(content) > 600:
            content = f"{content[:597].rstrip()}..."
        return title, content

    def _can_use_workspace_algorithm_cache(self, problem_key: str | None) -> bool:
        return problem_key in {None, "unknown", "unstructured"}

    def _algorithm_cache_context_key(self, *, workspace_id: int, problem_key: str | None) -> str:
        return (
            f"workspace:{workspace_id}|problem:{self._cache_problem_key(problem_key)}|"
            f"candidate_space:{self._algorithm_candidate_space_key()}"
        )

    def _latest_quality_challenge_for(
        self,
        *,
        workspace_id: int,
        agent_name: str,
        after_entry_id: int | None,
    ) -> dict[str, str] | None:
        if after_entry_id is None:
            return None
        for entry in reversed(self.repo.list_blackboard_entries(workspace_id)):
            if entry.entry_type != "agent_quality_flag":
                continue
            if entry.id is not None and entry.id <= after_entry_id:
                break
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if payload.get("flagged_agent") != agent_name:
                continue
            severity = payload.get("severity")
            reason = payload.get("reason")
            suggested_action = payload.get("suggested_action")
            if not (
                isinstance(severity, str)
                and isinstance(reason, str)
                and isinstance(suggested_action, str)
            ):
                continue
            return {
                "flagged_agent": agent_name,
                "severity": severity,
                "reason": reason,
                "suggested_action": suggested_action,
            }
        return None

    def _plan_cache_context_key(
        self,
        *,
        workspace_id: int,
        problem_key: str | None,
        family: AlgorithmFamily,
        depth: str,
        plan_id: int,
    ) -> str:
        return (
            f"workspace:{workspace_id}|problem:{self._cache_problem_key(problem_key)}|"
            f"family:{family.family_id}|depth:{depth}|plan:{plan_id}"
        )

    def _cache_bypass_payload(self, quality_challenge: dict[str, str] | None) -> dict[str, str]:
        if not quality_challenge:
            return {}
        return {
            "cache_bypass_reason": "quality_challenge",
            "cache_bypass_agent": quality_challenge["flagged_agent"],
        }

    def _with_cache_bypass_payload(self, trace_payload: dict, quality_challenge: dict[str, str] | None) -> dict:
        bypass_payload = self._cache_bypass_payload(quality_challenge)
        if not bypass_payload:
            return trace_payload
        provider = trace_payload.get("provider")
        if not isinstance(provider, dict):
            return trace_payload
        return {
            **trace_payload,
            "provider": {
                **provider,
                **bypass_payload,
            },
        }

    def _cached_algorithm_family(
        self,
        *,
        workspace_id: int,
        problem_key: str | None,
    ) -> AlgorithmFamilyCacheLookup:
        expected_candidate_space_key = self._algorithm_candidate_space_key()
        expected_context_key = self._algorithm_cache_context_key(
            workspace_id=workspace_id,
            problem_key=problem_key,
        )
        allow_workspace_anchor = self._can_use_workspace_algorithm_cache(problem_key)
        for entry in reversed(self.repo.list_blackboard_entries(workspace_id)):
            if entry.entry_type != "algorithm_family":
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if payload.get("candidate_space_key") != expected_candidate_space_key:
                continue
            cached_context_key = payload.get("cache_context_key")
            if allow_workspace_anchor:
                if not (
                    isinstance(cached_context_key, str)
                    and cached_context_key.startswith(f"workspace:{workspace_id}|problem:")
                ):
                    continue
                context_key = cached_context_key
            else:
                if payload.get("problem_key") != problem_key:
                    continue
                if cached_context_key != expected_context_key:
                    continue
                context_key = expected_context_key
            family_id = payload.get("family_id")
            if (
                not isinstance(family_id, str)
                or family_id == "unknown"
                or family_id not in self.taxonomy.family_ids()
            ):
                continue
            quality_challenge = self._latest_quality_challenge_for(
                workspace_id=workspace_id,
                agent_name=self.algorithm_node.agent_name,
                after_entry_id=entry.id,
            )
            if quality_challenge is not None:
                return AlgorithmFamilyCacheLookup(
                    family=None,
                    context_key=context_key,
                    quality_challenge=quality_challenge,
                )
            return AlgorithmFamilyCacheLookup(
                family=self.taxonomy.get_family(family_id),
                context_key=context_key,
            )
        return AlgorithmFamilyCacheLookup(family=None, context_key=expected_context_key)

    def _cached_algorithm_primer(
        self,
        *,
        workspace_id: int,
        problem_key: str | None,
        family: AlgorithmFamily,
        depth: str,
    ) -> AlgorithmPrimerCacheLookup:
        expected_candidate_space_key = self._algorithm_candidate_space_key()
        for entry in reversed(self.repo.list_blackboard_entries(workspace_id)):
            if entry.entry_type != "algorithm_primer":
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if payload.get("problem_key") != problem_key:
                continue
            if payload.get("candidate_space_key") != expected_candidate_space_key:
                continue
            if payload.get("family_id") != family.family_id:
                continue
            if payload.get("guidance_depth") != depth:
                continue
            primer_text = entry.content
            why_fit = payload.get("why_fit")
            pitfalls = payload.get("pitfalls")
            show_before_plan = payload.get("show_before_plan")
            if not isinstance(primer_text, str) or not primer_text.strip():
                continue
            if not isinstance(why_fit, str) or not why_fit.strip():
                continue
            if not isinstance(pitfalls, list) or not all(isinstance(item, str) for item in pitfalls):
                continue
            if not isinstance(show_before_plan, bool):
                continue
            original_source = payload.get("cached_primer_source") or payload.get("primer_source")
            if not isinstance(original_source, str) or original_source == "cache":
                original_source = "unknown"
            quality_challenge = self._latest_quality_challenge_for(
                workspace_id=workspace_id,
                agent_name=self.primer_node.agent_name,
                after_entry_id=entry.id,
            )
            if quality_challenge is not None:
                return AlgorithmPrimerCacheLookup(quality_challenge=quality_challenge)
            return AlgorithmPrimerCacheLookup(
                primer=AlgorithmPrimer(
                    primer_text=primer_text,
                    why_fit=why_fit,
                    pitfalls=tuple(pitfalls),
                    show_before_plan=show_before_plan,
                ),
                original_source=original_source,
            )
        return AlgorithmPrimerCacheLookup(primer=None)

    def _plan_source_for_active_plan(self, *, workspace_id: int, plan_id: int) -> str | None:
        for entry in reversed(self.repo.list_blackboard_entries(workspace_id)):
            if entry.entry_type not in {"teaching_plan", "plan_revision"}:
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            entry_plan_id = payload.get("plan_id", payload.get("new_plan_id"))
            plan_source = payload.get("plan_source")
            if entry_plan_id == plan_id and isinstance(plan_source, str) and plan_source:
                return plan_source
        return None

    def _resolve_workspace_reference(
        self,
        *,
        request: TeachingTurnRequest,
        session_id: int,
    ) -> tuple[int | None, str | None]:
        if request.problem_id is not None or request.submission_id is not None:
            return None, None
        reference_target = self.problem_node.intent_policy.detect_workspace_reference(request.message)
        if reference_target is None:
            return None, None
        if request.workspace_id is not None and reference_target != "previous":
            return None, None
        workspaces = [
            workspace
            for workspace in self.repo.list_workspaces_for_session(session_id)
            if workspace.id is not None
        ]
        if not workspaces:
            return None, None
        active_workspace = self.repo.get_active_workspace(session_id)
        topic_workspace_id = self._workspace_reference_topic_match(request.message, workspaces)
        if topic_workspace_id is not None:
            return topic_workspace_id, f"workspace_reference_{reference_target}_topic_match"
        if reference_target == "latest":
            return int(workspaces[0].id), "workspace_reference_latest"
        if reference_target == "first":
            return int(workspaces[-1].id), "workspace_reference_first"

        active_workspace_id = request.workspace_id or (active_workspace.id if active_workspace is not None else None)
        for workspace in workspaces:
            if workspace.id != active_workspace_id:
                return int(workspace.id), "workspace_reference_previous"
        return None, None

    def _workspace_reference_topic_match(self, message: str, workspaces: list[Any]) -> int | None:
        normalized_message = self._normalize_reference_text(message)
        if not normalized_message:
            return None
        best_workspace_id: int | None = None
        best_score = 0
        for workspace in workspaces:
            if workspace.id is None:
                continue
            corpus = self._normalize_reference_text(
                " ".join(
                    str(value)
                    for value in (workspace.title, workspace.problem_summary)
                    if isinstance(value, str) and value.strip()
                )
            )
            if not corpus:
                continue
            score = self._longest_reference_topic_span(normalized_message, corpus)
            if score > best_score:
                best_score = score
                best_workspace_id = int(workspace.id)
        return best_workspace_id if best_score >= 2 else None

    def _normalize_reference_text(self, value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())

    def _longest_reference_topic_span(self, message: str, corpus: str) -> int:
        max_span = min(12, len(message))
        for size in range(max_span, 1, -1):
            for start in range(0, len(message) - size + 1):
                span = message[start : start + size]
                if span in REFERENCE_TOPIC_STOPWORDS or span.isdigit():
                    continue
                if span in corpus:
                    return size
        return 0

    def _active_plan_quality_challenge(
        self,
        *,
        workspace_id: int,
        plan_id: int,
    ) -> dict[str, str] | None:
        for entry in reversed(self.repo.list_blackboard_entries(workspace_id)):
            if entry.entry_type not in {"teaching_plan", "plan_revision"}:
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            entry_plan_id = payload.get("plan_id", payload.get("new_plan_id"))
            if entry_plan_id != plan_id:
                continue
            return self._latest_quality_challenge_for(
                workspace_id=workspace_id,
                agent_name=self.plan_node.agent_name,
                after_entry_id=entry.id,
            )
        return None

    def _materialize_boardwork_artifact(
        self,
        *,
        workspace_id: int,
        session_id: int,
        turn_index: int,
        current_step,
        boardwork_payload: dict,
    ) -> None:
        boardwork = self.agent_store.get_or_create_active_boardwork(
            session_id=session_id,
            title="当前题目白板",
            source_turn_index=turn_index,
            workspace_id=workspace_id,
        )
        existing_node = self.repo.db.exec(
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
            "focus": boardwork_payload.get("focus"),
            "boardwork_goal": boardwork_payload.get("boardwork_goal"),
            "source_event_ids": boardwork_payload.get("source_event_ids", []),
            "source_event_types": boardwork_payload.get("source_event_types", []),
            "student_feedback": boardwork_payload.get("student_feedback"),
        }
        summary = boardwork_payload.get("boardwork_goal") or (
            f"{boardwork_payload.get('handoff_type', 'refresh_current_step')}: {current_step.title}"
        )
        coach_note = (
            boardwork_payload.get("focus")
            or boardwork_payload.get("student_feedback")
            or boardwork_payload.get("handoff_type")
        )
        if existing_node is not None:
            self.agent_store.update_board_node(
                existing_node,
                summary=summary,
                trace_payload=trace_payload,
                coach_note=str(coach_note) if coach_note else None,
                is_active=True,
            )
            return

        self.agent_store.create_board_node(
            session_id=session_id,
            title=current_step.title,
            summary=summary,
            source_turn_index=turn_index,
            trace_payload=trace_payload,
            coach_note=str(coach_note) if coach_note else None,
            teaching_step_id=current_step.id,
        )

    def _get_or_create_runtime_session(
        self,
        *,
        session_id: int,
        session_uuid: str,
        user_id: int,
    ) -> AgentSession:
        session = self.repo.db.get(AgentSession, session_id)
        if session is not None:
            return session
        session = AgentSession(
            id=session_id,
            session_uuid=session_uuid,
            user_id=user_id,
            mode=AgentMode.SOCRATIC,
            status=AgentSessionStatus.ACTIVE,
            source=AgentSessionSource.WEB,
        )
        self.repo.db.add(session)
        self.repo.db.flush()
        return session

    def _materialize_diagram_request_scene(
        self,
        *,
        request: TeachingTurnRequest,
        workspace_id: int,
        session_id: int,
        user_id: int,
        turn_index: int,
        current_step,
        diagram_payload: dict,
    ) -> str:
        runtime_session = self._get_or_create_runtime_session(
            session_id=session_id,
            session_uuid=request.session_id or str(session_id),
            user_id=user_id,
        )
        if not isinstance(diagram_payload.get("scene"), dict):
            raise ValueError("diagram_agent_scene_unavailable:missing_scene")
        return self._materialize_agent_diagram_scene(
            runtime_session=runtime_session,
            workspace_id=workspace_id,
            session_id=session_id,
            session_uuid=request.session_id or str(session_id),
            turn_index=turn_index,
            current_step=current_step,
            diagram_payload=diagram_payload,
        )

    def _materialize_agent_diagram_scene(
        self,
        *,
        runtime_session: AgentSession,
        workspace_id: int,
        session_id: int,
        session_uuid: str,
        turn_index: int,
        current_step,
        diagram_payload: dict,
    ) -> str:
        raw_scene = dict(diagram_payload["scene"])
        raw_scene.setdefault("source_turn_index", turn_index)
        raw_steps = raw_scene.get("steps")
        first_step_id = raw_steps[0].get("step_id") if isinstance(raw_steps, list) and raw_steps and isinstance(raw_steps[0], dict) else None
        raw_scene.setdefault("default_step_id", first_step_id)
        scene = DiagramSceneModel.model_validate(raw_scene)
        default_step_id = scene.default_step_id or (scene.steps[0].step_id if scene.steps else None)
        scene = scene.model_copy(
            update={
                "session_id": session_uuid,
                "default_step_id": default_step_id,
                "source_turn_index": turn_index,
            }
        )
        bundle = DiagramTeachingBundleModel(
            preface=str(diagram_payload.get("visual_focus") or diagram_payload.get("diagram_goal") or current_step.title),
            inline_payload=DiagramInlinePayloadModel(
                scene_id=scene.scene_id,
                default_step_id=default_step_id,
                default_step_title=scene.steps[0].title if scene.steps else None,
                title=scene.title,
                diagram_kind=scene.diagram_kind,
                teaching_goal=scene.teaching_goal,
                step_count=len(scene.steps),
                inline_caption=scene.steps[0].title if scene.steps else None,
            ),
            followup="",
            scene=scene,
        )

        verify_started_at = perf_counter()
        verification = diagram_verify_tool(
            session_id=session_uuid,
            bundle=bundle.model_dump(by_alias=True),
        )
        self.repo.record_tool_call_trace(
            session_id=session_id,
            workspace_id=workspace_id,
            turn_index=turn_index,
            owner_agent="DiagramAgent",
            tool_backend="local",
            tool_name="diagram_verify_tool",
            status="completed" if verification.ok else "failed",
            latency_ms=int((perf_counter() - verify_started_at) * 1000),
            request_summary="verify model-generated diagram scene",
            response_summary=verification.reason,
            payload={
                "argument_keys": ["bundle", "session_id"],
                "result_keys": ["ok", "reason", "scene"],
                "result_type": "diagram_verification",
            },
        )
        if not verification.ok or verification.scene is None:
            raise ValueError(f"diagram verification failed: {verification.reason}")

        started_at = perf_counter()
        scene_payload = {
            **verification.scene,
            "workspace_id": workspace_id,
            "teaching_step_id": current_step.id,
            "source_turn_index": turn_index,
            "metadata_json": {
                "diagram_source": diagram_payload.get("diagram_source"),
                "diagram_goal": diagram_payload.get("diagram_goal"),
                "visual_focus": diagram_payload.get("visual_focus"),
                "request_context": diagram_payload.get("request_context"),
                "source_event_ids": diagram_payload.get("source_event_ids", []),
                "source_event_types": diagram_payload.get("source_event_types", []),
                "scene_author": "DiagramAgent",
            },
        }
        scene_record = self.agent_store.upsert_diagram_scene(runtime_session, scene_payload)
        diagram_payload["diagram_scene_id"] = scene_record.scene_uuid
        diagram_payload["diagram_scene_status"] = scene_record.status
        diagram_payload["step_count"] = len(scene_payload.get("steps", []))
        self.repo.record_tool_call_trace(
            session_id=session_id,
            workspace_id=workspace_id,
            turn_index=turn_index,
            owner_agent="DiagramAgent",
            tool_backend="local",
            tool_name="diagram_scene_materializer",
            status="completed",
            latency_ms=int((perf_counter() - started_at) * 1000),
            request_summary="materialize verified diagram scene",
            response_summary="verified scene persisted",
            payload={
                "argument_keys": ["scene", "workspace_id", "teaching_step_id"],
                "result_keys": ["diagram_scene_id", "diagram_scene_status", "step_count"],
                "result_type": "diagram_scene",
                "step_count": len(scene_payload.get("steps", [])),
            },
        )
        return scene_record.scene_uuid

    def _normalize_unavailable_diagram_response(self, visible_text: str, *, reason: str = "unavailable") -> str:
        cleaned = visible_text.strip()
        claim_patterns = (
            r"[^。！？\n]*(?:已经|已)(?:为你)?(?:生成|画好|准备)[^。！？\n]*(?:图示|图表|图解|画布|面板|图)[^。！？\n]*[。！？]?",
            r"请查看[^。！？\n]*(?:图示|图表|图解|画布|面板|图)[^。！？\n]*[。！？]?",
            r"图中[^。！？\n]*[。！？]?",
            r"图里的[^。！？\n]*[。！？]?",
        )
        for pattern in claim_patterns:
            cleaned = re.sub(pattern, "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        cleaned = re.sub(r"^[，,。；;：:\s]+", "", cleaned).strip()
        prefix = (
            "本轮图示工具没有在时间预算内返回。"
            if reason == "timeout"
            else "本轮图示模型没有返回可用结果。"
            if reason == "provider_unavailable"
            else "本轮图示工具处理失败。"
            if reason == "failed"
            else "本轮图示没有返回可用结果。"
        )
        return f"{prefix}{cleaned}" if cleaned else prefix

    def _remove_visual_handoff_claims(self, visible_text: str) -> str:
        cleaned = visible_text.strip()
        claim_patterns = (
            r"[^。！？\n]*(?:图示面板|图表面板|画布|白板)[^。！？\n]*[。！？]?",
            r"[^。！？\n]*(?:将|会|稍后|正在|准备|继续)(?:为你)?(?:生成|展示|更新|调用|出现)[^。！？\n]*(?:图示|图表|图解|画布|面板|白板|图)[^。！？\n]*[。！？]?",
            r"[^。！？\n]*(?:右侧|图示面板|图表面板|画布|白板)[^。！？\n]*(?:将|会|正在|稍后)[^。！？\n]*[。！？]?",
            r"请(?:看|查看)[^。！？\n]*(?:图示|图表|图解|画布|面板|白板|图)[^。！？\n]*[。！？]?",
            r"[^。！？\n]*(?:建议|可以|可)(?:通过|用|借助)?[^。！？\n]*(?:diagram|diagrams|图示|图表|图解|画布|面板|白板|图)[^。！？\n]*[。！？]?",
        )
        for pattern in claim_patterns:
            cleaned = re.sub(pattern, "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        cleaned = re.sub(r"^[，,。；;：:\s]+", "", cleaned).strip()
        return cleaned

    def _diagram_unavailable_reason(self, traces: list[Any]) -> str:
        for trace in reversed(traces):
            if trace.agent_name != "DiagramAgent" or trace.status != "failed":
                continue
            payload = trace.payload if isinstance(trace.payload, dict) else {}
            error_type = payload.get("error_type")
            if error_type == "TimeoutError" or payload.get("timeout_scope"):
                return "timeout"
            if payload.get("provider_outcome"):
                return "provider_unavailable"
            return "failed"
        return "unavailable"

    def _is_short_clarification_followup(self, request: TeachingTurnRequest) -> bool:
        normalized = re.sub(r"\s+", "", request.message.strip().lower())
        if not normalized or len(normalized) > 24:
            return False
        if self.problem_node.intent_policy.detect_new_problem_intent(request.message):
            return False
        if self.problem_node.intent_policy.detect_problem_statement_candidate(request.message):
            return False
        clarification_markers = (
            "什么意思",
            "啥意思",
            "什么含义",
            "这是什么意思",
            "解释一下",
            "展开讲讲",
            "不知道",
            "不清楚",
            "没思路",
            "不懂",
            "没懂",
            "不会",
            "为什么",
        )
        return any(marker in normalized for marker in clarification_markers)

    def _should_suppress_inherited_visual_specialists(
        self,
        request: TeachingTurnRequest,
        *,
        short_clarification_followup: bool,
    ) -> bool:
        if not short_clarification_followup:
            return False
        if self.tutor_node._requests_diagram(request.message):
            return False
        return self.problem_node.intent_policy.detect_explicit_workspace_followup(request.message)

    def _stream_answer_deltas(self, response: str) -> list[str]:
        text = response or ""
        if not text:
            return []
        chunks: list[str] = []
        buffer = ""
        for char in text:
            buffer += char
            if char in "。！？；\n" or len(buffer) >= 18:
                chunks.append(buffer)
                buffer = ""
        if buffer:
            chunks.append(buffer)
        if len(chunks) == 1 and len(text) > 2:
            midpoint = max(1, len(text) // 2)
            chunks = [text[:midpoint], text[midpoint:]]
        return [chunk for chunk in chunks if chunk]

    def run_turn(
        self,
        request: TeachingTurnRequest,
        *,
        session_numeric_id: int,
        user_id: int,
        turn_index: int = 0,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> TeachingTurnResponse:
        def emit_progress(stage: str, message: str, *, agent: str | None = None) -> None:
            if progress_callback is None:
                return
            progress_callback(
                {
                    "type": "progress",
                    "stage": stage,
                    "agent": agent,
                    "message": message,
                }
            )

        def emit_partial_response(response: str) -> None:
            if progress_callback is None:
                return
            for delta in self._stream_answer_deltas(response):
                progress_callback(
                    {
                        "type": "answer_delta",
                        "stage": "tutor",
                        "agent": self.tutor_node.agent_name,
                        "delta": delta,
                    }
                )
            progress_callback(
                {
                    "type": "partial_response",
                    "stage": "tutor",
                    "agent": self.tutor_node.agent_name,
                    "response": response,
                }
            )

        emit_progress("problem_identifier", "正在调用 ProblemIdentifierAgent 识别题目和会话意图。", agent="ProblemIdentifierAgent")
        previous_workspace_id: int | None = None
        workspace = None
        referenced_workspace_id, workspace_reference_reason = self._resolve_workspace_reference(
            request=request,
            session_id=session_numeric_id,
        )
        if referenced_workspace_id is not None:
            request = request.model_copy(update={"workspace_id": referenced_workspace_id})
            problem_decision = self.problem_node.decide(request)
        else:
            active_workspace = self.repo.get_active_workspace(session_numeric_id)
            structured_active_followup = (
                self.require_model_provider
                and active_workspace is not None
                and request.workspace_id is None
                and request.problem_id is None
                and request.submission_id is None
                and self.problem_node.intent_policy.detect_explicit_workspace_followup(request.message)
                and not self.problem_node.intent_policy.detect_new_problem_intent(request.message)
                and not self.problem_node.intent_policy.detect_problem_statement_candidate(request.message)
            )
            if structured_active_followup:
                request = request.model_copy(update={"workspace_id": int(active_workspace.id)})
                problem_decision = ProblemDecision(
                    intent="continue_problem",
                    problem_key=None,
                    confidence=1.0,
                    should_start_new_workspace=False,
                    reason="active_workspace_followup",
                    decision_source="structured",
                )
            else:
                if (
                    active_workspace is not None
                    and request.workspace_id is None
                    and request.problem_id is None
                    and request.submission_id is None
                    and self.problem_node.intent_policy.detect_explicit_workspace_followup(request.message)
                    and not self.problem_node.intent_policy.detect_new_problem_intent(request.message)
                    and not self.problem_node.intent_policy.detect_problem_statement_candidate(request.message)
                ):
                    request = request.model_copy(update={"workspace_id": int(active_workspace.id)})
                problem_decision = self.problem_node.decide(request)
        self._enforce_strict_provider_source(
            self.problem_node.agent_name,
            source=problem_decision.decision_source,
        )
        if problem_decision.intent == "non_problem":
            trace = self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=None,
                turn_index=turn_index,
                agent_name=self.problem_node.agent_name,
                status="ok",
                input_summary="Classified the latest turn before entering the teaching workflow.",
                output_summary=(
                    f"{problem_decision.intent}: confidence={problem_decision.confidence:.2f}, "
                    f"reason={problem_decision.reason}."
                ),
                latency_ms=0,
                blackboard_write_count=0,
                memory_write_count=0,
                payload=self._trace_payload_for(
                    self.problem_node.agent_name,
                    source=problem_decision.decision_source,
                ),
            )
            self._record_provider_call_audits(
                session_id=session_numeric_id,
                workspace_id=None,
                turn_index=turn_index,
            )
            depth = request.guidance_depth or GuidanceDepth.SOCRATIC
            return TeachingTurnResponse(
                session_id=request.session_id or str(session_numeric_id),
                workspace_id=None,
                response="",
                teaching_state=TeachingStateResponse(
                    problem_title=None,
                    workspace_status="idle",
                    current_step_index=0,
                    total_reached_steps=0,
                    total_plan_steps=0,
                    current_step_title="未进入题目",
                    current_step_status="idle",
                    current_step_mastery_state=None,
                    guidance_depth=depth,
                    can_advance=False,
                ),
                agent_trace=[
                    AgentTraceResponse(
                        agent=trace.agent_name,
                        status=trace.status,
                        input_summary=trace.input_summary,
                        output_summary=trace.output_summary,
                        latency_ms=trace.latency_ms,
                        blackboard_writes=trace.blackboard_write_count,
                        memory_writes=trace.memory_write_count,
                        error_message=trace.error_message,
                        payload=trace.payload or {},
                    )
                ],
                events=[],
                boardwork_refresh=False,
                diagram_scene_id=None,
            )

        if request.workspace_id is not None:
            requested_workspace = self.repo.get_workspace(request.workspace_id)
            if (
                requested_workspace is not None
                and requested_workspace.session_id == session_numeric_id
                and requested_workspace.user_id == user_id
            ):
                active_workspace = self.repo.get_active_workspace(session_numeric_id)
                if active_workspace is not None and active_workspace.id != requested_workspace.id:
                    previous_workspace_id = int(active_workspace.id)
                    self.repo.set_workspace_status(previous_workspace_id, ProblemWorkspaceStatus.PAUSED)
                if requested_workspace.status != ProblemWorkspaceStatus.ACTIVE:
                    requested_workspace = self.repo.set_workspace_status(
                        int(requested_workspace.id),
                        ProblemWorkspaceStatus.ACTIVE,
                    )
                workspace = requested_workspace
                if previous_workspace_id is not None:
                    self._write_blackboard(
                        workspace_id=int(workspace.id),
                        session_id=session_numeric_id,
                        turn_index=turn_index,
                        event_type="workspace_transition",
                        actor=self.problem_node.agent_name,
                        entry_type="workspace_transition",
                        content="workspace_resumed",
                        payload={
                            "previous_workspace_id": previous_workspace_id,
                            "resumed_workspace_id": workspace.id,
                            "reason": workspace_reference_reason or "explicit_workspace_id",
                        },
                    )

        if workspace is None:
            workspace = self.repo.get_active_workspace(session_numeric_id)

        new_workspace_reason: str | None = None
        if (
            workspace is not None
            and request.problem_id is not None
            and workspace.problem_id is not None
            and workspace.problem_id != request.problem_id
        ):
            previous_workspace_id = int(workspace.id)
            self.repo.set_workspace_status(previous_workspace_id, ProblemWorkspaceStatus.PAUSED)
            workspace = None
            new_workspace_reason = "problem_id_changed"
        elif workspace is not None and problem_decision.should_start_new_workspace:
            previous_workspace_id = int(workspace.id)
            self.repo.set_workspace_status(previous_workspace_id, ProblemWorkspaceStatus.PAUSED)
            workspace = None
            new_workspace_reason = "problem_identifier_intent"

        if workspace is None:
            problem_title, injected_problem_summary = self._problem_summary_from_problem_id(request.problem_id)
            problem_summary = injected_problem_summary or self._problem_summary_from_request(request)
            workspace = self.repo.create_workspace(
                session_id=session_numeric_id,
                user_id=user_id,
                title=problem_title or self._workspace_title_from_request(request),
                guidance_depth=_to_model_depth(request.guidance_depth or GuidanceDepth.SOCRATIC),
                problem_id=request.problem_id,
                problem_summary=problem_summary or None,
                source=request.source,
            )
            if previous_workspace_id is not None:
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="workspace_transition",
                    actor=self.problem_node.agent_name,
                    entry_type="workspace_transition",
                    content="new_problem_detected",
                    payload={
                        "previous_workspace_id": previous_workspace_id,
                        "new_workspace_id": workspace.id,
                        "problem_id": request.problem_id,
                        "source": new_workspace_reason,
                    },
                )
        elif request.depth_override and request.guidance_depth is not None:
            workspace = self.repo.update_workspace_guidance_depth(
                int(workspace.id),
                _to_model_depth(request.guidance_depth),
                True,
            )

        if request.depth_override and request.guidance_depth is not None:
            effective_depth = request.guidance_depth
        elif workspace.depth_override:
            effective_depth = GuidanceDepth(workspace.guidance_depth.value)
        elif request.guidance_depth is not None:
            effective_depth = request.guidance_depth
        else:
            effective_depth = GuidanceDepth(workspace.guidance_depth.value)
        effective_request = request.model_copy(update={"guidance_depth": effective_depth})
        use_structured_teaching_frame = (
            self.require_model_provider
            and self.tutor_node.response_provider is not None
            and problem_decision.intent in {"new_problem", "continue_problem"}
            and not self._current_upstream_quality_flags(int(workspace.id))
        )
        short_clarification_followup = (
            self.require_model_provider
            and self.tutor_node.response_provider is not None
            and problem_decision.intent == "continue_problem"
            and self._is_short_clarification_followup(effective_request)
        )
        traces = []
        compactable_turns = self.repo.list_compactable_visible_messages(
            int(workspace.id),
            retain_recent=self.context_builder.raw_recent_turn_limit,
        )
        prior_workspace_summary = self.repo.get_workspace_summary(int(workspace.id))
        early_memory_collaboration_context = self._build_collaboration_context(
            upstream_results=[],
            context_snapshot={
                "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        summary_prewarm_executor: ThreadPoolExecutor | None = None
        summary_future = None
        if compactable_turns:
            summary_prewarm_executor = ThreadPoolExecutor(max_workers=1)
            summary_future = summary_prewarm_executor.submit(
                self.memory_manager_node.compact,
                prior_summary=prior_workspace_summary.content if prior_workspace_summary is not None else None,
                turns_to_compact=compactable_turns,
                collaboration_context=early_memory_collaboration_context,
            )
        memory_manager_error_message: str | None = None
        memory_manager_trace_payload: dict[str, Any] | None = None
        memory_settings = self.repo.get_or_create_memory_settings(user_id)
        memory_prewarm_executor: ThreadPoolExecutor | None = None
        memory_future = None
        memory_collaboration_context = early_memory_collaboration_context
        if memory_settings.long_term_memory_enabled:
            memory_prewarm_executor = ThreadPoolExecutor(max_workers=1)
            memory_future = memory_prewarm_executor.submit(
                self.memory_consolidator.extract_candidates,
                effective_request,
                collaboration_context=memory_collaboration_context,
            )
        problem_key = problem_decision.problem_key
        family_cache_lookup = self._cached_algorithm_family(
            workspace_id=int(workspace.id),
            problem_key=problem_key,
        )
        algorithm_cache_bypass = family_cache_lookup.quality_challenge
        algorithm_prewarm_executor: ThreadPoolExecutor | None = None
        algorithm_future = None
        if family_cache_lookup.family is None:
            algorithm_prewarm_executor = ThreadPoolExecutor(max_workers=1)
            algorithm_future = algorithm_prewarm_executor.submit(
                self._run_timed_node,
                self.algorithm_node.identify,
                effective_request,
            )

        emit_progress("supervisor", "正在生成本轮教学拓扑和需要参与的智能体。", agent="TeachingSupervisor")
        supervisor_graph_started_at = perf_counter()
        supervisor_graph = self.supervisor.build_turn_graph(problem_intent=problem_decision.intent)
        supervisor_graph_latency_ms = max(0, int((perf_counter() - supervisor_graph_started_at) * 1000))
        self._enforce_strict_supervisor_decision(supervisor_graph, phase="topology")
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="supervisor_graph_built",
            actor="TeachingSupervisor",
            entry_type="supervisor_execution_graph",
            content=supervisor_graph.graph_version,
            payload=supervisor_graph.to_blackboard_payload(),
        )
        if supervisor_graph.trace_payload:
            provider_trace = supervisor_graph.trace_payload.get("provider")
            provider_backend = provider_trace.get("backend") if isinstance(provider_trace, dict) else None
            if provider_backend == "deterministic":
                provider_trace = None
        if supervisor_graph.trace_payload and provider_trace is not None:
            provider_capability = (
                provider_trace.get("capability")
                if isinstance(provider_trace, dict) and isinstance(provider_trace.get("capability"), str)
                else ""
            )
            traces.append(
                self.repo.record_trace(
                    session_id=session_numeric_id,
                    workspace_id=workspace.id,
                    turn_index=turn_index,
                    agent_name="TeachingSupervisor",
                    status="completed",
                    input_summary="build supervisor execution graph",
                    output_summary=supervisor_graph.topology_decision.get("topology_reason", "supervisor_graph_built"),
                    latency_ms=supervisor_graph_latency_ms,
                    blackboard_write_count=1,
                    memory_write_count=0,
                    payload=self._merge_provider_telemetry(
                        supervisor_graph.trace_payload,
                        agent_name="TeachingSupervisor",
                        capability=provider_capability,
                    ),
                )
            )

        problem_result = self.problem_node.run(effective_request, decision=problem_decision)
        emit_progress("planning", "正在识别算法类型并整理讲解步骤。", agent="PlanDecomposerAgent")
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="problem_identified",
            actor=self.problem_node.agent_name,
            entry_type="problem_identification",
            content=problem_decision.intent,
            payload={
                "intent": problem_decision.intent,
                "problem_key": problem_decision.problem_key,
                "confidence": problem_decision.confidence,
                "reason": problem_decision.reason,
                "decision_source": problem_decision.decision_source,
                "should_start_new_workspace": problem_decision.should_start_new_workspace,
            },
        )
        if summary_future is None:
            memory_manager_result, workspace_summary, workspace_summary_source = self.memory_manager_node.compact(
                prior_summary=prior_workspace_summary.content if prior_workspace_summary is not None else None,
                turns_to_compact=compactable_turns,
                collaboration_context=early_memory_collaboration_context,
            )
        else:
            try:
                memory_manager_result, workspace_summary, workspace_summary_source = summary_future.result(
                    timeout=self.memory_consolidation_timeout_seconds
                )
            except FutureTimeoutError:
                if self.require_model_provider:
                    raise TeachingAgentProviderUnavailableError(
                        agent_name=self.memory_manager_node.agent_name,
                        capability=teaching_agent_primary_capability(self.memory_manager_node.agent_name) or "",
                        outcome="timeout",
                        provider_backend="agno",
                        details={"timeout_ms": self.memory_consolidation_timeout_ms},
                    )
                workspace_summary = prior_workspace_summary.content if prior_workspace_summary is not None else None
                workspace_summary_source = "timeout"
                memory_manager_error_message = "workspace summary compaction exceeded sidecar result collection budget"
                memory_manager_trace_payload = {
                    "provider": {
                        "capability": teaching_agent_primary_capability(self.memory_manager_node.agent_name),
                        "backend": "sidecar",
                        "outcome": "timeout",
                        "accepted": False,
                        "fallback_reason": "workspace_summary_timeout",
                        "timeout_ms": self.memory_consolidation_timeout_ms,
                    }
                }
                memory_manager_result = AgentNodeResult(
                    agent_name=self.memory_manager_node.agent_name,
                    status="failed",
                    input_summary="Compacted old visible turns into active workspace memory.",
                    output_summary=(
                        f"{len(compactable_turns)} old visible turns summary compaction timed out after "
                        f"{self.memory_consolidation_timeout_ms}ms; retained prior summary."
                    ),
                    blackboard_write_count=0,
                    memory_write_count=0,
                )
            except TeachingAgentProviderUnavailableError as exc:
                if self.require_model_provider:
                    raise
                workspace_summary = prior_workspace_summary.content if prior_workspace_summary is not None else None
                workspace_summary_source = "provider_unavailable"
                memory_manager_error_message = str(exc)
                memory_manager_trace_payload = {
                    "provider": {
                        "capability": exc.capability,
                        "backend": exc.provider_backend,
                        "outcome": exc.outcome,
                        "accepted": False,
                        "fallback_reason": exc.outcome,
                    }
                }
                memory_manager_result = AgentNodeResult(
                    agent_name=self.memory_manager_node.agent_name,
                    status="failed",
                    input_summary="Compacted old visible turns into active workspace memory.",
                    output_summary=(
                        f"{len(compactable_turns)} old visible turns summary provider unavailable; "
                        "retained prior summary."
                    ),
                    blackboard_write_count=0,
                    memory_write_count=0,
                )
            except Exception as exc:
                if self.require_model_provider:
                    raise TeachingAgentProviderUnavailableError(
                        agent_name=self.memory_manager_node.agent_name,
                        capability=teaching_agent_primary_capability(self.memory_manager_node.agent_name) or "",
                        outcome="exception",
                        provider_backend="agno",
                        details={"error_type": type(exc).__name__},
                    ) from exc
                workspace_summary = prior_workspace_summary.content if prior_workspace_summary is not None else None
                workspace_summary_source = "failed"
                memory_manager_error_message = str(exc)
                memory_manager_trace_payload = {
                    "provider": {
                        "capability": teaching_agent_primary_capability(self.memory_manager_node.agent_name),
                        "backend": "sidecar",
                        "outcome": "exception",
                        "accepted": False,
                        "fallback_reason": "workspace_summary_exception",
                        "error_type": type(exc).__name__,
                    }
                }
                memory_manager_result = AgentNodeResult(
                    agent_name=self.memory_manager_node.agent_name,
                    status="failed",
                    input_summary="Compacted old visible turns into active workspace memory.",
                    output_summary=(
                        f"{len(compactable_turns)} old visible turns summary compaction failed; retained prior summary."
                    ),
                    blackboard_write_count=0,
                    memory_write_count=0,
                )
            finally:
                if summary_prewarm_executor is not None:
                    summary_prewarm_executor.shutdown(wait=False, cancel_futures=True)
        if workspace_summary is not None and workspace_summary_source == "agent":
            self.repo.upsert_workspace_summary(
                user_id=user_id,
                workspace_id=int(workspace.id),
                content=workspace_summary,
                confidence=0.85,
                importance=0.7,
            )
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="workspace_summary_compacted",
                actor=self.memory_manager_node.agent_name,
                entry_type="workspace_summary",
                content="workspace_summary_updated",
                payload={
                    "compacted_turn_count": len(compactable_turns),
                    "summary_source": workspace_summary_source,
                },
            )
        self._enforce_strict_provider_source(
            self.memory_manager_node.agent_name,
            source=workspace_summary_source,
        )
        context_bundle = self.context_builder.build(
            user_id=user_id,
            workspace=workspace,
        )
        context_memories = context_bundle.retrieved_memory
        memory_result = self.memory_node.run(context_memories)
        if context_memories:
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="memory_context_retrieved",
                actor=self.memory_node.agent_name,
                entry_type="memory_context",
                content=f"{len(context_memories)} context memories",
                payload={
                    "memory_ids": [memory["memory_id"] for memory in context_memories],
                    "memory_types": [memory["memory_type"] for memory in context_memories],
                    "scopes": [memory["scope"] for memory in context_memories],
                },
            )
        memory_candidates = []
        memory_write_count = 0
        memory_consolidation_status = "ok"
        memory_consolidation_summary = "long-term memory disabled; skipped consolidation."
        memory_consolidation_error_message: str | None = None
        memory_consolidation_trace_payload: dict[str, Any] | None = None

        if family_cache_lookup.family is not None:
            family = family_cache_lookup.family
            algorithm_cache_context_key = family_cache_lookup.context_key
            algorithm_source = "cache"
            algorithm_result = self.algorithm_node.result_from_family(family, source=algorithm_source)
            algorithm_latency_ms = 0
        else:
            if algorithm_future is None:
                algorithm_timed = self._run_timed_node(self.algorithm_node.identify, effective_request)
            else:
                try:
                    algorithm_timed = algorithm_future.result()
                finally:
                    if algorithm_prewarm_executor is not None:
                        algorithm_prewarm_executor.shutdown(wait=False, cancel_futures=True)
            algorithm_result, family, algorithm_source = algorithm_timed.value
            algorithm_latency_ms = algorithm_timed.latency_ms
            algorithm_cache_context_key = self._algorithm_cache_context_key(
                workspace_id=int(workspace.id),
                problem_key=problem_key,
            )
        self._enforce_strict_provider_source(
            self.algorithm_node.agent_name,
            source=algorithm_source,
        )
        if workspace.algorithm_family != family.family_id:
            workspace.algorithm_family = family.family_id
            self.repo.db.add(workspace)
            self.repo.db.flush()
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="algorithm_classified",
            actor=self.algorithm_node.agent_name,
            entry_type="algorithm_family",
            content=family.family_id,
            payload={
                "family_id": family.family_id,
                "display_name": family.display_name,
                "classification_source": algorithm_source,
                "problem_key": problem_key,
                "candidate_space_key": self._algorithm_candidate_space_key(),
                "cache_scope": "workspace_problem",
                "cache_context_key": algorithm_cache_context_key,
                **self._cache_bypass_payload(algorithm_cache_bypass),
            },
        )
        existing_plan_for_prewarm = self.repo.get_active_plan(workspace.id)
        existing_current_step_for_prewarm = self.repo.get_current_step(workspace.id)
        initial_plan_executor: ThreadPoolExecutor | None = None
        initial_plan_future = None
        if existing_plan_for_prewarm is None or existing_current_step_for_prewarm is None:
            initial_plan_collaboration_context = self._build_collaboration_context(
                upstream_results=[
                    problem_result,
                    memory_manager_result,
                    memory_result,
                    algorithm_result,
                ],
                context_snapshot={
                    "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                    "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
                },
            )
            initial_plan_executor = ThreadPoolExecutor(max_workers=1)
            plan_builder = (
                self.plan_node.build_structured_plan_with_source
                if use_structured_teaching_frame
                else self.plan_node.build_plan_with_source
            )
            initial_plan_future = initial_plan_executor.submit(
                self._run_timed_node,
                plan_builder,
                effective_request,
                family,
                collaboration_context=initial_plan_collaboration_context,
            )
        primer_cache_lookup = self._cached_algorithm_primer(
            workspace_id=int(workspace.id),
            problem_key=problem_key,
            family=family,
            depth=effective_depth.value,
        )
        primer_cache_bypass = primer_cache_lookup.quality_challenge
        if primer_cache_lookup.primer is not None:
            algorithm_primer = primer_cache_lookup.primer
            cached_primer_source = primer_cache_lookup.original_source or "unknown"
            primer_source = "cache"
            primer_result = self.primer_node.result_from_primer(
                family=family,
                depth=effective_depth.value,
                source=primer_source,
            )
            primer_latency_ms = 0
        else:
            primer_runner = (
                self.primer_node.prepare_structured_details
                if use_structured_teaching_frame or short_clarification_followup
                else self.primer_node.prepare_details
            )
            primer_timed = self._run_timed_node(primer_runner, family=family, depth=effective_depth.value)
            primer_result, algorithm_primer, primer_source = primer_timed.value
            primer_latency_ms = primer_timed.latency_ms
            cached_primer_source = primer_source
        self._enforce_strict_provider_source(
            self.primer_node.agent_name,
            source=primer_source,
        )
        if family.family_id != "unknown":
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="algorithm_primer_prepared",
                actor=self.primer_node.agent_name,
                entry_type="algorithm_primer",
                content=algorithm_primer.primer_text,
                payload={
                    "family_id": family.family_id,
                    "guidance_depth": effective_depth.value,
                    "why_fit": algorithm_primer.why_fit,
                    "pitfalls": list(algorithm_primer.pitfalls),
                    "show_before_plan": algorithm_primer.show_before_plan,
                    "primer_source": primer_source,
                    "cached_primer_source": cached_primer_source,
                    "problem_key": problem_key,
                    "candidate_space_key": self._algorithm_candidate_space_key(),
                    **self._cache_bypass_payload(primer_cache_bypass),
                },
            )

        plan = self.repo.get_active_plan(workspace.id)
        current_step = self.repo.get_current_step(workspace.id)
        revision_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
            ],
            context_snapshot={
                "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        if plan is None or current_step is None:
            plan_revision = PlanRevisionDecision(
                intent="continue_plan",
                confidence=1.0,
                reason="no_active_plan",
                decision_source="skipped",
            )
            plan_revision_result = AgentNodeResult(
                agent_name=self.plan_revision_node.agent_name,
                status="skipped",
                input_summary="No active teaching plan or current step was available for revision.",
                output_summary=(
                    "continue_plan; confidence=1.00; reason=no active teaching plan/current step; "
                    "source=skipped."
                ),
            )
            plan_revision_latency_ms = 0
        elif short_clarification_followup:
            plan_revision = PlanRevisionDecision(
                intent="continue_plan",
                confidence=1.0,
                reason="short_clarification_followup",
                decision_source="structured",
            )
            plan_revision_result = AgentNodeResult(
                agent_name=self.plan_revision_node.agent_name,
                status="ok",
                input_summary="Short clarification follow-up kept the active teaching plan.",
                output_summary=(
                    "continue_plan; confidence=1.00; reason=short clarification follow-up; "
                    "source=structured."
                ),
            )
            plan_revision_latency_ms = 0
        else:
            revision_runner = (
                self.plan_revision_node.run_structured
                if use_structured_teaching_frame
                else self.plan_revision_node.run
            )
            plan_revision_timed = self._run_timed_node(
                revision_runner,
                effective_request,
                current_step=current_step,
                plan_summary=plan.plan_summary,
                collaboration_context=revision_collaboration_context,
            )
            plan_revision_result, plan_revision = plan_revision_timed.value
            plan_revision_latency_ms = plan_revision_timed.latency_ms
        self._enforce_strict_provider_source(
            self.plan_revision_node.agent_name,
            source=plan_revision.decision_source,
        )
        plan_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
                plan_revision_result,
            ],
            context_snapshot={
                "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        generated_step_specs: list[dict[str, str]] | None = None
        generated_plan_source: str | None = None
        plan_latency_ms = 0
        plan_cache_bypass = (
            self._active_plan_quality_challenge(workspace_id=int(workspace.id), plan_id=int(plan.id))
            if plan is not None and not short_clarification_followup
            else None
        )
        if plan is not None and current_step is not None and plan_revision.intent == "branch_plan":
            previous_plan_id = int(plan.id)
            previous_step_id = int(current_step.id)
            self.repo.supersede_plan(previous_plan_id)
            current_step.status = TeachingStepStatus.BRANCHED
            self.repo.db.add(current_step)
            self.repo.db.flush()
            plan_timed = self._run_timed_node(
                self.plan_node.build_plan_with_source,
                effective_request,
                family,
                collaboration_context=plan_collaboration_context,
            )
            step_specs, plan_summary, plan_source = plan_timed.value
            plan_latency_ms = plan_timed.latency_ms
            generated_step_specs = step_specs
            generated_plan_source = plan_source
            plan = self.repo.create_plan(
                workspace_id=workspace.id,
                planner_agent=self.plan_node.agent_name,
                plan_summary=(
                    plan_summary
                    if plan_source == "agent"
                    else f"{family.family_id}: branched teaching plan from student alternative."
                ),
                branch_from_plan_id=previous_plan_id,
            )
            steps = self.repo.create_steps(
                plan_id=plan.id,
                workspace_id=workspace.id,
                step_specs=step_specs,
            )
            workspace = self.repo.activate_plan(workspace.id, plan.id, steps[0].id)
            current_step = steps[0]
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="teaching_plan_branched",
                actor=self.plan_node.agent_name,
                entry_type="plan_revision",
                content=plan_revision.intent,
                payload={
                    "intent": plan_revision.intent,
                    "confidence": plan_revision.confidence,
                    "reason": plan_revision.reason,
                    "revision_source": plan_revision.decision_source,
                    "previous_plan_id": previous_plan_id,
                    "previous_step_id": previous_step_id,
                    "new_plan_id": plan.id,
                    "plan_source": plan_source,
                    "cache_scope": "workspace_problem",
                    "cache_context_key": self._plan_cache_context_key(
                        workspace_id=int(workspace.id),
                        problem_key=problem_key,
                        family=family,
                        depth=effective_depth.value,
                        plan_id=int(plan.id),
                    ),
                },
                step_id=current_step.id,
            )
            self._enforce_strict_provider_source(
                self.plan_node.agent_name,
                source=plan_source,
            )
        if (
            generated_step_specs is None
            and plan_cache_bypass is not None
            and plan is not None
            and current_step is not None
        ):
            previous_plan_id = int(plan.id)
            self.repo.supersede_plan(previous_plan_id)
            current_step.status = TeachingStepStatus.BRANCHED
            self.repo.db.add(current_step)
            self.repo.db.flush()
            plan_timed = self._run_timed_node(
                self.plan_node.build_plan_with_source,
                effective_request,
                family,
                collaboration_context=plan_collaboration_context,
            )
            step_specs, plan_summary, plan_source = plan_timed.value
            plan_latency_ms = plan_timed.latency_ms
            generated_step_specs = step_specs
            generated_plan_source = plan_source
            plan = self.repo.create_plan(
                workspace_id=workspace.id,
                planner_agent=self.plan_node.agent_name,
                plan_summary=plan_summary,
                branch_from_plan_id=previous_plan_id,
            )
            steps = self.repo.create_steps(
                plan_id=plan.id,
                workspace_id=workspace.id,
                step_specs=step_specs,
            )
            workspace = self.repo.activate_plan(workspace.id, plan.id, steps[0].id)
            current_step = steps[0]
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="teaching_plan_cache_bypassed",
                actor=self.plan_node.agent_name,
                entry_type="teaching_plan",
                content=plan.plan_summary or "",
                payload={
                    "plan_id": plan.id,
                    "step_count": len(steps),
                    "plan_source": plan_source,
                    "previous_plan_id": previous_plan_id,
                    "cache_scope": "workspace_problem",
                    "cache_context_key": self._plan_cache_context_key(
                        workspace_id=int(workspace.id),
                        problem_key=problem_key,
                        family=family,
                        depth=effective_depth.value,
                        plan_id=int(plan.id),
                    ),
                    **self._cache_bypass_payload(plan_cache_bypass),
                },
                step_id=current_step.id,
            )
            self._enforce_strict_provider_source(
                self.plan_node.agent_name,
                source=plan_source,
            )
        if plan is None or current_step is None:
            if initial_plan_future is not None:
                try:
                    plan_timed = initial_plan_future.result()
                finally:
                    if initial_plan_executor is not None:
                        initial_plan_executor.shutdown(wait=False, cancel_futures=True)
                    initial_plan_future = None
                    initial_plan_executor = None
            else:
                plan_timed = self._run_timed_node(
                    self.plan_node.build_plan_with_source,
                    effective_request,
                    family,
                    collaboration_context=plan_collaboration_context,
                )
            step_specs, plan_summary, plan_source = plan_timed.value
            plan_latency_ms = plan_timed.latency_ms
            generated_step_specs = step_specs
            generated_plan_source = plan_source
            plan = self.repo.create_plan(
                workspace_id=workspace.id,
                planner_agent=self.plan_node.agent_name,
                plan_summary=plan_summary,
            )
            steps = self.repo.create_steps(
                plan_id=plan.id,
                workspace_id=workspace.id,
                step_specs=step_specs,
            )
            workspace = self.repo.activate_plan(workspace.id, plan.id, steps[0].id)
            current_step = steps[0]
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="teaching_plan_created",
                actor=self.plan_node.agent_name,
                entry_type="teaching_plan",
                content=plan.plan_summary or "",
                payload={
                    "plan_id": plan.id,
                    "step_count": len(steps),
                    "plan_source": plan_source,
                    "cache_scope": "workspace_problem",
                    "cache_context_key": self._plan_cache_context_key(
                        workspace_id=int(workspace.id),
                        problem_key=problem_key,
                        family=family,
                        depth=effective_depth.value,
                        plan_id=int(plan.id),
                    ),
                },
                step_id=current_step.id,
            )
            self._enforce_strict_provider_source(
                self.plan_node.agent_name,
                source=plan_source,
            )

        plan_steps = self.repo.list_steps(plan.id)
        primer_trace_payload = self._trace_payload_for(primer_result.agent_name, source=primer_source)
        primer_trace_payload = self._with_cache_bypass_payload(primer_trace_payload, primer_cache_bypass)
        if primer_source == "cache" and isinstance(primer_trace_payload.get("provider"), dict):
            primer_trace_payload["provider"]["primer_source"] = cached_primer_source
        algorithm_trace_payload = (
            {
                "provider": {
                    **self._trace_payload_for(
                        algorithm_result.agent_name,
                        source=algorithm_source,
                    ).get("provider", {}),
                    "cache_scope": "workspace_problem",
                    "cache_context_key": algorithm_cache_context_key,
                }
            }
            if algorithm_source == "cache"
            else self._trace_payload_for(algorithm_result.agent_name, source=algorithm_source)
        )
        algorithm_trace_payload = self._with_cache_bypass_payload(algorithm_trace_payload, algorithm_cache_bypass)
        traces.extend([
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=problem_result.agent_name,
                status=problem_result.status,
                input_summary=problem_result.input_summary,
                output_summary=problem_result.output_summary,
                latency_ms=0,
                blackboard_write_count=problem_result.blackboard_write_count,
                memory_write_count=problem_result.memory_write_count,
                payload=self._trace_payload_for(problem_result.agent_name, source=problem_decision.decision_source),
            ),
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=memory_manager_result.agent_name,
                status=memory_manager_result.status,
                input_summary=memory_manager_result.input_summary,
                output_summary=memory_manager_result.output_summary,
                latency_ms=0,
                blackboard_write_count=memory_manager_result.blackboard_write_count,
                memory_write_count=memory_manager_result.memory_write_count,
                error_message=memory_manager_error_message,
                payload=(
                    memory_manager_trace_payload
                    or self._trace_payload_for(
                        memory_manager_result.agent_name,
                        source=workspace_summary_source,
                        allow_rejected_provider_fallback=True,
                    )
                ),
            ),
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=memory_result.agent_name,
                status=memory_result.status,
                input_summary=memory_result.input_summary,
                output_summary=memory_result.output_summary,
                latency_ms=0,
                blackboard_write_count=memory_result.blackboard_write_count,
                memory_write_count=memory_result.memory_write_count,
                payload=self._trace_payload_for(memory_result.agent_name),
            ),
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=algorithm_result.agent_name,
                status=algorithm_result.status,
                input_summary=algorithm_result.input_summary,
                output_summary=algorithm_result.output_summary,
                latency_ms=algorithm_latency_ms,
                blackboard_write_count=algorithm_result.blackboard_write_count,
                memory_write_count=algorithm_result.memory_write_count,
                payload=algorithm_trace_payload,
            ),
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=primer_result.agent_name,
                status=primer_result.status,
                input_summary=primer_result.input_summary,
                output_summary=primer_result.output_summary,
                latency_ms=primer_latency_ms,
                blackboard_write_count=primer_result.blackboard_write_count,
                memory_write_count=primer_result.memory_write_count,
                payload=primer_trace_payload,
            ),
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=plan_revision_result.agent_name,
                status=plan_revision_result.status,
                input_summary=plan_revision_result.input_summary,
                output_summary=plan_revision_result.output_summary,
                latency_ms=plan_revision_latency_ms,
                blackboard_write_count=plan_revision_result.blackboard_write_count,
                memory_write_count=plan_revision_result.memory_write_count,
                payload=self._trace_payload_for(
                    plan_revision_result.agent_name,
                    source=plan_revision.decision_source,
                ),
            ),
        ])
        if generated_step_specs is not None and generated_plan_source is not None:
            plan_result = self.plan_node.result_from_step_specs(
                family=family,
                step_specs=generated_step_specs,
                plan_source=generated_plan_source,
            )
            plan_trace_source = generated_plan_source
            plan_original_source = generated_plan_source
        else:
            plan_original_source = self._plan_source_for_active_plan(
                workspace_id=int(workspace.id),
                plan_id=int(plan.id),
            ) or "policy"
            plan_result = self.plan_node.result_from_cached_plan(
                family=family,
                step_count=len(plan_steps),
                original_plan_source=plan_original_source,
            )
            plan_trace_source = "cache"
            self._enforce_strict_provider_source(
                self.plan_node.agent_name,
                source=plan_trace_source if plan_original_source == "agent" else plan_original_source,
            )
        plan_trace_payload = self._trace_payload_for(plan_result.agent_name, source=plan_trace_source)
        plan_trace_payload = self._with_cache_bypass_payload(plan_trace_payload, plan_cache_bypass)
        if plan_trace_source == "cache" and isinstance(plan_trace_payload.get("provider"), dict):
            plan_trace_payload["provider"]["plan_source"] = plan_original_source
            plan_trace_payload["provider"]["cache_scope"] = "workspace_problem"
            plan_trace_payload["provider"]["cache_context_key"] = self._plan_cache_context_key(
                workspace_id=int(workspace.id),
                problem_key=problem_key,
                family=family,
                depth=effective_depth.value,
                plan_id=int(plan.id),
            )
        traces.append(
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=plan_result.agent_name,
                status=plan_result.status,
                input_summary=plan_result.input_summary,
                output_summary=plan_result.output_summary,
                latency_ms=plan_latency_ms,
                blackboard_write_count=plan_result.blackboard_write_count,
                memory_write_count=plan_result.memory_write_count,
                payload=plan_trace_payload,
            )
        )
        hint_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
                plan_revision_result,
                plan_result,
            ],
            context_snapshot={
                "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        hint_decision_step = current_step.model_copy(
            update={"repeat_count": current_step.repeat_count + 1}
        )
        response_prewarm_executor = ThreadPoolExecutor(max_workers=2)
        encouragement_future = None
        encouragement_prewarm_step_id = int(hint_decision_step.id)
        pre_response_quality_flags = self._current_upstream_quality_flags(int(workspace.id))
        tutor_provider_handles_visible_move = (
            self.tutor_node.response_provider is not None
            and (not pre_response_quality_flags or short_clarification_followup)
        )
        use_structured_hint_policy = (
            (use_structured_teaching_frame and not pre_response_quality_flags)
            or short_clarification_followup
        )
        if self.encouragement_node.strategy_provider is not None and not tutor_provider_handles_visible_move:
            encouragement_prewarm_context = self._build_collaboration_context(
                upstream_results=[
                    problem_result,
                    memory_manager_result,
                    memory_result,
                    algorithm_result,
                    primer_result,
                    plan_revision_result,
                    plan_result,
                ],
                context_snapshot={
                    "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                    "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
                    "parallel_hint_policy_pending": True,
                },
            )
            encouragement_future = response_prewarm_executor.submit(
                self._run_timed_node,
                self.encouragement_node.run_agent_strategy,
                current_step=hint_decision_step,
                collaboration_context=encouragement_prewarm_context,
            )
        emit_progress("hint_policy", "正在判断本轮应该直接讲解、提示还是追问。", agent="HintPolicyAgent")
        hint_runner = (
            self.hint_policy_node.run_structured
            if use_structured_hint_policy
            else self.hint_policy_node.run
        )
        hint_future = response_prewarm_executor.submit(
            self._run_timed_node,
            hint_runner,
            effective_request,
            hint_decision_step,
            collaboration_context=hint_collaboration_context,
        )
        try:
            hint_timed = hint_future.result()
        except Exception:
            if encouragement_future is not None:
                encouragement_future.cancel()
            response_prewarm_executor.shutdown(wait=False, cancel_futures=True)
            raise
        hint_result, hint_policy = hint_timed.value
        self._enforce_strict_provider_source(
            self.hint_policy_node.agent_name,
            source=hint_policy.decision_source,
        )
        decision_step_id = int(current_step.id)
        if current_step.repeat_count > 0 and hint_policy.action == "confirm_and_advance":
            next_step = self.repo.advance_step(int(workspace.id), decision_step_id)
            if next_step is not None and int(next_step.id) != decision_step_id:
                current_step = next_step
                plan_steps = self.repo.list_steps(int(plan.id))
                self._write_blackboard(
                    workspace_id=int(workspace.id),
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="step_advanced",
                    actor=self.hint_policy_node.agent_name,
                    entry_type="step_transition",
                    content="student_confirmed",
                    payload={
                        "confirmed_step_id": decision_step_id,
                        "current_step_id": current_step.id,
                        "current_step_key": current_step.step_key,
                    },
                    step_id=current_step.id,
                )
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="hint_policy_selected",
            actor=self.hint_policy_node.agent_name,
            entry_type="hint_policy",
            content=hint_policy.action,
            payload={
                "action": hint_policy.action,
                "tactic": hint_policy.tactic,
                "student_state": hint_policy.student_state,
                "repeat_risk": hint_policy.repeat_risk,
                "max_question_count": hint_policy.max_question_count,
                "encouragement": bool(hint_policy.encouragement),
                "hint_source": hint_policy.decision_source,
            },
            step_id=decision_step_id,
        )
        traces.append(
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=hint_result.agent_name,
                status=hint_result.status,
                input_summary=hint_result.input_summary,
                output_summary=hint_result.output_summary,
                latency_ms=hint_timed.latency_ms,
                blackboard_write_count=hint_result.blackboard_write_count,
                memory_write_count=hint_result.memory_write_count,
                payload=self._trace_payload_for(
                    hint_result.agent_name,
                    source=hint_policy.decision_source,
                    policy_decision_reason=hint_policy.reason,
                ),
            )
        )
        current_step = self.repo.record_step_prompt(current_step.id, turn_index)
        current_context_bundle = self.context_builder.build(
            user_id=user_id,
            workspace=workspace,
        )
        diagram_recent_visible_messages = [
            message
            for message in current_context_bundle.recent_turns
            if not (
                isinstance(message, dict)
                and message.get("turn_index") == turn_index
                and message.get("role") == "user"
            )
        ]
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="context_snapshot_built",
            actor="ContextBuilder",
            entry_type="context_snapshot",
            content="current_step_context",
            payload=current_context_bundle.context_snapshot,
            step_id=current_step.id,
        )
        encouragement_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
                plan_revision_result,
                plan_result,
                hint_result,
            ],
            context_snapshot={
                "blackboard_summary": self._current_blackboard_summary(int(workspace.id)),
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        encouragement_result: AgentNodeResult
        encouragement_strategy = None
        encouragement_timed = TimedNodeResult(value=(None, None), latency_ms=0)
        if tutor_provider_handles_visible_move:
            response_prewarm_executor.shutdown(wait=False, cancel_futures=True)
            encouragement_result = AgentNodeResult(
                agent_name=self.encouragement_node.agent_name,
                status="skipped",
                input_summary="Skipped standalone encouragement because TutorAgent owns the visible response.",
                output_summary="merged_into_tutor: TutorAgent provider returns the user-visible teaching move.",
                blackboard_write_count=0,
                memory_write_count=0,
            )
        else:
            try:
                encouragement_timed = None
                if encouragement_future is not None:
                    prewarmed_encouragement_timed = encouragement_future.result()
                    prewarmed_result, prewarmed_strategy = prewarmed_encouragement_timed.value
                    if prewarmed_strategy is not None and encouragement_prewarm_step_id == int(current_step.id):
                        encouragement_timed = prewarmed_encouragement_timed
                if encouragement_timed is None:
                    encouragement_timed = self._run_timed_node(
                        self.encouragement_node.run,
                        current_step=current_step,
                        policy=hint_policy,
                        collaboration_context=encouragement_collaboration_context,
                    )
            finally:
                response_prewarm_executor.shutdown(wait=False, cancel_futures=True)
            encouragement_result, encouragement_strategy = encouragement_timed.value
            self._enforce_strict_provider_source(
                self.encouragement_node.agent_name,
                source=encouragement_strategy.strategy_source,
            )
            if encouragement_strategy.visible_prefix:
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="encouragement_strategy_selected",
                    actor=self.encouragement_node.agent_name,
                    entry_type="encouragement_strategy",
                    content=encouragement_strategy.feedback_type,
                    payload=encouragement_strategy.model_dump(),
                    step_id=current_step.id,
                )
            traces.append(
                self.repo.record_trace(
                    session_id=session_numeric_id,
                    workspace_id=workspace.id,
                    turn_index=turn_index,
                    agent_name=encouragement_result.agent_name,
                    status=encouragement_result.status,
                    input_summary=encouragement_result.input_summary,
                    output_summary=encouragement_result.output_summary,
                    latency_ms=encouragement_timed.latency_ms,
                    blackboard_write_count=encouragement_result.blackboard_write_count,
                    memory_write_count=encouragement_result.memory_write_count,
                    payload=self._trace_payload_for(
                        encouragement_result.agent_name,
                        source=encouragement_strategy.strategy_source,
                    ),
                )
            )
        tutor_policy = (
            hint_policy
            if encouragement_strategy is None
            else replace(
                hint_policy,
                encouragement=encouragement_strategy.visible_prefix or None,
                encouragement_type=encouragement_strategy.feedback_type
                if encouragement_strategy.visible_prefix
                else None,
            )
        )
        tutor_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
                plan_revision_result,
                plan_result,
                hint_result,
                encouragement_result,
            ],
            context_snapshot={
                **current_context_bundle.context_snapshot,
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        emit_progress("tutor", "正在调用 TutorAgent 组织给学生看的回复。", agent="TutorAgent")
        try:
            tutor_timed = self._run_timed_node(
                self.tutor_node.run,
                effective_request,
                family=family,
                current_step=current_step,
                policy=tutor_policy,
                primer=algorithm_primer.primer_text,
                primer_details={
                    "primer_text": algorithm_primer.primer_text,
                    "why_fit": algorithm_primer.why_fit,
                    "pitfalls": list(algorithm_primer.pitfalls),
                    "show_before_plan": algorithm_primer.show_before_plan,
                },
                collaboration_context=tutor_collaboration_context,
            )
        except TeachingAgentProviderUnavailableError:
            if self.require_model_provider:
                raise
            fallback_started_at = perf_counter()
            tutor_result, tutor_output = self._policy_tutor_output(
                effective_request,
                current_step=current_step,
                policy=tutor_policy,
                primer=algorithm_primer.primer_text,
                show_primer_before_plan=algorithm_primer.show_before_plan,
            )
            tutor_timed = TimedNodeResult(
                value=(tutor_result, tutor_output),
                latency_ms=max(0, int((perf_counter() - fallback_started_at) * 1000)),
            )
        else:
            tutor_result, tutor_output = tutor_timed.value
        tutor_source = "agent" if "source=agent" in tutor_result.output_summary else "policy"
        suppress_inherited_visual_specialists = self._should_suppress_inherited_visual_specialists(
            effective_request,
            short_clarification_followup=short_clarification_followup,
        )
        if suppress_inherited_visual_specialists and (tutor_output.needs_diagram or tutor_output.needs_boardwork):
            tutor_output = tutor_output.model_copy(
                update={
                    "visible_text": self._remove_visual_handoff_claims(tutor_output.visible_text),
                    "needs_diagram": False,
                    "needs_boardwork": False,
                }
            )
        self._enforce_strict_provider_source(
            self.tutor_node.agent_name,
            source=tutor_source,
        )
        emit_partial_response(tutor_output.visible_text)
        traces.append(
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=tutor_result.agent_name,
                status=tutor_result.status,
                input_summary=tutor_result.input_summary,
                output_summary=tutor_result.output_summary,
                latency_ms=tutor_timed.latency_ms,
                blackboard_write_count=tutor_result.blackboard_write_count,
                memory_write_count=tutor_result.memory_write_count,
                payload=self._tutor_trace_payload(tutor_result.agent_name, source=tutor_source),
            )
        )
        if memory_settings.long_term_memory_enabled:
            try:
                if memory_future is None:
                    memory_candidates = self.memory_consolidator.extract_candidates(
                        effective_request,
                        collaboration_context=memory_collaboration_context,
                    )
                else:
                    memory_candidates = memory_future.result(timeout=self.memory_consolidation_timeout_seconds)
                for candidate in memory_candidates:
                    self.repo.upsert_memory_item(
                        user_id=user_id,
                        memory_type=candidate.memory_type,
                        content=candidate.content,
                        scope=candidate.scope,
                        entity_type=candidate.entity_type,
                        entity_id=candidate.entity_id,
                        confidence=candidate.confidence,
                        importance=candidate.importance,
                    )
                memory_write_count = len(memory_candidates)
                memory_consolidation_summary = (
                    f"{memory_write_count} memory candidates consolidated; "
                    f"source={self.memory_consolidator.last_source}."
                )
                self._enforce_strict_provider_source(
                    self.memory_consolidator.agent_name,
                    source=self.memory_consolidator.last_source,
                )
            except FutureTimeoutError:
                memory_consolidation_status = "failed"
                memory_consolidation_error_message = (
                    "memory consolidation exceeded sidecar result collection budget"
                )
                memory_consolidation_summary = (
                    f"memory consolidation timed out after {self.memory_consolidation_timeout_ms}ms; skipped writes."
                )
                memory_consolidation_trace_payload = {
                    "provider": {
                        "capability": teaching_agent_primary_capability(self.memory_consolidator.agent_name),
                        "backend": "sidecar",
                        "outcome": "timeout",
                        "accepted": False,
                        "fallback_reason": "memory_consolidation_timeout",
                        "timeout_ms": self.memory_consolidation_timeout_ms,
                    }
                }
            except TeachingAgentProviderUnavailableError as exc:
                if self.require_model_provider:
                    raise
                memory_consolidation_status = "failed"
                memory_consolidation_error_message = str(exc)
                memory_consolidation_summary = (
                    f"memory consolidation provider unavailable; skipped writes; outcome={exc.outcome}."
                )
                memory_consolidation_trace_payload = {
                    "provider": {
                        "capability": exc.capability,
                        "backend": exc.provider_backend,
                        "outcome": exc.outcome,
                        "accepted": False,
                        "fallback_reason": exc.outcome,
                    }
                }
            except Exception as exc:
                if self.require_model_provider:
                    raise TeachingAgentProviderUnavailableError(
                        agent_name=self.memory_consolidator.agent_name,
                        capability=teaching_agent_primary_capability(self.memory_consolidator.agent_name) or "",
                        outcome="exception",
                        provider_backend="agno",
                        details={"error_type": type(exc).__name__},
                    ) from exc
                memory_consolidation_status = "failed"
                memory_consolidation_error_message = str(exc)
                memory_consolidation_summary = (
                    f"memory consolidation failed; skipped writes; error={type(exc).__name__}."
                )
                memory_consolidation_trace_payload = {
                    "provider": {
                        "capability": teaching_agent_primary_capability(self.memory_consolidator.agent_name),
                        "backend": "sidecar",
                        "outcome": "exception",
                        "accepted": False,
                        "fallback_reason": "memory_consolidation_exception",
                        "error_type": type(exc).__name__,
                    }
                }
            finally:
                if memory_prewarm_executor is not None:
                    memory_prewarm_executor.shutdown(wait=False, cancel_futures=True)
        if memory_write_count > 0:
            self._write_blackboard(
                workspace_id=workspace.id,
                session_id=session_numeric_id,
                turn_index=turn_index,
                event_type="memory_consolidated",
                actor=self.memory_consolidator.agent_name,
                entry_type="memory_consolidation",
                content=f"{memory_write_count} memory writes",
                payload={
                    "memory_write_count": memory_write_count,
                    "memory_types": [candidate.memory_type for candidate in memory_candidates],
                    "scopes": [candidate.scope.value for candidate in memory_candidates],
                    "memory_source": self.memory_consolidator.last_source,
                },
            )
        traces.append(
            self.repo.record_trace(
                session_id=session_numeric_id,
                workspace_id=workspace.id,
                turn_index=turn_index,
                agent_name=self.memory_consolidator.agent_name,
                status=memory_consolidation_status,
                input_summary="Consolidated explicit stable memory candidates from the current turn.",
                output_summary=memory_consolidation_summary,
                latency_ms=0,
                blackboard_write_count=1 if memory_write_count else 0,
                memory_write_count=memory_write_count,
                error_message=memory_consolidation_error_message,
                payload=(
                    memory_consolidation_trace_payload
                    or self._trace_payload_for(
                        self.memory_consolidator.agent_name,
                        source=self.memory_consolidator.last_source,
                        allow_rejected_provider_fallback=True,
                    )
                ),
            )
        )
        current_step = self.repo.record_tutor_step_delta(current_step.id, tutor_output.step_delta)
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="tutor_step_delta_applied",
            actor=self.tutor_node.agent_name,
            entry_type="tutor_step_delta",
            content=tutor_output.step_delta.get("status", "in_progress"),
            payload={
                "status": tutor_output.step_delta.get("status", "in_progress"),
                "step_status": current_step.status.value,
                "mastery_state": current_step.mastery_state,
                "source": self.tutor_node.agent_name,
            },
            step_id=current_step.id,
        )
        self._write_quality_flags_from_payload(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            reviewing_agent=self.tutor_node.agent_name,
            payload={"upstream_quality_flags": tutor_output.upstream_quality_flags},
            step_id=current_step.id,
        )
        specialist_collaboration_context = self._build_collaboration_context(
            upstream_results=[
                problem_result,
                memory_manager_result,
                memory_result,
                algorithm_result,
                primer_result,
                plan_revision_result,
                plan_result,
                hint_result,
                encouragement_result,
                tutor_result,
            ],
            context_snapshot={
                **current_context_bundle.context_snapshot,
                "current_step": {
                    **current_context_bundle.context_snapshot.get("current_step", {}),
                    "status": current_step.status.value,
                    "mastery_state": current_step.mastery_state,
                },
                "upstream_quality_flags": self._current_upstream_quality_flags(int(workspace.id)),
            },
        )
        runtime_upstream_quality_flags = self._current_upstream_quality_flags(int(workspace.id))
        current_turn_source_events = [
            event
            for event in self.repo.list_events(int(workspace.id))
            if event.turn_index == turn_index
        ]
        graph_agents = {node.agent_name for node in supervisor_graph.nodes}
        prewarmed_optional_agents = [
            agent_name
            for agent_name in OPTIONAL_SPECIALIST_EXECUTION_ORDER
            if agent_name in graph_agents
            and (
                (agent_name == "CodingAgent" and hint_policy.action == "enter_code")
                or (agent_name == "DebugAgent" and hint_policy.action == "enter_debug")
                or (agent_name == "DiagramAgent" and tutor_output.needs_diagram)
                or (agent_name == "BoardworkAgent" and tutor_output.needs_boardwork)
            )
        ]
        optional_jobs = {}
        optional_submission_failures: set[str] = set()
        executor = ThreadPoolExecutor(max_workers=OPTIONAL_SPECIALIST_MAX_WORKERS)

        def record_optional_specialist_failure(
            agent_name: str,
            exc: Exception,
            *,
            latency_ms: int = 0,
            payload: dict[str, Any] | None = None,
        ) -> None:
            trace_payload = {
                "error_type": type(exc).__name__,
                "failure_scope": "optional_specialist",
            }
            if isinstance(exc, TeachingAgentProviderUnavailableError):
                trace_payload.update(
                    {
                        "capability": exc.capability,
                        "provider_backend": exc.provider_backend,
                        "provider_outcome": exc.outcome,
                    }
                )
                if isinstance(exc.details, dict):
                    trace_payload.update(exc.details)
            if payload:
                trace_payload.update(payload)
            traces.append(
                self.repo.record_trace(
                    session_id=session_numeric_id,
                    workspace_id=workspace.id,
                    turn_index=turn_index,
                    agent_name=agent_name,
                    status="failed",
                    input_summary="Optional specialist failed while preparing a side-channel handoff.",
                    output_summary=f"optional specialist failure: {type(exc).__name__}",
                    latency_ms=latency_ms,
                    blackboard_write_count=0,
                    memory_write_count=0,
                    error_message=str(exc),
                    payload=trace_payload,
                )
            )

        def submit_optional_specialist_job(agent_name: str) -> None:
            if agent_name in optional_jobs or agent_name in optional_submission_failures:
                return
            try:
                self._require_provider_agent_ready(agent_name)
            except TeachingAgentProviderUnavailableError as exc:
                optional_submission_failures.add(agent_name)
                record_optional_specialist_failure(agent_name, exc)
                return
            if agent_name == "CodingAgent":
                optional_jobs["CodingAgent"] = executor.submit(
                    self._run_optional_specialist_job,
                    self.coding_node.run,
                    current_step,
                    collaboration_context=specialist_collaboration_context,
                )
            elif agent_name == "DebugAgent":
                optional_jobs["DebugAgent"] = executor.submit(
                    self._run_optional_specialist_job,
                    self.debug_node.run,
                    current_step,
                    collaboration_context=specialist_collaboration_context,
                )
            elif agent_name == "DiagramAgent":
                diagram_source_events = [
                    event
                    for event in current_turn_source_events
                    if event.event_type != "diagram_handoff_prepared"
                ]
                optional_jobs["DiagramAgent"] = executor.submit(
                    self._run_optional_specialist_job,
                    self.diagram_node.run,
                    current_step,
                    source_events=diagram_source_events,
                    collaboration_context=specialist_collaboration_context,
                    request_context={
                        "latest_user_message": effective_request.message,
                        "tutor_visible_text": tutor_output.visible_text,
                        "tutor_needs_diagram": tutor_output.needs_diagram,
                        "recent_visible_messages": diagram_recent_visible_messages,
                    },
                )
            elif agent_name == "BoardworkAgent":
                boardwork_source_events = [
                    event
                    for event in current_turn_source_events
                    if event.event_type != "boardwork_refresh_requested"
                ]
                optional_jobs["BoardworkAgent"] = executor.submit(
                    self._run_optional_specialist_job,
                    self.boardwork_node.run,
                    current_step,
                    source_events=boardwork_source_events,
                    collaboration_context=specialist_collaboration_context,
                )
            else:
                raise ValueError(f"unknown optional specialist: {agent_name}")

        for agent_name in prewarmed_optional_agents:
            if agent_name == "DiagramAgent":
                emit_progress("diagram", "正在调用 DiagramAgent 生成并校验图示。", agent="DiagramAgent")
            elif agent_name == "BoardworkAgent":
                emit_progress("boardwork", "正在调用 BoardworkAgent 更新白板。", agent="BoardworkAgent")
            elif agent_name == "CodingAgent":
                emit_progress("coding", "正在调用 CodingAgent 准备代码步骤。", agent="CodingAgent")
            elif agent_name == "DebugAgent":
                emit_progress("debug", "正在调用 DebugAgent 分析调试线索。", agent="DebugAgent")
            submit_optional_specialist_job(agent_name)

        runtime_plan_started_at = perf_counter()
        runtime_plan_kwargs = {
            "problem_intent": problem_decision.intent,
            "hint_action": hint_policy.action,
            "tutor_needs_diagram": tutor_output.needs_diagram,
            "tutor_needs_boardwork": tutor_output.needs_boardwork,
            "upstream_quality_flags": runtime_upstream_quality_flags,
            "base_graph": supervisor_graph,
        }
        runtime_plan_signature = inspect.signature(self.supervisor.build_runtime_plan)
        if "prewarmed_optional_agents" in runtime_plan_signature.parameters:
            runtime_plan_kwargs["prewarmed_optional_agents"] = prewarmed_optional_agents
        runtime_plan = self.supervisor.build_runtime_plan(**runtime_plan_kwargs)
        runtime_plan_latency_ms = max(0, int((perf_counter() - runtime_plan_started_at) * 1000))
        self._enforce_strict_supervisor_decision(runtime_plan, phase="runtime")
        runtime_agents = {node.agent_name for node in runtime_plan.nodes}
        selected_optional_agents = [
            agent_name for agent_name in OPTIONAL_SPECIALIST_EXECUTION_ORDER if agent_name in runtime_agents
        ]
        runtime_plan_payload = runtime_plan.to_blackboard_payload()
        runtime_plan_payload["execution_strategy"] = {
            "mode": (
                "prewarmed_parallel_optional_specialists"
                if prewarmed_optional_agents
                else "parallel_optional_specialists"
            ),
            "max_workers": OPTIONAL_SPECIALIST_MAX_WORKERS,
            "result_timeout_ms": self.optional_specialist_result_timeout_ms,
            "selected_optional_agents": selected_optional_agents,
            "prewarmed_optional_agents": prewarmed_optional_agents,
            "discarded_prewarmed_optional_agents": [
                agent_name for agent_name in prewarmed_optional_agents if agent_name not in runtime_agents
            ],
        }
        self._write_blackboard(
            workspace_id=workspace.id,
            session_id=session_numeric_id,
            turn_index=turn_index,
            event_type="supervisor_runtime_plan_built",
            actor="TeachingSupervisor",
            entry_type="supervisor_runtime_plan",
            content=runtime_plan.graph_version,
            payload=runtime_plan_payload,
            step_id=current_step.id,
        )
        if runtime_plan.trace_payload:
            provider_trace = runtime_plan.trace_payload.get("provider")
            provider_backend = provider_trace.get("backend") if isinstance(provider_trace, dict) else None
            if provider_backend == "deterministic":
                provider_trace = None
        if runtime_plan.trace_payload and provider_trace is not None:
            provider_capability = (
                provider_trace.get("capability")
                if isinstance(provider_trace, dict) and isinstance(provider_trace.get("capability"), str)
                else ""
            )
            traces.append(
                self.repo.record_trace(
                    session_id=session_numeric_id,
                    workspace_id=workspace.id,
                    turn_index=turn_index,
                    agent_name="TeachingSupervisor",
                    status="completed",
                    input_summary="build supervisor runtime plan",
                    output_summary=runtime_plan.routing_decisions.get("route_reason", "supervisor_runtime_plan_built"),
                    latency_ms=runtime_plan_latency_ms,
                    blackboard_write_count=1,
                    memory_write_count=0,
                    payload=self._merge_provider_telemetry(
                        runtime_plan.trace_payload,
                        agent_name="TeachingSupervisor",
                        capability=provider_capability,
                    ),
                )
            )
        diagram_scene_id: str | None = None
        boardwork_refresh = "BoardworkAgent" in runtime_agents

        def persist_optional_specialist_result(
            agent_name: str,
            job_result: OptionalSpecialistJobResult,
        ) -> None:
            nonlocal diagram_scene_id
            result = job_result.result
            payload = job_result.payload
            if agent_name == "CodingAgent":
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="coding_handoff_prepared",
                    actor=self.coding_node.agent_name,
                    entry_type="coding_handoff",
                    content=payload["handoff_type"],
                    payload=payload,
                    step_id=current_step.id,
                )
                self._write_quality_flags_from_payload(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    reviewing_agent=self.coding_node.agent_name,
                    payload=payload,
                    step_id=current_step.id,
                )
                source = payload.get("coding_source")
            elif agent_name == "DebugAgent":
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="debug_handoff_prepared",
                    actor=self.debug_node.agent_name,
                    entry_type="debug_handoff",
                    content=payload["handoff_type"],
                    payload=payload,
                    step_id=current_step.id,
                )
                self._write_quality_flags_from_payload(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    reviewing_agent=self.debug_node.agent_name,
                    payload=payload,
                    step_id=current_step.id,
                )
                source = payload.get("debug_source")
            elif agent_name == "DiagramAgent":
                payload = {**payload, "visible_text": tutor_output.visible_text}
                diagram_scene_id = self._materialize_diagram_request_scene(
                    request=effective_request,
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    user_id=user_id,
                    turn_index=turn_index,
                    current_step=current_step,
                    diagram_payload=payload,
                )
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="diagram_handoff_prepared",
                    actor=self.diagram_node.agent_name,
                    entry_type="diagram_handoff",
                    content=payload["handoff_type"],
                    payload=payload,
                    step_id=current_step.id,
                )
                self._write_quality_flags_from_payload(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    reviewing_agent=self.diagram_node.agent_name,
                    payload=payload,
                    step_id=current_step.id,
                )
                source = payload.get("diagram_source")
            elif agent_name == "BoardworkAgent":
                payload["student_feedback"] = tutor_output.student_feedback
                self._write_blackboard(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    event_type="boardwork_refresh_requested",
                    actor=self.boardwork_node.agent_name,
                    entry_type="boardwork_request",
                    content="refresh_current_step",
                    payload=payload,
                    step_id=current_step.id,
                )
                self._write_quality_flags_from_payload(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    reviewing_agent=self.boardwork_node.agent_name,
                    payload=payload,
                    step_id=current_step.id,
                )
                self._materialize_boardwork_artifact(
                    workspace_id=workspace.id,
                    session_id=session_numeric_id,
                    turn_index=turn_index,
                    current_step=current_step,
                    boardwork_payload=payload,
                )
                source = payload.get("boardwork_source")
            else:
                raise ValueError(f"unknown optional specialist: {agent_name}")

            self._enforce_strict_provider_source(result.agent_name, source=source)
            traces.append(
                self.repo.record_trace(
                    session_id=session_numeric_id,
                    workspace_id=workspace.id,
                    turn_index=turn_index,
                    agent_name=result.agent_name,
                    status=result.status,
                    input_summary=result.input_summary,
                    output_summary=result.output_summary,
                    latency_ms=job_result.latency_ms,
                    blackboard_write_count=result.blackboard_write_count,
                    memory_write_count=result.memory_write_count,
                    payload=self._trace_payload_for(
                        result.agent_name,
                        source=source,
                        allow_rejected_provider_fallback=True,
                        provider_rejection_reason=(
                            str(payload["provider_rejection_reason"])
                            if isinstance(payload.get("provider_rejection_reason"), str)
                            else None
                        ),
                    ),
                )
            )

        try:
            for agent_name in selected_optional_agents:
                submit_optional_specialist_job(agent_name)
            for agent_name, future in optional_jobs.items():
                if agent_name not in runtime_agents:
                    future.cancel()
            future_to_agent = {
                future: agent_name
                for agent_name, future in optional_jobs.items()
                if agent_name in runtime_agents
            }
            pending = set(future_to_agent)
            deadline = perf_counter() + self.optional_specialist_result_timeout_seconds
            completed_results: list[tuple[int, float, str, OptionalSpecialistJobResult]] = []
            failures: list[tuple[str, Exception, int]] = []
            while pending:
                remaining_seconds = deadline - perf_counter()
                if remaining_seconds <= 0:
                    break
                done, pending = wait(pending, timeout=remaining_seconds, return_when=FIRST_COMPLETED)
                for future in done:
                    agent_name = future_to_agent[future]
                    try:
                        job_result = future.result()
                    except TeachingAgentProviderUnavailableError as exc:
                        failures.append((agent_name, exc, 0))
                        continue
                    except Exception as exc:
                        failures.append((agent_name, exc, 0))
                        continue
                    completed_results.append(
                        (job_result.latency_ms, job_result.completed_at, agent_name, job_result)
                    )

            for future in pending:
                agent_name = future_to_agent[future]
                future.cancel()
                record_optional_specialist_failure(
                    agent_name,
                    TimeoutError("optional specialist exceeded result collection budget"),
                    latency_ms=self.optional_specialist_result_timeout_ms,
                    payload={
                        "timeout_ms": self.optional_specialist_result_timeout_ms,
                        "timeout_scope": "result_collection",
                    },
                )

            for agent_name, exc, latency_ms in sorted(failures, key=lambda item: item[0]):
                record_optional_specialist_failure(agent_name, exc, latency_ms=latency_ms)

            for _, _, agent_name, job_result in sorted(
                completed_results,
                key=lambda item: (item[0], item[1]),
            ):
                try:
                    persist_optional_specialist_result(agent_name, job_result)
                except TeachingAgentProviderUnavailableError as exc:
                    record_optional_specialist_failure(
                        agent_name,
                        exc,
                        latency_ms=job_result.latency_ms,
                    )
                except Exception as exc:
                    record_optional_specialist_failure(
                        agent_name,
                        exc,
                        latency_ms=job_result.latency_ms,
                    )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        current_step_index = current_step.order_index + 1
        can_advance = any(step.order_index > current_step.order_index for step in plan_steps)
        turn_events = [
            self._serialize_event(event)
            for event in self.repo.list_events(int(workspace.id))
            if event.turn_index == turn_index
        ]
        self._record_provider_call_audits(
            session_id=session_numeric_id,
            workspace_id=int(workspace.id),
            turn_index=turn_index,
        )
        visible_response = tutor_output.visible_text
        if tutor_output.needs_diagram and diagram_scene_id is None:
            visible_response = self._normalize_unavailable_diagram_response(
                visible_response,
                reason=self._diagram_unavailable_reason(traces),
            )
        return TeachingTurnResponse(
            session_id=request.session_id or str(session_numeric_id),
            workspace_id=workspace.id,
            response=visible_response,
            teaching_state=TeachingStateResponse(
                problem_title=workspace.title,
                workspace_status=workspace.status.value,
                current_step_index=current_step_index,
                total_reached_steps=current_step_index,
                total_plan_steps=len(plan_steps),
                current_step_title=current_step.title,
                current_step_status=current_step.status.value,
                current_step_mastery_state=current_step.mastery_state,
                guidance_depth=effective_depth,
                can_advance=can_advance,
            ),
            agent_trace=[
                AgentTraceResponse(
                    agent=trace.agent_name,
                    status=trace.status,
                    input_summary=trace.input_summary,
                    output_summary=trace.output_summary,
                    latency_ms=trace.latency_ms,
                    blackboard_writes=trace.blackboard_write_count,
                    memory_writes=trace.memory_write_count,
                    error_message=trace.error_message,
                    payload=trace.payload or {},
                )
                for trace in traces
            ],
            events=turn_events,
            boardwork_refresh=boardwork_refresh,
            diagram_scene_id=diagram_scene_id,
        )
