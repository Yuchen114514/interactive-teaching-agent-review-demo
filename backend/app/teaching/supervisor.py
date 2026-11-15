from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable

from app.teaching.agent_prompts import (
    AgentPromptSpec,
    teaching_supervisor_schedule_schema,
    teaching_supervisor_prompt_spec,
    teaching_supervisor_route_schema,
    teaching_supervisor_topology_schema,
)


FORBIDDEN_SUPERVISOR_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
SUPERVISOR_PROVIDER_TIMEOUT_SECONDS = 0.25
SUPERVISOR_PROVIDER_TIMEOUT_MS = int(SUPERVISOR_PROVIDER_TIMEOUT_SECONDS * 1000)
SUPERVISOR_PRIMARY_INTERFACE = "supervisor_schedule"


@dataclass(frozen=True)
class SupervisorNode:
    agent_name: str
    depends_on: tuple[str, ...] = ()
    condition: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent_name": self.agent_name,
            "depends_on": list(self.depends_on),
            "inputs": self.inputs,
        }
        if self.condition is not None:
            payload["condition"] = self.condition
        return payload


@dataclass(frozen=True)
class SupervisorGraph:
    graph_version: str
    handoff_reason: str
    nodes: tuple[SupervisorNode, ...]
    plan_type: str = "supervisor_graph"
    routing_decisions: dict[str, Any] = field(default_factory=dict)
    topology_decision: dict[str, Any] = field(default_factory=dict)
    trace_payload: dict[str, Any] = field(default_factory=dict)

    def node(self, agent_name: str) -> SupervisorNode:
        for node in self.nodes:
            if node.agent_name == agent_name:
                return node
        raise KeyError(agent_name)

    def to_blackboard_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "graph_version": self.graph_version,
            "handoff_reason": self.handoff_reason,
            "nodes": [node.to_payload() for node in self.nodes],
        }
        if self.plan_type != "supervisor_graph":
            payload["plan_type"] = self.plan_type
        if self.routing_decisions:
            payload["routing_decisions"] = self.routing_decisions
        if self.topology_decision:
            payload["topology_decision"] = self.topology_decision
        return payload

    def contains_problem_specific_runtime_rules(self) -> bool:
        payload = self.to_blackboard_payload()
        return any(key in str(payload) for key in FORBIDDEN_SUPERVISOR_KEYS)


@dataclass(frozen=True)
class _TopologyProviderCandidate:
    nodes: tuple[SupervisorNode, ...]
    reason: str
    rejection_reason: str | None = None
    repair_attempted: bool = False
    repair_rejection_reason: str | None = None
    previous_rejection_reason: str | None = None


@dataclass(frozen=True)
class _ParsedTopologyNodes:
    nodes: tuple[SupervisorNode, ...] | None
    rejection_reason: str | None
    raw_node_count: int


class TeachingSupervisor:
    graph_version = "v2.supervisor.1"
    optional_agents = frozenset({"DiagramAgent", "CodingAgent", "DebugAgent", "BoardworkAgent"})
    allowed_agents = frozenset(
        {
            "ProblemIdentifierAgent",
            "MemoryManagerAgent",
            "MemoryRetrieverAgent",
            "MemoryConsolidatorAgent",
            "AlgorithmIdentifierAgent",
            "AlgorithmPrimerAgent",
            "PlanRevisionAgent",
            "PlanDecomposerAgent",
            "HintPolicyAgent",
            "EncouragementAgent",
            "TutorAgent",
            "DiagramAgent",
            "CodingAgent",
            "DebugAgent",
            "BoardworkAgent",
        }
    )

    def __init__(
        self,
        *,
        schedule_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        route_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        topology_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        topology_policy: Any | None = None,
        provider_timeout_seconds: float = SUPERVISOR_PROVIDER_TIMEOUT_SECONDS,
        deterministic_common_path: bool = False,
        deterministic_common_path_overrides_provider: bool = False,
    ):
        self.schedule_provider = schedule_provider
        self.route_provider = route_provider
        self.topology_provider = topology_provider
        self.prompt_spec = prompt_spec or teaching_supervisor_prompt_spec()
        self.provider_timeout_seconds = max(0.001, provider_timeout_seconds)
        self.provider_timeout_ms = int(self.provider_timeout_seconds * 1000)
        self.deterministic_common_path = deterministic_common_path
        self.deterministic_common_path_overrides_provider = deterministic_common_path_overrides_provider
        if topology_policy is None:
            from app.teaching.supervisor_topology import default_supervisor_topology_policy

            topology_policy = default_supervisor_topology_policy()
        self.topology_policy = topology_policy

    @staticmethod
    def _provider_capability(provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None) -> str | None:
        capability = getattr(provider, "_teaching_capability", None)
        return capability if isinstance(capability, str) else None

    def _has_dedicated_topology_provider(self) -> bool:
        return self.topology_provider is not None and self._provider_capability(self.topology_provider) != "supervisor_topology"

    def _has_dedicated_route_provider(self) -> bool:
        return self.route_provider is not None and self._provider_capability(self.route_provider) != "supervisor_route"

    def build_turn_graph(self, *, problem_intent: str) -> SupervisorGraph:
        handoff_reason = self._handoff_reason(problem_intent)
        expected_action = "enter_debug" if problem_intent == "debug_submission" else "select_from_depth_and_step_state"
        from app.teaching.supervisor_topology import SupervisorTopologyContext

        context = SupervisorTopologyContext(
            problem_intent=problem_intent,
            handoff_reason=handoff_reason,
            expected_action=expected_action,
        )
        nodes, topology_decision, trace_payload = self._safe_topology_nodes(context)
        return SupervisorGraph(
            graph_version=self.graph_version,
            handoff_reason=handoff_reason,
            nodes=nodes,
            topology_decision=topology_decision,
            trace_payload=trace_payload,
        )

    def _safe_topology_nodes(self, context: Any) -> tuple[tuple[SupervisorNode, ...], dict[str, Any], dict[str, Any]]:
        if (
            self.deterministic_common_path
            and (
                (
                    self.deterministic_common_path_overrides_provider
                    and not self._has_dedicated_topology_provider()
                )
                or (self.schedule_provider is None and self.topology_provider is None)
            )
            and self._is_common_teaching_intent(context.problem_intent)
        ):
            nodes = self.topology_policy.build_nodes(context)
            if self._is_valid_topology(nodes):
                return tuple(nodes), {
                    "topology_source": "deterministic",
                    "topology_reason": "common_teaching_path",
                }, self._deterministic_trace_payload(
                    capability="supervisor_schedule",
                    decision_phase="topology",
                    reason="common_teaching_path",
                )
        provider_candidate = self._provider_topology_nodes(context)
        if provider_candidate is not None:
            provider_nodes = provider_candidate.nodes
            provider_reason = provider_candidate.reason
            if provider_candidate.rejection_reason is None:
                trace_capability = "supervisor_schedule" if self.schedule_provider is not None else "supervisor_topology"
                return provider_nodes, {
                    "topology_source": "agent",
                    "topology_reason": provider_reason,
                }, self._topology_trace_payload(
                    capability=trace_capability,
                    outcome="candidate_repaired" if provider_candidate.repair_attempted else "candidate_accepted",
                    reason=provider_reason,
                    candidate_node_count=len(provider_nodes),
                    repair_attempted=provider_candidate.repair_attempted,
                    previous_rejection_reason=provider_candidate.previous_rejection_reason,
                )
            policy_nodes = self.topology_policy.build_nodes(context)
            if self._is_valid_topology(policy_nodes):
                trace_capability = "supervisor_schedule" if self.schedule_provider is not None else "supervisor_topology"
                topology_reason = (
                    "agent_topology_timeout" if provider_reason == "provider_timeout" else "agent_topology_rejected"
                )
                return tuple(policy_nodes), {
                    "topology_source": "policy",
                    "topology_reason": topology_reason,
                }, self._topology_trace_payload(
                    capability=trace_capability,
                    outcome="candidate_rejected",
                    reason=topology_reason if provider_reason == "provider_timeout" else provider_reason,
                    candidate_node_count=len(provider_nodes),
                    rejection_reason=provider_candidate.rejection_reason,
                    repair_attempted=provider_candidate.repair_attempted,
                    repair_rejection_reason=provider_candidate.repair_rejection_reason,
                )
        nodes = self.topology_policy.build_nodes(context)
        if self._is_valid_topology(nodes):
            return tuple(nodes), {
                "topology_source": "policy",
                "topology_reason": "default_topology_policy",
            }, {}
        from app.teaching.supervisor_topology import default_supervisor_topology_policy

        return default_supervisor_topology_policy().build_nodes(context), {
            "topology_source": "fallback",
            "topology_reason": "invalid_topology_policy",
        }, {}

    def _topology_trace_payload(
        self,
        *,
        capability: str,
        outcome: str,
        reason: str,
        candidate_node_count: int,
        rejection_reason: str | None = None,
        repair_attempted: bool = False,
        repair_rejection_reason: str | None = None,
        previous_rejection_reason: str | None = None,
    ) -> dict[str, Any]:
        provider = {
            **self._scheduler_interface_trace_fields(capability=capability, decision_phase="topology"),
            "outcome": outcome,
            "topology_reason": reason,
            "candidate_node_count": candidate_node_count,
        }
        payload = {
            "provider": provider
        }
        if rejection_reason is not None:
            payload["provider"]["rejection_reason"] = rejection_reason
        if repair_attempted:
            payload["provider"]["repair_attempted"] = True
        if repair_rejection_reason is not None:
            payload["provider"]["repair_rejection_reason"] = repair_rejection_reason
        if previous_rejection_reason is not None:
            payload["provider"]["previous_rejection_reason"] = previous_rejection_reason
        if rejection_reason == "provider_timeout":
            payload["provider"]["timeout_ms"] = self.provider_timeout_ms
        return payload

    def _scheduler_interface_trace_fields(self, *, capability: str, decision_phase: str) -> dict[str, str]:
        fields = {
            "capability": capability,
            "primary_interface": SUPERVISOR_PRIMARY_INTERFACE,
            "interface_mode": "unified" if capability == SUPERVISOR_PRIMARY_INTERFACE else "compatibility",
            "decision_phase": decision_phase,
        }
        if capability != SUPERVISOR_PRIMARY_INTERFACE:
            fields["compatibility_capability"] = capability
        return fields

    def _is_common_teaching_intent(self, problem_intent: str) -> bool:
        return problem_intent in {"new_problem", "continue_problem", "branch_problem"}

    def _deterministic_trace_payload(
        self,
        *,
        capability: str,
        decision_phase: str,
        reason: str,
        include_agents: list[str] | None = None,
        exclude_agents: list[str] | None = None,
        quality_flag_count: int = 0,
        prewarmed_optional_agent_count: int = 0,
    ) -> dict[str, Any]:
        provider: dict[str, Any] = {
            **self._scheduler_interface_trace_fields(capability=capability, decision_phase=decision_phase),
            "backend": "deterministic",
            "outcome": "not_applicable",
            "accepted": True,
            "latency_ms": 0,
        }
        if decision_phase == "topology":
            provider["topology_reason"] = reason
        else:
            provider.update(
                {
                    "route_reason": reason,
                    "include_agents": include_agents or [],
                    "exclude_agents": exclude_agents or [],
                    "quality_flag_count": quality_flag_count,
                    "prewarmed_optional_agent_count": prewarmed_optional_agent_count,
                }
            )
        return {"provider": provider}

    def _is_valid_topology(self, nodes: Any) -> bool:
        return self._topology_rejection_reason(nodes) is None

    def _topology_rejection_reason(self, nodes: Any) -> str | None:
        if not isinstance(nodes, tuple) or not nodes:
            return "empty_topology"
        if not all(isinstance(node, SupervisorNode) for node in nodes):
            return "invalid_topology_node"
        agent_names = [node.agent_name for node in nodes]
        if len(agent_names) != len(set(agent_names)):
            return "duplicate_topology_agent"
        if not set(agent_names).issubset(self.allowed_agents):
            return "invalid_topology_graph"
        if any(node.agent_name in node.depends_on for node in nodes):
            return "invalid_topology_graph"
        if any(agent not in agent_names for node in nodes for agent in node.depends_on):
            return "invalid_topology_graph"
        validation_graph = SupervisorGraph(
            graph_version=self.graph_version,
            handoff_reason="topology_validation",
            nodes=nodes,
        )
        if validation_graph.contains_problem_specific_runtime_rules():
            return "invalid_topology_graph"
        return None

    def _provider_topology_nodes(self, context: Any) -> _TopologyProviderCandidate | None:
        topology_provider = self.schedule_provider or self.topology_provider
        if topology_provider is None:
            return None
        provider_payload = {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "decision_phase": "topology",
            "problem_intent": context.problem_intent,
            "handoff_reason": context.handoff_reason,
            "expected_action": context.expected_action,
            "allowed_agents": sorted(self.allowed_agents),
            "optional_agents": sorted(self.optional_agents),
            "required_schema": (
                teaching_supervisor_schedule_schema()
                if self.schedule_provider is not None
                else teaching_supervisor_topology_schema()
            ),
        }
        raw_route, provider_error = self._call_topology_provider(topology_provider, provider_payload)
        if provider_error == "provider_timeout":
            return _TopologyProviderCandidate(
                nodes=tuple(),
                reason="provider_timeout",
                rejection_reason="provider_timeout",
            )
        if provider_error is not None:
            return None
        candidate = self._topology_candidate_from_raw(raw_route)
        if candidate is None or candidate.rejection_reason is None:
            return candidate

        repair_payload = {
            **provider_payload,
            "repair_context": self._topology_repair_context(
                raw_route=raw_route,
                previous_rejection_reason=candidate.rejection_reason,
                previous_topology_reason=candidate.reason,
                received_node_count=len(candidate.nodes),
            ),
        }
        repaired_raw_route, repair_error = self._call_topology_provider(topology_provider, repair_payload)
        if repair_error is None:
            repaired_candidate = self._topology_candidate_from_raw(repaired_raw_route)
            if repaired_candidate is not None:
                if repaired_candidate.rejection_reason is None:
                    return _TopologyProviderCandidate(
                        nodes=repaired_candidate.nodes,
                        reason=repaired_candidate.reason,
                        repair_attempted=True,
                        previous_rejection_reason=candidate.rejection_reason,
                    )
                return _TopologyProviderCandidate(
                    nodes=candidate.nodes,
                    reason=candidate.reason,
                    rejection_reason=candidate.rejection_reason,
                    repair_attempted=True,
                    repair_rejection_reason=repaired_candidate.rejection_reason,
                )
        return _TopologyProviderCandidate(
            nodes=candidate.nodes,
            reason=candidate.reason,
            rejection_reason=candidate.rejection_reason,
            repair_attempted=True,
            repair_rejection_reason=repair_error or "invalid_topology_payload",
        )

    def _call_topology_provider(
        self,
        topology_provider: Callable[[dict[str, Any]], dict[str, Any] | None],
        provider_payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(topology_provider, provider_payload)
        try:
            raw_route = future.result(timeout=self.provider_timeout_seconds)
        except FuturesTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return None, "provider_timeout"
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            return None, "provider_exception"
        executor.shutdown(wait=False, cancel_futures=True)
        return raw_route, None

    def _topology_candidate_from_raw(self, raw_route: dict[str, Any] | None) -> _TopologyProviderCandidate | None:
        if not isinstance(raw_route, dict) or FORBIDDEN_SUPERVISOR_KEYS.intersection(raw_route):
            return None
        raw_nodes = raw_route.get("topology_nodes")
        if raw_nodes is None:
            return None
        parsed_nodes = self._parse_topology_nodes(raw_nodes)
        reason = raw_route.get("reason") or "agent_topology_candidate"
        if not isinstance(reason, str):
            return None
        if parsed_nodes.nodes is None:
            return _TopologyProviderCandidate(
                nodes=tuple(),
                reason=reason,
                rejection_reason=parsed_nodes.rejection_reason or "invalid_topology_payload",
            )
        graph_rejection_reason = self._topology_rejection_reason(parsed_nodes.nodes)
        if graph_rejection_reason is None and self.schedule_provider is not None:
            graph_rejection_reason = self._schedule_topology_rejection_reason(parsed_nodes.nodes)
        return _TopologyProviderCandidate(
            nodes=parsed_nodes.nodes,
            reason=reason,
            rejection_reason=graph_rejection_reason,
        )

    def _schedule_topology_rejection_reason(self, nodes: tuple[SupervisorNode, ...]) -> str | None:
        agent_names = {node.agent_name for node in nodes}
        if not self.optional_agents.issubset(agent_names):
            return "topology_missing_optional_agents"
        return None

    def _parse_topology_nodes(self, raw_nodes: Any) -> _ParsedTopologyNodes:
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return _ParsedTopologyNodes(nodes=None, rejection_reason="topology_nodes_not_nonempty_list", raw_node_count=0)
        nodes: list[SupervisorNode] = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                return _ParsedTopologyNodes(
                    nodes=None,
                    rejection_reason="topology_node_not_object",
                    raw_node_count=len(raw_nodes),
                )
            agent_name = raw_node.get("agent_name")
            depends_on = raw_node.get("depends_on", [])
            condition = raw_node.get("condition")
            inputs = raw_node.get("inputs", {})
            if not isinstance(agent_name, str):
                return _ParsedTopologyNodes(
                    nodes=None,
                    rejection_reason="topology_node_agent_name_not_string",
                    raw_node_count=len(raw_nodes),
                )
            if not isinstance(depends_on, list) or not all(isinstance(agent, str) for agent in depends_on):
                return _ParsedTopologyNodes(
                    nodes=None,
                    rejection_reason="topology_node_depends_on_not_string_list",
                    raw_node_count=len(raw_nodes),
                )
            if condition is not None and not isinstance(condition, str):
                return _ParsedTopologyNodes(
                    nodes=None,
                    rejection_reason="topology_node_condition_not_string",
                    raw_node_count=len(raw_nodes),
                )
            if not isinstance(inputs, dict):
                return _ParsedTopologyNodes(
                    nodes=None,
                    rejection_reason="topology_node_inputs_not_object",
                    raw_node_count=len(raw_nodes),
                )
            nodes.append(
                SupervisorNode(
                    agent_name=agent_name,
                    depends_on=tuple(depends_on),
                    condition=condition,
                    inputs=inputs,
                )
            )
        return _ParsedTopologyNodes(nodes=tuple(nodes), rejection_reason=None, raw_node_count=len(raw_nodes))

    def _topology_repair_context(
        self,
        *,
        raw_route: dict[str, Any] | None,
        previous_rejection_reason: str,
        previous_topology_reason: str,
        received_node_count: int,
    ) -> dict[str, Any]:
        raw_nodes = raw_route.get("topology_nodes") if isinstance(raw_route, dict) else None
        raw_node_count = len(raw_nodes) if isinstance(raw_nodes, list) else received_node_count
        return {
            "previous_rejection_reason": previous_rejection_reason,
            "previous_topology_reason": previous_topology_reason,
            "received_node_count": raw_node_count,
            "required_corrections": [
                "Return topology_nodes as a non-empty array of node objects.",
                "Every node.agent_name must be one of allowed_agents.",
                "Every node.depends_on must list only earlier nodes from topology_nodes.",
                "Every node.inputs must be a JSON object.",
                "Include DiagramAgent, CodingAgent, DebugAgent, and BoardworkAgent nodes so runtime routing can select them when needed.",
                "Do not include forbidden problem-specific solution keys.",
            ],
        }

    def build_runtime_plan(
        self,
        *,
        problem_intent: str,
        hint_action: str,
        tutor_needs_diagram: bool,
        tutor_needs_boardwork: bool,
        upstream_quality_flags: list[dict[str, str]] | None = None,
        prewarmed_optional_agents: list[str] | None = None,
        base_graph: SupervisorGraph | None = None,
    ) -> SupervisorGraph:
        graph = base_graph or self.build_turn_graph(problem_intent=problem_intent)
        sanitized_quality_flags = self._sanitize_quality_flags(upstream_quality_flags)
        sanitized_prewarmed_optional_agents = self._sanitize_optional_agents(prewarmed_optional_agents)
        include_diagram = tutor_needs_diagram
        include_coding = hint_action == "enter_code"
        include_debug = hint_action == "enter_debug"
        include_boardwork = tutor_needs_boardwork
        route_source = "policy"
        route_reason = "default_optional_routing"
        trace_payload: dict[str, Any] = {}

        provider_route = None
        route_rejection_reason = None
        if (
            self.deterministic_common_path
            and (
                (
                    self.deterministic_common_path_overrides_provider
                    and not self._has_dedicated_route_provider()
                )
                or (self.schedule_provider is None and self.route_provider is None)
            )
            and self._is_common_teaching_intent(problem_intent)
        ):
            route_source = "deterministic"
            route_reason = "common_teaching_path"
            trace_payload = self._deterministic_trace_payload(
                capability="supervisor_schedule",
                decision_phase="runtime",
                reason=route_reason,
                include_agents=[
                    agent
                    for agent, include in (
                        ("DiagramAgent", include_diagram),
                        ("CodingAgent", include_coding),
                        ("DebugAgent", include_debug),
                        ("BoardworkAgent", include_boardwork),
                    )
                    if include
                ],
                exclude_agents=[
                    agent
                    for agent, include in (
                        ("DiagramAgent", include_diagram),
                        ("CodingAgent", include_coding),
                        ("DebugAgent", include_debug),
                        ("BoardworkAgent", include_boardwork),
                    )
                    if not include
                ],
                quality_flag_count=len(sanitized_quality_flags),
                prewarmed_optional_agent_count=len(sanitized_prewarmed_optional_agents),
            )
        else:
            provider_route, route_rejection_reason = self._provider_route(
                problem_intent=problem_intent,
                hint_action=hint_action,
                default_routing_decisions={
                    "include_diagram": include_diagram,
                    "include_coding": include_coding,
                    "include_debug": include_debug,
                    "include_boardwork": include_boardwork,
                },
                topology_context=self._route_topology_context(graph),
                upstream_quality_flags=sanitized_quality_flags,
                prewarmed_optional_agents=sanitized_prewarmed_optional_agents,
            )
        if provider_route is not None:
            route_source = "agent"
            route_reason = provider_route["reason"]
            included_agents = provider_route["include_agents"]
            excluded_agents = provider_route["exclude_agents"]
            repair_attempted = bool(provider_route.get("repair_attempted"))
            trace_payload = self._route_trace_payload(
                capability="supervisor_schedule" if self.schedule_provider is not None else "supervisor_route",
                outcome="route_repaired" if repair_attempted else "route_applied",
                reason=route_reason,
                include_agents=sorted(included_agents),
                exclude_agents=sorted(excluded_agents),
                quality_flag_count=len(sanitized_quality_flags),
                prewarmed_optional_agent_count=len(sanitized_prewarmed_optional_agents),
                repair_attempted=repair_attempted,
                previous_rejection_reason=provider_route.get("previous_rejection_reason"),
            )
            include_diagram = self._apply_agent_route(
                "DiagramAgent",
                current=include_diagram,
                included_agents=included_agents,
                excluded_agents=excluded_agents,
            )
            include_coding = self._apply_agent_route(
                "CodingAgent",
                current=include_coding,
                included_agents=included_agents,
                excluded_agents=excluded_agents,
            )
            include_debug = self._apply_agent_route(
                "DebugAgent",
                current=include_debug,
                included_agents=included_agents,
                excluded_agents=excluded_agents,
            )
            include_boardwork = self._apply_agent_route(
                "BoardworkAgent",
                current=include_boardwork,
                included_agents=included_agents,
                excluded_agents=excluded_agents,
            )
        elif route_rejection_reason is not None:
            route_reason = "agent_route_rejected"
            trace_payload = self._route_trace_payload(
                capability="supervisor_schedule" if self.schedule_provider is not None else "supervisor_route",
                outcome="route_rejected",
                reason=route_reason,
                rejection_reason=route_rejection_reason,
                include_agents=[],
                exclude_agents=[],
                quality_flag_count=len(sanitized_quality_flags),
                prewarmed_optional_agent_count=len(sanitized_prewarmed_optional_agents),
            )

        selected_nodes = tuple(
            node
            for node in graph.nodes
            if node.agent_name not in {"DiagramAgent", "CodingAgent", "DebugAgent", "BoardworkAgent"}
            or (node.agent_name == "DiagramAgent" and include_diagram)
            or (node.agent_name == "CodingAgent" and include_coding)
            or (node.agent_name == "DebugAgent" and include_debug)
            or (node.agent_name == "BoardworkAgent" and include_boardwork)
        )
        selected_agent_names = {node.agent_name for node in selected_nodes}
        normalized_nodes = tuple(
            SupervisorNode(
                agent_name=node.agent_name,
                depends_on=tuple(agent for agent in node.depends_on if agent in selected_agent_names),
                condition=node.condition,
                inputs=node.inputs,
            )
            for node in selected_nodes
        )
        return SupervisorGraph(
            graph_version=graph.graph_version,
            handoff_reason=graph.handoff_reason,
            nodes=normalized_nodes,
            plan_type="dynamic_runtime_plan",
            routing_decisions={
                "include_diagram": include_diagram,
                "include_coding": include_coding,
                "include_debug": include_debug,
                "include_boardwork": include_boardwork,
                "route_source": route_source,
                "route_reason": route_reason,
                "quality_flag_count": len(sanitized_quality_flags),
                "prewarmed_optional_agents": sanitized_prewarmed_optional_agents,
            },
            topology_decision=graph.topology_decision,
            trace_payload=trace_payload,
        )

    def _route_trace_payload(
        self,
        *,
        capability: str,
        outcome: str,
        reason: str,
        include_agents: list[str],
        exclude_agents: list[str],
        quality_flag_count: int = 0,
        prewarmed_optional_agent_count: int = 0,
        rejection_reason: str | None = None,
        repair_attempted: bool = False,
        previous_rejection_reason: str | None = None,
        repair_rejection_reason: str | None = None,
    ) -> dict[str, Any]:
        provider = {
            **self._scheduler_interface_trace_fields(capability=capability, decision_phase="runtime"),
            "outcome": outcome,
            "route_reason": reason,
            "include_agents": include_agents,
            "exclude_agents": exclude_agents,
            "quality_flag_count": quality_flag_count,
            "prewarmed_optional_agent_count": prewarmed_optional_agent_count,
        }
        provider_payload = {
            "provider": provider
        }
        if rejection_reason is not None:
            provider_payload["provider"]["rejection_reason"] = rejection_reason
        if repair_attempted:
            provider_payload["provider"]["repair_attempted"] = True
        if previous_rejection_reason is not None:
            provider_payload["provider"]["previous_rejection_reason"] = previous_rejection_reason
        if repair_rejection_reason is not None:
            provider_payload["provider"]["repair_rejection_reason"] = repair_rejection_reason
        if rejection_reason == "provider_timeout":
            provider_payload["provider"]["timeout_ms"] = self.provider_timeout_ms
        return provider_payload

    def _provider_route(
        self,
        *,
        problem_intent: str,
        hint_action: str,
        default_routing_decisions: dict[str, bool],
        topology_context: dict[str, Any],
        upstream_quality_flags: list[dict[str, str]],
        prewarmed_optional_agents: list[str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        route_provider = self.schedule_provider or self.route_provider
        if route_provider is None:
            return None, None
        provider_payload = {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "decision_phase": "runtime",
            "problem_intent": problem_intent,
            "hint_action": hint_action,
            "default_routing_decisions": default_routing_decisions,
            "topology_context": topology_context,
            "upstream_quality_flags": upstream_quality_flags,
            "prewarmed_optional_agents": prewarmed_optional_agents,
            "optional_agents": sorted(self.optional_agents),
            "required_schema": (
                teaching_supervisor_schedule_schema()
                if self.schedule_provider is not None
                else teaching_supervisor_route_schema()
            ),
        }
        raw_route, route_error = self._call_route_provider(route_provider, provider_payload)
        if route_error is not None:
            return None, route_error
        parsed_route, rejection_reason = self._validated_provider_route(raw_route, topology_context)
        if rejection_reason is None:
            return parsed_route, None
        if self.schedule_provider is None:
            return None, rejection_reason
        if raw_route is None:
            return None, None

        repair_payload = {
            **provider_payload,
            "repair_context": self._route_repair_context(
                previous_rejection_reason=rejection_reason,
                previous_route_reason=(
                    str(raw_route.get("reason"))
                    if isinstance(raw_route, dict) and isinstance(raw_route.get("reason"), str)
                    else "agent_optional_routing"
                ),
                topology_context=topology_context,
            ),
        }
        repaired_raw_route, repair_error = self._call_route_provider(route_provider, repair_payload)
        if repair_error is not None:
            return None, f"repair_{repair_error}"
        repaired_route, repair_rejection_reason = self._validated_provider_route(repaired_raw_route, topology_context)
        if repair_rejection_reason is None:
            if repaired_route is not None:
                repaired_route = {
                    **repaired_route,
                    "repair_attempted": True,
                    "previous_rejection_reason": rejection_reason,
                }
            return repaired_route, None
        return None, f"repair_{repair_rejection_reason}"

    def _call_route_provider(
        self,
        route_provider: Callable[[dict[str, Any]], dict[str, Any] | None],
        provider_payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(route_provider, provider_payload)
        try:
            raw_route = future.result(timeout=self.provider_timeout_seconds)
        except FuturesTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return None, "provider_timeout"
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            return None, "provider_exception"
        executor.shutdown(wait=False, cancel_futures=True)
        return raw_route, None

    def _validated_provider_route(
        self,
        raw_route: dict[str, Any] | None,
        topology_context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        parsed_route = self._parse_provider_route(raw_route)
        if parsed_route is None:
            return None, "invalid_route_payload" if raw_route is not None else None
        optional_agents_in_graph = set(topology_context.get("optional_agents_in_graph", []))
        if not parsed_route["include_agents"].issubset(optional_agents_in_graph):
            return None, "route_agent_not_in_topology"
        if not parsed_route["exclude_agents"].issubset(optional_agents_in_graph):
            return None, "route_agent_not_in_topology"
        return parsed_route, None

    def _route_repair_context(
        self,
        *,
        previous_rejection_reason: str,
        previous_route_reason: str,
        topology_context: dict[str, Any],
    ) -> dict[str, Any]:
        available_optional_agents = topology_context.get("optional_agents_in_graph", [])
        if not isinstance(available_optional_agents, list):
            available_optional_agents = []
        return {
            "previous_rejection_reason": previous_rejection_reason,
            "previous_route_reason": previous_route_reason,
            "available_optional_agents": [
                agent for agent in available_optional_agents if isinstance(agent, str) and agent in self.optional_agents
            ],
            "required_corrections": [
                "Return include_agents and exclude_agents as arrays of optional agent names.",
                "Only include or exclude optional agents present in topology_context.optional_agents_in_graph.",
                "Do not include forbidden problem-specific solution keys.",
                "If no optional agent should be changed, return empty include_agents and exclude_agents.",
            ],
        }

    def _parse_provider_route(self, raw_route: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(raw_route, dict):
            return None
        if FORBIDDEN_SUPERVISOR_KEYS.intersection(raw_route):
            return None
        include_agents = raw_route.get("include_agents", [])
        exclude_agents = raw_route.get("exclude_agents", [])
        reason = raw_route.get("reason") or "agent_optional_routing"
        if not isinstance(include_agents, list) or not isinstance(exclude_agents, list) or not isinstance(reason, str):
            return None
        if not all(isinstance(agent, str) for agent in include_agents + exclude_agents):
            return None
        if not set(include_agents).issubset(self.optional_agents):
            return None
        if not set(exclude_agents).issubset(self.optional_agents):
            return None
        return {
            "include_agents": set(include_agents),
            "exclude_agents": set(exclude_agents),
            "reason": reason,
        }

    def _sanitize_quality_flags(self, flags: list[dict[str, str]] | None) -> list[dict[str, str]]:
        if not isinstance(flags, list):
            return []
        sanitized_flags: list[dict[str, str]] = []
        for raw_flag in flags:
            if not isinstance(raw_flag, dict):
                continue
            flag = {
                key: raw_flag[key].strip()[:120]
                for key in ("flagged_agent", "severity", "reason", "suggested_action")
                if isinstance(raw_flag.get(key), str) and raw_flag[key].strip()
            }
            if len(flag) != 4:
                continue
            if any(forbidden_key in str(flag) for forbidden_key in FORBIDDEN_SUPERVISOR_KEYS):
                continue
            sanitized_flags.append(flag)
            if len(sanitized_flags) >= 8:
                break
        return sanitized_flags

    def _sanitize_optional_agents(self, agents: list[str] | None) -> list[str]:
        if not isinstance(agents, list):
            return []
        sanitized_agents: list[str] = []
        for agent_name in agents:
            if agent_name not in self.optional_agents:
                continue
            if agent_name in sanitized_agents:
                continue
            sanitized_agents.append(agent_name)
        return sanitized_agents

    def _route_topology_context(self, graph: SupervisorGraph) -> dict[str, Any]:
        available_agents = [node.agent_name for node in graph.nodes]
        return {
            "topology_source": graph.topology_decision.get("topology_source", "unknown"),
            "topology_reason": graph.topology_decision.get("topology_reason", "unknown"),
            "available_agents": available_agents,
            "optional_agents_in_graph": [agent for agent in available_agents if agent in self.optional_agents],
        }

    def _apply_agent_route(
        self,
        agent_name: str,
        *,
        current: bool,
        included_agents: set[str],
        excluded_agents: set[str],
    ) -> bool:
        if agent_name in excluded_agents:
            return False
        if agent_name in included_agents:
            return True
        return current

    def _handoff_reason(self, problem_intent: str) -> str:
        if problem_intent == "new_problem":
            return "new_problem_workspace"
        if problem_intent == "debug_submission":
            return "structured_submission_debug"
        return "standard_teaching_turn"
