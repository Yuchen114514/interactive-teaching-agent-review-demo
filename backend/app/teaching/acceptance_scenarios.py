from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings
from app.schemas import TeachingTurnRequest
from app.teaching.agent_providers import (
    AgnoTeachingAgentProviderAdapter,
    TeachingAgentProviderEvent,
    TeachingAgentProviderTelemetry,
    TeachingAgentProviders,
    _build_default_agno_agent_factory,
)
from app.teaching.orchestrator import TeachingOrchestrator
from app.teaching.provider_acceptance import DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS


DEFAULT_STUDENT_MESSAGES = [
    "这题有很多区间加一操作，最后问每个位置的值。先讲讲算法思想。",
    "我明白了，可以继续下一步。",
    "讲细一点，我想确认当前步骤为什么这么做。",
]
VISIBLE_ANSWER_FIRST_TURN_BUDGET_MS = 40000
VISIBLE_ANSWER_FOLLOWUP_BUDGET_MS = 20000

COLLABORATION_STUDENT_TURNS = [
    {
        "message": "这题有很多区间加一操作，最后问每个位置的值。直接讲算法思想。",
        "guidance_depth": "direct",
        "depth_override": True,
        "problem_id": 42,
        "reuse_workspace": False,
    },
    {
        "message": "我想要更细一点的引导。",
        "guidance_depth": "coached",
        "depth_override": True,
        "problem_id": 42,
        "reuse_workspace": True,
    },
    {
        "message": "我还是卡住了，不太理解。",
        "guidance_depth": "coached",
        "depth_override": False,
        "problem_id": 42,
        "reuse_workspace": True,
    },
    {
        "message": "请画图说明当前状态变化。",
        "guidance_depth": "coached",
        "depth_override": False,
        "problem_id": 42,
        "reuse_workspace": True,
    },
    {
        "message": "开始写代码。",
        "guidance_depth": "coached",
        "depth_override": False,
        "problem_id": 42,
        "reuse_workspace": True,
    },
    {
        "message": "开始 debug。",
        "guidance_depth": "coached",
        "depth_override": False,
        "problem_id": 42,
        "submission_id": 9001,
        "reuse_workspace": True,
    },
    {
        "message": "换一道新题，这次是排序相关。",
        "guidance_depth": "direct",
        "depth_override": True,
        "problem_id": 43,
        "reuse_workspace": False,
    },
]


@dataclass(frozen=True)
class AcceptanceTurnSummary:
    turn_index: int
    current_step_index: int
    total_reached_steps: int
    current_step_title: str
    guidance_depth: str
    trace_agents: tuple[str, ...]
    provider_payload_count: int
    total_trace_latency_ms: int
    slowest_agent: str | None
    slowest_agent_latency_ms: int
    response_preview: str
    visible_answer_latency_ms: int = 0
    provider_prompt_tokens: int = 0
    provider_completion_tokens: int = 0
    provider_total_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "current_step_index": self.current_step_index,
            "total_reached_steps": self.total_reached_steps,
            "current_step_title": self.current_step_title,
            "guidance_depth": self.guidance_depth,
            "trace_agents": list(self.trace_agents),
            "provider_payload_count": self.provider_payload_count,
            "total_trace_latency_ms": self.total_trace_latency_ms,
            "visible_answer_latency_ms": self.visible_answer_latency_ms,
            "slowest_agent": self.slowest_agent,
            "slowest_agent_latency_ms": self.slowest_agent_latency_ms,
            "provider_prompt_tokens": self.provider_prompt_tokens,
            "provider_completion_tokens": self.provider_completion_tokens,
            "provider_total_tokens": self.provider_total_tokens,
            "response_preview": self.response_preview,
        }


@dataclass(frozen=True)
class AcceptanceScenarioResult:
    scenario_id: str
    status: str
    turn_count: int
    checks: dict[str, bool]
    failed_checks: list[str]
    performance: dict[str, Any]
    turns: tuple[AcceptanceTurnSummary, ...]
    real_provider: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status,
            "turn_count": self.turn_count,
            "checks": self.checks,
            "failed_checks": self.failed_checks,
            "performance": self.performance,
            "turns": [turn.to_dict() for turn in self.turns],
            "real_provider": self.real_provider,
        }


@dataclass(frozen=True)
class SelectiveRealProviderConfig:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: float
    provider_timeout_seconds: float
    json_mode: bool = False


def build_selective_real_provider_config(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: float = 20.0,
    provider_timeout_seconds: float = 10.0,
    json_mode: bool = False,
) -> SelectiveRealProviderConfig:
    return SelectiveRealProviderConfig(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        provider_timeout_seconds=provider_timeout_seconds,
        json_mode=json_mode,
    )


def run_mock_student_acceptance_scenario(
    *,
    messages: list[str] | None = None,
    problem_message: str | None = None,
    real_provider_config: SelectiveRealProviderConfig | None = None,
) -> AcceptanceScenarioResult:
    scenario_messages = list(messages or DEFAULT_STUDENT_MESSAGES)
    if problem_message is not None and problem_message.strip():
        scenario_messages[0] = problem_message.strip()
    providers, telemetry = _build_selective_real_providers(real_provider_config)
    turns, responses = _run_guidance_acceptance_turns(
        scenario_messages,
        providers=providers,
    )

    checks = _evaluate_mock_student_checks(responses)
    real_provider_summary = _summarize_real_provider(real_provider_config, telemetry)
    if real_provider_config is not None:
        checks["real_provider_algorithm_accepted"] = bool(real_provider_summary.get("accepted"))
    failed_checks = [name for name, passed in checks.items() if not passed]
    return AcceptanceScenarioResult(
        scenario_id="mock_student_guidance_depth_control",
        status="passed" if not failed_checks else "failed",
        turn_count=len(turns),
        checks=checks,
        failed_checks=failed_checks,
        performance=_summarize_performance(
            turns,
            turn_latency_budget_ms=int(DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS * 1000),
        ),
        turns=tuple(turns),
        real_provider=real_provider_summary if real_provider_config is not None else None,
    )


def run_mock_student_collaboration_acceptance_scenario(
    *,
    problem_message: str | None = None,
    real_provider_config: SelectiveRealProviderConfig | None = None,
) -> AcceptanceScenarioResult:
    scenario_turns = [dict(turn) for turn in COLLABORATION_STUDENT_TURNS]
    if problem_message is not None and problem_message.strip():
        scenario_turns[0]["message"] = problem_message.strip()
    providers, telemetry = _build_selective_real_providers(real_provider_config)
    turns, responses = _run_collaboration_acceptance_turns(
        scenario_turns,
        providers=providers,
    )

    checks = _evaluate_collaboration_checks(responses)
    real_provider_summary = _summarize_real_provider(real_provider_config, telemetry)
    if real_provider_config is not None:
        checks["real_provider_algorithm_accepted"] = bool(real_provider_summary.get("accepted"))
    failed_checks = [name for name, passed in checks.items() if not passed]
    return AcceptanceScenarioResult(
        scenario_id="mock_student_collaboration_breadth",
        status="passed" if not failed_checks else "failed",
        turn_count=len(turns),
        checks=checks,
        failed_checks=failed_checks,
        performance=_summarize_performance(
            turns,
            turn_latency_budget_ms=int(DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS * 1000),
        ),
        turns=tuple(turns),
        real_provider=real_provider_summary if real_provider_config is not None else None,
    )


def run_runtime_equivalent_acceptance_scenario(
    *,
    scenario_id: str,
    providers: TeachingAgentProviders,
    enabled_agents: tuple[str, ...],
    model: str | None = None,
    problem_message: str | None = None,
) -> AcceptanceScenarioResult:
    if scenario_id == "mock_student_collaboration_breadth":
        scenario_turns = [dict(turn) for turn in COLLABORATION_STUDENT_TURNS]
        if problem_message is not None and problem_message.strip():
            scenario_turns[0]["message"] = problem_message.strip()
        turns, responses = _run_collaboration_acceptance_turns(
            scenario_turns,
            providers=providers,
        )
        checks = _evaluate_collaboration_checks(responses)
    else:
        scenario_messages = list(DEFAULT_STUDENT_MESSAGES)
        if problem_message is not None and problem_message.strip():
            scenario_messages[0] = problem_message.strip()
        turns, responses = _run_guidance_acceptance_turns(
            scenario_messages,
            providers=providers,
        )
        checks = _evaluate_mock_student_checks(responses)

    failed_checks = [name for name, passed in checks.items() if not passed]
    return AcceptanceScenarioResult(
        scenario_id=scenario_id,
        status="passed" if not failed_checks else "failed",
        turn_count=len(turns),
        checks=checks,
        failed_checks=failed_checks,
        performance=_summarize_performance(
            turns,
            turn_latency_budget_ms=int(DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS * 1000),
        ),
        turns=tuple(turns),
        real_provider=_summarize_runtime_providers(
            enabled_agents=enabled_agents,
            telemetry=providers.telemetry,
            model=model,
        ),
    )


def acceptance_scenario_json(result: AcceptanceScenarioResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _run_guidance_acceptance_turns(
    scenario_messages: list[str],
    *,
    providers: TeachingAgentProviders,
) -> tuple[list[AcceptanceTurnSummary], list[Any]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    turns: list[AcceptanceTurnSummary] = []
    responses: list[Any] = []
    with Session(engine) as session:
        orchestrator = TeachingOrchestrator(session, agent_providers=providers)
        workspace_id: int | None = None
        for index, message in enumerate(scenario_messages):
            response = orchestrator.run_turn(
                TeachingTurnRequest(
                    message=message,
                    guidance_depth="coached" if index < 2 else "socratic",
                    workspace_id=workspace_id,
                    depth_override=index == 2,
                ),
                session_numeric_id=1,
                user_id=19,
                turn_index=index,
            )
            workspace_id = response.workspace_id
            responses.append(response)
            turns.append(_summarize_turn(index, response))
    return turns, responses


def _run_collaboration_acceptance_turns(
    scenario_turns: list[dict[str, Any]],
    *,
    providers: TeachingAgentProviders,
) -> tuple[list[AcceptanceTurnSummary], list[Any]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    turns: list[AcceptanceTurnSummary] = []
    responses: list[Any] = []
    with Session(engine) as session:
        orchestrator = TeachingOrchestrator(session, agent_providers=providers)
        workspace_id: int | None = None
        for index, turn in enumerate(scenario_turns):
            request_kwargs = {
                "message": turn["message"],
                "guidance_depth": turn["guidance_depth"],
                "depth_override": turn["depth_override"],
                "problem_id": turn["problem_id"],
            }
            if isinstance(turn.get("submission_id"), int):
                request_kwargs["submission_id"] = turn["submission_id"]
            if turn["reuse_workspace"] and workspace_id is not None:
                request_kwargs["workspace_id"] = workspace_id
            response = orchestrator.run_turn(
                TeachingTurnRequest(**request_kwargs),
                session_numeric_id=1,
                user_id=19,
                turn_index=index,
            )
            workspace_id = response.workspace_id
            responses.append(response)
            turns.append(_summarize_turn(index, response))
    return turns, responses


def _summarize_turn(turn_index: int, response: Any) -> AcceptanceTurnSummary:
    provider_payload_count = sum(1 for trace in response.agent_trace if trace.payload.get("provider"))
    trace_latencies = [max(0, int(trace.latency_ms)) for trace in response.agent_trace]
    total_trace_latency_ms = sum(trace_latencies)
    visible_answer_latency_ms = _visible_answer_latency_ms(response.agent_trace)
    slowest_trace = max(response.agent_trace, key=lambda trace: max(0, int(trace.latency_ms)), default=None)
    token_totals = _provider_token_totals_from_traces(response.agent_trace)
    return AcceptanceTurnSummary(
        turn_index=turn_index,
        current_step_index=response.teaching_state.current_step_index,
        total_reached_steps=response.teaching_state.total_reached_steps,
        current_step_title=response.teaching_state.current_step_title,
        guidance_depth=response.teaching_state.guidance_depth.value,
        trace_agents=tuple(trace.agent for trace in response.agent_trace),
        provider_payload_count=provider_payload_count,
        total_trace_latency_ms=total_trace_latency_ms,
        visible_answer_latency_ms=visible_answer_latency_ms,
        slowest_agent=slowest_trace.agent if slowest_trace is not None else None,
        slowest_agent_latency_ms=(
            max(0, int(slowest_trace.latency_ms)) if slowest_trace is not None else 0
        ),
        provider_prompt_tokens=token_totals["prompt_tokens"],
        provider_completion_tokens=token_totals["completion_tokens"],
        provider_total_tokens=token_totals["total_tokens"],
        response_preview=response.response[:120],
    )


def _summarize_performance(
    turns: list[AcceptanceTurnSummary],
    *,
    turn_latency_budget_ms: int,
) -> dict[str, Any]:
    total_trace_latency_ms = sum(turn.total_trace_latency_ms for turn in turns)
    p95_turn_trace_latency_ms = _nearest_rank_percentile(
        [turn.total_trace_latency_ms for turn in turns],
        percentile=95,
    )
    p95_visible_answer_latency_ms = _nearest_rank_percentile(
        [turn.visible_answer_latency_ms for turn in turns],
        percentile=95,
    )
    total_provider_payload_count = sum(turn.provider_payload_count for turn in turns)
    total_provider_prompt_tokens = sum(turn.provider_prompt_tokens for turn in turns)
    total_provider_completion_tokens = sum(turn.provider_completion_tokens for turn in turns)
    total_provider_tokens = sum(turn.provider_total_tokens for turn in turns)
    slowest_turn = max(turns, key=lambda turn: turn.total_trace_latency_ms, default=None)
    slowest_visible_answer_turn = max(turns, key=lambda turn: turn.visible_answer_latency_ms, default=None)
    slowest_agent_turn = max(turns, key=lambda turn: turn.slowest_agent_latency_ms, default=None)
    over_budget_turn_count = sum(
        1 for turn in turns if turn.total_trace_latency_ms > turn_latency_budget_ms
    )
    over_visible_answer_budget_turn_count = sum(
        1
        for turn in turns
        if turn.visible_answer_latency_ms
        > (
            VISIBLE_ANSWER_FIRST_TURN_BUDGET_MS
            if turn.turn_index == 0
            else VISIBLE_ANSWER_FOLLOWUP_BUDGET_MS
        )
    )
    return {
        "total_trace_latency_ms": total_trace_latency_ms,
        "max_turn_trace_latency_ms": slowest_turn.total_trace_latency_ms if slowest_turn else 0,
        "p95_turn_trace_latency_ms": p95_turn_trace_latency_ms,
        "max_visible_answer_latency_ms": (
            slowest_visible_answer_turn.visible_answer_latency_ms
            if slowest_visible_answer_turn
            else 0
        ),
        "p95_visible_answer_latency_ms": p95_visible_answer_latency_ms,
        "slowest_turn_index": slowest_turn.turn_index if slowest_turn else None,
        "slowest_agent": slowest_agent_turn.slowest_agent if slowest_agent_turn else None,
        "slowest_agent_latency_ms": slowest_agent_turn.slowest_agent_latency_ms if slowest_agent_turn else 0,
        "turn_latency_budget_ms": turn_latency_budget_ms,
        "over_budget_turn_count": over_budget_turn_count,
        "within_turn_latency_budget": over_budget_turn_count == 0,
        "visible_answer_first_turn_budget_ms": VISIBLE_ANSWER_FIRST_TURN_BUDGET_MS,
        "visible_answer_followup_budget_ms": VISIBLE_ANSWER_FOLLOWUP_BUDGET_MS,
        "over_visible_answer_budget_turn_count": over_visible_answer_budget_turn_count,
        "within_visible_answer_latency_budget": over_visible_answer_budget_turn_count == 0,
        "total_provider_payload_count": total_provider_payload_count,
        "total_provider_prompt_tokens": total_provider_prompt_tokens,
        "total_provider_completion_tokens": total_provider_completion_tokens,
        "total_provider_tokens": total_provider_tokens,
    }


def _visible_answer_latency_ms(agent_trace: list[Any]) -> int:
    total_latency_ms = 0
    for trace in agent_trace:
        total_latency_ms += max(0, int(trace.latency_ms))
        if trace.agent == "TutorAgent":
            return total_latency_ms
    return total_latency_ms


def _nearest_rank_percentile(values: list[int], *, percentile: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(max(0, int(value)) for value in values)
    rank = max(1, (percentile * len(sorted_values) + 99) // 100)
    return sorted_values[min(len(sorted_values) - 1, rank - 1)]


def _provider_token_totals_from_traces(traces: Any) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for trace in traces:
        provider = trace.payload.get("provider") if isinstance(getattr(trace, "payload", None), dict) else None
        if not isinstance(provider, dict):
            continue
        totals["prompt_tokens"] += _nonnegative_int(provider.get("prompt_tokens"))
        totals["completion_tokens"] += _nonnegative_int(provider.get("completion_tokens"))
        totals["total_tokens"] += _nonnegative_int(provider.get("total_tokens"))
    return totals


def _provider_token_totals_from_events(events: tuple[TeachingAgentProviderEvent, ...]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for event in events:
        payload = event.to_trace_payload()
        totals["prompt_tokens"] += _nonnegative_int(payload.get("prompt_tokens"))
        totals["completion_tokens"] += _nonnegative_int(payload.get("completion_tokens"))
        totals["total_tokens"] += _nonnegative_int(payload.get("total_tokens"))
    return totals


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return 0


def _evaluate_mock_student_checks(responses: list[Any]) -> dict[str, bool]:
    if len(responses) < 3:
        return {
            "agent_trace_visible": False,
            "algorithm_primer_present": False,
            "confirmation_advances_step": False,
            "no_repeated_confirmed_step": False,
            "current_progress_only": False,
            "provider_diagnostics_visible": False,
        }

    first, second, third = responses[:3]
    first_trace_agents = {trace.agent for trace in first.agent_trace}
    second_trace_agents = {trace.agent for trace in second.agent_trace}
    return {
        "agent_trace_visible": all(response.agent_trace for response in responses[:3]),
        "algorithm_primer_present": "AlgorithmPrimerAgent" in first_trace_agents and bool(first.response.strip()),
        "confirmation_advances_step": second.teaching_state.current_step_index > first.teaching_state.current_step_index,
        "no_repeated_confirmed_step": first.teaching_state.current_step_title not in second.response,
        "current_progress_only": all(
            response.teaching_state.total_reached_steps == response.teaching_state.current_step_index
            for response in responses[:3]
        ),
        "provider_diagnostics_visible": any(
            trace.payload.get("provider")
            for response in responses[:3]
            for trace in response.agent_trace
        )
        and "HintPolicyAgent" in second_trace_agents,
    }


def _evaluate_collaboration_checks(responses: list[Any]) -> dict[str, bool]:
    if len(responses) < len(COLLABORATION_STUDENT_TURNS):
        return {
            "direct_depth_supported": False,
            "manual_depth_deepening_visible": False,
            "stuck_turn_gets_encouragement": False,
            "diagram_specialist_triggered": False,
            "coding_specialist_triggered": False,
            "debug_specialist_triggered": False,
            "boardwork_refresh_visible": False,
            "new_problem_creates_isolated_workspace": False,
            "current_progress_only": False,
        }

    direct_turn, deepened_turn, stuck_turn, diagram_turn, coding_turn, debug_turn, new_problem_turn = responses[:7]
    return {
        "direct_depth_supported": direct_turn.teaching_state.guidance_depth.value == "direct",
        "manual_depth_deepening_visible": deepened_turn.teaching_state.guidance_depth.value == "coached",
        "stuck_turn_gets_encouragement": "encouragement_strategy_selected" in _event_types(stuck_turn),
        "diagram_specialist_triggered": "DiagramAgent" in _trace_agents(diagram_turn),
        "coding_specialist_triggered": "CodingAgent" in _trace_agents(coding_turn),
        "debug_specialist_triggered": "DebugAgent" in _trace_agents(debug_turn),
        "boardwork_refresh_visible": all(response.boardwork_refresh for response in responses[:7]),
        "new_problem_creates_isolated_workspace": (
            new_problem_turn.workspace_id != debug_turn.workspace_id
            and new_problem_turn.teaching_state.current_step_index == 1
        ),
        "current_progress_only": all(
            response.teaching_state.total_reached_steps == response.teaching_state.current_step_index
            for response in responses[:7]
        ),
    }


def _trace_agents(response: Any) -> set[str]:
    return {trace.agent for trace in response.agent_trace}


def _event_types(response: Any) -> set[str]:
    event_types: set[str] = set()
    for event in response.events:
        if isinstance(event, dict):
            event_type = event.get("event_type")
        else:
            event_type = getattr(event, "event_type", None)
        if isinstance(event_type, str):
            event_types.add(event_type)
    return event_types


def _build_selective_real_providers(
    config: SelectiveRealProviderConfig | None,
) -> tuple[TeachingAgentProviders, TeachingAgentProviderTelemetry | None]:
    if config is None or not config.api_key.strip():
        return TeachingAgentProviders(), None
    telemetry = TeachingAgentProviderTelemetry()
    settings = Settings(
        app_env="test",
        external_llm_api_key=config.api_key,
        external_llm_model=config.model,
        external_llm_base_url=config.base_url,
        external_llm_timeout_seconds=config.timeout_seconds,
        teaching_agent_provider_timeout_seconds=config.provider_timeout_seconds,
        teaching_agent_provider_json_mode=config.json_mode,
        database_url="sqlite:///acceptance_scenario.db",
        token_secret="acceptance-scenario",
    )
    adapter = AgnoTeachingAgentProviderAdapter(
        _build_default_agno_agent_factory(settings),
        event_sink=telemetry.record,
        timeout_seconds=config.provider_timeout_seconds,
    )
    return (
        TeachingAgentProviders(
            algorithm_family=adapter.provider_for("AlgorithmIdentifierAgent", capability="algorithm_family"),
            telemetry=telemetry,
        ),
        telemetry,
    )


def _summarize_real_provider(
    config: SelectiveRealProviderConfig | None,
    telemetry: TeachingAgentProviderTelemetry | None,
) -> dict[str, Any]:
    if config is None:
        return {}
    event = telemetry.latest_for("AlgorithmIdentifierAgent", capability="algorithm_family") if telemetry else None
    if event is None:
        return {
            "enabled_agents": ["AlgorithmIdentifierAgent"],
            "selection_mode": "selective_agent_override",
            "runtime_equivalent": False,
            "scope_note": "only AlgorithmIdentifierAgent.algorithm_family uses real provider",
            "model": config.model,
            "accepted": False,
            "reason": "no_provider_event",
        }
    payload = event.to_trace_payload()
    return {
        "enabled_agents": ["AlgorithmIdentifierAgent"],
        "selection_mode": "selective_agent_override",
        "runtime_equivalent": False,
        "scope_note": "only AlgorithmIdentifierAgent.algorithm_family uses real provider",
        "model": config.model,
        "backend": payload.get("backend"),
        "accepted": payload.get("accepted") is True,
        "outcome": payload.get("outcome"),
        "latency_ms": payload.get("latency_ms"),
        "prompt_size_bytes": payload.get("prompt_size_bytes"),
        "payload_size_bytes": payload.get("payload_size_bytes"),
        "prompt_tokens": payload.get("prompt_tokens"),
        "completion_tokens": payload.get("completion_tokens"),
        "total_tokens": payload.get("total_tokens"),
        "token_usage_source": payload.get("token_usage_source"),
        "reason": payload.get("fallback_reason"),
    }


def _summarize_runtime_providers(
    *,
    enabled_agents: tuple[str, ...],
    telemetry: TeachingAgentProviderTelemetry | None,
    model: str | None,
) -> dict[str, Any]:
    events = telemetry.events() if telemetry is not None else ()
    observed_agents = sorted({event.agent_name for event in events})
    accepted_agents = sorted({event.agent_name for event in events if event.accepted})
    fallback_agents = sorted({event.agent_name for event in events if not event.accepted})
    token_totals = _provider_token_totals_from_events(events)
    return {
        "enabled_agents": list(enabled_agents),
        "selection_mode": "runtime_registry",
        "runtime_equivalent": True,
        "scope_note": "uses current runtime provider registry",
        "model": model,
        "accepted": bool(events) and not fallback_agents,
        "observed_agents": observed_agents,
        "accepted_agents": accepted_agents,
        "fallback_agents": fallback_agents,
        "event_count": len(events),
        "prompt_tokens": token_totals["prompt_tokens"],
        "completion_tokens": token_totals["completion_tokens"],
        "total_tokens": token_totals["total_tokens"],
        "token_usage_source": "provider" if token_totals["total_tokens"] else None,
    }
