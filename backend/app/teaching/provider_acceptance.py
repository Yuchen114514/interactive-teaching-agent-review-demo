from __future__ import annotations

from dataclasses import dataclass
import json
from time import perf_counter
from typing import Any

from app.config import Settings
from app.schemas import TeachingTurnRequest
from app.teaching.agent_nodes import AlgorithmIdentifierNode
from app.teaching.agent_providers import (
    AgnoTeachingAgentProviderAdapter,
    TeachingAgentProviderEvent,
    _build_default_agno_agent_factory,
)
from app.teaching.taxonomy import load_default_taxonomy


DEFAULT_ACCEPTANCE_MESSAGE = "有很多区间加一操作，最后要求输出每个位置的最终值。"
DEFAULT_ACCEPTANCE_TARGET_AGENT = "AlgorithmIdentifierAgent"
DEFAULT_ACCEPTANCE_TARGET_CAPABILITY = "algorithm_family"
DEFAULT_ACCEPTANCE_RECOMMENDED_MODEL = "doubao-seed-1-6-250615"
DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS = 45.0
DEFAULT_ACCEPTANCE_PROVIDER_TIMEOUT_SECONDS = 40.0
DEFAULT_ACCEPTANCE_JSON_MODE = False


@dataclass(frozen=True)
class ProviderAcceptanceResult:
    status: str
    accepted: bool
    model: str
    backend: str = "agno"
    family_id: str | None = None
    source: str | None = None
    latency_ms: int = 0
    prompt_size_bytes: int = 0
    payload_size_bytes: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    token_usage_source: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "backend": self.backend,
            "model": self.model,
            "family_id": self.family_id,
            "source": self.source,
            "latency_ms": self.latency_ms,
            "prompt_size_bytes": self.prompt_size_bytes,
            "payload_size_bytes": self.payload_size_bytes,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "token_usage_source": self.token_usage_source,
            "reason": self.reason,
        }


def run_algorithm_identifier_acceptance(
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 20.0,
    provider_timeout_seconds: float = 10.0,
    json_mode: bool = False,
    message: str = DEFAULT_ACCEPTANCE_MESSAGE,
    family_provider: Any | None = None,
) -> ProviderAcceptanceResult:
    if not api_key.strip():
        return ProviderAcceptanceResult(
            status="skipped",
            accepted=False,
            model=model,
            reason="missing_api_key",
        )

    events: list[TeachingAgentProviderEvent] = []
    provider = family_provider
    if provider is None:
        settings = Settings(
            app_env="test",
            external_llm_api_key=api_key,
            external_llm_model=model,
            external_llm_base_url=base_url,
            external_llm_timeout_seconds=timeout_seconds,
            teaching_agent_provider_timeout_seconds=provider_timeout_seconds,
            teaching_agent_provider_json_mode=json_mode,
            database_url="sqlite:///provider_acceptance.db",
            token_secret="provider-acceptance",
        )
        adapter = AgnoTeachingAgentProviderAdapter(
            _build_default_agno_agent_factory(settings),
            event_sink=events.append,
            timeout_seconds=provider_timeout_seconds,
        )
        provider = adapter.provider_for("AlgorithmIdentifierAgent", capability="algorithm_family")

    started_at = perf_counter()
    _, family, source = AlgorithmIdentifierNode(
        load_default_taxonomy(),
        family_provider=provider,
    ).identify(TeachingTurnRequest(message=message, guidance_depth="coached"))
    latency_ms = max(0, int((perf_counter() - started_at) * 1000))
    latest_event = events[-1] if events else None
    trace_payload = latest_event.to_trace_payload() if latest_event is not None else {}
    accepted = source == "agent"

    return ProviderAcceptanceResult(
        status="accepted" if accepted else "fallback",
        accepted=accepted,
        model=model,
        family_id=family.family_id,
        source=source,
        latency_ms=int(trace_payload.get("latency_ms") or latency_ms),
        prompt_size_bytes=int(trace_payload.get("prompt_size_bytes") or _estimate_prompt_size(message)),
        payload_size_bytes=int(trace_payload.get("payload_size_bytes") or _estimate_payload_size(message)),
        prompt_tokens=_nonnegative_int(trace_payload.get("prompt_tokens")),
        completion_tokens=_nonnegative_int(trace_payload.get("completion_tokens")),
        total_tokens=_nonnegative_int(trace_payload.get("total_tokens")),
        token_usage_source=(
            str(trace_payload.get("token_usage_source"))
            if isinstance(trace_payload.get("token_usage_source"), str)
            else None
        ),
        reason=None if accepted else str(trace_payload.get("fallback_reason") or "provider_not_accepted"),
    )


def acceptance_result_json(result: ProviderAcceptanceResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _estimate_prompt_size(message: str) -> int:
    return len(message.encode("utf-8"))


def _estimate_payload_size(message: str) -> int:
    return len(json.dumps({"message": message}, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return 0
