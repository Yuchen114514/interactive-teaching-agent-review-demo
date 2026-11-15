from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_CONTRACT_CONFIG_PATH = Path(__file__).with_name("runtime_contracts.json")
FORBIDDEN_RUNTIME_CONTRACT_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
}


@dataclass(frozen=True)
class RuntimeContractConfig:
    policies: tuple[str, ...]
    policy_surfaces: dict[str, dict[str, object]]
    supervisor: dict[str, object]
    acceptance: dict[str, object]
    streaming: dict[str, object]
    cache: dict[str, object]
    tools: dict[str, object]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "RuntimeContractConfig":
        if _contains_forbidden_runtime_contract_key(payload):
            raise ValueError("runtime contract config contains problem-specific runtime rules")
        policies = payload.get("policies")
        policy_surfaces = payload.get("policy_surfaces")
        supervisor = payload.get("supervisor")
        acceptance = payload.get("acceptance")
        streaming = payload.get("streaming")
        cache = payload.get("cache")
        tools = payload.get("tools")
        if not isinstance(policies, list) or not policies or not all(isinstance(item, str) for item in policies):
            raise ValueError("runtime contract config requires non-empty policies")
        if not isinstance(policy_surfaces, dict):
            raise ValueError("runtime contract config requires policy_surfaces")
        if not isinstance(supervisor, dict):
            raise ValueError("runtime contract config requires supervisor")
        if not isinstance(acceptance, dict):
            raise ValueError("runtime contract config requires acceptance")
        if not isinstance(streaming, dict):
            raise ValueError("runtime contract config requires streaming")
        if not isinstance(cache, dict):
            raise ValueError("runtime contract config requires cache")
        if not isinstance(tools, dict):
            raise ValueError("runtime contract config requires tools")

        parsed_surfaces = _parse_policy_surfaces(policy_surfaces)
        if set(parsed_surfaces) != set(policies):
            raise ValueError("runtime contract policy registry must match policy_surfaces")
        return cls(
            policies=tuple(policies),
            policy_surfaces=parsed_surfaces,
            supervisor=_parse_supervisor_contract(supervisor),
            acceptance=_parse_acceptance_contract(acceptance),
            streaming=_parse_streaming_contract(streaming),
            cache=_parse_cache_contract(cache),
            tools=_parse_tools_contract(tools),
            source=source,
            source_detail=source_detail,
        )


def load_default_runtime_contract_config() -> RuntimeContractConfig:
    return load_runtime_contract_config("")


def load_runtime_contract_config(path: str | None = None) -> RuntimeContractConfig:
    if path is not None and path.strip():
        config_path = Path(path).expanduser()
        source = "custom_config"
    else:
        config_path = DEFAULT_RUNTIME_CONTRACT_CONFIG_PATH
        source = "bundled_config"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return RuntimeContractConfig.from_dict(
        payload,
        source=source,
        source_detail=config_path.name,
    )


def teaching_policy_registry(config: RuntimeContractConfig | None = None) -> list[str]:
    contract_config = config or load_default_runtime_contract_config()
    return list(contract_config.policies)


def teaching_policy_surfaces_with_provider_status(
    *,
    provider_fields: dict[str, str],
    enabled_agents: tuple[str, ...],
    provider_enabled: bool,
    config: RuntimeContractConfig | None = None,
) -> dict[str, dict[str, object]]:
    contract_config = config or load_default_runtime_contract_config()
    surfaces: dict[str, dict[str, object]] = {}
    for policy_name, surface in contract_config.policy_surfaces.items():
        owner_agent = str(surface["owner_agent"])
        owner_agents = tuple(agent for agent in owner_agent.split("|") if agent)
        provider_coverage = [
            {
                "owner_agent": agent_name,
                "provider_capability": provider_fields.get(agent_name),
                "provider_available": bool(provider_fields.get(agent_name) and agent_name in enabled_agents),
                "provider_unavailable_reason": _provider_unavailable_reason(
                    agent_name=agent_name,
                    provider_capability=provider_fields.get(agent_name),
                    enabled_agents=enabled_agents,
                    provider_enabled=provider_enabled,
                ),
            }
            for agent_name in owner_agents
        ]
        provider_capability = provider_coverage[0]["provider_capability"] if len(provider_coverage) == 1 else None
        surfaces[policy_name] = {
            **surface,
            "provider_capability": provider_capability,
            "provider_available": any(item["provider_available"] for item in provider_coverage),
            "provider_unavailable_reason": _surface_provider_unavailable_reason(provider_coverage),
        }
        if len(provider_coverage) > 1:
            surfaces[policy_name]["provider_coverage"] = provider_coverage
    return surfaces


def teaching_supervisor_runtime_contract(
    *,
    provider_timeout_seconds: float,
    allowed_agents: tuple[str, ...],
    optional_agents: tuple[str, ...],
    optional_agents_parallelizable: tuple[str, ...],
    config: RuntimeContractConfig | None = None,
) -> dict[str, object]:
    contract_config = config or load_default_runtime_contract_config()
    supervisor = _deep_copy_dict(contract_config.supervisor)
    scheduler = supervisor["scheduler"]
    if not isinstance(scheduler, dict):
        raise ValueError("runtime contract supervisor scheduler must be an object")
    provider_timeout_ms = int(provider_timeout_seconds * 1000)
    max_serial_provider_calls = int(scheduler["max_serial_provider_calls"])
    supervisor["allowed_agents"] = sorted(allowed_agents)
    supervisor["optional_agents"] = sorted(optional_agents)
    scheduler["provider_timeout_ms"] = provider_timeout_ms
    scheduler["max_serial_provider_timeout_ms"] = provider_timeout_ms * max_serial_provider_calls
    scheduler["optional_agents_parallelizable"] = sorted(optional_agents_parallelizable)
    optional_parallelism = scheduler["optional_parallelism"]
    if not isinstance(optional_parallelism, dict):
        raise ValueError("runtime contract optional_parallelism must be an object")
    optional_parallelism["max_parallel_agents"] = len(optional_agents_parallelizable)
    return supervisor


def teaching_cache_runtime_contract(config: RuntimeContractConfig | None = None) -> dict[str, object]:
    contract_config = config or load_default_runtime_contract_config()
    return _deep_copy_dict(contract_config.cache)


def teaching_acceptance_runtime_contract(config: RuntimeContractConfig | None = None) -> dict[str, object]:
    contract_config = config or load_default_runtime_contract_config()
    return _deep_copy_dict(contract_config.acceptance)


def teaching_streaming_runtime_contract(config: RuntimeContractConfig | None = None) -> dict[str, object]:
    contract_config = config or load_default_runtime_contract_config()
    return _deep_copy_dict(contract_config.streaming)


def teaching_tools_runtime_contract(
    *,
    mcp_enabled: bool,
    mcp_mode: str,
    config: RuntimeContractConfig | None = None,
) -> dict[str, object]:
    contract_config = config or load_default_runtime_contract_config()
    tools = _deep_copy_dict(contract_config.tools)
    mcp = tools["mcp"]
    if not isinstance(mcp, dict):
        raise ValueError("runtime contract mcp tools must be an object")
    mcp["enabled"] = mcp_enabled
    mcp["mode"] = mcp_mode
    return tools


def _parse_policy_surfaces(payload: dict[str, Any]) -> dict[str, dict[str, object]]:
    surfaces: dict[str, dict[str, object]] = {}
    for policy_name, surface in payload.items():
        if not isinstance(policy_name, str) or not isinstance(surface, dict):
            raise ValueError("runtime contract policy surface must be keyed objects")
        owner_agent = surface.get("owner_agent")
        surface_name = surface.get("surface")
        replacement_target = surface.get("replacement_target")
        guardrails = surface.get("guardrails")
        if not isinstance(owner_agent, str) or not owner_agent:
            raise ValueError("runtime contract policy surface requires owner_agent")
        if not isinstance(surface_name, str) or not surface_name:
            raise ValueError("runtime contract policy surface requires surface")
        if not isinstance(replacement_target, str) or not replacement_target:
            raise ValueError("runtime contract policy surface requires replacement_target")
        if not isinstance(guardrails, list) or not guardrails or not all(isinstance(item, str) for item in guardrails):
            raise ValueError("runtime contract policy surface requires guardrails")
        surfaces[policy_name] = {
            "owner_agent": owner_agent,
            "surface": surface_name,
            "replacement_target": replacement_target,
            "guardrails": list(guardrails),
        }
    return surfaces


def _parse_supervisor_contract(payload: dict[str, Any]) -> dict[str, object]:
    scheduler = payload.get("scheduler")
    context_quality = payload.get("context_quality")
    fallback_visibility = payload.get("fallback_visibility")
    blackboard_fields = payload.get("blackboard_fields")
    if not isinstance(payload.get("topology_policy"), str):
        raise ValueError("runtime contract supervisor requires topology_policy")
    if not isinstance(blackboard_fields, list) or not all(isinstance(item, str) for item in blackboard_fields):
        raise ValueError("runtime contract supervisor requires blackboard_fields")
    if not isinstance(scheduler, dict):
        raise ValueError("runtime contract supervisor requires scheduler")
    if not isinstance(context_quality, dict):
        raise ValueError("runtime contract supervisor requires context_quality")
    if not isinstance(fallback_visibility, dict):
        raise ValueError("runtime contract supervisor requires fallback_visibility")
    _require_string_list(scheduler, "compatibility_interfaces")
    _require_string_list(scheduler, "decision_phases")
    _require_string_list(scheduler, "bottleneck_mitigations")
    _require_string_list(scheduler, "early_prewarm_agents")
    _require_string_list(scheduler, "response_parallel_agents")
    _require_string_list(scheduler, "trace_fields")
    _require_string(scheduler, "primary_interface")
    _require_bool(scheduler, "topology_runtime_unified")
    _require_string(scheduler, "execution_profile")
    _require_string(scheduler, "supervisor_bottleneck")
    _require_string(scheduler, "provider_call_model")
    _require_int(scheduler, "max_serial_provider_calls")
    _require_string(scheduler, "interface_consolidation")
    _require_object(scheduler, "interface_migration_status")
    _require_string(scheduler, "bottleneck_control")
    _require_string(scheduler, "core_execution")
    _require_string_list(scheduler, "conditional_skip_agents")
    _require_string(scheduler, "conditional_skip_policy")
    _require_object(scheduler, "response_parallelism")
    response_parallelism = scheduler["response_parallelism"]
    _require_string(response_parallelism, "strategy")
    _require_int(response_parallelism, "max_parallel_agents")
    _require_bool(response_parallelism, "side_effects_deferred_until_ready")
    _require_string(response_parallelism, "fallback_policy")
    _require_string(scheduler, "optional_execution")
    _require_object(scheduler, "optional_parallelism")
    optional_parallelism = scheduler["optional_parallelism"]
    _require_string(optional_parallelism, "strategy")
    _require_bool(optional_parallelism, "prewarm_before_runtime_route")
    _require_bool(optional_parallelism, "side_effects_deferred_until_admitted")
    _require_string(scheduler, "final_admission_policy")
    _require_bool(context_quality, "downstream_flags_enabled")
    _require_string(context_quality, "blackboard_entry_type")
    _require_string(context_quality, "supervisor_feedback_field")
    _require_bool(context_quality, "cache_invalidation_enabled")
    _require_string(context_quality, "review_policy")
    _require_string_list(context_quality, "reviewing_payload_fields")
    _require_bool(fallback_visibility, "visible_in_tester_ui")
    _require_string(fallback_visibility, "ui_trace_field")
    _require_string(fallback_visibility, "timeout_rejection_field")
    return _deep_copy_dict(payload)


def _parse_cache_contract(payload: dict[str, Any]) -> dict[str, object]:
    if not isinstance(payload.get("enabled"), bool):
        raise ValueError("runtime contract cache requires enabled")
    if not isinstance(payload.get("quality_bypass_enabled"), bool):
        raise ValueError("runtime contract cache requires quality_bypass_enabled")
    _require_string_list(payload, "trace_fields")
    entries = payload.get("entries")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("runtime contract cache requires entries")
    for agent_name, entry in entries.items():
        if not isinstance(agent_name, str) or not isinstance(entry, dict):
            raise ValueError("runtime contract cache entries must be keyed objects")
        _require_string(entry, "scope")
        _require_string_list(entry, "keys")
        _require_string_list(entry, "invalidated_by")
    return _deep_copy_dict(payload)


def _parse_acceptance_contract(payload: dict[str, Any]) -> dict[str, object]:
    performance_summary = payload.get("performance_summary")
    if not isinstance(performance_summary, dict):
        raise ValueError("runtime contract acceptance requires performance_summary")
    _require_string(performance_summary, "source")
    _require_string(performance_summary, "history_endpoint")
    _require_string_list(performance_summary, "latency_fields")
    _require_string_list(performance_summary, "provider_diagnostic_fields")
    if "total_provider_payload_count" not in performance_summary["provider_diagnostic_fields"]:
        raise ValueError("runtime contract acceptance requires provider payload diagnostics")
    return _deep_copy_dict(payload)


def _parse_streaming_contract(payload: dict[str, Any]) -> dict[str, object]:
    _require_string(payload, "transport")
    _require_string(payload, "progress_event_type")
    _require_string(payload, "partial_response_event_type")
    _require_string(payload, "final_event_type")
    _require_string(payload, "visible_answer_metric")
    _require_string(payload, "visible_answer_metric_scope")
    _require_bool(payload, "partial_response_before_final")
    _require_string(payload, "debug_record_metric_surface")
    if payload["visible_answer_metric"] != "v2_stream_partial_response_latency_ms":
        raise ValueError("runtime contract streaming visible-answer metric mismatch")
    if payload["partial_response_event_type"] == payload["final_event_type"]:
        raise ValueError("runtime contract streaming requires distinct partial and final events")
    return _deep_copy_dict(payload)


def _parse_tools_contract(payload: dict[str, Any]) -> dict[str, object]:
    _require_string(payload, "owner_agent")
    mcp = payload.get("mcp")
    local = payload.get("local")
    if not isinstance(mcp, dict):
        raise ValueError("runtime contract tools requires mcp")
    if not isinstance(local, dict):
        raise ValueError("runtime contract tools requires local")
    _require_string(mcp, "owner_agent")
    _require_string(mcp, "trigger_policy")
    _require_string(mcp, "handoff_policy")
    _require_string(mcp, "v2_specialist_access")
    _require_string_list(mcp, "enabled_for_agents")
    diagram = local.get("diagram")
    if not isinstance(diagram, dict):
        raise ValueError("runtime contract tools requires local.diagram")
    _require_string(diagram, "owner_agent")
    _require_string_list(diagram, "tool_names")
    _require_string(diagram, "access")
    return _deep_copy_dict(payload)


def _provider_unavailable_reason(
    *,
    agent_name: str,
    provider_capability: str | None,
    enabled_agents: tuple[str, ...],
    provider_enabled: bool,
) -> str | None:
    if not provider_capability:
        return "provider_contract_missing"
    if not provider_enabled:
        return "provider_disabled"
    if agent_name not in enabled_agents:
        return "agent_not_enabled"
    return None


def _surface_provider_unavailable_reason(provider_coverage: list[dict[str, object]]) -> str | None:
    unavailable_reasons = {
        item["provider_unavailable_reason"]
        for item in provider_coverage
        if item["provider_unavailable_reason"] is not None
    }
    if not unavailable_reasons:
        return None
    if len(unavailable_reasons) == 1:
        return str(next(iter(unavailable_reasons)))
    return "mixed_provider_unavailable_reasons"


def _contains_forbidden_runtime_contract_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key in FORBIDDEN_RUNTIME_CONTRACT_KEYS:
                return True
            if _contains_forbidden_runtime_contract_key(nested):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_runtime_contract_key(item) for item in value)
    return False


def _require_string(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), str) or not payload[key]:
        raise ValueError(f"runtime contract requires string {key}")


def _require_bool(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), bool):
        raise ValueError(f"runtime contract requires boolean {key}")


def _require_int(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), int):
        raise ValueError(f"runtime contract requires integer {key}")


def _require_object(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), dict):
        raise ValueError(f"runtime contract requires object {key}")


def _require_string_list(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"runtime contract requires string list {key}")


def _deep_copy_dict(payload: dict[str, Any]) -> dict[str, object]:
    return json.loads(json.dumps(payload))
