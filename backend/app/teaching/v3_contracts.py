from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TeachingV3Intent = Literal["new_problem", "continue_problem", "non_problem", "state_change"]
TeachingV3StateAction = Literal[
    "none",
    "switch_workspace",
    "change_depth",
    "advance_step",
    "enter_code",
    "enter_debug",
]
TeachingV3PlanSignal = Literal[
    "none",
    "keep_current_step",
    "advance_step",
    "complete_solution",
    "branch",
    "repair_plan",
    "reset_plan",
]
TeachingV3VisualPriority = Literal["none", "low", "normal", "high"]
TeachingV3DepositType = Literal["concept", "mistake", "formula", "strategy", "example"]
TeachingV3PlanOperation = Literal["create", "update", "keep", "advance", "branch", "repair", "reset"]
TeachingV3StepStatus = Literal["active", "pending", "done"]
TeachingV3MCPResource = Literal[
    "none",
    "problem_detail",
    "problem_search",
    "problem_submissions",
    "submission_detail",
    "submit_code_feedback",
    "user_info",
    "user_stats",
    "user_weekly_submissions",
    "contest_list",
    "contest_info",
    "contest_problems_and_status",
    "contest_rank_list",
    "contest_submissions",
    "class_info",
    "class_list",
    "my_class_list",
    "class_contest_list",
    "class_problem_list",
    "platform_query",
]


class MainIntentStateBlock(BaseModel):
    block_type: Literal["intent_state"] = "intent_state"
    intent: TeachingV3Intent
    confidence: float = Field(ge=0.0, le=1.0)
    state_action: TeachingV3StateAction = "none"
    should_start_new_workspace: bool = False
    plan_signal: TeachingV3PlanSignal = "none"
    target_step_key: str | None = None
    problem_summary: str | None = None
    off_plan_reason: str | None = None
    reason: str = ""


class VisualNeedBlock(BaseModel):
    block_type: Literal["visual_need"] = "visual_need"
    need_diagram: bool = False
    diagram_goal: str | None = None
    visual_focus: str | None = None
    suggested_case: str | None = None
    priority: TeachingV3VisualPriority = "none"


class KnowledgeDepositHintBlock(BaseModel):
    block_type: Literal["knowledge_deposit_hint"] = "knowledge_deposit_hint"
    can_deposit: bool = False
    deposit_type: TeachingV3DepositType | None = None
    candidate_summary: str | None = None
    source_step_key: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MCPRequestBlock(BaseModel):
    block_type: Literal["mcp_request"] = "mcp_request"
    need_mcp: bool = False
    resource: TeachingV3MCPResource = "none"
    problem_id: int | None = None
    submission_id: int | None = None
    contest_id: int | None = None
    class_id: int | None = None
    title_search: str | None = None
    id_search: str | None = None
    author_search: str | None = None
    tag_names: list[str] = Field(default_factory=list)
    class_name: str | None = None
    course_name: str | None = None
    result: str | None = None
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    language_id: int | None = None
    language: str | None = None
    code: str | None = None
    reason: str = ""


class PlanDeltaBlock(BaseModel):
    block_type: Literal["plan_delta"] = "plan_delta"
    operation: TeachingV3PlanOperation
    plan_version: int = Field(ge=1)
    plan_summary: str
    current_step_key: str
    reason: str = ""


class ChainStep(BaseModel):
    step_key: str
    title: str
    status: TeachingV3StepStatus
    teaching_goal: str = ""


class ChainDeltaBlock(BaseModel):
    block_type: Literal["chain_delta"] = "chain_delta"
    steps: list[ChainStep] = Field(default_factory=list)


class TeachingV3TurnResponse(BaseModel):
    session_id: str
    workspace_id: int | None = None
    response: str
    intent_state: MainIntentStateBlock
    visual_need: VisualNeedBlock | None = None
    knowledge_deposit_hint: KnowledgeDepositHintBlock | None = None
    mcp_request: MCPRequestBlock | None = None
    diagram_scene_id: str | None = None
    metrics: dict[str, int | float] = Field(default_factory=dict)
    sidecar_statuses: dict[str, str] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
