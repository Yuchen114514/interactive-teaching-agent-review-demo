from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import perf_counter
from typing import Any

from agno.agent import Agent
from agno.models.openai.like import OpenAILike

from app.config import Settings
from app.teaching.agent_prompts import (
    AgentPromptSpec,
    prompt_spec_for_agent,
    teaching_supervisor_schedule_schema,
    teaching_supervisor_route_schema,
    teaching_supervisor_topology_schema,
)

AgentProvider = Callable[[dict[str, Any]], dict[str, Any] | None]

DEFAULT_TEACHING_AGENT_PROVIDER_REGISTRY_PATH = Path(__file__).with_name("teaching_agent_provider_registry.json")
FORBIDDEN_PROVIDER_REGISTRY_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
}
_SUPERVISOR_SCHEMA_LOADERS: dict[str, Callable[[], dict[str, Any]]] = {
    "teaching_supervisor_schedule": teaching_supervisor_schedule_schema,
    "teaching_supervisor_route": teaching_supervisor_route_schema,
    "teaching_supervisor_topology": teaching_supervisor_topology_schema,
}


def _required_provider_output_keys(agent_name: str, capability: str, payload: dict[str, Any]) -> set[str] | None:
    if agent_name == "MainDialogAgent" and capability == "main_dialog_turn":
        return {"visible_reply"}
    return None


def _matches_required_provider_output_keys(value: dict[str, Any], required_keys: set[str] | None) -> bool:
    return required_keys is None or required_keys.issubset(value)


class TeachingAgentProviderUnavailableError(RuntimeError):
    def __init__(
        self,
        *,
        agent_name: str,
        capability: str,
        outcome: str,
        provider_backend: str = "agno",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{agent_name}.{capability} provider unavailable: {outcome}")
        self.agent_name = agent_name
        self.capability = capability
        self.outcome = outcome
        self.provider_backend = provider_backend
        self.details = details or {}


@dataclass(frozen=True)
class AdapterPromptConfig:
    instruction: str


@dataclass(frozen=True)
class AgnoAgentDefaults:
    description_template: str
    instructions: tuple[str, ...]


@dataclass(frozen=True)
class TeachingAgentProviderRegistry:
    agents: dict[str, dict[str, object]]
    adapter_prompt: AdapterPromptConfig
    agno_agent_defaults: AgnoAgentDefaults
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "TeachingAgentProviderRegistry":
        if _contains_forbidden_provider_registry_key(payload):
            raise ValueError("teaching agent provider registry contains problem-specific runtime rules")
        raw_agents = payload.get("agents")
        raw_adapter_prompt = payload.get("adapter_prompt")
        raw_agno_defaults = payload.get("agno_agent_defaults")
        if not isinstance(raw_agents, dict) or not raw_agents:
            raise ValueError("teaching agent provider registry requires non-empty agents")
        adapter_prompt = _parse_adapter_prompt_config(raw_adapter_prompt)
        agno_defaults = _parse_agno_agent_defaults(raw_agno_defaults)
        agents: dict[str, dict[str, object]] = {}
        for agent_name, raw_spec in raw_agents.items():
            if not isinstance(agent_name, str) or not isinstance(raw_spec, dict):
                raise ValueError("teaching agent provider registry agents must be keyed objects")
            capabilities = raw_spec.get("capabilities")
            prompt_spec = raw_spec.get("prompt_spec", agent_name)
            schemas = raw_spec.get("schemas", {})
            if (
                not isinstance(capabilities, list)
                or not capabilities
                or not all(isinstance(item, str) and item for item in capabilities)
            ):
                raise ValueError(f"teaching agent provider registry requires capabilities: {agent_name}")
            if len(capabilities) != len(set(capabilities)):
                raise ValueError(f"teaching agent provider registry has duplicate capabilities: {agent_name}")
            if not isinstance(prompt_spec, str) or not prompt_spec:
                raise ValueError(f"teaching agent provider registry requires prompt_spec: {agent_name}")
            if not isinstance(schemas, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in schemas.items()):
                raise ValueError(f"teaching agent provider registry schemas must be string map: {agent_name}")
            missing_schema_capabilities = [capability for capability in schemas if capability not in capabilities]
            if missing_schema_capabilities:
                raise ValueError(
                    "teaching agent provider registry schema capability is not registered: "
                    f"{agent_name}:{missing_schema_capabilities}"
                )
            agents[agent_name] = {
                "capabilities": tuple(capabilities),
                "prompt_spec": prompt_spec,
                "schemas": dict(schemas),
            }
        return cls(
            agents=agents,
            adapter_prompt=adapter_prompt,
            agno_agent_defaults=agno_defaults,
            source=source,
            source_detail=source_detail,
        )

    def capabilities_for(self, agent_name: str) -> tuple[str, ...]:
        spec = self.agents[agent_name]
        capabilities = spec["capabilities"]
        if not isinstance(capabilities, tuple):
            raise ValueError(f"invalid capabilities for {agent_name}")
        return capabilities

    def primary_capability(self, agent_name: str) -> str | None:
        spec = self.agents.get(agent_name)
        if spec is None:
            return None
        return self.capabilities_for(agent_name)[0]

    def prompt_spec_name_for(self, agent_name: str) -> str:
        prompt_spec = self.agents[agent_name]["prompt_spec"]
        if not isinstance(prompt_spec, str):
            raise ValueError(f"invalid prompt spec name for {agent_name}")
        return prompt_spec

    def schema_name_for(self, agent_name: str, capability: str) -> str | None:
        schemas = self.agents[agent_name]["schemas"]
        if not isinstance(schemas, dict):
            raise ValueError(f"invalid schema map for {agent_name}")
        schema_name = schemas.get(capability)
        return schema_name if isinstance(schema_name, str) else None


def load_default_teaching_agent_provider_registry() -> TeachingAgentProviderRegistry:
    return load_teaching_agent_provider_registry("")


def load_teaching_agent_provider_registry(path: str | None = None) -> TeachingAgentProviderRegistry:
    if path is not None and path.strip():
        registry_path = Path(path).expanduser()
        source = "custom_config"
    else:
        registry_path = DEFAULT_TEACHING_AGENT_PROVIDER_REGISTRY_PATH
        source = "bundled_config"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("teaching agent provider registry must be a JSON object")
    return TeachingAgentProviderRegistry.from_dict(
        payload,
        source=source,
        source_detail=registry_path.name,
    )


def teaching_agent_provider_fields(registry: TeachingAgentProviderRegistry | None = None) -> dict[str, str]:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    fields: dict[str, str] = {}
    for agent_name in provider_registry.agents:
        fields[agent_name] = provider_registry.primary_capability(agent_name) or ""
    return fields


def teaching_agent_primary_capability(
    agent_name: str,
    registry: TeachingAgentProviderRegistry | None = None,
) -> str | None:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    return provider_registry.primary_capability(agent_name)


def teaching_agent_provider_capabilities(
    registry: TeachingAgentProviderRegistry | None = None,
) -> dict[str, list[str]]:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    capabilities: dict[str, list[str]] = {}
    for agent_name in provider_registry.agents:
        capabilities[agent_name] = list(provider_registry.capabilities_for(agent_name))
    return capabilities


def teaching_agent_provider_contracts(
    registry: TeachingAgentProviderRegistry | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    contracts: dict[str, dict[str, dict[str, Any]]] = {}
    for agent_name in provider_registry.agents:
        prompt_spec_name = provider_registry.prompt_spec_name_for(agent_name)
        spec = prompt_spec_for_agent(prompt_spec_name)
        agent_contracts: dict[str, dict[str, Any]] = {}
        for capability in provider_registry.capabilities_for(agent_name):
            schema_name = provider_registry.schema_name_for(agent_name, capability)
            required_schema = _provider_required_schema(spec, schema_name)
            agent_contracts[capability] = _contract_payload(spec, required_schema)
        contracts[agent_name] = agent_contracts
    return contracts


def _contract_payload(spec: AgentPromptSpec, required_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "purpose": spec.purpose,
        "required_schema": required_schema,
    }


def enabled_teaching_agent_names(settings: Settings) -> tuple[str, ...]:
    if not settings.teaching_agent_providers_enabled:
        return ()
    if settings.teaching_agent_provider_backend == "disabled":
        return ()
    return _parse_enabled_provider_agents(settings.teaching_agent_provider_agents)


@dataclass(frozen=True)
class TeachingAgentProviderEvent:
    agent_name: str
    capability: str
    provider_backend: str
    outcome: str
    accepted: bool
    latency_ms: int
    payload: dict[str, Any]

    def to_trace_payload(self) -> dict[str, Any]:
        trace_payload = {
            "capability": self.capability,
            "backend": self.provider_backend,
            "outcome": self.outcome,
            "accepted": self.accepted,
            "latency_ms": self.latency_ms,
            **self.payload,
        }
        if not self.accepted and "fallback_reason" not in trace_payload:
            trace_payload["fallback_reason"] = self.outcome
        return trace_payload


class TeachingAgentProviderTelemetry:
    def __init__(self) -> None:
        self._events: list[TeachingAgentProviderEvent] = []

    def record(self, event: TeachingAgentProviderEvent) -> None:
        self._events.append(event)

    def events(self) -> tuple[TeachingAgentProviderEvent, ...]:
        return tuple(self._events)

    def latest_for(self, agent_name: str, *, capability: str | None = None) -> TeachingAgentProviderEvent | None:
        for event in reversed(self._events):
            if event.agent_name == agent_name and (capability is None or event.capability == capability):
                return event
        return None

    def trace_payload_for(self, agent_name: str, *, capability: str | None = None) -> dict[str, Any]:
        event = self.latest_for(agent_name, capability=capability)
        if event is None:
            return {}
        return {"provider": event.to_trace_payload()}


@dataclass(frozen=True)
class TeachingAgentProviders:
    main_dialog_turn: AgentProvider | None = None
    supervisor_schedule: AgentProvider | None = None
    supervisor_route: AgentProvider | None = None
    supervisor_topology: AgentProvider | None = None
    problem_decision: AgentProvider | None = None
    algorithm_family: AgentProvider | None = None
    algorithm_primer: AgentProvider | None = None
    plan_revision: AgentProvider | None = None
    workspace_summary: AgentProvider | None = None
    plan: AgentProvider | None = None
    hint_policy: AgentProvider | None = None
    encouragement_strategy: AgentProvider | None = None
    tutor_response: AgentProvider | None = None
    memory_candidates: AgentProvider | None = None
    boardwork_handoff: AgentProvider | None = None
    diagram_handoff: AgentProvider | None = None
    coding_handoff: AgentProvider | None = None
    debug_handoff: AgentProvider | None = None
    telemetry: TeachingAgentProviderTelemetry | None = None


class AgnoTeachingAgentProviderAdapter:
    def __init__(
        self,
        agent_factory: Callable[[str], Any],
        *,
        event_sink: Callable[[TeachingAgentProviderEvent], None] | None = None,
        timeout_seconds: float | None = None,
        prompt_config: AdapterPromptConfig | None = None,
        repair_attempts: int = 1,
    ):
        self.agent_factory = agent_factory
        self.event_sink = event_sink
        self.timeout_seconds = timeout_seconds
        self.prompt_config = prompt_config or load_default_teaching_agent_provider_registry().adapter_prompt
        self.repair_attempts = max(0, repair_attempts)

    def provider_for(self, agent_name: str, *, capability: str) -> AgentProvider:
        def provider(payload: dict[str, Any]) -> dict[str, Any] | None:
            started_at = perf_counter()
            agent = self.agent_factory(agent_name)
            compact_payload = _compact_provider_payload(payload)
            prompt_metadata = _provider_prompt_metadata(
                agent_name=agent_name,
                capability=capability,
                payload=compact_payload,
                instruction=self.prompt_config.instruction,
            )
            if agent is None:
                event_payload = prompt_metadata
                self._record_event(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="agent_unavailable",
                    accepted=False,
                    started_at=started_at,
                    payload=event_payload,
                )
                raise TeachingAgentProviderUnavailableError(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="agent_unavailable",
                    details=event_payload,
                )
            prompt = self._build_prompt(agent_name, capability, payload)
            try:
                response = self._run_agent(agent, prompt)
            except TimeoutError:
                event_payload = {**prompt_metadata, "timeout_seconds": self.timeout_seconds}
                self._record_event(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="timeout",
                    accepted=False,
                    started_at=started_at,
                    payload=event_payload,
                )
                raise TeachingAgentProviderUnavailableError(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="timeout",
                    details=event_payload,
                )
            except Exception as exc:
                event_payload = {**prompt_metadata, "error_type": type(exc).__name__}
                self._record_event(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="exception",
                    accepted=False,
                    started_at=started_at,
                    payload=event_payload,
                )
                raise TeachingAgentProviderUnavailableError(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="exception",
                    details=event_payload,
                )
            content = str(response.content) if hasattr(response, "content") else str(response)
            token_usage = _response_token_usage_payload(response)
            required_keys = _required_provider_output_keys(agent_name, capability, payload)
            parsed = self._extract_json_object(content, required_keys=required_keys)
            if parsed is None:
                event_payload = {**prompt_metadata, **token_usage, "content_length": len(content)}
                self._record_event(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="non_json_response",
                    accepted=False,
                    started_at=started_at,
                    payload=event_payload,
                )
                parsed = self._repair_non_json_response(
                    agent=agent,
                    agent_name=agent_name,
                    capability=capability,
                    payload=payload,
                    required_keys=required_keys,
                )
                if parsed is not None:
                    return parsed
                raise TeachingAgentProviderUnavailableError(
                    agent_name=agent_name,
                    capability=capability,
                    outcome="non_json_response",
                    details=event_payload,
                )
            self._record_event(
                agent_name=agent_name,
                capability=capability,
                outcome="json_object",
                accepted=True,
                started_at=started_at,
                payload={
                    **prompt_metadata,
                    **token_usage,
                    "json_keys": sorted(str(key) for key in parsed.keys()),
                },
            )
            return parsed

        setattr(provider, "_teaching_agent_name", agent_name)
        setattr(provider, "_teaching_capability", capability)
        return provider

    def _repair_non_json_response(
        self,
        *,
        agent: Any,
        agent_name: str,
        capability: str,
        payload: dict[str, Any],
        required_keys: set[str] | None = None,
    ) -> dict[str, Any] | None:
        if self.repair_attempts <= 0:
            return None
        repair_prompt = self._build_repair_prompt(agent_name, capability, payload)
        compact_payload = _compact_provider_payload(payload)
        prompt_metadata = _provider_prompt_metadata(
            agent_name=agent_name,
            capability=capability,
            payload=compact_payload,
            instruction=self.prompt_config.instruction,
            prompt=repair_prompt,
        )
        started_at = perf_counter()
        try:
            response = self._run_agent(agent, repair_prompt)
        except TimeoutError:
            event_payload = {**prompt_metadata, "timeout_seconds": self.timeout_seconds}
            self._record_event(
                agent_name=agent_name,
                capability=capability,
                outcome="timeout",
                accepted=False,
                started_at=started_at,
                payload=event_payload,
            )
            raise TeachingAgentProviderUnavailableError(
                agent_name=agent_name,
                capability=capability,
                outcome="timeout",
                details=event_payload,
            )
        except Exception as exc:
            event_payload = {**prompt_metadata, "error_type": type(exc).__name__}
            self._record_event(
                agent_name=agent_name,
                capability=capability,
                outcome="exception",
                accepted=False,
                started_at=started_at,
                payload=event_payload,
            )
            raise TeachingAgentProviderUnavailableError(
                agent_name=agent_name,
                capability=capability,
                outcome="exception",
                details=event_payload,
            )
        content = str(response.content) if hasattr(response, "content") else str(response)
        token_usage = _response_token_usage_payload(response)
        parsed = self._extract_json_object(content, required_keys=required_keys)
        if parsed is None:
            self._record_event(
                agent_name=agent_name,
                capability=capability,
                outcome="non_json_response",
                accepted=False,
                started_at=started_at,
                payload={**prompt_metadata, **token_usage, "content_length": len(content)},
            )
            return None
        self._record_event(
            agent_name=agent_name,
            capability=capability,
            outcome="json_object",
            accepted=True,
            started_at=started_at,
            payload={
                **prompt_metadata,
                **token_usage,
                "json_keys": sorted(str(key) for key in parsed.keys()),
            },
        )
        return parsed

    def _run_agent(self, agent: Any, prompt: str) -> Any:
        if self.timeout_seconds is None:
            return agent.run(prompt)

        results: Queue[tuple[str, Any]] = Queue()

        def run() -> None:
            try:
                results.put(("response", agent.run(prompt)))
            except Exception as exc:
                results.put(("exception", exc))

        Thread(target=run, daemon=True).start()
        try:
            result_type, value = results.get(timeout=self.timeout_seconds)
        except Empty as exc:
            raise TimeoutError from exc
        if result_type == "exception":
            raise value
        return value

    def _record_event(
        self,
        *,
        agent_name: str,
        capability: str,
        outcome: str,
        accepted: bool,
        started_at: float,
        payload: dict[str, Any],
    ) -> None:
        if self.event_sink is None:
            return
        latency_ms = max(0, int((perf_counter() - started_at) * 1000))
        self.event_sink(
            TeachingAgentProviderEvent(
                agent_name=agent_name,
                capability=capability,
                provider_backend="agno",
                outcome=outcome,
                accepted=accepted,
                latency_ms=latency_ms,
                payload=payload,
            )
        )

    def _build_prompt(self, agent_name: str, capability: str, payload: dict[str, Any]) -> str:
        return json.dumps(
            {
                "agent_name": agent_name,
                "capability": capability,
                "instruction": self.prompt_config.instruction,
                "payload": _compact_provider_payload(payload),
            },
            ensure_ascii=False,
        )

    def _build_repair_prompt(self, agent_name: str, capability: str, payload: dict[str, Any]) -> str:
        return json.dumps(
            {
                "agent_name": agent_name,
                "capability": capability,
                "instruction": (
                    "Previous response was not a JSON object matching the complete output contract. "
                    "Return exactly one root JSON object and include every required output field. "
                    f"{self.prompt_config.instruction}"
                ),
                "payload": _compact_provider_payload(payload),
            },
            ensure_ascii=False,
        )

    def _extract_json_object(
        self,
        content: str,
        *,
        required_keys: set[str] | None = None,
    ) -> dict[str, Any] | None:
        stripped = content.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and _matches_required_provider_output_keys(parsed, required_keys):
            return parsed

        decoder = json.JSONDecoder()
        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and _matches_required_provider_output_keys(parsed, required_keys):
                return parsed
        return None


def build_teaching_agent_providers(
    settings: Settings,
    *,
    agno_agent_factory: Callable[[str], Any] | None = None,
    provider_telemetry: TeachingAgentProviderTelemetry | None = None,
    provider_registry: TeachingAgentProviderRegistry | None = None,
) -> TeachingAgentProviders:
    if not settings.teaching_agent_providers_enabled:
        return TeachingAgentProviders()
    if settings.teaching_agent_provider_backend == "disabled":
        return TeachingAgentProviders()
    if settings.teaching_agent_provider_backend == "agno":
        registry = provider_registry or load_default_teaching_agent_provider_registry()
        telemetry = provider_telemetry or TeachingAgentProviderTelemetry()
        adapter = AgnoTeachingAgentProviderAdapter(
            agno_agent_factory or _build_default_agno_agent_factory(settings, registry=registry),
            event_sink=telemetry.record,
            timeout_seconds=settings.teaching_agent_provider_timeout_seconds,
            prompt_config=registry.adapter_prompt,
        )
        enabled_agents = _parse_enabled_provider_agents(settings.teaching_agent_provider_agents, registry=registry)
        providers: dict[str, AgentProvider] = {}
        for agent_name in enabled_agents:
            for field in registry.capabilities_for(agent_name):
                providers[field] = adapter.provider_for(agent_name, capability=field)
        return TeachingAgentProviders(**providers, telemetry=telemetry)
    raise ValueError(f"Unsupported teaching agent provider backend: {settings.teaching_agent_provider_backend}")


def _parse_enabled_provider_agents(
    raw_agents: str,
    *,
    registry: TeachingAgentProviderRegistry | None = None,
) -> tuple[str, ...]:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    parsed = tuple(agent.strip() for agent in raw_agents.split(",") if agent.strip())
    enabled_agents = parsed
    unknown_agents = [agent for agent in enabled_agents if agent not in provider_registry.agents]
    if unknown_agents:
        raise ValueError(f"Unsupported teaching agent provider specialist: {', '.join(unknown_agents)}")
    return enabled_agents


def _provider_required_schema(spec: AgentPromptSpec, schema_name: str | None) -> dict[str, Any]:
    if schema_name is None:
        return spec.required_schema
    schema_loader = _SUPERVISOR_SCHEMA_LOADERS.get(schema_name)
    if schema_loader is None:
        raise ValueError(f"Unsupported teaching agent provider schema: {schema_name}")
    return schema_loader()


def _parse_adapter_prompt_config(raw_config: Any) -> AdapterPromptConfig:
    if not isinstance(raw_config, dict):
        raise ValueError("teaching agent provider registry requires adapter_prompt")
    instruction = raw_config.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("teaching agent provider registry adapter_prompt requires instruction")
    return AdapterPromptConfig(instruction=instruction.strip())


def _parse_agno_agent_defaults(raw_config: Any) -> AgnoAgentDefaults:
    if not isinstance(raw_config, dict):
        raise ValueError("teaching agent provider registry requires agno_agent_defaults")
    description_template = raw_config.get("description_template")
    instructions = raw_config.get("instructions")
    if not isinstance(description_template, str) or "{agent_name}" not in description_template:
        raise ValueError("teaching agent provider registry requires description_template with {agent_name}")
    if not isinstance(instructions, list) or not instructions or not all(isinstance(item, str) and item.strip() for item in instructions):
        raise ValueError("teaching agent provider registry requires agno_agent_defaults instructions")
    return AgnoAgentDefaults(
        description_template=description_template.strip(),
        instructions=tuple(item.strip() for item in instructions),
    )


def _contains_forbidden_provider_registry_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key in FORBIDDEN_PROVIDER_REGISTRY_KEYS:
                return True
            if _contains_forbidden_provider_registry_key(nested):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_provider_registry_key(item) for item in value)
    return False


def _build_default_agno_agent_factory(
    settings: Settings,
    *,
    registry: TeachingAgentProviderRegistry | None = None,
) -> Callable[[str], Agent]:
    provider_registry = registry or load_default_teaching_agent_provider_registry()
    agno_defaults = provider_registry.agno_agent_defaults
    max_tokens = (
        settings.teaching_agent_provider_max_tokens
        if settings.teaching_agent_provider_max_tokens > 0
        else None
    )
    model = OpenAILike(
        id=settings.external_llm_model,
        base_url=settings.external_llm_base_url,
        api_key=settings.external_llm_api_key,
        temperature=settings.llm_temperature,
        timeout=settings.external_llm_timeout_seconds,
        max_retries=settings.external_llm_max_retries,
        max_tokens=max_tokens,
        extra_body=_parse_provider_extra_body(settings.teaching_agent_provider_extra_body_json),
    )

    def factory(agent_name: str) -> Agent:
        prompt_spec_name = provider_registry.prompt_spec_name_for(agent_name)
        prompt_spec = prompt_spec_for_agent(prompt_spec_name)
        demo_language_instruction = (
            "English paper demo: all student-visible prose, including visible_reply, step titles, "
            "whiteboard text, diagram labels, and errors, MUST be in English. Override any earlier "
            "instruction requesting Chinese. Keep JSON field names and machine-readable codes unchanged."
            if settings.demo_mode else None
        )
        return Agent(
            model=model,
            tools=[],
            db=None,
            add_history_to_context=False,
            num_history_runs=0,
            read_chat_history=False,
            store_history_messages=False,
            session_id=f"teaching-v2:{agent_name}",
            description=agno_defaults.description_template.format(agent_name=agent_name),
            instructions=[
                *agno_defaults.instructions,
                f"Purpose: {prompt_spec.purpose}",
                f"System prompt: {prompt_spec.system_prompt}",
                "Use the required JSON schema supplied in the user payload.",
                *([demo_language_instruction] if demo_language_instruction else []),
            ],
            use_json_mode=settings.teaching_agent_provider_json_mode,
        )

    return factory


def _parse_provider_extra_body(raw_json: str) -> dict[str, Any] | None:
    raw = (raw_json or "").strip()
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("TEACHING_AGENT_PROVIDER_EXTRA_BODY_JSON must be a JSON object")
    if _contains_forbidden_provider_registry_key(parsed):
        raise ValueError("teaching agent provider extra_body contains problem-specific runtime rules")
    return parsed


def _compact_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted = {
        key: value
        for key, value in payload.items()
        if key not in {"agent_name", "purpose", "system_prompt"}
    }
    collaboration_context = compacted.get("collaboration_context")
    if isinstance(collaboration_context, dict):
        compacted["collaboration_context"] = _compact_collaboration_context(collaboration_context)
    return compacted


def _compact_collaboration_context(context: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    upstream_agents = context.get("upstream_agents")
    if isinstance(upstream_agents, list):
        compacted["upstream_agents"] = [
            {
                "agent": _truncate_text(str(item.get("agent") or ""), 80),
                "summary": _truncate_text(str(item.get("summary") or ""), 140),
            }
            for item in upstream_agents[:6]
            if isinstance(item, dict) and item.get("agent") and item.get("summary")
        ]

    blackboard_summary = context.get("blackboard_summary")
    if isinstance(blackboard_summary, list):
        compacted["blackboard_summary"] = [
            _truncate_text(item, 140)
            for item in blackboard_summary[:6]
            if isinstance(item, str) and item.strip()
        ]

    context_snapshot = context.get("context_snapshot")
    if isinstance(context_snapshot, dict):
        compacted["context_snapshot"] = _compact_snapshot(context_snapshot)

    upstream_quality_flags = context.get("upstream_quality_flags")
    if isinstance(upstream_quality_flags, list) and upstream_quality_flags:
        compacted["upstream_quality_flags"] = [
            {
                "flagged_agent": _truncate_text(str(flag.get("flagged_agent") or ""), 80),
                "severity": str(flag.get("severity") or ""),
                "reason": _truncate_text(str(flag.get("reason") or ""), 120),
                "suggested_action": _truncate_text(str(flag.get("suggested_action") or ""), 120),
            }
            for flag in upstream_quality_flags[:4]
            if isinstance(flag, dict)
            and flag.get("flagged_agent")
            and flag.get("severity")
            and flag.get("reason")
            and flag.get("suggested_action")
        ]

    return compacted


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    allowed_top_level = {
        "workspace_id",
        "problem_key",
        "problem_id",
        "problem_title",
        "problem_summary",
        "guidance_depth",
        "algorithm_family",
        "recent_turn_count",
    }
    compacted = {key: snapshot.get(key) for key in allowed_top_level if key in snapshot}
    current_step = snapshot.get("current_step")
    if isinstance(current_step, dict):
        compacted["current_step"] = {
            key: _truncate_text(value, 120) if isinstance(value, str) else value
            for key, value in current_step.items()
            if key
            in {
                "step_id",
                "step_key",
                "title",
                "repeat_count",
                "status",
                "mastery_state",
            }
        }
    for key in ("memory_policy", "workspace_summary", "compaction_policy"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            compacted[key] = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if isinstance(nested_value, (str, int, float, bool)) or nested_value is None
            }
    return compacted


def _truncate_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _provider_prompt_metadata(
    *,
    agent_name: str,
    capability: str,
    payload: dict[str, Any],
    instruction: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    prompt_text = prompt or json.dumps(
        {
            "agent_name": agent_name,
            "capability": capability,
            "instruction": instruction,
            "payload": payload,
        },
        ensure_ascii=False,
    )
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {
        "prompt_size_bytes": len(prompt_text.encode("utf-8")),
        "payload_size_bytes": len(payload_json.encode("utf-8")),
        "compact_payload_keys": sorted(str(key) for key in payload.keys()),
    }


def _response_token_usage_payload(response: Any) -> dict[str, Any]:
    for candidate in (
        getattr(response, "usage", None),
        getattr(response, "metrics", None),
        response,
    ):
        usage = _coerce_token_usage(candidate)
        if usage is not None:
            return {
                "prompt_tokens": usage[0],
                "completion_tokens": usage[1],
                "total_tokens": usage[2],
                "token_usage_source": "provider",
            }
    return {}


def _coerce_token_usage(value: Any) -> tuple[int, int, int] | None:
    prompt_tokens = _token_usage_int(value, "prompt_tokens", "input_tokens")
    completion_tokens = _token_usage_int(value, "completion_tokens", "output_tokens")
    total_tokens = _token_usage_int(value, "total_tokens")
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    prompt_tokens = max(0, prompt_tokens or 0)
    completion_tokens = max(0, completion_tokens or 0)
    total_tokens = max(0, total_tokens if total_tokens is not None else prompt_tokens + completion_tokens)
    if total_tokens < prompt_tokens + completion_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _token_usage_int(value: Any, *keys: str) -> int | None:
    for key in keys:
        raw_value: Any
        if isinstance(value, dict):
            raw_value = value.get(key)
        else:
            raw_value = getattr(value, key, None)
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float) and raw_value.is_integer():
            return int(raw_value)
    return None
