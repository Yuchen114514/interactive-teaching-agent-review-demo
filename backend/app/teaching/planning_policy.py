from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


FORBIDDEN_PLANNING_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
DEFAULT_PLANNING_POLICY_PATH = Path(__file__).with_name("teaching_plan_policy.json")


@dataclass(frozen=True)
class IntroStepTemplate:
    step_key: str
    title_template: str
    teaching_goal: str
    expected_student_action: str
    mastery_criteria: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IntroStepTemplate":
        return cls(
            step_key=str(payload["step_key"]),
            title_template=str(payload["title_template"]),
            teaching_goal=str(payload["teaching_goal"]),
            expected_student_action=str(payload["expected_student_action"]),
            mastery_criteria=str(payload["mastery_criteria"]),
        )

    def render(self, *, problem_anchor: str) -> dict[str, str]:
        return {
            "step_key": self.step_key,
            "title": self.title_template.format(problem_anchor=problem_anchor),
            "teaching_goal": self.teaching_goal,
            "expected_student_action": self.expected_student_action,
            "mastery_criteria": self.mastery_criteria,
        }


@dataclass(frozen=True)
class DimensionStepTemplate:
    step_key_template: str
    title_template: str
    teaching_goal_template: str
    expected_student_action: str
    mastery_criteria: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DimensionStepTemplate":
        return cls(
            step_key_template=str(payload["step_key_template"]),
            title_template=str(payload["title_template"]),
            teaching_goal_template=str(payload["teaching_goal_template"]),
            expected_student_action=str(payload["expected_student_action"]),
            mastery_criteria=str(payload["mastery_criteria"]),
        )

    def render(self, *, family_display_name: str, dimension: str) -> dict[str, str]:
        context = {
            "family_display_name": family_display_name,
            "dimension": dimension,
        }
        return {
            "step_key": self.step_key_template.format(**context),
            "title": self.title_template.format(**context),
            "teaching_goal": self.teaching_goal_template.format(**context),
            "expected_student_action": self.expected_student_action,
            "mastery_criteria": self.mastery_criteria,
        }


@dataclass(frozen=True)
class ClosingStepTemplate:
    step_key: str
    title_template: str
    teaching_goal: str
    expected_student_action: str
    mastery_criteria: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClosingStepTemplate":
        return cls(
            step_key=str(payload["step_key"]),
            title_template=str(payload["title_template"]),
            teaching_goal=str(payload["teaching_goal"]),
            expected_student_action=str(payload["expected_student_action"]),
            mastery_criteria=str(payload["mastery_criteria"]),
        )

    def render(self) -> dict[str, str]:
        return {
            "step_key": self.step_key,
            "title": self.title_template,
            "teaching_goal": self.teaching_goal,
            "expected_student_action": self.expected_student_action,
            "mastery_criteria": self.mastery_criteria,
        }


@dataclass(frozen=True)
class TeachingPlanPolicy:
    intro_step: IntroStepTemplate
    dimension_step: DimensionStepTemplate
    closing_steps: tuple[ClosingStepTemplate, ...]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "TeachingPlanPolicy":
        forbidden = FORBIDDEN_PLANNING_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"planning policy contains problem-specific runtime rules: {sorted(forbidden)}")
        return cls(
            intro_step=IntroStepTemplate.from_dict(payload["intro_step"]),
            dimension_step=DimensionStepTemplate.from_dict(payload["dimension_step"]),
            closing_steps=tuple(
                ClosingStepTemplate.from_dict(step)
                for step in payload.get("closing_steps", [])
            ),
            source=source,
            source_detail=source_detail,
        )


def load_default_planning_policy() -> TeachingPlanPolicy:
    return load_planning_policy("")


def load_planning_policy(path: str | None = None) -> TeachingPlanPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_PLANNING_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return TeachingPlanPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
