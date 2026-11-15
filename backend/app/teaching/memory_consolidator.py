from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models import MemoryScope
from app.schemas import TeachingTurnRequest
from app.teaching.agent_prompts import AgentPromptSpec, memory_consolidator_prompt_spec
from app.teaching.agent_nodes import build_collaboration_context_payload
from app.teaching.memory_consolidation_policy import (
    MemoryConsolidationPolicy,
    load_default_memory_consolidation_policy,
)


@dataclass(frozen=True)
class MemoryCandidate:
    memory_type: str
    content: str
    scope: MemoryScope
    entity_type: str | None = None
    entity_id: str | None = None
    confidence: float = 0.0
    importance: float = 0.0


@dataclass(frozen=True)
class _ParsedMemoryCandidates:
    candidates: list[MemoryCandidate] | None
    rejection_reason: str | None
    raw_candidate_count: int


class MemoryConsolidator:
    agent_name = "MemoryConsolidatorAgent"
    _allowed_memory_types = {"preference", "weakness"}

    def __init__(
        self,
        candidate_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        consolidation_policy: MemoryConsolidationPolicy | None = None,
    ):
        self.candidate_provider = candidate_provider
        self.prompt_spec = prompt_spec or memory_consolidator_prompt_spec()
        self.consolidation_policy = consolidation_policy or load_default_memory_consolidation_policy()
        self.last_source = "policy"
        self.last_rejection_reason: str | None = None

    def extract_candidates(
        self,
        request: TeachingTurnRequest,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> list[MemoryCandidate]:
        message = " ".join(request.message.strip().split())
        normalized = message.lower()
        self.last_rejection_reason = None
        if self._is_solution_like(normalized) and self.candidate_provider is None:
            self.last_source = "policy"
            return []
        agent_candidates = self._extract_agent_candidates(
            request,
            message,
            collaboration_context=collaboration_context,
        )
        if agent_candidates is not None:
            self.last_source = "agent"
            return agent_candidates
        self.last_source = "policy"

        if self._is_solution_like(normalized):
            return []

        if self.consolidation_policy.has_preference_signal(normalized):
            content = self._trim_memory_content(message)
            return [
                MemoryCandidate(
                    memory_type="preference",
                    content=content,
                    scope=MemoryScope.USER_PROFILE,
                    confidence=self.consolidation_policy.preference_confidence,
                    importance=self.consolidation_policy.preference_importance,
                )
            ]

        if request.problem_id is not None and self.consolidation_policy.has_weakness_signal(normalized):
            content = self._trim_memory_content(message)
            return [
                MemoryCandidate(
                    memory_type="weakness",
                    content=content,
                    scope=MemoryScope.PROBLEM,
                    entity_type="problem",
                    entity_id=str(request.problem_id),
                    confidence=self.consolidation_policy.weakness_confidence,
                    importance=self.consolidation_policy.weakness_importance,
                )
            ]

        return []

    def _extract_agent_candidates(
        self,
        request: TeachingTurnRequest,
        message: str,
        *,
        collaboration_context: dict[str, Any] | None,
    ) -> list[MemoryCandidate] | None:
        if self.candidate_provider is None:
            return None
        raw_payload = self.candidate_provider(
            self._build_agent_payload(
                request,
                message,
                collaboration_context=collaboration_context,
            )
        )
        parsed = self._parse_agent_candidates(raw_payload, request)
        if parsed.candidates is not None:
            self.last_rejection_reason = None
            return parsed.candidates
        if parsed.rejection_reason is None:
            self.last_rejection_reason = None
            return None

        repair_payload = self._build_agent_payload(
            request,
            message,
            collaboration_context=collaboration_context,
        )
        repair_payload["repair_context"] = self._memory_repair_context(
            previous_rejection_reason=parsed.rejection_reason,
            received_candidate_count=parsed.raw_candidate_count,
        )
        repaired_raw_payload = self.candidate_provider(repair_payload)
        repaired = self._parse_agent_candidates(repaired_raw_payload, request)
        if repaired.candidates is not None:
            self.last_rejection_reason = None
            return repaired.candidates
        self.last_rejection_reason = repaired.rejection_reason or parsed.rejection_reason
        return None

    def _build_agent_payload(
        self,
        request: TeachingTurnRequest,
        message: str,
        *,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": message,
            "problem_id": request.problem_id,
            "source": request.source,
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_candidates(
        self,
        raw_payload: dict[str, Any] | None,
        request: TeachingTurnRequest,
    ) -> _ParsedMemoryCandidates:
        if not isinstance(raw_payload, dict):
            return _ParsedMemoryCandidates(candidates=None, rejection_reason=None, raw_candidate_count=0)
        raw_candidates = raw_payload.get("candidates")
        if not isinstance(raw_candidates, list):
            return _ParsedMemoryCandidates(
                candidates=None,
                rejection_reason="candidates_not_list",
                raw_candidate_count=0,
            )
        parsed: list[MemoryCandidate] = []
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                return _ParsedMemoryCandidates(
                    candidates=None,
                    rejection_reason="candidate_not_object",
                    raw_candidate_count=len(raw_candidates),
                )
            candidate, rejection_reason = self._parse_agent_candidate(raw_candidate, request)
            if candidate is None:
                return _ParsedMemoryCandidates(
                    candidates=None,
                    rejection_reason=rejection_reason or "invalid_candidate",
                    raw_candidate_count=len(raw_candidates),
                )
            parsed.append(candidate)
        return _ParsedMemoryCandidates(candidates=parsed, rejection_reason=None, raw_candidate_count=len(raw_candidates))

    def _parse_agent_candidate(
        self,
        raw_candidate: dict[str, Any],
        request: TeachingTurnRequest,
    ) -> tuple[MemoryCandidate | None, str | None]:
        memory_type = raw_candidate.get("memory_type")
        content = raw_candidate.get("content")
        scope_value = raw_candidate.get("scope")
        confidence = raw_candidate.get("confidence")
        importance = raw_candidate.get("importance")
        if memory_type not in self._allowed_memory_types:
            return None, "invalid_memory_type"
        if not isinstance(content, str) or not content.strip():
            return None, "invalid_content"
        if self._is_solution_like(content.lower()):
            return None, "solution_like_content"
        if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
            return None, "invalid_confidence"
        if not isinstance(importance, int | float) or not 0.0 <= float(importance) <= 1.0:
            return None, "invalid_importance"
        try:
            scope = MemoryScope(scope_value)
        except ValueError:
            return None, "invalid_scope"
        entity_type = raw_candidate.get("entity_type")
        entity_id = raw_candidate.get("entity_id")
        if scope == MemoryScope.PROBLEM:
            if request.problem_id is None:
                return None, "problem_scope_without_problem_id"
            if entity_type not in {None, "problem"}:
                return None, "invalid_problem_entity_type"
            entity_type = "problem"
            entity_id = str(request.problem_id)
        else:
            entity_type = None
            entity_id = None
        return MemoryCandidate(
            memory_type=str(memory_type),
            content=self._trim_memory_content(content.strip()),
            scope=scope,
            entity_type=entity_type,
            entity_id=entity_id,
            confidence=float(confidence),
            importance=float(importance),
        ), None

    def _memory_repair_context(
        self,
        *,
        previous_rejection_reason: str,
        received_candidate_count: int,
    ) -> dict[str, Any]:
        return {
            "previous_rejection_reason": previous_rejection_reason,
            "received_candidate_count": received_candidate_count,
            "required_corrections": [
                "Return only candidates that match the required schema.",
                "When problem_id is null, do not return scope=problem.",
                "Use scope=user_profile only for durable cross-problem preferences or weaknesses.",
                "Return an empty candidates array if no durable user_profile memory applies.",
                "Do not store solution content, code, temporary progress, or vague continuation.",
            ],
        }

    def _is_solution_like(self, normalized: str) -> bool:
        return self.consolidation_policy.is_solution_like(normalized)

    def _trim_memory_content(self, message: str) -> str:
        return self.consolidation_policy.trim_content(message)
