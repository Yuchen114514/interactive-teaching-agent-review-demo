from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


FORBIDDEN_REVISION_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
DEFAULT_PLAN_REVISION_POLICY_PATH = Path(__file__).with_name("plan_revision_policy.json")


@dataclass(frozen=True)
class PlanRevisionDecision:
    intent: str
    confidence: float
    reason: str
    decision_source: str = "policy"


@dataclass(frozen=True)
class PlanRevisionPolicy:
    branch_markers: tuple[str, ...]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "PlanRevisionPolicy":
        forbidden = FORBIDDEN_REVISION_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"plan revision policy contains problem-specific runtime rules: {sorted(forbidden)}")
        return cls(
            branch_markers=tuple(str(marker) for marker in payload.get("branch_markers", [])),
            source=source,
            source_detail=source_detail,
        )

    def decide(self, message: str) -> PlanRevisionDecision:
        normalized = message.strip().lower()
        if any(marker.lower() in normalized for marker in self.branch_markers):
            return PlanRevisionDecision(
                intent="branch_plan",
                confidence=0.8,
                reason="generic_branch_marker",
            )
        return PlanRevisionDecision(
            intent="continue_plan",
            confidence=0.4,
            reason="default_continue",
        )


def load_default_plan_revision_policy() -> PlanRevisionPolicy:
    return load_plan_revision_policy("")


def load_plan_revision_policy(path: str | None = None) -> PlanRevisionPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_PLAN_REVISION_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return PlanRevisionPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
