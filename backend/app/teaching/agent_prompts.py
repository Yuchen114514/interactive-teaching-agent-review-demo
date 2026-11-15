from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_AGENT_PROMPT_SPECS_PATH = Path(__file__).with_name("agent_prompt_specs.json")
STANDARD_MATH_MARKDOWN_POLICY = (
    "Math format policy: for any student-visible text, boardwork text, whiteboard summary, diagram title, "
    "diagram teaching_note, diagram label, or secondary_label, write mathematical expressions with standard "
    "Markdown math: inline formulas as $...$ and display formulas as $$...$$. Do not use \\(...\\), \\[...\\], "
    "bare multi-line formulas, or ad-hoc line-broken variables. Keep prose outside math delimiters."
)
MATH_VISIBLE_PROMPT_AGENTS = {
    "MainDialogAgent",
    "AlgorithmPrimerAgent",
    "TutorAgent",
    "MemoryManagerAgent",
    "BoardworkAgent",
    "DiagramAgent",
}

FORBIDDEN_PROMPT_SPEC_KEYS = {
    "problem_rules",
    "fixed_solution",
    "fixed_steps",
    "fixed_diagram",
    "answer_template",
    "solution_code",
    "hard_coded_solution",
}


@dataclass(frozen=True)
class AgentPromptSpec:
    agent_name: str
    purpose: str
    system_prompt: str
    required_schema: dict[str, Any]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        agent_name: str,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "AgentPromptSpec":
        forbidden = FORBIDDEN_PROMPT_SPEC_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"agent prompt spec contains problem-specific runtime rules: {sorted(forbidden)}")
        purpose = payload.get("purpose")
        system_prompt = payload.get("system_prompt")
        required_schema = payload.get("required_schema")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError(f"agent prompt spec requires non-empty purpose: {agent_name}")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError(f"agent prompt spec requires non-empty system prompt: {agent_name}")
        if not isinstance(required_schema, dict) or not required_schema:
            raise ValueError(f"agent prompt spec requires non-empty required schema: {agent_name}")
        if agent_name in MATH_VISIBLE_PROMPT_AGENTS and STANDARD_MATH_MARKDOWN_POLICY not in system_prompt:
            system_prompt = f"{system_prompt.strip()} {STANDARD_MATH_MARKDOWN_POLICY}"
        return cls(
            agent_name=agent_name,
            purpose=purpose.strip(),
            system_prompt=system_prompt.strip(),
            required_schema=required_schema,
            source=source,
            source_detail=source_detail,
        )


def load_agent_prompt_registry(path: str | None = None) -> dict[str, AgentPromptSpec]:
    payload, source, source_detail = _load_prompt_registry_payload(path)
    raw_agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else payload
    return {
        str(agent_name): AgentPromptSpec.from_dict(
            str(agent_name),
            spec_payload,
            source=source,
            source_detail=source_detail,
        )
        for agent_name, spec_payload in raw_agents.items()
        if isinstance(spec_payload, dict)
    }


def prompt_spec_for_agent(
    agent_name: str,
    *,
    registry: dict[str, AgentPromptSpec] | None = None,
) -> AgentPromptSpec:
    specs = registry or load_agent_prompt_registry()
    return specs[agent_name]


def teaching_supervisor_route_schema() -> dict[str, Any]:
    return _load_prompt_schemas()["teaching_supervisor_route"]


def teaching_supervisor_topology_schema() -> dict[str, Any]:
    return _load_prompt_schemas()["teaching_supervisor_topology"]


def teaching_supervisor_schedule_schema() -> dict[str, Any]:
    return _load_prompt_schemas()["teaching_supervisor_schedule"]


def problem_identifier_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("ProblemIdentifierAgent")


def algorithm_identifier_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("AlgorithmIdentifierAgent")


def algorithm_primer_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("AlgorithmPrimerAgent")


def plan_revision_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("PlanRevisionAgent")


def plan_decomposer_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("PlanDecomposerAgent")


def hint_policy_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("HintPolicyAgent")


def encouragement_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("EncouragementAgent")


def tutor_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("TutorAgent")


def memory_consolidator_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("MemoryConsolidatorAgent")


def memory_manager_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("MemoryManagerAgent")


def boardwork_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("BoardworkAgent")


def diagram_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("DiagramAgent")


def coding_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("CodingAgent")


def debug_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("DebugAgent")


def teaching_supervisor_prompt_spec() -> AgentPromptSpec:
    return prompt_spec_for_agent("TeachingSupervisor")


def _load_prompt_schemas() -> dict[str, Any]:
    payload, _, _ = _load_prompt_registry_payload("")
    schemas = payload.get("schemas")
    if not isinstance(schemas, dict):
        raise ValueError("agent prompt registry requires schemas")
    return schemas


def _load_prompt_registry_payload(path: str | None) -> tuple[dict[str, Any], str, str]:
    if path is not None and path.strip():
        registry_path = Path(path).expanduser()
        source = "custom_config"
    else:
        registry_path = DEFAULT_AGENT_PROMPT_SPECS_PATH
        source = "bundled_config"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("agent prompt registry must be a JSON object")
    return payload, source, registry_path.name
