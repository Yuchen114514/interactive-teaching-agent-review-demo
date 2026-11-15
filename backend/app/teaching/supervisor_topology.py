from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from app.teaching.supervisor import SupervisorNode


DEFAULT_SUPERVISOR_TOPOLOGY_POLICY_PATH = Path(__file__).with_name("supervisor_topology_policy.json")
FORBIDDEN_TOPOLOGY_POLICY_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}


@dataclass(frozen=True)
class SupervisorTopologyContext:
    problem_intent: str
    handoff_reason: str
    expected_action: str


class SupervisorTopologyPolicy(Protocol):
    def build_nodes(self, context: SupervisorTopologyContext) -> tuple[SupervisorNode, ...]:
        ...


@dataclass(frozen=True)
class DefaultSupervisorTopologyPolicy:
    node_specs: tuple[dict[str, Any], ...]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "DefaultSupervisorTopologyPolicy":
        forbidden = FORBIDDEN_TOPOLOGY_POLICY_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"supervisor topology policy contains problem-specific runtime rules: {sorted(forbidden)}")
        nodes = payload.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("supervisor topology policy requires non-empty nodes")
        node_specs: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("supervisor topology node must be an object")
            agent_name = node.get("agent_name")
            depends_on = node.get("depends_on", [])
            condition = node.get("condition")
            inputs = node.get("inputs", {})
            if not isinstance(agent_name, str) or not agent_name.strip():
                raise ValueError("supervisor topology node requires agent_name")
            if not isinstance(depends_on, list) or not all(isinstance(agent, str) for agent in depends_on):
                raise ValueError("supervisor topology node depends_on must be a string list")
            if condition is not None and not isinstance(condition, str):
                raise ValueError("supervisor topology node condition must be a string")
            if not isinstance(inputs, dict):
                raise ValueError("supervisor topology node inputs must be an object")
            node_specs.append(
                {
                    "agent_name": agent_name.strip(),
                    "depends_on": tuple(depends_on),
                    "condition": condition,
                    "inputs": inputs,
                }
            )
        return cls(
            node_specs=tuple(node_specs),
            source=source,
            source_detail=source_detail,
        )

    def build_nodes(self, context: SupervisorTopologyContext) -> tuple[SupervisorNode, ...]:
        return tuple(
            SupervisorNode(
                spec["agent_name"],
                depends_on=spec["depends_on"],
                condition=spec["condition"],
                inputs=_render_context_values(spec["inputs"], context),
            )
            for spec in self.node_specs
        )


def default_supervisor_topology_policy() -> SupervisorTopologyPolicy:
    return load_supervisor_topology_policy("")


def load_supervisor_topology_policy(path: str | None = None) -> DefaultSupervisorTopologyPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_SUPERVISOR_TOPOLOGY_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return DefaultSupervisorTopologyPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )


def _render_context_values(payload: dict[str, Any], context: SupervisorTopologyContext) -> dict[str, Any]:
    return {
        str(key): _render_value(value, context)
        for key, value in payload.items()
    }


def _render_value(value: Any, context: SupervisorTopologyContext) -> Any:
    if isinstance(value, str):
        return value.format(
            problem_intent=context.problem_intent,
            handoff_reason=context.handoff_reason,
            expected_action=context.expected_action,
        )
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, dict):
        return _render_context_values(value, context)
    return value
