from __future__ import annotations

from dataclasses import dataclass, replace

from collections.abc import Callable
import re
from typing import Any

from pydantic import BaseModel, Field

from app.diagram_protocol import DiagramSceneModel, DiagramTeachingBundleModel, DiagramInlinePayloadModel
from app.diagram_verifier import verify_diagram_teaching_bundle
from app.models import AgentTeachingStep
from app.schemas import TeachingTurnRequest
from app.teaching.agent_prompts import (
    AgentPromptSpec,
    algorithm_identifier_prompt_spec,
    algorithm_primer_prompt_spec,
    boardwork_prompt_spec,
    coding_prompt_spec,
    debug_prompt_spec,
    diagram_prompt_spec,
    encouragement_prompt_spec,
    hint_policy_prompt_spec,
    memory_manager_prompt_spec,
    plan_decomposer_prompt_spec,
    plan_revision_prompt_spec,
    problem_identifier_prompt_spec,
    tutor_prompt_spec,
)
from app.teaching.agent_contracts import (
    build_source_event_payloads,
    build_step_payload,
    contains_forbidden_text,
    parse_string_list,
)
from app.teaching.diagram_request_policy import DiagramRequestPolicy, load_default_diagram_request_policy
from app.teaching.encouragement_policy import EncouragementFallbackPolicy, EncouragementFallbackSelection
from app.teaching.hint_signal_policy import HintSignalPolicy, load_default_hint_signal_policy
from app.teaching.intent_policy import ProblemIntentPolicy, load_default_intent_policy
from app.teaching.planning_policy import TeachingPlanPolicy, load_default_planning_policy
from app.teaching.plan_revision_policy import (
    PlanRevisionDecision,
    PlanRevisionPolicy,
    load_default_plan_revision_policy,
)
from app.teaching.primer_policy import AlgorithmPrimerPolicy
from app.teaching.specialist_handoff_policy import (
    SpecialistHandoffPolicy,
    load_default_specialist_handoff_policy,
)
from app.teaching.taxonomy import AlgorithmFamily, AlgorithmTaxonomy
from app.teaching.agent_providers import TeachingAgentProviderUnavailableError


@dataclass(frozen=True)
class AgentNodeResult:
    agent_name: str
    status: str
    input_summary: str
    output_summary: str
    blackboard_write_count: int = 0
    memory_write_count: int = 0


@dataclass(frozen=True)
class HintPolicyDecision:
    action: str
    encouragement: str | None = None
    encouragement_type: str | None = None
    student_state: str = "neutral"
    repeat_risk: str = "low"
    max_question_count: int = 1
    decision_source: str = "policy"
    reason: str | None = None

    @property
    def tactic(self) -> str:
        return self.action


@dataclass(frozen=True)
class ProblemDecision:
    intent: str
    problem_key: str | None = None
    confidence: float = 0.0
    should_start_new_workspace: bool = False
    reason: str = ""
    decision_source: str = "fallback"


class TutorOutput(BaseModel):
    visible_text: str
    step_delta: dict[str, str]
    student_feedback: str
    needs_boardwork: bool
    needs_diagram: bool
    upstream_quality_flags: list[dict[str, str]] = Field(default_factory=list)


class EncouragementStrategy(BaseModel):
    feedback_type: str
    visible_prefix: str
    rationale: str
    strategy_source: str = "policy"


def build_collaboration_context_payload(collaboration_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(collaboration_context, dict):
        return {"upstream_agents": [], "blackboard_summary": []}

    upstream_agents: list[dict[str, str]] = []
    raw_upstream_agents = collaboration_context.get("upstream_agents")
    if isinstance(raw_upstream_agents, list):
        for raw_agent in raw_upstream_agents[:12]:
            if not isinstance(raw_agent, dict):
                continue
            agent = raw_agent.get("agent")
            summary = raw_agent.get("summary")
            if isinstance(agent, str) and agent.strip() and isinstance(summary, str) and summary.strip():
                sanitized_summary = summary.strip()
                if contains_forbidden_text(sanitized_summary):
                    continue
                upstream_agents.append({"agent": agent.strip()[:80], "summary": sanitized_summary[:240]})

    blackboard_summary: list[str] = []
    raw_blackboard_summary = collaboration_context.get("blackboard_summary")
    if isinstance(raw_blackboard_summary, list):
        blackboard_summary = [
            item.strip()[:240]
            for item in raw_blackboard_summary[:12]
            if isinstance(item, str) and item.strip() and not contains_forbidden_text(item.strip())
        ]

    upstream_quality_flags = parse_upstream_quality_flags(collaboration_context.get("upstream_quality_flags"))

    payload = {
        "upstream_agents": upstream_agents,
        "blackboard_summary": blackboard_summary,
    }
    context_snapshot = _build_collaboration_context_snapshot_payload(
        collaboration_context.get("context_snapshot")
    )
    if context_snapshot:
        payload["context_snapshot"] = context_snapshot
    if upstream_quality_flags:
        payload["upstream_quality_flags"] = upstream_quality_flags
    return payload


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _safe_enum(value: Any, allowed: set[str], default: str | None = None) -> str | None:
    if isinstance(value, str) and value in allowed:
        return value
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _build_collaboration_context_snapshot_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    raw_step = value.get("current_step")
    current_step = raw_step if isinstance(raw_step, dict) else {}
    raw_memory_policy = value.get("memory_policy")
    memory_policy = raw_memory_policy if isinstance(raw_memory_policy, dict) else {}
    raw_workspace_summary = value.get("workspace_summary")
    workspace_summary = raw_workspace_summary if isinstance(raw_workspace_summary, dict) else {}
    raw_compaction_policy = value.get("compaction_policy")
    compaction_policy = raw_compaction_policy if isinstance(raw_compaction_policy, dict) else {}

    return {
        "workspace_id": _safe_scalar(value.get("workspace_id")),
        "problem_key": _safe_scalar(value.get("problem_key")),
        "problem_id": _safe_scalar(value.get("problem_id")),
        "problem_title": _safe_scalar(value.get("problem_title")),
        "problem_summary": _safe_scalar(value.get("problem_summary")),
        "guidance_depth": _safe_scalar(value.get("guidance_depth")),
        "algorithm_family": _safe_scalar(value.get("algorithm_family")),
        "current_step": {
            "step_id": _safe_scalar(current_step.get("step_id")),
            "step_key": _safe_scalar(current_step.get("step_key")),
            "title": _safe_scalar(current_step.get("title")),
            "repeat_count": _safe_scalar(current_step.get("repeat_count")),
            "status": _safe_scalar(current_step.get("status")),
            "mastery_state": _safe_scalar(current_step.get("mastery_state")),
        },
        "recent_turn_count": _safe_scalar(value.get("recent_turn_count")),
        "memory_policy": {
            "long_term_enabled": _safe_scalar(memory_policy.get("long_term_enabled")),
            "retrieved_memory_count": _safe_scalar(memory_policy.get("retrieved_memory_count")),
        },
        "workspace_summary": {
            "available": _safe_scalar(workspace_summary.get("available")),
            "preview": _safe_scalar(workspace_summary.get("preview")),
        },
        "compaction_policy": {
            "current_step_only": _safe_scalar(compaction_policy.get("current_step_only")),
            "event_window_limit": _safe_scalar(compaction_policy.get("event_window_limit")),
            "blackboard_summary_limit": _safe_scalar(compaction_policy.get("blackboard_summary_limit")),
            "raw_recent_turn_limit": _safe_scalar(compaction_policy.get("raw_recent_turn_limit")),
            "compacted_turn_count": _safe_scalar(compaction_policy.get("compacted_turn_count")),
        },
    }


def attach_upstream_quality_flags(target: dict[str, Any], raw_value: Any) -> None:
    upstream_quality_flags = parse_upstream_quality_flags(raw_value)
    if upstream_quality_flags:
        target["upstream_quality_flags"] = upstream_quality_flags


def parse_upstream_quality_flags(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    parsed: list[dict[str, str]] = []
    for raw_flag in value[:8]:
        if not isinstance(raw_flag, dict):
            continue
        flagged_agent = raw_flag.get("flagged_agent")
        severity = raw_flag.get("severity")
        reason = raw_flag.get("reason")
        suggested_action = raw_flag.get("suggested_action")
        if severity not in {"info", "warn", "blocker"}:
            continue
        if not (
            isinstance(flagged_agent, str)
            and flagged_agent.strip()
            and isinstance(reason, str)
            and reason.strip()
            and isinstance(suggested_action, str)
            and suggested_action.strip()
        ):
            continue
        if contains_forbidden_text(reason) or contains_forbidden_text(suggested_action):
            continue
        parsed.append(
            {
                "flagged_agent": flagged_agent.strip()[:80],
                "severity": severity,
                "reason": reason.strip()[:120],
                "suggested_action": suggested_action.strip()[:120],
            }
        )
    return parsed


class ProblemIdentifierNode:
    agent_name = "ProblemIdentifierAgent"
    _valid_intents = {"continue_problem", "new_problem", "debug_submission", "non_problem"}

    def __init__(
        self,
        intent_policy: ProblemIntentPolicy | None = None,
        decision_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.intent_policy = intent_policy or load_default_intent_policy()
        self.decision_provider = decision_provider
        self.prompt_spec = prompt_spec or problem_identifier_prompt_spec()
        self.last_rejection_reason: str | None = None

    def decide(self, request: TeachingTurnRequest) -> ProblemDecision:
        self.last_rejection_reason = None
        problem_key = self._problem_key(request)
        if request.submission_id is not None:
            return ProblemDecision(
                intent="debug_submission",
                problem_key=problem_key,
                confidence=0.95,
                should_start_new_workspace=False,
                reason="structured_submission_id",
                decision_source="structured",
            )
        if problem_key is not None:
            return ProblemDecision(
                intent="continue_problem",
                problem_key=problem_key,
                confidence=0.95,
                should_start_new_workspace=False,
                reason="structured_problem_id",
                decision_source="structured",
            )
        explicit_workspace_followup = self._is_explicit_workspace_followup(request)
        agent_decision = self._decide_with_agent(request)
        if agent_decision is not None:
            return agent_decision
        if explicit_workspace_followup:
            return ProblemDecision(
                intent="continue_problem",
                problem_key=problem_key,
                confidence=0.9,
                should_start_new_workspace=False,
                reason="explicit_workspace_followup",
                decision_source="policy",
            )
        if request.workspace_id is not None:
            return ProblemDecision(
                intent="continue_problem",
                problem_key=problem_key,
                confidence=1.0,
                should_start_new_workspace=False,
                reason="explicit_workspace_id",
                decision_source="structured",
            )
        if self.intent_policy.detect_new_problem_intent(request.message):
            return ProblemDecision(
                intent="new_problem",
                problem_key=None,
                confidence=0.75,
                should_start_new_workspace=True,
                reason="generic_new_problem_marker",
                decision_source="policy",
            )
        if not self.intent_policy.detect_problem_statement_candidate(request.message):
            if self._looks_like_standalone_non_problem(request.message):
                return ProblemDecision(
                    intent="non_problem",
                    problem_key=None,
                    confidence=0.7,
                    should_start_new_workspace=False,
                    reason="standalone_non_problem_smalltalk",
                    decision_source="policy",
                )
            return ProblemDecision(
                intent="continue_problem",
                problem_key=None,
                confidence=0.65,
                should_start_new_workspace=False,
                reason="short_teaching_turn",
                decision_source="policy",
            )
        return ProblemDecision(
            intent="continue_problem",
            problem_key=None,
            confidence=0.5,
            should_start_new_workspace=False,
            reason="default_continue",
        )

    def _looks_like_standalone_non_problem(self, message: str) -> bool:
        normalized = message.strip().lower()
        compact = re.sub(r"[\s，。！？,.!?：:；;“”\"'（）()\[\]【】]+", "", normalized)
        if compact in {"继续"}:
            return True
        standalone_non_problem_markers = (
            "哈喽",
            "你好",
            "您好",
            "你是谁",
            "打个招呼",
            "暂时还没有发题面",
            "暂时没发题面",
            "还没有发题面",
            "还没发题面",
            "没有发题面",
            "没发题面",
        )
        return any(marker in normalized for marker in standalone_non_problem_markers)

    def _problem_key(self, request: TeachingTurnRequest) -> str | None:
        if request.problem_id is None:
            return None
        return f"oj:problem:{request.problem_id}"

    def _is_explicit_workspace_followup(self, request: TeachingTurnRequest) -> bool:
        return (
            request.workspace_id is not None
            and self.intent_policy.detect_explicit_workspace_followup(request.message)
            and not self.intent_policy.detect_new_problem_intent(request.message)
            and not self.intent_policy.detect_problem_statement_candidate(request.message)
        )

    def _decide_with_agent(self, request: TeachingTurnRequest) -> ProblemDecision | None:
        if self.decision_provider is None:
            return None
        payload = self._build_agent_payload(request)
        raw_decision = self.decision_provider(payload)
        decision = self._parse_agent_decision(raw_decision)
        rejection_reason = self._agent_decision_rejection_reason(request, decision)
        if rejection_reason is None:
            return decision
        self.last_rejection_reason = rejection_reason
        latest_raw_decision = raw_decision
        latest_rejection_reason = rejection_reason
        for repair_attempt in range(1, 3):
            repaired_raw_decision = self.decision_provider(
                {
                    **payload,
                    "repair_context": self._problem_decision_repair_context(
                        request,
                        previous_rejection_reason=latest_rejection_reason,
                        raw_decision=latest_raw_decision,
                        repair_attempt=repair_attempt,
                    ),
                }
            )
            repaired_decision = self._parse_agent_decision(repaired_raw_decision)
            repaired_rejection_reason = self._agent_decision_rejection_reason(request, repaired_decision)
            if repaired_rejection_reason is None:
                self.last_rejection_reason = None
                return repaired_decision
            self.last_rejection_reason = repaired_rejection_reason
            latest_raw_decision = repaired_raw_decision
            latest_rejection_reason = repaired_rejection_reason
        return None

    def _agent_decision_rejection_reason(
        self,
        request: TeachingTurnRequest,
        decision: ProblemDecision | None,
    ) -> str | None:
        if decision is None:
            return None
        if (
            decision.intent == "non_problem"
            and self._is_explicit_workspace_followup(request)
        ):
            return "non_problem_explicit_workspace_followup"
        if decision.intent == "new_problem" and self._is_explicit_workspace_followup(request):
            return "new_problem_explicit_workspace_followup"
        return None

    def _build_agent_payload(self, request: TeachingTurnRequest) -> dict[str, Any]:
        explicit_workspace_followup = self._is_explicit_workspace_followup(request)
        problem_statement_candidate = self.intent_policy.detect_problem_statement_candidate(request.message)
        new_problem_marker_detected = self.intent_policy.detect_new_problem_intent(request.message)
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "source": request.source,
            "problem_id": request.problem_id,
            "submission_id": request.submission_id,
            "workspace_id": request.workspace_id,
            "problem_statement_candidate": problem_statement_candidate,
            "workspace_reference_target": self.intent_policy.detect_workspace_reference(request.message),
            "classification_contract": self._problem_classification_contract(
                request,
                explicit_workspace_followup=explicit_workspace_followup,
                problem_statement_candidate=problem_statement_candidate,
                new_problem_marker_detected=new_problem_marker_detected,
            ),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _problem_classification_contract(
        self,
        request: TeachingTurnRequest,
        *,
        explicit_workspace_followup: bool,
        problem_statement_candidate: bool,
        new_problem_marker_detected: bool,
    ) -> dict[str, Any]:
        required_intent = "continue_problem" if explicit_workspace_followup else None
        return {
            "workspace_id": request.workspace_id,
            "explicit_workspace_followup": explicit_workspace_followup,
            "problem_statement_candidate": problem_statement_candidate,
            "new_problem_marker_detected": new_problem_marker_detected,
            "required_intent_when_explicit_followup": required_intent,
            "required_intent": required_intent,
            "required_should_start_new_workspace": False if explicit_workspace_followup else None,
            "classification_rules": [
                "Treat workspace_id as context, not proof that the turn starts a new task.",
                "Example/sample walkthroughs inside the selected workspace are continue_problem",
                "Use new_problem only when the message clearly introduces a different concrete task.",
                "Use non_problem only for turns unrelated to the selected workspace.",
            ],
        }

    def _problem_decision_repair_context(
        self,
        request: TeachingTurnRequest,
        *,
        previous_rejection_reason: str,
        raw_decision: dict[str, Any] | None,
        repair_attempt: int = 1,
    ) -> dict[str, Any]:
        received_intent = raw_decision.get("intent") if isinstance(raw_decision, dict) else None
        classification_contract = self._problem_classification_contract(
            request,
            explicit_workspace_followup=self._is_explicit_workspace_followup(request),
            problem_statement_candidate=self.intent_policy.detect_problem_statement_candidate(request.message),
            new_problem_marker_detected=self.intent_policy.detect_new_problem_intent(request.message),
        )
        return {
            "previous_rejection_reason": previous_rejection_reason,
            "repair_attempt": repair_attempt,
            "received_intent": received_intent,
            "workspace_id": request.workspace_id,
            "explicit_workspace_followup": classification_contract["explicit_workspace_followup"],
            "problem_statement_candidate": classification_contract["problem_statement_candidate"],
            "new_problem_marker_detected": classification_contract["new_problem_marker_detected"],
            "classification_contract": classification_contract,
            "must_return": {
                "intent": "continue_problem",
                "problem_key": None,
                "should_start_new_workspace": False,
            },
            "required_intent": "continue_problem",
            "required_should_start_new_workspace": False,
            "required_corrections": [
                "Use continue_problem for visual/explanation requests about the selected workspace.",
                "Do not classify a concrete example or sample walkthrough as new_problem when it stays inside the selected workspace.",
                "Use non_problem only for turns unrelated to the selected workspace.",
                "Use new_problem only when the message introduces a different concrete problem.",
                "Return only the required JSON schema.",
            ],
        }

    def _parse_agent_decision(self, raw_decision: dict[str, Any] | None) -> ProblemDecision | None:
        if not isinstance(raw_decision, dict):
            return None
        intent = raw_decision.get("intent")
        confidence = raw_decision.get("confidence")
        reason = raw_decision.get("reason")
        should_start = raw_decision.get("should_start_new_workspace")
        if intent not in self._valid_intents:
            return None
        if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
            return None
        if not isinstance(reason, str) or not reason.strip():
            return None
        if not isinstance(should_start, bool):
            return None
        problem_key = raw_decision.get("problem_key")
        if problem_key is not None and not isinstance(problem_key, str):
            return None
        return ProblemDecision(
            intent=str(intent),
            problem_key=problem_key,
            confidence=float(confidence),
            should_start_new_workspace=should_start,
            reason=reason.strip(),
            decision_source="agent",
        )

    def run(self, request: TeachingTurnRequest, decision: ProblemDecision | None = None) -> AgentNodeResult:
        source = request.source or "chat"
        decision = decision or self.decide(request)
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Received a teaching turn request.",
            output_summary=(
                f"{decision.intent}: problem_key={decision.problem_key or 'unknown'}, "
                f"confidence={decision.confidence:.2f}, reason={decision.reason}, source={source}."
            ),
        )


class AlgorithmIdentifierNode:
    agent_name = "AlgorithmIdentifierAgent"

    def __init__(
        self,
        taxonomy: AlgorithmTaxonomy,
        family_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.taxonomy = taxonomy
        self.family_provider = family_provider
        self.prompt_spec = prompt_spec or algorithm_identifier_prompt_spec()

    def run(self, request: TeachingTurnRequest) -> AgentNodeResult:
        result, _, _ = self.identify(request)
        return result

    def identify(self, request: TeachingTurnRequest) -> tuple[AgentNodeResult, AlgorithmFamily, str]:
        taxonomy_family = self.taxonomy.classify_message(request.message)
        agent_family = self._identify_with_agent(request)
        if (
            agent_family is not None
            and taxonomy_family.family_id != "unknown"
            and taxonomy_family.family_id != agent_family.family_id
        ):
            family = taxonomy_family
            source = "taxonomy_evidence"
        elif agent_family is not None:
            family = agent_family
            source = "agent"
        else:
            family = taxonomy_family
            source = "taxonomy"
        return self.result_from_family(family, source=source), family, source

    def result_from_family(self, family: AlgorithmFamily, *, source: str) -> AgentNodeResult:
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Classified algorithm family using taxonomy candidate space and current request.",
            output_summary=f"{family.family_id}: {family.display_name}; source={source}.",
            blackboard_write_count=1 if family.family_id != "unknown" else 0,
        )

    def _identify_with_agent(self, request: TeachingTurnRequest) -> AlgorithmFamily | None:
        if self.family_provider is None:
            return None
        raw_family = self.family_provider(self._build_agent_payload(request))
        return self._parse_agent_family(raw_family)

    def _build_agent_payload(self, request: TeachingTurnRequest) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "problem_id": request.problem_id,
            "available_families": [
                {
                    "family_id": family.family_id,
                    "display_name": family.display_name,
                    "description": family.description,
                    "classification_evidence": [list(group) for group in family.classification_evidence],
                }
                for family in (self.taxonomy.get_family(family_id) for family_id in self.taxonomy.family_ids())
            ],
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_family(self, raw_family: dict[str, Any] | None) -> AlgorithmFamily | None:
        if not isinstance(raw_family, dict):
            return None
        family_id = raw_family.get("family_id")
        confidence = raw_family.get("confidence")
        reason = raw_family.get("reason")
        if not isinstance(family_id, str) or family_id not in self.taxonomy.family_ids():
            return None
        if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
            return None
        if not isinstance(reason, str) or not reason.strip() or contains_forbidden_text(reason):
            return None
        return self.taxonomy.get_family(family_id)


class MemoryRetrieverNode:
    agent_name = "MemoryRetrieverAgent"

    def run(self, memories: list[Any]) -> AgentNodeResult:
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Retrieved scoped long-term memory for the active problem workspace.",
            output_summary=f"{len(memories)} context memories retrieved.",
            blackboard_write_count=1 if memories else 0,
        )


class MemoryManagerNode:
    agent_name = "MemoryManagerAgent"

    def __init__(
        self,
        summary_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.summary_provider = summary_provider
        self.prompt_spec = prompt_spec or memory_manager_prompt_spec()

    def compact(
        self,
        *,
        prior_summary: str | None,
        turns_to_compact: list[dict[str, Any]],
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, str | None, str]:
        normalized_turns = self._normalize_turns(turns_to_compact)
        prior = (prior_summary or "").strip() or None
        summary = self._compact_with_agent(
            prior_summary=prior,
            turns_to_compact=normalized_turns,
            collaboration_context=collaboration_context,
        )
        if summary is not None:
            source = "agent"
            output_summary = f"{len(normalized_turns)} old visible turns compacted; source=agent."
            blackboard_writes = 1
            memory_writes = 1
        elif prior is not None:
            source = "existing"
            summary = prior
            output_summary = f"{len(normalized_turns)} old visible turns retained existing summary; source=existing."
            blackboard_writes = 0
            memory_writes = 0
        else:
            source = "skipped"
            output_summary = f"{len(normalized_turns)} old visible turns; source=skipped."
            blackboard_writes = 0
            memory_writes = 0
        return (
            AgentNodeResult(
                agent_name=self.agent_name,
                status="ok",
                input_summary="Compacted old visible turns into active workspace memory.",
                output_summary=output_summary,
                blackboard_write_count=blackboard_writes,
                memory_write_count=memory_writes,
            ),
            summary,
            source,
        )

    def _compact_with_agent(
        self,
        *,
        prior_summary: str | None,
        turns_to_compact: list[dict[str, Any]],
        collaboration_context: dict[str, Any] | None,
    ) -> str | None:
        if self.summary_provider is None or not turns_to_compact:
            return None
        raw_summary = self.summary_provider(
            {
                "agent_name": self.prompt_spec.agent_name,
                "purpose": self.prompt_spec.purpose,
                "system_prompt": self.prompt_spec.system_prompt,
                "prior_summary": prior_summary,
                "turns_to_compact": turns_to_compact,
                "collaboration_context": build_collaboration_context_payload(collaboration_context),
                "required_schema": self.prompt_spec.required_schema,
            }
        )
        return self._parse_agent_summary(raw_summary)

    def _parse_agent_summary(self, raw_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(raw_summary, dict):
            return None
        summary = raw_summary.get("workspace_summary")
        reason = raw_summary.get("reason")
        if not isinstance(summary, str) or not summary.strip() or contains_forbidden_text(summary):
            return None
        if not isinstance(reason, str) or not reason.strip() or contains_forbidden_text(reason):
            return None
        return summary.strip()[:1200]

    def _normalize_turns(self, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for turn in turns:
            role = turn.get("role")
            content = turn.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
                continue
            normalized.append(
                {
                    "role": role,
                    "content": content.strip()[:1200],
                    "turn_index": turn.get("turn_index"),
                }
            )
        return normalized


@dataclass(frozen=True)
class AlgorithmPrimer:
    primer_text: str
    why_fit: str
    pitfalls: tuple[str, ...] = ()
    show_before_plan: bool = True


class AlgorithmPrimerNode:
    agent_name = "AlgorithmPrimerAgent"

    def __init__(
        self,
        primer_policy: AlgorithmPrimerPolicy | None = None,
        primer_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.primer_policy = primer_policy
        self.primer_provider = primer_provider
        self.prompt_spec = prompt_spec or algorithm_primer_prompt_spec()

    def run(
        self,
        *,
        family: AlgorithmFamily,
        depth: str,
    ) -> tuple[AgentNodeResult, str]:
        result, primer, _ = self.prepare(family=family, depth=depth)
        return result, primer

    def prepare(
        self,
        *,
        family: AlgorithmFamily,
        depth: str,
    ) -> tuple[AgentNodeResult, str, str]:
        result, primer, source = self.prepare_details(family=family, depth=depth)
        return result, primer.primer_text, source

    def prepare_details(
        self,
        *,
        family: AlgorithmFamily,
        depth: str,
    ) -> tuple[AgentNodeResult, AlgorithmPrimer, str]:
        primer = self._prepare_with_agent(family=family, depth=depth)
        if primer is not None:
            source = "agent"
        elif depth in family.primer_guidance:
            primer = self._taxonomy_primer(family=family, primer_text=family.primer_guidance[depth])
            source = "taxonomy"
        elif self.primer_policy is not None:
            primer = self._taxonomy_primer(
                family=family,
                primer_text=self.primer_policy.fallback_for(
                    family=family,
                    depth=depth,
                ),
            )
            source = "policy"
        else:
            primer = self._taxonomy_primer(family=family, primer_text="")
            source = "none"
        result = self.result_from_primer(family=family, depth=depth, source=source)
        return result, primer, source

    def prepare_structured_details(
        self,
        *,
        family: AlgorithmFamily,
        depth: str,
    ) -> tuple[AgentNodeResult, AlgorithmPrimer, str]:
        if depth in family.primer_guidance:
            primer = self._taxonomy_primer(family=family, primer_text=family.primer_guidance[depth])
        elif self.primer_policy is not None:
            primer = self._taxonomy_primer(
                family=family,
                primer_text=self.primer_policy.fallback_for(
                    family=family,
                    depth=depth,
                ),
            )
        else:
            primer = self._taxonomy_primer(family=family, primer_text="")
        source = "structured"
        result = self.result_from_primer(family=family, depth=depth, source=source)
        return result, primer, source

    def result_from_primer(
        self,
        *,
        family: AlgorithmFamily,
        depth: str,
        source: str,
    ) -> AgentNodeResult:
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Prepared algorithm primer from family metadata and guidance depth.",
            output_summary=f"{family.family_id}: {depth} primer prepared; source={source}.",
            blackboard_write_count=1 if family.family_id != "unknown" else 0,
        )

    def _taxonomy_primer(self, *, family: AlgorithmFamily, primer_text: str) -> AlgorithmPrimer:
        return AlgorithmPrimer(
            primer_text=primer_text,
            why_fit=family.description,
            pitfalls=(),
            show_before_plan=True,
        )

    def _prepare_with_agent(self, *, family: AlgorithmFamily, depth: str) -> AlgorithmPrimer | None:
        if self.primer_provider is None:
            return None
        raw_primer = self.primer_provider(self._build_agent_payload(family=family, depth=depth))
        return self._parse_agent_primer(raw_primer)

    def _build_agent_payload(self, *, family: AlgorithmFamily, depth: str) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "family_id": family.family_id,
            "family_display_name": family.display_name,
            "family_description": family.description,
            "teaching_dimensions": list(family.teaching_dimensions),
            "guidance_depth": depth,
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_primer(self, raw_primer: dict[str, Any] | None) -> AlgorithmPrimer | None:
        if not isinstance(raw_primer, dict):
            return None
        primer_text = raw_primer.get("primer_text")
        why_fit = raw_primer.get("why_fit")
        pitfalls = parse_string_list(raw_primer.get("pitfalls"), min_items=0, max_items=6)
        show_before_plan = raw_primer.get("show_before_plan")
        reason = raw_primer.get("reason")
        if not isinstance(primer_text, str) or not primer_text.strip() or contains_forbidden_text(primer_text):
            return None
        if not isinstance(why_fit, str) or not why_fit.strip() or contains_forbidden_text(why_fit):
            return None
        if pitfalls is None or any(contains_forbidden_text(item) for item in pitfalls):
            return None
        if not isinstance(show_before_plan, bool):
            return None
        if not isinstance(reason, str) or not reason.strip() or contains_forbidden_text(reason):
            return None
        return AlgorithmPrimer(
            primer_text=primer_text.strip(),
            why_fit=why_fit.strip(),
            pitfalls=tuple(pitfalls),
            show_before_plan=show_before_plan,
        )


class PlanRevisionNode:
    agent_name = "PlanRevisionAgent"
    _valid_intents = {"continue_plan", "branch_plan"}

    def __init__(
        self,
        revision_policy: PlanRevisionPolicy | None = None,
        decision_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.revision_policy = revision_policy or load_default_plan_revision_policy()
        self.decision_provider = decision_provider
        self.prompt_spec = prompt_spec or plan_revision_prompt_spec()

    def run(
        self,
        request: TeachingTurnRequest,
        *,
        current_step: AgentTeachingStep | None,
        plan_summary: str | None,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, PlanRevisionDecision]:
        decision = self._decide_with_agent(
            request,
            current_step=current_step,
            plan_summary=plan_summary,
            collaboration_context=collaboration_context,
        )
        if decision is None:
            decision = self.revision_policy.decide(request.message)
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Selected whether the active teaching plan should continue or branch.",
            output_summary=(
                f"{decision.intent}; confidence={decision.confidence:.2f}; "
                f"reason={decision.reason}; source={decision.decision_source}."
            ),
        )
        return result, decision

    def run_structured(
        self,
        request: TeachingTurnRequest,
        *,
        current_step: AgentTeachingStep | None,
        plan_summary: str | None,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, PlanRevisionDecision]:
        policy_decision = self.revision_policy.decide(request.message)
        decision = PlanRevisionDecision(
            intent=policy_decision.intent,
            confidence=policy_decision.confidence,
            reason=policy_decision.reason,
            decision_source="structured",
        )
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Selected whether the active teaching plan should continue or branch.",
            output_summary=(
                f"{decision.intent}; confidence={decision.confidence:.2f}; "
                f"reason={decision.reason}; source={decision.decision_source}."
            ),
        )
        return result, decision

    def _decide_with_agent(
        self,
        request: TeachingTurnRequest,
        *,
        current_step: AgentTeachingStep | None,
        plan_summary: str | None,
        collaboration_context: dict[str, Any] | None,
    ) -> PlanRevisionDecision | None:
        if self.decision_provider is None:
            return None
        raw_decision = self.decision_provider(
            self._build_agent_payload(
                request,
                current_step=current_step,
                plan_summary=plan_summary,
                collaboration_context=collaboration_context,
            )
        )
        return self._parse_agent_decision(raw_decision)

    def _build_agent_payload(
        self,
        request: TeachingTurnRequest,
        *,
        current_step: AgentTeachingStep | None,
        plan_summary: str | None,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "guidance_depth": request.guidance_depth.value if request.guidance_depth is not None else None,
            "plan_summary": plan_summary,
            "current_step": build_step_payload(current_step) if current_step is not None else None,
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_decision(self, raw_decision: dict[str, Any] | None) -> PlanRevisionDecision | None:
        if not isinstance(raw_decision, dict):
            return None
        intent = raw_decision.get("intent")
        confidence = raw_decision.get("confidence")
        reason = raw_decision.get("reason")
        if intent not in self._valid_intents:
            return None
        if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
            return None
        if not isinstance(reason, str) or not reason.strip() or contains_forbidden_text(reason):
            return None
        return PlanRevisionDecision(
            intent=str(intent),
            confidence=float(confidence),
            reason=reason.strip(),
            decision_source="agent",
        )


class PlanDecomposerNode:
    agent_name = "PlanDecomposerAgent"
    _required_step_fields = {
        "step_key",
        "title",
        "teaching_goal",
        "expected_student_action",
        "mastery_criteria",
    }
    _forbidden_agent_plan_fields = {
        "fixed_solution",
        "hard_coded_solution",
        "solution_code",
        "diagram_layout",
        "problem_specific_rule",
    }

    def __init__(
        self,
        planning_policy: TeachingPlanPolicy | None = None,
        plan_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
    ):
        self.planning_policy = planning_policy or load_default_planning_policy()
        self.plan_provider = plan_provider
        self.prompt_spec = prompt_spec or plan_decomposer_prompt_spec()

    def run(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> AgentNodeResult:
        step_specs, _, plan_source = self.build_plan_with_source(
            request,
            family,
            collaboration_context=collaboration_context,
        )
        return self.result_from_step_specs(family=family, step_specs=step_specs, plan_source=plan_source)

    def result_from_step_specs(
        self,
        *,
        family: AlgorithmFamily,
        step_specs: list[dict[str, str]],
        plan_source: str,
    ) -> AgentNodeResult:
        step_count = len(step_specs)
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Built a teaching plan from taxonomy dimensions and current request.",
            output_summary=f"{family.family_id}: {step_count} teaching steps prepared; source={plan_source}.",
            blackboard_write_count=1,
        )

    def result_from_cached_plan(
        self,
        *,
        family: AlgorithmFamily,
        step_count: int,
        original_plan_source: str,
    ) -> AgentNodeResult:
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Reused the active teaching plan from workspace state.",
            output_summary=(
                f"{family.family_id}: {step_count} teaching steps reused; "
                f"source=cache; plan_source={original_plan_source}."
            ),
            blackboard_write_count=0,
        )

    def build_step_specs(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        specs, _, _ = self.build_plan_with_source(
            request,
            family,
            collaboration_context=collaboration_context,
        )
        return specs

    def build_step_specs_with_source(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, str]], str]:
        specs, _, source = self.build_plan_with_source(
            request,
            family,
            collaboration_context=collaboration_context,
        )
        return specs, source

    def build_plan_with_source(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, str]], str, str]:
        agent_plan = self._build_agent_plan(request, family, collaboration_context)
        if agent_plan is not None:
            agent_specs, plan_summary = agent_plan
            return agent_specs, plan_summary, "agent"
        problem_anchor = self._summarize_problem_anchor(request)
        specs = [self.planning_policy.intro_step.render(problem_anchor=problem_anchor)]

        for dimension in family.teaching_dimensions:
            specs.append(
                self.planning_policy.dimension_step.render(
                    family_display_name=family.display_name,
                    dimension=dimension,
                )
            )

        specs.extend(step.render() for step in self.planning_policy.closing_steps)
        return specs, f"{family.family_id}: taxonomy-driven teaching dimensions.", "policy"

    def build_structured_plan_with_source(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, str]], str, str]:
        problem_anchor = self._summarize_problem_anchor(request)
        specs = [self.planning_policy.intro_step.render(problem_anchor=problem_anchor)]
        for dimension in family.teaching_dimensions:
            specs.append(
                self.planning_policy.dimension_step.render(
                    family_display_name=family.display_name,
                    dimension=dimension,
                )
            )
        specs.extend(step.render() for step in self.planning_policy.closing_steps)
        return specs, f"{family.family_id}: structured teaching frame.", "structured"

    def _build_agent_plan(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        collaboration_context: dict[str, Any] | None,
    ) -> tuple[list[dict[str, str]], str] | None:
        if self.plan_provider is None:
            return None
        payload = self._build_agent_payload(request, family, collaboration_context)
        raw_plan = self.plan_provider(payload)
        parsed_plan, rejection_reason = self._parse_agent_plan_with_reason(raw_plan)
        if parsed_plan is not None:
            return parsed_plan
        if rejection_reason is None:
            return None
        repaired_plan = self.plan_provider(self._build_agent_repair_payload(payload, raw_plan, rejection_reason))
        parsed_repaired_plan, _ = self._parse_agent_plan_with_reason(repaired_plan)
        return parsed_repaired_plan

    def _build_agent_repair_payload(
        self,
        payload: dict[str, Any],
        raw_plan: dict[str, Any] | None,
        rejection_reason: str,
    ) -> dict[str, Any]:
        repair_context: dict[str, Any] = {
            "previous_rejection_reason": rejection_reason,
            "instruction": (
                "Return the same required_schema with at least two complete, problem-specific teaching steps. "
                "Do not include solution code, fixed diagrams, or hard-coded problem templates."
            ),
        }
        if isinstance(raw_plan, dict):
            repair_context["received_keys"] = sorted(str(key) for key in raw_plan.keys())
            raw_steps = raw_plan.get("steps")
            if isinstance(raw_steps, list):
                repair_context["received_step_count"] = len(raw_steps)
        return {**payload, "repair_context": repair_context}

    def _build_agent_payload(
        self,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "guidance_depth": request.guidance_depth.value if request.guidance_depth is not None else None,
            "problem_id": request.problem_id,
            "family_id": family.family_id,
            "family_display_name": family.display_name,
            "teaching_dimensions": list(family.teaching_dimensions),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_plan(self, raw_plan: dict[str, Any] | None) -> tuple[list[dict[str, str]], str] | None:
        parsed_plan, _ = self._parse_agent_plan_with_reason(raw_plan)
        return parsed_plan

    def _parse_agent_plan_with_reason(
        self,
        raw_plan: dict[str, Any] | None,
    ) -> tuple[tuple[list[dict[str, str]], str] | None, str | None]:
        if not isinstance(raw_plan, dict):
            return None, None
        plan_summary = raw_plan.get("plan_summary")
        if not isinstance(plan_summary, str) or not plan_summary.strip() or contains_forbidden_text(plan_summary):
            return None, "invalid_plan_summary"
        steps = raw_plan.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            return None, "too_few_steps"
        parsed_steps: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        for raw_step in steps:
            if not isinstance(raw_step, dict):
                return None, "invalid_step"
            if self._forbidden_agent_plan_fields.intersection(raw_step.keys()):
                return None, "forbidden_step_fields"
            if not self._required_step_fields.issubset(raw_step.keys()):
                return None, "missing_step_fields"
            parsed_step: dict[str, str] = {}
            for field_name in self._required_step_fields:
                value = raw_step.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    return None, "invalid_step_field_value"
                parsed_step[field_name] = value.strip()
            if parsed_step["step_key"] in seen_keys:
                return None, "duplicate_step_key"
            seen_keys.add(parsed_step["step_key"])
            parsed_steps.append(parsed_step)
        return (parsed_steps, plan_summary.strip()), None

    def _summarize_problem_anchor(self, request: TeachingTurnRequest) -> str:
        if request.problem_id is not None:
            return f"problem {request.problem_id}"
        compact = " ".join(request.message.strip().split())
        if not compact:
            return "当前问题"
        return compact[:24]


class HintPolicyNode:
    agent_name = "HintPolicyAgent"
    _required_decision_fields = {"action", "student_state", "repeat_risk", "max_question_count", "reason"}
    _valid_actions = {"ask", "give_hint", "explain", "summarize", "confirm_and_advance", "enter_code", "enter_debug"}
    _valid_student_states = {"neutral", "progressing", "stuck"}
    _valid_repeat_risks = {"low", "medium", "high"}

    def __init__(
        self,
        decision_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        signal_policy: HintSignalPolicy | None = None,
    ):
        self.decision_provider = decision_provider
        self.prompt_spec = prompt_spec or hint_policy_prompt_spec()
        self.signal_policy = signal_policy or load_default_hint_signal_policy()

    def run(
        self,
        request: TeachingTurnRequest,
        current_step: AgentTeachingStep,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, HintPolicyDecision]:
        decision = self._build_explicit_specialist_decision(request)
        if decision is None:
            decision = self._build_agent_decision(request, current_step, collaboration_context)
        if decision is None:
            decision = self._build_policy_decision(request, current_step)
        return self._result_for_decision(decision), decision

    def run_structured(
        self,
        request: TeachingTurnRequest,
        current_step: AgentTeachingStep,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, HintPolicyDecision]:
        decision = self._build_policy_decision(request, current_step)
        decision = replace(
            decision,
            decision_source="structured",
            reason="tutor_provider_owns_visible_teaching_move",
        )
        return self._result_for_decision(decision), decision

    def _result_for_decision(self, decision: HintPolicyDecision) -> AgentNodeResult:
        return AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Selected next teaching tactic from step repeat state and student signal.",
            output_summary=(
                f"{decision.action}; student_state={decision.student_state}; "
                f"repeat_risk={decision.repeat_risk}; max_questions={decision.max_question_count}; "
                f"encouragement={bool(decision.encouragement)}; source={decision.decision_source}."
            ),
        )

    def _build_policy_decision(
        self,
        request: TeachingTurnRequest,
        current_step: AgentTeachingStep,
    ) -> HintPolicyDecision:
        has_progress = self._shows_progress(request.message)
        repeated_once = current_step.repeat_count == 2
        repeated_more = current_step.repeat_count > 2
        if request.submission_id is not None or self._requests_debug(request.message):
            action = "enter_debug"
            max_question_count = 0
            repeat_risk = "low"
        elif self._requests_code(request.message):
            action = "enter_code"
            max_question_count = 0
            repeat_risk = "low"
        elif current_step.repeat_count > 1 and self.confirms_current_step(request.message):
            action = "confirm_and_advance"
            max_question_count = 0
            repeat_risk = "low"
        elif repeated_more:
            action = "give_hint"
            max_question_count = 0
            repeat_risk = "high"
        elif repeated_once:
            action = "summarize"
            max_question_count = 0
            repeat_risk = "medium"
        elif request.guidance_depth is not None and request.guidance_depth.value == "direct":
            action = "explain"
            max_question_count = 0
            repeat_risk = "low"
        else:
            action = "ask"
            max_question_count = 1
            repeat_risk = "low"

        return HintPolicyDecision(
            action=action,
            encouragement=self.signal_policy.progress_encouragement if has_progress else None,
            encouragement_type="positive_specific" if has_progress else None,
            student_state=self._student_state(has_progress=has_progress, repeat_risk=repeat_risk),
            repeat_risk=repeat_risk,
            max_question_count=max_question_count,
            decision_source="policy",
        )

    def _build_explicit_specialist_decision(
        self,
        request: TeachingTurnRequest,
    ) -> HintPolicyDecision | None:
        has_progress = self._shows_progress(request.message)
        if request.submission_id is not None or self._requests_debug(request.message):
            return HintPolicyDecision(
                action="enter_debug",
                encouragement=self.signal_policy.progress_encouragement if has_progress else None,
                encouragement_type="positive_specific" if has_progress else None,
                student_state=self._student_state(has_progress=has_progress, repeat_risk="low"),
                repeat_risk="low",
                max_question_count=0,
                decision_source="policy",
                reason="explicit_debug_request",
            )
        if self._requests_code(request.message):
            return HintPolicyDecision(
                action="enter_code",
                encouragement=self.signal_policy.progress_encouragement if has_progress else None,
                encouragement_type="positive_specific" if has_progress else None,
                student_state=self._student_state(has_progress=has_progress, repeat_risk="low"),
                repeat_risk="low",
                max_question_count=0,
                decision_source="policy",
                reason="explicit_code_request",
            )
        return None

    def _build_agent_decision(
        self,
        request: TeachingTurnRequest,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> HintPolicyDecision | None:
        if self.decision_provider is None:
            return None
        raw_decision = self.decision_provider(self._build_agent_payload(request, current_step, collaboration_context))
        return self._parse_agent_decision(raw_decision)

    def _build_agent_payload(
        self,
        request: TeachingTurnRequest,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "guidance_depth": request.guidance_depth.value if request.guidance_depth is not None else None,
            "submission_id": request.submission_id,
            "current_step": build_step_payload(current_step),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_decision(self, raw_decision: dict[str, Any] | None) -> HintPolicyDecision | None:
        if not isinstance(raw_decision, dict) or not self._required_decision_fields.issubset(raw_decision):
            return None
        action = raw_decision.get("action")
        student_state = raw_decision.get("student_state")
        repeat_risk = raw_decision.get("repeat_risk")
        max_question_count = raw_decision.get("max_question_count")
        reason = raw_decision.get("reason")
        if action not in self._valid_actions:
            return None
        if student_state not in self._valid_student_states:
            return None
        if repeat_risk not in self._valid_repeat_risks:
            return None
        if isinstance(max_question_count, str) and max_question_count.strip() in {"0", "1"}:
            max_question_count = int(max_question_count.strip())
        if not isinstance(max_question_count, int) or isinstance(max_question_count, bool):
            return None
        if max_question_count not in {0, 1}:
            return None
        if action != "ask":
            max_question_count = 0
        if not isinstance(reason, str) or not reason.strip() or contains_forbidden_text(reason):
            return None
        progressing = student_state == "progressing"
        return HintPolicyDecision(
            action=action,
            encouragement="agent_requested_positive_feedback" if progressing else None,
            encouragement_type="positive_specific" if progressing else None,
            student_state=student_state,
            repeat_risk=repeat_risk,
            max_question_count=max_question_count,
            decision_source="agent",
            reason=reason.strip(),
        )

    def _shows_progress(self, message: str) -> bool:
        return self.signal_policy.shows_progress(message)

    def _student_state(self, *, has_progress: bool, repeat_risk: str) -> str:
        if has_progress:
            return "progressing"
        if repeat_risk in {"medium", "high"}:
            return "stuck"
        return "neutral"

    def _requests_code(self, message: str) -> bool:
        return self.signal_policy.requests_code(message)

    def _requests_debug(self, message: str) -> bool:
        return self.signal_policy.requests_debug(message)

    def confirms_current_step(self, message: str) -> bool:
        return self.signal_policy.confirms_current_step(message)


class EncouragementNode:
    agent_name = "EncouragementAgent"
    _required_strategy_fields = {"feedback_type", "visible_prefix", "rationale"}
    _valid_feedback_types = {"neutral", "positive_specific", "normalize_stuck"}

    def __init__(
        self,
        strategy_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        fallback_policy: EncouragementFallbackPolicy | None = None,
    ):
        self.strategy_provider = strategy_provider
        self.prompt_spec = prompt_spec or encouragement_prompt_spec()
        self.fallback_policy = fallback_policy

    def run(
        self,
        *,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, EncouragementStrategy]:
        strategy = self._build_agent_strategy(
            current_step=current_step,
            policy=policy,
            collaboration_context=collaboration_context,
        )
        if strategy is None:
            strategy = self._build_policy_strategy(current_step=current_step, policy=policy)
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Selected affective feedback from student state and repeat risk.",
            output_summary=(
                f"{strategy.feedback_type}: prefix={bool(strategy.visible_prefix)}; "
                f"source={strategy.strategy_source}."
            ),
            blackboard_write_count=1 if strategy.visible_prefix else 0,
        )
        return result, strategy

    def run_agent_strategy(
        self,
        *,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, EncouragementStrategy | None]:
        strategy = self._build_agent_strategy(
            current_step=current_step,
            policy=None,
            collaboration_context=collaboration_context,
        )
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok" if strategy is not None else "fallback",
            input_summary="Prewarmed affective feedback from current-step context.",
            output_summary=(
                f"{strategy.feedback_type}: prefix={bool(strategy.visible_prefix)}; source={strategy.strategy_source}."
                if strategy is not None
                else "agent strategy unavailable; defer to hint-aware policy fallback."
            ),
            blackboard_write_count=1 if strategy is not None and strategy.visible_prefix else 0,
        )
        return result, strategy

    def _build_policy_strategy(
        self,
        *,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
    ) -> EncouragementStrategy:
        if policy.encouragement:
            return EncouragementStrategy(
                feedback_type=policy.encouragement_type or "positive_specific",
                visible_prefix=policy.encouragement,
                rationale="hint_policy_encouragement",
                strategy_source="policy",
            )
        selection = self.fallback_policy.select(
            current_step_title=current_step.title,
            student_state=policy.student_state,
            repeat_risk=policy.repeat_risk,
        ) if self.fallback_policy is not None else EncouragementFallbackSelection(
            feedback_type="neutral",
            visible_prefix="",
            rationale="no_local_visible_fallback",
        )
        return EncouragementStrategy(
            feedback_type=selection.feedback_type,
            visible_prefix=selection.visible_prefix,
            rationale=selection.rationale,
            strategy_source="policy",
        )

    def _build_agent_strategy(
        self,
        *,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision | None,
        collaboration_context: dict[str, Any] | None,
    ) -> EncouragementStrategy | None:
        if self.strategy_provider is None:
            return None
        raw_strategy = self.strategy_provider(
            self._build_agent_payload(
                current_step=current_step,
                policy=policy,
                collaboration_context=collaboration_context,
            )
        )
        return self._parse_agent_strategy(raw_strategy)

    def _build_agent_payload(
        self,
        *,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision | None,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        hint_policy = (
            {
                "action": policy.action,
                "student_state": policy.student_state,
                "repeat_risk": policy.repeat_risk,
                "max_question_count": policy.max_question_count,
            }
            if policy is not None
            else None
        )
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "current_step": build_step_payload(current_step),
            "hint_policy": hint_policy,
            "hint_policy_pending": policy is None,
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_strategy(self, raw_strategy: dict[str, Any] | None) -> EncouragementStrategy | None:
        if not isinstance(raw_strategy, dict) or not self._required_strategy_fields.issubset(raw_strategy):
            return None
        feedback_type = raw_strategy.get("feedback_type")
        visible_prefix = raw_strategy.get("visible_prefix")
        rationale = raw_strategy.get("rationale")
        if feedback_type not in self._valid_feedback_types:
            return None
        if not isinstance(visible_prefix, str) or not isinstance(rationale, str) or not rationale.strip():
            return None
        if contains_forbidden_text(visible_prefix) or contains_forbidden_text(rationale):
            return None
        if feedback_type == "neutral" and visible_prefix.strip():
            return None
        if feedback_type != "neutral" and not visible_prefix.strip():
            return None
        return EncouragementStrategy(
            feedback_type=feedback_type,
            visible_prefix=visible_prefix.strip(),
            rationale=rationale.strip(),
            strategy_source="agent",
        )


class TutorNode:
    agent_name = "TutorAgent"
    _required_output_fields = {
        "visible_text",
        "step_delta",
        "student_feedback",
        "needs_boardwork",
        "needs_diagram",
    }
    _valid_step_statuses = {"in_progress", "blocked", "ready_for_code", "ready_for_debug"}
    _valid_feedback_types = {"neutral", "positive_specific", "normalize_stuck"}
    _forbidden_output_fields = {"future_steps", "solution_code", "fixed_solution", "fixed_diagram", "hard_coded_solution"}

    def __init__(
        self,
        response_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        diagram_request_policy: DiagramRequestPolicy | None = None,
    ):
        self.response_provider = response_provider
        self.prompt_spec = prompt_spec or tutor_prompt_spec()
        self.diagram_request_policy = diagram_request_policy or load_default_diagram_request_policy()
        self.last_rejection_reason: str | None = None
        self.last_rejection_details: dict[str, Any] | None = None
        self.last_provider_call_count: int = 0
        self.last_repair_attempt_count: int = 0

    def run(
        self,
        request: TeachingTurnRequest,
        *,
        family: AlgorithmFamily,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
        primer: str,
        primer_details: dict[str, Any] | None = None,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, TutorOutput]:
        self.last_rejection_reason = None
        self.last_rejection_details = None
        self.last_provider_call_count = 0
        self.last_repair_attempt_count = 0
        depth = request.guidance_depth.value if request.guidance_depth is not None else "socratic"
        output = self._build_agent_output(
            request=request,
            family=family,
            current_step=current_step,
            policy=policy,
            primer=primer,
            primer_details=primer_details,
            collaboration_context=collaboration_context,
            depth=depth,
        )
        if output is None:
            raise TeachingAgentProviderUnavailableError(
                agent_name=self.agent_name,
                capability="tutor_response",
                outcome=self.last_rejection_reason or "provider_output_unavailable",
                provider_backend="agno",
                details=self.last_rejection_details,
            )
        output_source = "agent"
        if self._requests_diagram(request.message) and not output.needs_diagram:
            output = output.model_copy(update={"needs_diagram": True})
        if policy.action == "enter_code" and depth == "direct":
            output = output.model_copy(
                update={"visible_text": self._normalize_direct_complete_code_text(output.visible_text)}
            )
        if output.needs_diagram:
            output = output.model_copy(
                update={"visible_text": self._normalize_diagram_tool_language(output.visible_text)}
            )
        output = self._ensure_visible_algorithm_primer(
            output,
            family=family,
            current_step=current_step,
            primer=primer,
            primer_details=primer_details,
            depth=depth,
        )
        if output.needs_diagram and not output.needs_boardwork:
            output = output.model_copy(update={"needs_boardwork": True})
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Generated visible tutor response from current step and guidance depth.",
            output_summary=(
                f"{depth}: current step response generated; "
                f"needs_boardwork={output.needs_boardwork}; needs_diagram={output.needs_diagram}; "
                f"quality_flags={len(output.upstream_quality_flags)}; "
                f"source={output_source}."
            ),
        )
        return result, output

    def _build_agent_output(
        self,
        *,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
        primer: str,
        primer_details: dict[str, Any] | None,
        collaboration_context: dict[str, Any] | None,
        depth: str,
    ) -> TutorOutput | None:
        if self.response_provider is None:
            return None
        agent_payload = self._build_agent_payload(
            request=request,
            family=family,
            current_step=current_step,
            policy=policy,
            primer=primer,
            primer_details=primer_details,
            collaboration_context=collaboration_context,
            depth=depth,
        )
        self.last_provider_call_count = 1
        raw_output = self.response_provider(agent_payload)
        output, rejection_reason, rejection_details = self._parse_agent_output_with_reason(
            raw_output,
            request=request,
            family=family,
        )
        repair_attempt = 1
        while (
            output is None
            and repair_attempt <= 2
            and rejection_reason == "difference_array_example_contract_mismatch"
        ):
            self.last_provider_call_count += 1
            self.last_repair_attempt_count += 1
            repaired_output = self.response_provider(
                {
                    **agent_payload,
                    "repair_context": self._tutor_repair_context(
                        request=request,
                        family=family,
                        previous_rejection_reason=rejection_reason,
                        rejection_details=rejection_details,
                        repair_attempt=repair_attempt,
                    ),
                }
            )
            output, rejection_reason, rejection_details = self._parse_agent_output_with_reason(
                repaired_output,
                request=request,
                family=family,
            )
            repair_attempt += 1
        self.last_rejection_reason = rejection_reason
        self.last_rejection_details = rejection_details
        return output

    def _build_agent_payload(
        self,
        *,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        current_step: AgentTeachingStep,
        policy: HintPolicyDecision,
        primer: str,
        primer_details: dict[str, Any] | None,
        collaboration_context: dict[str, Any] | None,
        depth: str,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "message": request.message,
            "guidance_depth": depth,
            "family": {"family_id": family.family_id, "display_name": family.display_name},
            "current_step": {
                "step_key": current_step.step_key,
                "title": current_step.title,
                "teaching_goal": current_step.teaching_goal,
                "repeat_count": current_step.repeat_count,
            },
            "policy": {
                "action": policy.action,
                "student_state": policy.student_state,
                "repeat_risk": policy.repeat_risk,
                "max_question_count": policy.max_question_count,
                "encouragement": policy.encouragement,
                "encouragement_type": policy.encouragement_type,
            },
            "primer": primer,
            "primer_details": self._build_primer_payload(primer=primer, primer_details=primer_details),
            "delivery_contract": self._build_delivery_contract(
                request=request,
                family=family,
                policy=policy,
                depth=depth,
            ),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _build_delivery_contract(
        self,
        *,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        policy: HintPolicyDecision,
        depth: str,
    ) -> dict[str, Any]:
        allow_complete_code = policy.action == "enter_code" and depth == "direct"
        code_requirements = "Use the programming language and standard requested by the student."
        if "C++17" in request.message or "c++17" in request.message.lower():
            code_requirements = "Generate a complete C++17 solution when code is requested."
        contract = {
            "allow_complete_code": allow_complete_code,
            "code_requirements": code_requirements,
            "code_guardrails": (
                "Only include complete code when allow_complete_code is true. "
                "Otherwise provide an implementation plan or focused pseudocode for the current step."
            ),
            "max_question_count": policy.max_question_count,
            "must_answer_latest_concrete_request": self._must_answer_latest_concrete_request(
                request.message,
                policy=policy,
            ),
            "direct_guidance_requires_explanation": depth == "direct",
            "response_shape": (
                "In direct guidance, explain the current step directly instead of assigning the student to simulate it. "
                "When the student is stuck or asks about an example, first give one concrete anchor "
                "from the latest message/current step, then ask at most max_question_count questions. "
                "Do not ask a question just to ask one."
            ),
            "diagram_tool_surface": (
                "When a diagram is requested or useful, set needs_diagram=true and mention the diagram panel/canvas. "
                "Do not include inline drawings, numbered state timelines, markdown fences, Mermaid, Graphviz, "
                "shell commands, or plotting code."
            ),
        }
        concrete_example_contract = self._difference_array_example_contract(
            request.message,
            family=family,
        )
        if concrete_example_contract:
            contract["concrete_example_contract"] = concrete_example_contract
            contract["concrete_example_guardrails"] = (
                "For this concrete difference-array example, preserve the exact arrays and boundary updates in "
                "concrete_example_contract. Use a diff sentinel slot of length n+1; do not say the r+1 boundary "
                "is out of range or can be ignored when r+1 equals n."
            )
        return contract

    def _must_answer_latest_concrete_request(
        self,
        message: str,
        *,
        policy: HintPolicyDecision,
    ) -> bool:
        normalized = message.lower()
        concrete_markers = (
            "不会",
            "不懂",
            "没懂",
            "样例",
            "例子",
            "具体",
            "为什么",
            "怎么想",
            "看不出来",
            "where",
            "why",
            "example",
        )
        return policy.student_state == "stuck" or any(marker in normalized for marker in concrete_markers)

    def _normalize_direct_complete_code_text(self, visible_text: str) -> str:
        text = visible_text.strip()
        code_fence_positions = [
            text.find(marker)
            for marker in ("```cpp", "```c++", "```c", "```java", "```python", "```")
            if text.find(marker) >= 0
        ]
        if not code_fence_positions:
            return text
        code_start = min(code_fence_positions)
        preface = text[:code_start]
        refusal_markers = (
            "剥夺",
            "不能直接提供",
            "无法直接提供",
            "不建议直接提供",
            "系统允许",
            "鉴于你的明确请求",
        )
        if not any(marker in preface for marker in refusal_markers):
            return text
        code = text[code_start:].lstrip()
        implementation_label = "完整的 C++17 参考实现" if "C++17" in text or "c++17" in text.lower() else "完整的参考实现"
        return f"下面给出一份{implementation_label}：\n\n{code}"

    def _ensure_visible_algorithm_primer(
        self,
        output: TutorOutput,
        *,
        family: AlgorithmFamily,
        current_step: AgentTeachingStep,
        primer: str,
        primer_details: dict[str, Any] | None,
        depth: str,
    ) -> TutorOutput:
        if depth == "minimal" or family.family_id == "unknown" or current_step.order_index != 0:
            return output
        if current_step.repeat_count > 1:
            return output
        primer_payload = self._build_primer_payload(primer=primer, primer_details=primer_details)
        if primer_payload.get("show_before_plan") is False:
            return output
        primer_text = str(primer_payload.get("primer_text") or "").strip()
        if not primer_text:
            return output
        visible_text = output.visible_text.strip()
        primer_probe = primer_text[: min(len(primer_text), 24)]
        if primer_probe and primer_probe in visible_text:
            return output
        return output.model_copy(update={"visible_text": f"{primer_text}\n\n{visible_text}"})

    def _build_primer_payload(self, *, primer: str, primer_details: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(primer_details, dict):
            return {
                "primer_text": primer,
                "why_fit": "",
                "pitfalls": [],
                "show_before_plan": True,
            }
        primer_text = primer_details.get("primer_text")
        why_fit = primer_details.get("why_fit")
        pitfalls = primer_details.get("pitfalls")
        show_before_plan = primer_details.get("show_before_plan")
        return {
            "primer_text": primer_text.strip() if isinstance(primer_text, str) and primer_text.strip() else primer,
            "why_fit": why_fit.strip() if isinstance(why_fit, str) else "",
            "pitfalls": [item.strip() for item in pitfalls if isinstance(item, str) and item.strip()]
            if isinstance(pitfalls, list)
            else [],
            "show_before_plan": show_before_plan if isinstance(show_before_plan, bool) else True,
        }

    def _parse_agent_output_with_reason(
        self,
        raw_output: dict[str, Any] | None,
        *,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
    ) -> tuple[TutorOutput | None, str | None, dict[str, Any] | None]:
        if not isinstance(raw_output, dict):
            return None, "invalid_payload", None
        if self._forbidden_output_fields.intersection(raw_output.keys()):
            return None, "forbidden_fields", None
        if not self._required_output_fields.issubset(raw_output.keys()):
            return None, "missing_required_fields", None
        visible_text = raw_output.get("visible_text")
        step_delta = raw_output.get("step_delta")
        student_feedback = raw_output.get("student_feedback")
        needs_boardwork = raw_output.get("needs_boardwork")
        needs_diagram = raw_output.get("needs_diagram")
        upstream_quality_flags = parse_upstream_quality_flags(raw_output.get("upstream_quality_flags"))
        if not isinstance(visible_text, str) or not visible_text.strip():
            return None, "invalid_visible_text", None
        if not isinstance(step_delta, dict):
            return None, "invalid_step_delta", None
        status = step_delta.get("status")
        if status not in self._valid_step_statuses:
            return None, "invalid_step_status", None
        if student_feedback not in self._valid_feedback_types:
            return None, "invalid_student_feedback", None
        if not isinstance(needs_boardwork, bool) or not isinstance(needs_diagram, bool):
            return None, "invalid_surface_flags", None
        contract = self._difference_array_example_contract(request.message, family=family)
        contract_rejection = self._difference_array_example_contract_rejection_details(
            visible_text,
            contract=contract,
        )
        if contract_rejection is not None:
            return None, "difference_array_example_contract_mismatch", contract_rejection
        has_diagram_text_artifact = self._contains_text_diagram_artifact(
            visible_text,
            needs_diagram=needs_diagram,
        )
        if has_diagram_text_artifact and not needs_diagram:
            return None, "text_diagram_artifact_without_diagram", None
        if needs_diagram and has_diagram_text_artifact:
            normalized_visible_text = ""
        elif needs_diagram:
            normalized_visible_text = self._normalize_diagram_tool_language(visible_text)
        else:
            normalized_visible_text = visible_text.strip()
        return (
            TutorOutput(
                visible_text=normalized_visible_text,
                step_delta={"status": status},
                student_feedback=student_feedback,
                needs_boardwork=needs_boardwork,
                needs_diagram=needs_diagram,
                upstream_quality_flags=upstream_quality_flags,
            ),
            None,
            None,
        )

    def _parse_agent_output(self, raw_output: dict[str, Any] | None) -> TutorOutput | None:
        output, _, _ = self._parse_agent_output_with_reason(
            raw_output,
            request=TeachingTurnRequest(message=""),
            family=AlgorithmFamily(
                family_id="unknown",
                display_name="unknown",
                description="",
                teaching_dimensions=(),
                classification_evidence=(),
                primer_guidance={},
            ),
        )
        return output

    def _difference_array_example_contract(
        self,
        message: str,
        *,
        family: AlgorithmFamily,
    ) -> dict[str, Any] | None:
        if family.family_id != "difference_array":
            return None
        n_match = re.search(r"\bn\s*=\s*(\d+)", message, flags=re.IGNORECASE)
        interval_match = re.search(r"[\[【]\s*(\d+)\s*[,，、]\s*(\d+)\s*[\]】]", message)
        if n_match is None or interval_match is None:
            return None
        n = int(n_match.group(1))
        left = int(interval_match.group(1))
        right = int(interval_match.group(2))
        if n <= 0 or left < 0 or right < left or right >= n:
            return None
        delta_match = re.search(r"(?:加|\+)\s*(-?\d+)", message)
        delta = int(delta_match.group(1)) if delta_match is not None else 1
        diff = [0 for _ in range(n + 1)]
        diff[left] += delta
        diff[right + 1] -= delta
        restored: list[int] = []
        running = 0
        for value in diff[:n]:
            running += value
            restored.append(running)
        return {
            "kind": "difference_array_interval_update",
            "index_base": "zero_based",
            "n": n,
            "left": left,
            "right": right,
            "delta": delta,
            "diff_length": n + 1,
            "initial_diff": [0 for _ in range(n + 1)],
            "boundary_updates": [
                f"diff[{left}]+={delta}",
                f"diff[{right + 1}]-={delta}",
            ],
            "updated_diff": diff,
            "restored_array": restored,
        }

    def _difference_array_example_contract_rejection_details(
        self,
        visible_text: str,
        *,
        contract: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not contract:
            return None
        normalized = self._normalize_contract_text(visible_text)
        required_anchor_groups = [
            [self._contract_array_text(contract["initial_diff"])],
            [self._contract_array_text(contract["updated_diff"])],
            [self._contract_array_text(contract["restored_array"])],
            ["n+1=6" if contract.get("n") == 5 else f"n+1={contract['diff_length']}"],
        ]
        for update in contract["boundary_updates"]:
            required_anchor_groups.append(self._boundary_update_variants(update))
        missing = [
            "/".join(group)
            for group in required_anchor_groups
            if not any(self._normalize_contract_text(anchor) in normalized for anchor in group)
        ]
        forbidden_markers = [
            marker
            for marker in ("超出范围", "可忽略", "忽略")
            if marker in visible_text and f"diff[{contract['right'] + 1}]" in normalized
        ]
        if not missing and not forbidden_markers:
            return None
        return {
            "contract": contract,
            "missing_anchors": missing,
            "forbidden_markers": forbidden_markers,
        }

    def _boundary_update_variants(self, update: str) -> list[str]:
        if update.startswith("diff"):
            return [update, update.replace("diff", "d", 1)]
        return [update]

    def _contract_array_text(self, values: list[int]) -> str:
        return "[" + ",".join(str(value) for value in values) + "]"

    def _normalize_contract_text(self, value: str) -> str:
        normalized = value.lower()
        normalized = normalized.replace("＋", "+").replace("－", "-").replace("＝", "=")
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _tutor_repair_context(
        self,
        *,
        request: TeachingTurnRequest,
        family: AlgorithmFamily,
        previous_rejection_reason: str,
        rejection_details: dict[str, Any] | None,
        repair_attempt: int,
    ) -> dict[str, Any]:
        contract = self._difference_array_example_contract(request.message, family=family)
        instruction = (
            "Return the same TutorAgent JSON schema again, but repair visible_text so it exactly satisfies "
            "the concrete example contract. Keep the answer concise and direct."
        )
        if contract:
            instruction += (
                f" Use diff length n+1={contract['diff_length']}; include initial diff "
                f"{self._contract_array_text(contract['initial_diff'])}; include boundary updates "
                f"{', '.join(contract['boundary_updates'])}; include updated diff "
                f"{self._contract_array_text(contract['updated_diff'])}; include restored array "
                f"{self._contract_array_text(contract['restored_array'])}. Do not say the r+1 sentinel "
                "boundary is out of range or can be ignored."
            )
        return {
            "previous_rejection_reason": previous_rejection_reason,
            "rejection_details": rejection_details or {},
            "repair_attempt": repair_attempt,
            "instruction": instruction,
        }

    def _contains_text_diagram_artifact(self, visible_text: str, *, needs_diagram: bool = False) -> bool:
        normalized = visible_text.lower()
        if needs_diagram and "```" in normalized:
            return True
        if needs_diagram and self._contains_textual_state_timeline(visible_text):
            return True
        if "```mermaid" in normalized or "```dot" in normalized or "```graphviz" in normalized:
            return True
        if "flowchart td" in normalized or "graph td" in normalized or "graph lr" in normalized:
            return True
        if "```" in normalized:
            drawing_snippet_markers = (
                "```bash",
                "```sh",
                "```shell",
                "```python",
                "matplotlib",
                "networkx",
                "graphviz",
                "gnuplot",
                "plt.",
                "python ",
                "dot -t",
                "draw_",
            )
            if any(marker in normalized for marker in drawing_snippet_markers):
                return True
        command_line_markers = (
            "命令行画",
            "命令行去画",
            "命令行绘图",
            "终端画图",
            "终端绘图",
            "用命令行",
            "走命令行",
            "python draw",
            "python3 draw",
            "dot -t",
            "graphviz",
        )
        if any(marker in normalized for marker in command_line_markers):
            return True
        diagram_like_lines = [
            line
            for line in visible_text.splitlines()
            if "-->" in line or "->" in line or "=> " in line or "─" in line
        ]
        return len(diagram_like_lines) >= 2

    def _contains_textual_state_timeline(self, visible_text: str) -> bool:
        state_markers = (
            "初始状态",
            "当前状态",
            "最终状态",
            "状态变化",
            "第一次",
            "第二次",
            "第三次",
            "第一步",
            "第二步",
            "第三步",
        )
        marker_hits = sum(1 for marker in state_markers if marker in visible_text)
        if marker_hits >= 2:
            return True
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
        has_visual_heading = any(marker in visible_text for marker in ("时间轴", "流程示意", "状态示意", "示意图"))
        visual_detail_lines = [
            line
            for line in lines
            if (
                ("[" in line and "]" in line)
                or any(marker in line for marker in ("选中", "跳过", "移动", "返回", "变化"))
                or any(token in line for token in ("-->", "->", "=>", "|"))
            )
        ]
        if has_visual_heading and len(visual_detail_lines) >= 2:
            return True
        numeric_axis_lines = [
            line
            for line in lines
            if len(line.split()) >= 4 and all(part.strip(".,，。").isdigit() for part in line.split())
        ]
        if has_visual_heading and numeric_axis_lines and visual_detail_lines:
            return True
        timeline_lines = [
            line
            for line in lines
            if line.startswith(("1.", "2.", "3.", "1、", "2、", "3、", "-", "•"))
            and any(marker in line for marker in ("状态", "操作", "变化", "合并", "移动", "返回", "选择", "更新"))
        ]
        return len(timeline_lines) >= 2

    def _normalize_diagram_tool_language(self, visible_text: str) -> str:
        text = visible_text.strip()
        text = re.sub(r"(?<![A-Za-z])diagram\s*面板", "图示面板", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<![A-Za-z])diagram\s*panel", "图示面板", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<![A-Za-z])diagram(?![A-Za-z])", "图示", text, flags=re.IGNORECASE)
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=图示)", "", text)
        inline_preface_markers = (
            "图示如下",
            "示意如下",
            "如下图",
            "图如下",
            "画成这样",
            "我画成这样",
            "下面这张图",
            "下面的图",
        )
        if not any(marker in text for marker in inline_preface_markers):
            return text
        cleaned = text
        for marker in inline_preface_markers:
            cleaned = cleaned.replace(marker, "")
        cleaned = cleaned.lstrip("：:，,。；; \n\t")
        return cleaned

    def _diagram_tool_placeholder(self) -> str:
        return ""

    def _step_delta_status_for_policy(self, policy: HintPolicyDecision) -> str:
        if policy.action == "enter_code":
            return "ready_for_code"
        if policy.action == "enter_debug":
            return "ready_for_debug"
        return "in_progress"

    def _requests_diagram(self, message: str) -> bool:
        return self.diagram_request_policy.requests_diagram(message)


class BoardworkNode:
    agent_name = "BoardworkAgent"
    _required_handoff_fields = {"handoff_type", "focus", "boardwork_goal", "requirements"}
    _forbidden_handoff_fields = {
        "future_steps",
        "solution_code",
        "fixed_solution",
        "hard_coded_solution",
        "fixed_diagram",
        "diagram_layout",
        "board_nodes",
        "nodes",
        "content",
    }
    _required_agent_requirements = {"current_step_only", "event_sourced", "no_solution_content"}

    def __init__(
        self,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        handoff_policy: SpecialistHandoffPolicy | None = None,
    ):
        self.handoff_provider = handoff_provider
        self.prompt_spec = prompt_spec or boardwork_prompt_spec()
        self.handoff_policy = handoff_policy or load_default_specialist_handoff_policy()

    def run(
        self,
        current_step: AgentTeachingStep,
        *,
        source_events: list[Any] | None = None,
        collaboration_context: dict[str, Any] | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, dict[str, Any]]:
        events = source_events or []
        payload = self._build_agent_handoff(current_step, events, collaboration_context, request_context)
        handoff_source = "agent" if payload is not None else "policy"
        if payload is None:
            payload = self._build_policy_handoff(current_step, events)
        payload["boardwork_source"] = handoff_source
        quality_flag_count = len(payload.get("upstream_quality_flags", []))
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Inspected current teaching step for boardwork synchronization.",
            output_summary=(
                f"refresh_current_step: {current_step.step_key}; "
                f"quality_flags={quality_flag_count}; source={handoff_source}."
            ),
            blackboard_write_count=1,
        )
        return result, payload

    def _build_policy_handoff(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
    ) -> dict[str, Any]:
        return self.handoff_policy.build_boardwork_handoff(current_step, events)

    def _build_agent_handoff(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
        collaboration_context: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if self.handoff_provider is None:
            return None
        try:
            raw_handoff = self.handoff_provider(
                self._build_agent_payload(current_step, events, collaboration_context, request_context)
            )
        except TeachingAgentProviderUnavailableError:
            return None
        payload = self._parse_agent_handoff(raw_handoff, current_step, events)
        if payload is not None:
            request_context_payload = self._build_request_context_payload(request_context)
            if request_context_payload:
                payload["request_context"] = request_context_payload
        return payload

    def _build_agent_payload(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
        collaboration_context: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "current_step": build_step_payload(current_step),
            "source_events": self._build_compact_source_event_payloads(events),
            "request_context": self._build_request_context_payload(request_context),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _build_compact_source_event_payloads(self, events: list[Any]) -> list[dict[str, Any]]:
        compact_events: list[dict[str, Any]] = []
        for event in events[-8:]:
            compact_event = {
                "event_id": event.id,
                "event_type": event.event_type,
                "actor": event.actor,
            }
            content = getattr(event, "content", None)
            if isinstance(content, str) and content.strip():
                compact_event["content"] = content.strip()[:180]
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                summary: dict[str, Any] = {}
                for key in (
                    "family_id",
                    "action",
                    "student_state",
                    "repeat_risk",
                    "diagram_goal",
                    "visual_focus",
                    "reason",
                ):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        summary[key] = value.strip()[:120]
                    elif isinstance(value, int | float | bool):
                        summary[key] = value
                if summary:
                    compact_event["summary"] = summary
            compact_events.append(compact_event)
        return compact_events

    def _build_request_context_payload(self, request_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(request_context, dict):
            return {}
        payload: dict[str, Any] = {}
        latest_user_message = request_context.get("latest_user_message")
        tutor_visible_text = request_context.get("tutor_visible_text")
        tutor_needs_diagram = request_context.get("tutor_needs_diagram")
        knowledge_candidate_summary = request_context.get("knowledge_candidate_summary")
        knowledge_deposit_type = request_context.get("knowledge_deposit_type")
        recent_visible_messages = request_context.get("recent_visible_messages")
        previous_whiteboard = request_context.get("previous_whiteboard")
        response_language = request_context.get("response_language")
        if isinstance(latest_user_message, str) and latest_user_message.strip():
            payload["latest_user_message"] = latest_user_message.strip()[:600]
        if isinstance(tutor_visible_text, str) and tutor_visible_text.strip():
            payload["tutor_visible_text"] = tutor_visible_text.strip()[:600]
        if isinstance(tutor_needs_diagram, bool):
            payload["tutor_needs_diagram"] = tutor_needs_diagram
        if isinstance(knowledge_candidate_summary, str) and knowledge_candidate_summary.strip():
            payload["knowledge_candidate_summary"] = knowledge_candidate_summary.strip()[:480]
        if isinstance(knowledge_deposit_type, str) and knowledge_deposit_type.strip():
            payload["knowledge_deposit_type"] = knowledge_deposit_type.strip()[:64]
        if response_language in {"en", "zh"}:
            payload["response_language"] = response_language
        if isinstance(previous_whiteboard, list):
            cards: list[dict[str, Any]] = []
            for raw_card in previous_whiteboard[-6:]:
                if not isinstance(raw_card, dict):
                    continue
                takeaways = raw_card.get("takeaways")
                if not isinstance(takeaways, list):
                    continue
                card = {
                    "step_id": raw_card.get("step_id"),
                    "step_title": str(raw_card.get("step_title") or "")[:120],
                    "takeaways": [str(item).strip()[:350] for item in takeaways[-4:] if isinstance(item, str) and item.strip()],
                }
                if card["takeaways"]:
                    cards.append(card)
            payload["previous_whiteboard"] = cards
        if isinstance(recent_visible_messages, list):
            messages: list[dict[str, Any]] = []
            for raw_message in recent_visible_messages[-4:]:
                if not isinstance(raw_message, dict):
                    continue
                role = raw_message.get("role")
                content = raw_message.get("content")
                turn_index = raw_message.get("turn_index")
                if not isinstance(role, str) or not isinstance(content, str) or not content.strip():
                    continue
                message: dict[str, Any] = {
                    "role": role[:32],
                    "content": content.strip()[:500],
                }
                if isinstance(turn_index, int):
                    message["turn_index"] = turn_index
                messages.append(message)
            if messages:
                payload["recent_visible_messages"] = messages
        return payload

    def _parse_agent_handoff(
        self,
        raw_handoff: dict[str, Any] | None,
        current_step: AgentTeachingStep,
        events: list[Any],
    ) -> dict[str, Any] | None:
        if not isinstance(raw_handoff, dict):
            return None
        if self._forbidden_handoff_fields.intersection(raw_handoff.keys()):
            return None
        if not self._required_handoff_fields.issubset(raw_handoff.keys()):
            return None
        if raw_handoff.get("handoff_type") != "boardwork_refresh":
            return None
        focus = raw_handoff.get("focus")
        goal = raw_handoff.get("boardwork_goal")
        requirements = raw_handoff.get("requirements")
        if not isinstance(focus, str) or not focus.strip():
            return None
        if not isinstance(goal, str) or not goal.strip():
            return None
        if not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements):
            return None
        parsed_requirements = [item.strip() for item in requirements if item.strip()]
        if not self.handoff_policy.boardwork_agent_required_requirements.issubset(set(parsed_requirements)):
            return None
        if contains_forbidden_text(focus) or contains_forbidden_text(goal):
            return None
        payload = {
            "handoff_type": "boardwork_refresh",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "focus": focus.strip(),
            "boardwork_goal": goal.strip(),
            "requirements": parsed_requirements,
            "source_event_ids": [event.id for event in events if event.id is not None],
            "source_event_types": [event.event_type for event in events],
        }
        continuity_action = raw_handoff.get("continuity_action")
        if continuity_action in {"append", "refine", "repeat"}:
            payload["continuity_action"] = continuity_action
        incremental_takeaway = raw_handoff.get("incremental_takeaway")
        if isinstance(incremental_takeaway, str):
            payload["incremental_takeaway"] = incremental_takeaway.strip()[:500]
        attach_upstream_quality_flags(payload, raw_handoff.get("upstream_quality_flags"))
        return payload

class CodingNode:
    agent_name = "CodingAgent"
    _required_handoff_fields = {"handoff_type", "coding_goal", "checkpoints"}
    _forbidden_handoff_fields = {
        "solution_code",
        "patch_code",
        "fixed_solution",
        "hard_coded_solution",
        "future_steps",
        "answer_template",
    }
    def __init__(
        self,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        handoff_policy: SpecialistHandoffPolicy | None = None,
    ):
        self.handoff_provider = handoff_provider
        self.prompt_spec = prompt_spec or coding_prompt_spec()
        self.handoff_policy = handoff_policy or load_default_specialist_handoff_policy()

    def run(
        self,
        current_step: AgentTeachingStep,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, dict[str, Any]]:
        payload = self._build_agent_handoff(current_step, collaboration_context)
        handoff_source = "agent" if payload is not None else "policy"
        if payload is None:
            payload = self._build_policy_handoff(current_step)
        payload["coding_source"] = handoff_source
        quality_flag_count = len(payload.get("upstream_quality_flags", []))
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Prepared a generic implementation handoff from the current teaching step.",
            output_summary=(
                f"implementation_plan: {current_step.step_key}; "
                f"quality_flags={quality_flag_count}; source={handoff_source}."
            ),
            blackboard_write_count=1,
        )
        return result, payload

    def _build_policy_handoff(self, current_step: AgentTeachingStep) -> dict[str, Any]:
        return self.handoff_policy.build_coding_handoff(current_step)

    def _build_agent_handoff(
        self,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if self.handoff_provider is None:
            return None
        raw_handoff = self.handoff_provider(self._build_agent_payload(current_step, collaboration_context))
        return self._parse_agent_handoff(raw_handoff, current_step)

    def _build_agent_payload(
        self,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "current_step": build_step_payload(current_step),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_handoff(
        self,
        raw_handoff: dict[str, Any] | None,
        current_step: AgentTeachingStep,
    ) -> dict[str, Any] | None:
        if not isinstance(raw_handoff, dict):
            return None
        if self._forbidden_handoff_fields.intersection(raw_handoff.keys()):
            return None
        if not self._required_handoff_fields.issubset(raw_handoff.keys()):
            return None
        if raw_handoff.get("handoff_type") != "implementation_plan":
            return None
        goal = raw_handoff.get("coding_goal")
        checkpoints = parse_string_list(raw_handoff.get("checkpoints"), min_items=3, max_items=6)
        if not isinstance(goal, str) or not goal.strip():
            return None
        if checkpoints is None:
            return None
        if contains_forbidden_text(goal) or any(contains_forbidden_text(checkpoint) for checkpoint in checkpoints):
            return None
        payload = {
            "handoff_type": "implementation_plan",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "coding_goal": goal.strip(),
            "checkpoints": checkpoints,
        }
        attach_upstream_quality_flags(payload, raw_handoff.get("upstream_quality_flags"))
        return payload

class DebugNode:
    agent_name = "DebugAgent"
    _required_handoff_fields = {"handoff_type", "debug_goal", "checkpoints"}
    _forbidden_handoff_fields = {
        "solution_code",
        "patch_code",
        "fixed_solution",
        "hard_coded_solution",
        "future_steps",
        "answer_template",
    }
    def __init__(
        self,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        handoff_policy: SpecialistHandoffPolicy | None = None,
    ):
        self.handoff_provider = handoff_provider
        self.prompt_spec = prompt_spec or debug_prompt_spec()
        self.handoff_policy = handoff_policy or load_default_specialist_handoff_policy()

    def run(
        self,
        current_step: AgentTeachingStep,
        *,
        collaboration_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, dict[str, Any]]:
        payload = self._build_agent_handoff(current_step, collaboration_context)
        handoff_source = "agent" if payload is not None else "policy"
        if payload is None:
            payload = self._build_policy_handoff(current_step)
        payload["debug_source"] = handoff_source
        quality_flag_count = len(payload.get("upstream_quality_flags", []))
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Prepared a generic debug handoff from the current teaching step.",
            output_summary=(
                f"debug_plan: {current_step.step_key}; "
                f"quality_flags={quality_flag_count}; source={handoff_source}."
            ),
            blackboard_write_count=1,
        )
        return result, payload

    def _build_policy_handoff(self, current_step: AgentTeachingStep) -> dict[str, Any]:
        return self.handoff_policy.build_debug_handoff(current_step)

    def _build_agent_handoff(
        self,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if self.handoff_provider is None:
            return None
        raw_handoff = self.handoff_provider(self._build_agent_payload(current_step, collaboration_context))
        return self._parse_agent_handoff(raw_handoff, current_step)

    def _build_agent_payload(
        self,
        current_step: AgentTeachingStep,
        collaboration_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "current_step": build_step_payload(current_step),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }

    def _parse_agent_handoff(
        self,
        raw_handoff: dict[str, Any] | None,
        current_step: AgentTeachingStep,
    ) -> dict[str, Any] | None:
        if not isinstance(raw_handoff, dict):
            return None
        if self._forbidden_handoff_fields.intersection(raw_handoff.keys()):
            return None
        if not self._required_handoff_fields.issubset(raw_handoff.keys()):
            return None
        if raw_handoff.get("handoff_type") != "debug_plan":
            return None
        goal = raw_handoff.get("debug_goal")
        checkpoints = parse_string_list(raw_handoff.get("checkpoints"), min_items=3, max_items=6)
        if not isinstance(goal, str) or not goal.strip():
            return None
        if checkpoints is None:
            return None
        if contains_forbidden_text(goal) or any(contains_forbidden_text(checkpoint) for checkpoint in checkpoints):
            return None
        payload = {
            "handoff_type": "debug_plan",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "debug_goal": goal.strip(),
            "checkpoints": checkpoints,
        }
        attach_upstream_quality_flags(payload, raw_handoff.get("upstream_quality_flags"))
        return payload


class DiagramNode:
    agent_name = "DiagramAgent"
    _diagram_tool_contract = {
        "owner_agent": "DiagramAgent",
        "tool_backend": "local",
        "tool_names": ["diagram_verify_tool", "diagram_scene_materializer"],
        "scene_output_field": "scene",
        "student_visible_surface": "tester-ui diagram canvas",
        "forbidden_outputs": ["markdown", "code_fence", "ascii_diagram", "mermaid", "graphviz", "cli_drawing"],
    }
    _required_handoff_fields = {"handoff_type", "diagram_goal", "visual_focus", "requirements"}
    _forbidden_handoff_fields = {
        "future_steps",
        "solution_code",
        "fixed_solution",
        "hard_coded_solution",
        "fixed_diagram",
        "diagram_layout",
        "layout",
        "nodes",
        "edges",
        "coordinates",
        "steps",
    }
    _required_agent_requirements = {"model_generated_scene", "current_step_only", "no_static_layout"}
    _strict_repair_attempts = 2

    def __init__(
        self,
        handoff_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        prompt_spec: AgentPromptSpec | None = None,
        handoff_policy: SpecialistHandoffPolicy | None = None,
    ):
        self.handoff_provider = handoff_provider
        self.prompt_spec = prompt_spec or diagram_prompt_spec()
        self.handoff_policy = handoff_policy or load_default_specialist_handoff_policy()

    def run(
        self,
        current_step: AgentTeachingStep,
        *,
        source_events: list[Any] | None = None,
        collaboration_context: dict[str, Any] | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> tuple[AgentNodeResult, dict[str, Any]]:
        events = source_events or []
        payload, provider_rejection_reason, provider_rejection_details = self._build_agent_handoff_with_reason(
            current_step,
            events,
            collaboration_context,
            request_context,
        )
        handoff_source = "agent" if payload is not None else "policy"
        if payload is None:
            if self._strict_scene_required(request_context):
                reason = provider_rejection_reason or "provider_unavailable"
                raise TeachingAgentProviderUnavailableError(
                    agent_name=self.agent_name,
                    capability="diagram_handoff",
                    outcome="provider_output_rejected",
                    details={
                        "rejection_reason": reason,
                        "rejection_details": provider_rejection_details or {},
                    },
                )
            payload = self._build_policy_handoff(current_step, events)
            if provider_rejection_reason:
                payload["provider_rejection_reason"] = provider_rejection_reason
        payload["diagram_source"] = handoff_source
        quality_flag_count = len(payload.get("upstream_quality_flags", []))
        result = AgentNodeResult(
            agent_name=self.agent_name,
            status="ok",
            input_summary="Prepared a generic diagram generation handoff from the current teaching step.",
            output_summary=(
                f"diagram_request: {current_step.step_key}; "
                f"quality_flags={quality_flag_count}; source={handoff_source}."
            ),
            blackboard_write_count=1,
        )
        return result, payload

    def _build_policy_handoff(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
    ) -> dict[str, Any]:
        return self.handoff_policy.build_diagram_handoff(current_step, events)

    def _build_agent_handoff(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
        collaboration_context: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload, _, _ = self._build_agent_handoff_with_reason(
            current_step,
            events,
            collaboration_context,
            request_context,
        )
        return payload

    def _build_agent_handoff_with_reason(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
        collaboration_context: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
        if self.handoff_provider is None:
            return None, None, None
        agent_payload = self._build_agent_payload(current_step, events, collaboration_context, request_context)
        raw_handoff = self.handoff_provider(agent_payload)
        payload, rejection_reason, rejection_details = self._parse_agent_handoff_with_reason(
            raw_handoff,
            current_step,
            events,
            request_context=request_context,
        )
        repair_attempt = 1
        while (
            payload is None
            and repair_attempt <= self._strict_repair_attempts
            and self._strict_scene_required(request_context)
            and rejection_reason in {"missing_scene", "invalid_scene"}
        ):
            repaired_handoff = self.handoff_provider(
                {
                    **agent_payload,
                    "repair_context": {
                        "previous_rejection_reason": rejection_reason,
                        "rejection_details": rejection_details,
                        "repair_attempt": repair_attempt,
                        "instruction": self._diagram_repair_instruction(
                            request_context,
                            rejection_details=rejection_details,
                        ),
                    },
                }
            )
            payload, repair_rejection_reason, repair_rejection_details = self._parse_agent_handoff_with_reason(
                repaired_handoff,
                current_step,
                events,
                request_context=request_context,
            )
            rejection_reason = repair_rejection_reason or rejection_reason
            rejection_details = repair_rejection_details
            repair_attempt += 1
        if payload is not None:
            request_context_payload = self._build_request_context_payload(request_context)
            if request_context_payload:
                payload["request_context"] = request_context_payload
            return payload, None, None
        return None, rejection_reason, rejection_details

    def _build_agent_payload(
        self,
        current_step: AgentTeachingStep,
        events: list[Any],
        collaboration_context: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "agent_name": self.prompt_spec.agent_name,
            "purpose": self.prompt_spec.purpose,
            "system_prompt": self.prompt_spec.system_prompt,
            "current_step": build_step_payload(current_step),
            "source_events": self._build_compact_source_event_payloads(events),
            "request_context": self._build_request_context_payload(request_context),
            "diagram_tool_contract": dict(self._diagram_tool_contract),
            "collaboration_context": build_collaboration_context_payload(collaboration_context),
            "required_schema": self.prompt_spec.required_schema,
        }
        strict_scene_contract = self._build_strict_scene_contract(request_context)
        if strict_scene_contract:
            payload["strict_scene_contract"] = strict_scene_contract
            payload["provider_visible_scene_instruction"] = self._provider_visible_scene_instruction(request_context)
        return payload

    def _build_compact_source_event_payloads(self, events: list[Any]) -> list[dict[str, Any]]:
        compact_events: list[dict[str, Any]] = []
        for event in events[-8:]:
            compact_event = {
                "event_id": event.id,
                "event_type": event.event_type,
                "actor": event.actor,
            }
            content = getattr(event, "content", None)
            if isinstance(content, str) and content.strip():
                compact_event["content"] = content.strip()[:180]
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                summary: dict[str, Any] = {}
                for key in (
                    "family_id",
                    "action",
                    "student_state",
                    "repeat_risk",
                    "diagram_goal",
                    "visual_focus",
                    "reason",
                ):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        summary[key] = value.strip()[:120]
                    elif isinstance(value, int | float | bool):
                        summary[key] = value
                if summary:
                    compact_event["summary"] = summary
            compact_events.append(compact_event)
        return compact_events

    def _build_request_context_payload(self, request_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(request_context, dict):
            return {}
        payload: dict[str, Any] = {}
        latest_user_message = request_context.get("latest_user_message")
        tutor_visible_text = request_context.get("tutor_visible_text")
        tutor_needs_diagram = request_context.get("tutor_needs_diagram")
        recent_visible_messages = request_context.get("recent_visible_messages")
        if isinstance(latest_user_message, str) and latest_user_message.strip():
            payload["latest_user_message"] = latest_user_message.strip()[:600]
        if isinstance(tutor_visible_text, str) and tutor_visible_text.strip():
            payload["tutor_visible_text"] = tutor_visible_text.strip()[:600]
        if isinstance(tutor_needs_diagram, bool):
            payload["tutor_needs_diagram"] = tutor_needs_diagram
        if isinstance(recent_visible_messages, list):
            messages: list[dict[str, Any]] = []
            for raw_message in recent_visible_messages[-4:]:
                if not isinstance(raw_message, dict):
                    continue
                role = raw_message.get("role")
                content = raw_message.get("content")
                turn_index = raw_message.get("turn_index")
                if not isinstance(role, str) or not isinstance(content, str) or not content.strip():
                    continue
                message: dict[str, Any] = {
                    "role": role[:32],
                    "content": content.strip()[:500],
                }
                if isinstance(turn_index, int):
                    message["turn_index"] = turn_index
                messages.append(message)
            if messages:
                payload["recent_visible_messages"] = messages
        return payload

    def _parse_agent_handoff(
        self,
        raw_handoff: dict[str, Any] | None,
        current_step: AgentTeachingStep,
        events: list[Any],
    ) -> dict[str, Any] | None:
        payload, _, _ = self._parse_agent_handoff_with_reason(
            raw_handoff,
            current_step,
            events,
            request_context=None,
        )
        return payload

    def _parse_agent_handoff_with_reason(
        self,
        raw_handoff: dict[str, Any] | None,
        current_step: AgentTeachingStep,
        events: list[Any],
        *,
        request_context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
        if not isinstance(raw_handoff, dict):
            return None, "invalid_payload", None
        shorthand_handoff = self._coerce_agent_scene_shorthand(raw_handoff, current_step)
        if shorthand_handoff is not None:
            raw_handoff = shorthand_handoff
        if self._forbidden_handoff_fields.intersection(raw_handoff.keys()):
            return None, "forbidden_fields", None
        if not self._required_handoff_fields.issubset(raw_handoff.keys()):
            return None, "missing_required_fields", None
        if raw_handoff.get("handoff_type") != "diagram_request":
            return None, "invalid_handoff_type", None
        diagram_goal = raw_handoff.get("diagram_goal")
        visual_focus = raw_handoff.get("visual_focus")
        requirements = raw_handoff.get("requirements")
        if not isinstance(diagram_goal, str) or not diagram_goal.strip():
            return None, "invalid_diagram_goal", None
        if not isinstance(visual_focus, str) or not visual_focus.strip():
            return None, "invalid_visual_focus", None
        if not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements):
            return None, "invalid_requirements", None
        parsed_requirements = [item.strip() for item in requirements if item.strip()]
        if not self.handoff_policy.diagram_agent_required_requirements.issubset(set(parsed_requirements)):
            return None, "missing_required_requirements", None
        if (
            contains_forbidden_text(diagram_goal)
            or contains_forbidden_text(visual_focus)
            or self._contains_text_diagram_artifact(diagram_goal)
            or self._contains_text_diagram_artifact(visual_focus)
        ):
            return None, "forbidden_text", None
        payload = {
            "handoff_type": "diagram_request",
            "step_id": current_step.id,
            "step_key": current_step.step_key,
            "step_title": current_step.title,
            "diagram_goal": diagram_goal.strip(),
            "visual_focus": visual_focus.strip(),
            "requirements": parsed_requirements,
            "source_event_ids": [event.id for event in events if event.id is not None],
            "source_event_types": [event.event_type for event in events],
        }
        scene = raw_handoff.get("scene")
        strict_scene_required = self._strict_scene_required(request_context)
        if strict_scene_required and not isinstance(scene, dict):
            return None, "missing_scene", {"reason": "missing_scene"}
        normalized_scene = (
            self._normalize_agent_scene(
                scene,
                current_step=current_step,
                diagram_goal=diagram_goal.strip(),
                visual_focus=visual_focus.strip(),
            )
            if isinstance(scene, dict) and not contains_forbidden_text(str(scene))
            else None
        )
        scene_validation_details: dict[str, Any] | None = None
        if normalized_scene is not None and not contains_forbidden_text(str(normalized_scene)):
            scene_validation_details = self._agent_scene_rejection_details(
                normalized_scene,
                request_context=request_context,
            )
            if scene_validation_details is None:
                payload["scene"] = normalized_scene
        elif strict_scene_required:
            return None, "invalid_scene", scene_validation_details or {"reason": "invalid_scene"}
        if strict_scene_required and "scene" not in payload:
            return None, "invalid_scene", scene_validation_details or {"reason": "invalid_scene"}
        attach_upstream_quality_flags(payload, raw_handoff.get("upstream_quality_flags"))
        return payload, None, None

    def _coerce_agent_scene_shorthand(
        self,
        raw_handoff: dict[str, Any],
        current_step: AgentTeachingStep,
    ) -> dict[str, Any] | None:
        if self._required_handoff_fields.issubset(raw_handoff.keys()):
            return None
        if contains_forbidden_text(str(raw_handoff)) or self._contains_text_diagram_artifact(str(raw_handoff)):
            return None

        raw_scene = self._extract_agent_scene_shorthand(raw_handoff, current_step)
        if raw_scene is None:
            return None
        raw_steps = raw_scene.get("steps")
        first_step = raw_steps[0] if isinstance(raw_steps, list) and raw_steps and isinstance(raw_steps[0], dict) else {}
        diagram_goal = self._first_non_empty_string(
            raw_handoff.get("diagram_goal"),
            raw_scene.get("teaching_goal"),
            first_step.get("teaching_note"),
            current_step.teaching_goal,
            current_step.title,
        )
        visual_focus = self._first_non_empty_string(
            raw_handoff.get("visual_focus"),
            raw_scene.get("title"),
            first_step.get("title"),
            current_step.title,
        )
        if diagram_goal is None or visual_focus is None:
            return None
        return {
            "handoff_type": "diagram_request",
            "diagram_goal": diagram_goal,
            "visual_focus": visual_focus,
            "requirements": ["model_generated_scene", "current_step_only", "no_static_layout"],
            "scene": raw_scene,
            "upstream_quality_flags": raw_handoff.get("upstream_quality_flags"),
        }

    def _extract_agent_scene_shorthand(
        self,
        raw_handoff: dict[str, Any],
        current_step: AgentTeachingStep,
    ) -> dict[str, Any] | None:
        raw_scene = raw_handoff.get("scene")
        if isinstance(raw_scene, dict) and isinstance(raw_scene.get("steps"), list):
            return raw_scene
        if isinstance(raw_handoff.get("steps"), list):
            return raw_handoff
        if not self._looks_like_diagram_step(raw_handoff):
            return None
        step_id = self._first_non_empty_string(raw_handoff.get("step_id"), current_step.step_key, str(current_step.id or "focus"))
        return {
            "scene_id": self._first_non_empty_string(raw_handoff.get("scene_id"), f"agent-scene-{current_step.id or current_step.step_key}"),
            "title": self._first_non_empty_string(raw_handoff.get("scene_title"), raw_handoff.get("title"), current_step.title),
            "diagram_kind": raw_handoff.get("diagram_kind") or "flow",
            "layout": raw_handoff.get("layout") or "flow",
            "teaching_goal": self._first_non_empty_string(
                raw_handoff.get("teaching_goal"),
                raw_handoff.get("teaching_note"),
                current_step.teaching_goal,
                current_step.title,
            ),
            "default_step_id": step_id,
            "steps": [{**raw_handoff, "step_id": step_id}],
        }

    def _looks_like_diagram_step(self, value: dict[str, Any]) -> bool:
        return all(isinstance(value.get(key), dict) for key in ("entry_state", "exit_state", "transition"))

    def _first_non_empty_string(self, *values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _normalize_agent_scene(
        self,
        scene: dict[str, Any],
        *,
        current_step: AgentTeachingStep,
        diagram_goal: str,
        visual_focus: str,
    ) -> dict[str, Any] | None:
        steps = scene.get("steps")
        if not isinstance(steps, list) or not steps:
            return None
        scene_id = scene.get("scene_id") or f"agent-scene-{current_step.id or current_step.step_key}"
        normalized_steps: list[dict[str, Any]] = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                return None
            entry_state = self._normalize_diagram_state(raw_step.get("entry_state"), step_index=index)
            exit_state = self._normalize_diagram_state(raw_step.get("exit_state"), step_index=index)
            transition = self._normalize_diagram_transition(
                raw_step.get("transition"),
                step_index=index,
                state=exit_state,
            )
            normalized_steps.append(
                {
                    "step_id": str(raw_step.get("step_id") or current_step.id or index),
                    "order_index": index,
                    "title": str(raw_step.get("title") or current_step.title or visual_focus),
                    "teaching_note": str(raw_step.get("teaching_note") or diagram_goal),
                    "entry_state": entry_state,
                    "exit_state": exit_state,
                    "transition": transition,
                }
            )
        return {
            "scene_id": str(scene_id),
            "session_id": scene.get("session_id"),
            "parent_scene_id": scene.get("parent_scene_id"),
            "base_step_id": scene.get("base_step_id"),
            "inherit_mode": _safe_enum(
                scene.get("inherit_mode"),
                {"new", "extend", "revise", "branch"},
                "new",
            ),
            "title": str(scene.get("title") or current_step.title or visual_focus),
            "diagram_kind": _safe_enum(
                scene.get("diagram_kind"),
                {
                    "array",
                    "linked_list",
                    "stack",
                    "queue",
                    "deque",
                    "tree",
                    "heap",
                    "segment_tree",
                    "trie",
                    "graph",
                    "flow",
                    "union_find",
                    "fenwick",
                    "matrix",
                    "table",
                    "custom",
                },
                "flow",
            ),
            "layout": _safe_enum(
                scene.get("layout"),
                {
                    "freeform",
                    "array",
                    "linked_list",
                    "stack",
                    "queue",
                    "deque",
                    "tree",
                    "graph",
                    "matrix",
                    "table",
                    "flow",
                },
                "flow",
            ),
            "teaching_goal": str(scene.get("teaching_goal") or diagram_goal),
            "default_step_id": str(scene.get("default_step_id") or normalized_steps[0]["step_id"]),
            "status": _safe_enum(scene.get("status"), {"draft", "verified", "archived"}, "draft"),
            "source_turn_index": _safe_int(scene.get("source_turn_index"), 0),
            "steps": normalized_steps,
        }

    def _normalize_diagram_state(self, raw_state: Any, *, step_index: int) -> dict[str, Any]:
        state = raw_state if isinstance(raw_state, dict) else {}
        normalized_nodes: list[dict[str, Any]] = []
        node_lookup: dict[str, str] = {}
        raw_nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
        for index, raw_node in enumerate(raw_nodes):
            node = self._normalize_diagram_node(raw_node, step_index=step_index, index=index)
            if node is None:
                continue
            normalized_nodes.append(node)
            node_lookup[str(raw_node)] = node["node_id"]
            node_lookup[node["label"]] = node["node_id"]
        normalized_items: list[dict[str, Any]] = []
        raw_items = state.get("items") if isinstance(state.get("items"), list) else []
        for index, raw_item in enumerate(raw_items):
            item = self._normalize_diagram_item(raw_item, step_index=step_index, index=index)
            if item is not None:
                normalized_items.append(item)
        normalized_edges: list[dict[str, Any]] = []
        raw_edges = state.get("edges") if isinstance(state.get("edges"), list) else []
        for index, raw_edge in enumerate(raw_edges):
            edge = self._normalize_diagram_edge(raw_edge, step_index=step_index, index=index, node_lookup=node_lookup)
            if edge is not None:
                normalized_edges.append(edge)
        return {"nodes": normalized_nodes, "edges": normalized_edges, "items": normalized_items}

    def _normalize_diagram_node(self, raw_node: Any, *, step_index: int, index: int) -> dict[str, Any] | None:
        if isinstance(raw_node, dict):
            label = raw_node.get("label") or raw_node.get("name") or raw_node.get("id") or raw_node.get("node_id")
            if not isinstance(label, str) or not label.strip():
                return None
            node_id = raw_node.get("node_id") or raw_node.get("id") or f"node_{step_index}_{index}"
            return {
                "node_id": str(node_id),
                "label": label.strip(),
                "x": float(raw_node.get("x") if isinstance(raw_node.get("x"), int | float) else 60 + index * 160),
                "y": float(raw_node.get("y") if isinstance(raw_node.get("y"), int | float) else 72),
                "state": _safe_enum(raw_node.get("state"), {"default", "highlighted", "muted"}),
                "shape": _safe_enum(raw_node.get("shape"), {"circle", "rounded_rect", "pill"}, "rounded_rect"),
                "width": raw_node.get("width") if isinstance(raw_node.get("width"), int | float) else 96,
                "height": raw_node.get("height") if isinstance(raw_node.get("height"), int | float) else 42,
            }
        if isinstance(raw_node, str) and raw_node.strip():
            return {
                "node_id": f"node_{step_index}_{index}",
                "label": raw_node.strip(),
                "x": float(60 + index * 160),
                "y": 72.0,
                "state": None,
                "shape": "rounded_rect",
                "width": 96.0,
                "height": 42.0,
            }
        return None

    def _normalize_diagram_edge(
        self,
        raw_edge: Any,
        *,
        step_index: int,
        index: int,
        node_lookup: dict[str, str],
    ) -> dict[str, Any] | None:
        if not isinstance(raw_edge, dict):
            return None
        from_value = raw_edge.get("from") or raw_edge.get("from_") or raw_edge.get("source")
        to_value = raw_edge.get("to") or raw_edge.get("target")
        if from_value is None or to_value is None:
            return None
        from_id = node_lookup.get(str(from_value), str(from_value))
        to_id = node_lookup.get(str(to_value), str(to_value))
        return {
            "edge_id": str(raw_edge.get("edge_id") or raw_edge.get("id") or f"edge_{step_index}_{index}"),
            "from": from_id,
            "to": to_id,
            "label": raw_edge.get("label") if isinstance(raw_edge.get("label"), str) else None,
            "state": _safe_enum(raw_edge.get("state"), {"default", "highlighted", "muted"}),
        }

    def _normalize_diagram_item(self, raw_item: Any, *, step_index: int, index: int) -> dict[str, Any] | None:
        if isinstance(raw_item, dict):
            item_id = raw_item.get("item_id") or raw_item.get("id") or raw_item.get("key") or f"item_{step_index}_{index}"
            label = (
                raw_item.get("label")
                or raw_item.get("text")
                or raw_item.get("data")
                or raw_item.get("title")
                or raw_item.get("type")
                or item_id
            )
            if not isinstance(label, str) or not label.strip():
                return None
            return {
                "item_id": str(item_id),
                "label": label.strip(),
                "x": float(raw_item.get("x") if isinstance(raw_item.get("x"), int | float) else 48),
                "y": float(raw_item.get("y") if isinstance(raw_item.get("y"), int | float) else 152 + index * 48),
                "width": float(raw_item.get("width") if isinstance(raw_item.get("width"), int | float) else 260),
                "height": float(raw_item.get("height") if isinstance(raw_item.get("height"), int | float) else 36),
                "kind": _safe_enum(raw_item.get("kind"), {"cell", "block", "frame", "pointer", "text"}, "text"),
                "state": _safe_enum(raw_item.get("state"), {"default", "highlighted", "muted"}),
                "secondary_label": raw_item.get("secondary_label") if isinstance(raw_item.get("secondary_label"), str) else None,
            }
        if isinstance(raw_item, str) and raw_item.strip():
            return {
                "item_id": f"item_{step_index}_{index}",
                "label": raw_item.strip(),
                "x": 48.0,
                "y": float(152 + index * 48),
                "width": 260.0,
                "height": 36.0,
                "kind": "text",
                "state": None,
                "secondary_label": None,
            }
        return None

    def _normalize_diagram_transition(
        self,
        raw_transition: Any,
        *,
        step_index: int,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        transition = raw_transition if isinstance(raw_transition, dict) else {}
        raw_actions = transition.get("actions") if isinstance(transition.get("actions"), list) else []
        target_id, target = self._default_transition_target(state)
        actions: list[dict[str, Any]] = []
        for index, raw_action in enumerate(raw_actions):
            if isinstance(raw_action, dict):
                action_target_id = raw_action.get("target_id") or target_id
                if not isinstance(action_target_id, str) or not action_target_id.strip():
                    continue
                kind = _safe_enum(raw_action.get("kind"), {"highlight", "focus", "move", "remove", "add"}, "highlight")
                raw_action_target = raw_action.get("target")
                action_target = self._normalize_transition_action_target(raw_action_target)
                if action_target is None:
                    action_target_id, action_target = self._default_transition_target_for_id(
                        state,
                        action_target_id.strip(),
                    )
                elif not self._transition_target_exists(
                    state,
                    target=action_target,
                    target_id=action_target_id.strip(),
                ):
                    if not self._is_recoverable_transition_action_target(raw_action_target):
                        action_target_id = action_target_id.strip()
                    else:
                        action_target_id = self._transition_target_id_for_kind(
                            state,
                            target=action_target,
                            preferred_id=action_target_id.strip(),
                        )
                        if action_target_id is None:
                            continue
                else:
                    action_target_id = self._transition_target_id_for_kind(
                        state,
                        target=action_target,
                        preferred_id=action_target_id.strip(),
                    )
                    if action_target_id is None:
                        continue
                actions.append(
                    {
                        "kind": kind,
                        "target": action_target,
                        "target_id": action_target_id,
                        "x": raw_action.get("x") if isinstance(raw_action.get("x"), int | float) else None,
                        "y": raw_action.get("y") if isinstance(raw_action.get("y"), int | float) else None,
                        "width": raw_action.get("width") if isinstance(raw_action.get("width"), int | float) else None,
                        "height": raw_action.get("height") if isinstance(raw_action.get("height"), int | float) else None,
                    }
                )
            elif isinstance(raw_action, str) and target_id:
                actions.append({"kind": "highlight", "target": target, "target_id": target_id})
        if not actions and target_id:
            actions.append({"kind": "highlight", "target": target, "target_id": target_id})
        if not actions:
            actions.append({"kind": "highlight", "target": "item", "target_id": f"item_{step_index}_0"})
        return {"actions": actions}

    def _default_transition_target(self, state: dict[str, Any]) -> tuple[str | None, str]:
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if items and isinstance(items[0], dict) and isinstance(items[0].get("item_id"), str):
            return items[0]["item_id"], "item"
        nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
        if nodes and isinstance(nodes[0], dict) and isinstance(nodes[0].get("node_id"), str):
            return nodes[0]["node_id"], "node"
        return None, "item"

    def _default_transition_target_for_id(self, state: dict[str, Any], target_id: str) -> tuple[str, str]:
        for target_kind in ("item", "node", "edge"):
            matching_id = self._matching_transition_target_id(
                state,
                target=target_kind,
                target_id=target_id,
            )
            if matching_id is not None:
                return matching_id, target_kind
        default_target_id, default_target = self._default_transition_target(state)
        return (default_target_id or target_id), default_target

    def _normalize_transition_action_target(self, value: Any) -> str | None:
        target = _safe_enum(value, {"node", "edge", "item"})
        if target is not None:
            return target
        if isinstance(value, list) and len(value) == 1:
            return _safe_enum(value[0], {"node", "edge", "item"})
        return None

    def _is_recoverable_transition_action_target(self, value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 1
            and _safe_enum(value[0], {"node", "edge", "item"}) is not None
        )

    def _transition_target_id_for_kind(
        self,
        state: dict[str, Any],
        *,
        target: str,
        preferred_id: str,
    ) -> str | None:
        matching_id = self._matching_transition_target_id(state, target=target, target_id=preferred_id)
        if matching_id is not None:
            return matching_id
        default_id, _ = self._default_transition_target_for_kind(state, target)
        return default_id

    def _default_transition_target_for_kind(
        self,
        state: dict[str, Any],
        target: str,
    ) -> tuple[str | None, str]:
        if target == "item":
            items = state.get("items") if isinstance(state.get("items"), list) else []
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("item_id"), str):
                    return item["item_id"], "item"
        if target == "node":
            nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
            for node in nodes:
                if isinstance(node, dict) and isinstance(node.get("node_id"), str):
                    return node["node_id"], "node"
        if target == "edge":
            edges = state.get("edges") if isinstance(state.get("edges"), list) else []
            for edge in edges:
                if isinstance(edge, dict) and isinstance(edge.get("edge_id"), str):
                    return edge["edge_id"], "edge"
        return None, target

    def _matching_transition_target_id(
        self,
        state: dict[str, Any],
        *,
        target: str,
        target_id: str,
    ) -> str | None:
        if target == "item":
            items = state.get("items") if isinstance(state.get("items"), list) else []
            if any(isinstance(item, dict) and item.get("item_id") == target_id for item in items):
                return target_id
            return self._matching_transition_target_id_by_visible_text(
                items,
                id_key="item_id",
                target_id=target_id,
            )
        if target == "node":
            nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
            if any(isinstance(node, dict) and node.get("node_id") == target_id for node in nodes):
                return target_id
            return self._matching_transition_target_id_by_visible_text(
                nodes,
                id_key="node_id",
                target_id=target_id,
            )
        if target == "edge":
            edges = state.get("edges") if isinstance(state.get("edges"), list) else []
            if any(isinstance(edge, dict) and edge.get("edge_id") == target_id for edge in edges):
                return target_id
            return self._matching_transition_target_id_by_visible_text(
                edges,
                id_key="edge_id",
                target_id=target_id,
            )
        return None

    def _matching_transition_target_id_by_visible_text(
        self,
        values: list[Any],
        *,
        id_key: str,
        target_id: str,
    ) -> str | None:
        query = self._normalize_anchor_text(target_id)
        if not query:
            return None
        matches: list[str] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            candidate_id = value.get(id_key)
            if not isinstance(candidate_id, str):
                continue
            visible_text = self._transition_target_visible_text(value)
            if not visible_text:
                continue
            if query in visible_text or visible_text in query:
                matches.append(candidate_id)
        if len(matches) == 1:
            return matches[0]
        return None

    def _transition_target_visible_text(self, value: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("label", "secondary_label", "title", "caption", "description"):
            raw_value = value.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                parts.append(raw_value.strip())
        return self._normalize_anchor_text(" ".join(parts))

    def _transition_target_exists(
        self,
        state: dict[str, Any],
        *,
        target: str,
        target_id: str,
    ) -> bool:
        return self._matching_transition_target_id(state, target=target, target_id=target_id) is not None

    def _diagram_repair_instruction(
        self,
        request_context: dict[str, Any] | None,
        *,
        rejection_details: dict[str, Any] | None = None,
    ) -> str:
        instruction = (
            "Return the same diagram_request schema again, but include a non-null scene object "
            "that validates against the local diagram protocol. The scene must visualize only "
            "the current step and must not use markdown, code fences, ASCII diagrams, command-line "
            "drawing instructions, fixed solutions, or memorized layouts."
        )
        anchors = self._explicit_visual_detail_anchors(request_context)
        if anchors:
            instruction += (
                " The repaired scene must explicitly include these student-requested visual details "
                f"in step titles, teaching notes, item labels, or secondary labels: {', '.join(anchors)}. "
                "Use separate visible diagram elements or steps for boundary updates and prefix sum restoration. "
                "Every explicit boundary update such as diff[i]+=... or diff[i]-=... must also have its own "
                "transition action targeting that visible element."
            )
            boundary_anchors = [
                anchor
                for anchor in anchors
                if re.match(r"^(?:diff|d)\[\d+\](?:\+=|-=|=)$", anchor)
            ]
            if boundary_anchors:
                instruction += (
                    " Required granular scene shape: create a separate step or separate visible item for each "
                    f"boundary update ({', '.join(boundary_anchors)}) plus a separate step for prefix sum restoration; "
                    "each boundary update must use a distinct item_id or node_id, and each must have exactly one "
                    "transition action targeting that same id. Do not put two boundary updates in one label, "
                    "one item_id, one node_id, or one transition target."
                )
        if isinstance(rejection_details, dict):
            reason = rejection_details.get("reason")
            if isinstance(reason, str) and reason:
                instruction += f" Previous validation diagnostic: {reason}."
            missing_anchors = rejection_details.get("missing_anchors")
            if isinstance(missing_anchors, list) and missing_anchors:
                instruction += f" Missing explicit visual anchors: {', '.join(str(anchor) for anchor in missing_anchors)}."
            combined_targets = rejection_details.get("combined_targets")
            if isinstance(combined_targets, list) and combined_targets:
                instruction += (
                    " Do not combine multiple explicit boundary updates into one target; split each listed "
                    "boundary update into its own item/node/edge and its own transition action."
                )
            missing_transition_anchors = rejection_details.get("missing_transition_anchors")
            if isinstance(missing_transition_anchors, list) and missing_transition_anchors:
                instruction += (
                    " Add transition actions for these explicit boundary anchors: "
                    f"{', '.join(str(anchor) for anchor in missing_transition_anchors)}."
                )
        return instruction

    def _build_strict_scene_contract(self, request_context: dict[str, Any] | None) -> dict[str, Any]:
        anchors = self._explicit_visual_detail_anchors(request_context)
        if not anchors:
            return {}
        boundary_anchors = [
            anchor
            for anchor in anchors
            if re.match(r"^(?:diff|d)\[\d+\](?:\+=|-=|=)$", anchor)
        ]
        contract: dict[str, Any] = {
            "required_explicit_visual_anchors": anchors,
            "granularity_rule": "separate visible target and separate transition action per boundary update",
        }
        if boundary_anchors:
            contract["required_boundary_updates"] = [
                {
                    "anchor": anchor,
                    "visible_target_rule": "one item/node/edge label or secondary_label contains this exact anchor",
                    "transition_rule": "one transition action targets that same item's/node's/edge's id",
                }
                for anchor in boundary_anchors
            ]
            contract["scene_shape_rule"] = (
                "Create distinct item_id/node_id/edge_id values for each boundary update; "
                "transition.actions[].target_id must equal those ids, not a combined boundary id."
            )
        blueprint = self._copyable_scene_blueprint(anchors)
        if blueprint:
            contract["copyable_scene_blueprint"] = blueprint
        return contract

    def _provider_visible_scene_instruction(self, request_context: dict[str, Any] | None) -> dict[str, Any]:
        anchors = self._explicit_visual_detail_anchors(request_context)
        return {
            "authoring_rule": (
                "The provider prompt compacts system_prompt away, so copy these strings directly into scene "
                "step titles, teaching notes, item labels, or secondary labels."
            ),
            "copy_required_strings_exactly": self._copy_required_strings(anchors),
            "must_use_copyable_scene_blueprint": bool(anchors),
        }

    def _copy_required_strings(self, anchors: list[str]) -> list[str]:
        required: list[str] = []
        for anchor in anchors:
            label = self._anchor_display_label(anchor)
            if label and label not in required:
                required.append(label)
        return required

    def _anchor_display_label(self, anchor: str) -> str:
        boundary_match = re.match(r"^(?:diff|d)\[(\d+)\](\+=|-=|=)$", anchor)
        if boundary_match:
            index, operator = boundary_match.groups()
            if operator == "+=":
                return f"diff[{index}]+=1"
            if operator == "-=":
                return f"diff[{index}]-=1"
            return f"diff[{index}]="
        if anchor == "prefix sum":
            return "前缀和"
        return anchor

    def _copyable_scene_blueprint(self, anchors: list[str]) -> list[dict[str, Any]]:
        blueprint: list[dict[str, Any]] = []
        for anchor in anchors:
            boundary_match = re.match(r"^(?:diff|d)\[(\d+)\](\+=|-=|=)$", anchor)
            if not boundary_match:
                continue
            index, operator = boundary_match.groups()
            direction = "plus" if operator == "+=" else "minus" if operator == "-=" else "assign"
            item_id = f"boundary_diff_{index}_{direction}"
            label = self._anchor_display_label(anchor)
            blueprint.append(
                {
                    "step_id": item_id,
                    "title": label,
                    "teaching_note": label,
                    "item": {"item_id": item_id, "label": label},
                    "transition_action": {"kind": "highlight", "target": "item", "target_id": item_id},
                }
            )
        if "prefix sum" in anchors or "恢复" in anchors:
            restored_array = next(
                (anchor for anchor in anchors if re.match(r"^\[-?\d+(?:,-?\d+)+\]$", anchor)),
                None,
            )
            label = f"前缀和恢复 {restored_array}" if restored_array else "前缀和恢复"
            blueprint.append(
                {
                    "step_id": "prefix_restore",
                    "title": "前缀和恢复",
                    "teaching_note": label,
                    "item": {"item_id": "prefix_restore_result", "label": label},
                    "transition_action": {
                        "kind": "highlight",
                        "target": "item",
                        "target_id": "prefix_restore_result",
                    },
                }
            )
        return blueprint

    def _is_valid_agent_scene(
        self,
        scene: dict[str, Any],
        *,
        request_context: dict[str, Any] | None = None,
    ) -> bool:
        return self._agent_scene_rejection_details(scene, request_context=request_context) is None

    def _agent_scene_rejection_details(
        self,
        scene: dict[str, Any],
        *,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            validated = DiagramSceneModel.model_validate(scene)
        except Exception as exc:
            return {"reason": "schema_validation_failed", "error_type": type(exc).__name__}
        explicit_details = self._explicit_visual_rejection_details(scene, request_context)
        if explicit_details is not None:
            return explicit_details
        default_step_id = validated.default_step_id or (validated.steps[0].step_id if validated.steps else None)
        if default_step_id is None:
            return {"reason": "missing_default_step_id"}
        bundle = DiagramTeachingBundleModel(
            preface=validated.teaching_goal or validated.title,
            inline_payload=DiagramInlinePayloadModel(
                scene_id=validated.scene_id,
                default_step_id=default_step_id,
                default_step_title=validated.steps[0].title if validated.steps else None,
                title=validated.title,
                diagram_kind=validated.diagram_kind,
                teaching_goal=validated.teaching_goal,
                step_count=len(validated.steps),
                inline_caption=validated.steps[0].title if validated.steps else None,
            ),
            followup="",
            scene=validated,
        )
        verification = verify_diagram_teaching_bundle(
            bundle=bundle.model_dump(by_alias=True),
            session_id=validated.session_id or "validation-session",
        )
        if verification.get("ok") is True:
            return None
        reason = verification.get("reason")
        return {
            "reason": "diagram_verification_failed",
            "verification_reason": reason if isinstance(reason, str) else "unknown",
        }

    def _covers_explicit_visual_details(
        self,
        scene: dict[str, Any],
        request_context: dict[str, Any] | None,
    ) -> bool:
        return self._explicit_visual_rejection_details(scene, request_context) is None

    def _explicit_visual_rejection_details(
        self,
        scene: dict[str, Any],
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        anchors = self._explicit_visual_detail_anchors(request_context)
        if not anchors:
            return None
        scene_text = self._diagram_scene_search_text(scene)
        missing_anchors = [anchor for anchor in anchors if not self._anchor_covered(scene_text, anchor)]
        if missing_anchors:
            return {
                "reason": "missing_explicit_visual_details",
                "anchors": anchors,
                "missing_anchors": missing_anchors,
            }
        restored_array_details = self._unexpected_restored_array_rejection_details(scene, anchors)
        if restored_array_details is not None:
            return restored_array_details
        return self._explicit_transition_rejection_details(scene, anchors)

    def _covers_explicit_transition_actions(self, scene: dict[str, Any], anchors: list[str]) -> bool:
        return self._explicit_transition_rejection_details(scene, anchors) is None

    def _explicit_transition_rejection_details(
        self,
        scene: dict[str, Any],
        anchors: list[str],
    ) -> dict[str, Any] | None:
        boundary_anchors = [
            anchor
            for anchor in anchors
            if re.match(r"^(?:diff|d)\[\d+\](?:\+=|-=|=)$", anchor)
        ]
        if not boundary_anchors:
            return None

        anchor_targets: dict[str, set[tuple[str, str]]] = {anchor: set() for anchor in boundary_anchors}
        target_anchors: dict[tuple[str, str], set[str]] = {}
        targeted: set[tuple[str, str]] = set()
        steps = scene.get("steps") if isinstance(scene.get("steps"), list) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for state_key in ("entry_state", "exit_state"):
                state = step.get(state_key)
                if not isinstance(state, dict):
                    continue
                self._collect_anchor_targets_from_state(state, anchor_targets, target_anchors)
            transition = step.get("transition")
            actions = transition.get("actions") if isinstance(transition, dict) else None
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, dict):
                    continue
                target = action.get("target")
                target_id = action.get("target_id")
                if isinstance(target, str) and isinstance(target_id, str):
                    targeted.add((target, target_id))

        combined_targets = [
            {
                "target": target[0],
                "target_id": target[1],
                "anchors": sorted(target_anchor_values),
            }
            for target, target_anchor_values in sorted(target_anchors.items())
            if target in targeted and len(target_anchor_values) > 1
        ]
        if combined_targets:
            return {
                "reason": "combined_explicit_boundary_targets",
                "anchors": boundary_anchors,
                "combined_targets": combined_targets,
            }
        missing_transition_anchors = [
            anchor
            for anchor, targets in anchor_targets.items()
            if not targets or targets.isdisjoint(targeted)
        ]
        if missing_transition_anchors:
            return {
                "reason": "missing_transition_for_explicit_boundary",
                "anchors": boundary_anchors,
                "missing_transition_anchors": missing_transition_anchors,
            }
        selected_targets: set[tuple[str, str]] = set()
        for anchor in boundary_anchors:
            anchor_target = next(
                (
                    target
                    for target in sorted(anchor_targets[anchor])
                    if target in targeted and target_anchors.get(target) == {anchor}
                ),
                None,
            )
            if anchor_target is None or anchor_target in selected_targets:
                return {
                    "reason": "missing_distinct_transition_target_for_explicit_boundary",
                    "anchors": boundary_anchors,
                }
            selected_targets.add(anchor_target)
        return None

    def _collect_anchor_targets_from_state(
        self,
        state: dict[str, Any],
        anchor_targets: dict[str, set[tuple[str, str]]],
        target_anchors: dict[tuple[str, str], set[str]],
    ) -> None:
        target_specs = (
            ("items", "item", "item_id"),
            ("nodes", "node", "node_id"),
            ("edges", "edge", "edge_id"),
        )
        for collection_key, target_kind, id_key in target_specs:
            values = state.get(collection_key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                target_id = value.get(id_key)
                if not isinstance(target_id, str):
                    continue
                target_text = self._diagram_scene_search_text(value)
                for anchor, targets in anchor_targets.items():
                    if self._anchor_covered(target_text, anchor):
                        target = (target_kind, target_id)
                        targets.add(target)
                        target_anchors.setdefault(target, set()).add(anchor)

    def _explicit_visual_detail_anchors(self, request_context: dict[str, Any] | None) -> list[str]:
        if not isinstance(request_context, dict):
            return []
        latest_user_message = request_context.get("latest_user_message")
        if not isinstance(latest_user_message, str) or not latest_user_message.strip():
            return []
        normalized_message = latest_user_message.lower()
        anchors: list[str] = []
        for match in re.finditer(r"\b(?:diff|d)\s*\[\s*\d+\s*\]\s*(?:\+=|-=|=)", normalized_message):
            anchors.append(self._normalize_anchor_text(match.group(0)))
        if "前缀和" in normalized_message or "prefix" in normalized_message:
            anchors.append("prefix sum")
        if "恢复" in normalized_message or "还原" in normalized_message:
            anchors.append("恢复")
            anchors.extend(self._restored_array_anchors(request_context))
        deduped: list[str] = []
        for anchor in anchors:
            if anchor and anchor not in deduped:
                deduped.append(anchor)
        return deduped

    def _restored_array_anchors(self, request_context: dict[str, Any]) -> list[str]:
        search_texts: list[str] = []
        for key in ("tutor_visible_text", "latest_user_message"):
            value = request_context.get(key)
            if isinstance(value, str) and value.strip():
                search_texts.append(value)
        recent_visible_messages = request_context.get("recent_visible_messages")
        if isinstance(recent_visible_messages, list):
            for raw_message in recent_visible_messages[-6:]:
                if not isinstance(raw_message, dict):
                    continue
                content = raw_message.get("content")
                if isinstance(content, str) and content.strip():
                    search_texts.append(content)

        anchors: list[str] = []
        array_pattern = re.compile(r"[\[【]\s*-?\d+(?:\s*[,，、]\s*-?\d+){1,}\s*[\]】]")
        result_context_pattern = re.compile(
            r"(?:最后|最终|通过前缀和|前缀和恢复|恢复|还原|得到|原数组)[^。！？.!?]{0,80}"
            r"([\[【]\s*-?\d+(?:\s*[,，、]\s*-?\d+){1,}\s*[\]】])"
        )
        for text in search_texts:
            if not any(marker in text for marker in ("前缀和", "恢复", "还原", "原数组", "最终")):
                continue
            matches = list(result_context_pattern.finditer(text))
            if not matches:
                continue
            raw_array = matches[-1].group(1)
            if not array_pattern.fullmatch(raw_array):
                continue
            anchor = self._normalize_anchor_text(raw_array.replace("【", "[").replace("】", "]"))
            if anchor not in anchors:
                anchors.append(anchor)
        return anchors

    def _unexpected_restored_array_rejection_details(
        self,
        scene: dict[str, Any],
        anchors: list[str],
    ) -> dict[str, Any] | None:
        expected_arrays = [
            anchor
            for anchor in anchors
            if re.match(r"^\[-?\d+(?:,-?\d+)+\]$", anchor)
        ]
        if not expected_arrays:
            return None
        expected_values = {array_text: self._parse_anchor_array_values(array_text) for array_text in expected_arrays}
        expected_lengths = {len(values) for values in expected_values.values() if values is not None}
        if not expected_lengths:
            return None

        unexpected: list[str] = []
        for text in self._restored_array_context_texts(scene):
            for raw_array in re.findall(r"[\[【]\s*-?\d+(?:\s*[,，、]\s*-?\d+){1,}\s*[\]】]", text):
                normalized = self._normalize_anchor_text(raw_array.replace("【", "[").replace("】", "]"))
                if normalized in expected_values:
                    continue
                values = self._parse_anchor_array_values(normalized)
                if values is None or len(values) not in expected_lengths:
                    continue
                if normalized not in unexpected:
                    unexpected.append(normalized)
        if not unexpected:
            return None
        return {
            "reason": "unexpected_restored_array_visual_details",
            "expected_arrays": expected_arrays,
            "unexpected_arrays": unexpected,
        }

    def _restored_array_context_texts(self, value: Any) -> list[str]:
        texts: list[str] = []
        markers = ("前缀和", "恢复", "还原", "原数组", "最终", "得到")

        def collect(raw: Any) -> None:
            if isinstance(raw, dict):
                for key, nested in raw.items():
                    if isinstance(nested, str) and key in {"title", "teaching_note", "label", "secondary_label", "caption"}:
                        if any(marker in nested for marker in markers):
                            texts.append(nested)
                    else:
                        collect(nested)
            elif isinstance(raw, list):
                for nested in raw:
                    collect(nested)

        collect(value)
        return texts

    def _parse_anchor_array_values(self, array_text: str) -> list[int] | None:
        normalized = array_text.strip().replace("【", "[").replace("】", "]")
        if not re.fullmatch(r"\[-?\d+(?:,-?\d+)+\]", self._normalize_anchor_text(normalized)):
            return None
        try:
            return [int(part) for part in self._normalize_anchor_text(normalized).strip("[]").split(",")]
        except ValueError:
            return None

    def _diagram_scene_search_text(self, value: Any) -> str:
        parts: list[str] = []

        def collect(raw_value: Any) -> None:
            if isinstance(raw_value, str):
                parts.append(raw_value)
            elif isinstance(raw_value, dict):
                for child_value in raw_value.values():
                    collect(child_value)
            elif isinstance(raw_value, list):
                for child_value in raw_value:
                    collect(child_value)

        collect(value)
        return self._normalize_anchor_text(" ".join(parts))

    def _anchor_covered(self, scene_text: str, anchor: str) -> bool:
        if anchor == "prefix sum":
            return "前缀和" in scene_text or "prefix" in scene_text
        boundary_match = re.match(r"^(?:diff|d)\[(\d+)\](\+=|-=|=)$", anchor)
        if boundary_match:
            index, operator = boundary_match.groups()
            if operator == "+=":
                candidates = (
                    f"diff[{index}]+=",
                    f"d[{index}]+=",
                    f"diff[{index}]+1",
                    f"d[{index}]+1",
                    f"diff[{index}]加1",
                    f"d[{index}]加1",
                    f"diff[{index}]加一",
                    f"d[{index}]加一",
                )
            elif operator == "-=":
                candidates = (
                    f"diff[{index}]-=",
                    f"d[{index}]-=",
                    f"diff[{index}]-1",
                    f"d[{index}]-1",
                    f"diff[{index}]减1",
                    f"d[{index}]减1",
                    f"diff[{index}]减一",
                    f"d[{index}]减一",
                )
            else:
                candidates = (f"diff[{index}]=", f"d[{index}]=", f"diff[{index}]", f"d[{index}]")
            return any(candidate in scene_text for candidate in candidates)
        return anchor in scene_text

    def _normalize_anchor_text(self, value: str) -> str:
        normalized = value.lower()
        normalized = normalized.replace("＋", "+").replace("－", "-").replace("＝", "=")
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _strict_scene_required(self, request_context: dict[str, Any] | None) -> bool:
        if not isinstance(request_context, dict):
            return False
        if request_context.get("tutor_needs_diagram") is True:
            return True
        latest_user_message = request_context.get("latest_user_message")
        if not isinstance(latest_user_message, str):
            return False
        normalized = latest_user_message.strip().lower()
        return any(marker in normalized for marker in ("画", "图", "diagram", "visualize"))

    def _contains_text_diagram_artifact(self, text: str) -> bool:
        normalized = text.lower()
        return any(
            marker in normalized
            for marker in (
                "```",
                "mermaid",
                "graphviz",
                "dot -t",
                "python draw",
                "python3 draw",
                "命令行画",
                "用命令行",
                "-->",
                "=>",
            )
        )
