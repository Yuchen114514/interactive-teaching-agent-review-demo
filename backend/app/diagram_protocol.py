from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DiagramElementState = Literal["default", "highlighted", "muted"]
DiagramKind = Literal[
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
]
DiagramLayoutMode = Literal[
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
]


class DiagramPlanIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str
    assistant_message: str
    session_id: str


class DiagramInlinePayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    default_step_id: str | None = None
    default_step_title: str | None = None
    title: str
    diagram_kind: str
    teaching_goal: str
    step_count: int
    inline_caption: str | None = None


class DiagramNodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    label: str
    x: float
    y: float
    state: DiagramElementState | None = None
    shape: Literal["circle", "rounded_rect", "pill"] | None = None
    width: float | None = None
    height: float | None = None


class DiagramEdgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    edge_id: str
    from_: str = Field(alias="from")
    to: str
    label: str | None = None
    state: DiagramElementState | None = None


class DiagramItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    label: str
    x: float
    y: float
    width: float = 48
    height: float = 34
    kind: Literal["cell", "block", "frame", "pointer", "text"] = "cell"
    state: DiagramElementState | None = None
    secondary_label: str | None = None


class DiagramStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[DiagramNodeModel]
    edges: list[DiagramEdgeModel]
    items: list[DiagramItemModel] = Field(default_factory=list)


class DiagramTransitionActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["highlight", "focus", "move", "remove", "add"]
    target: Literal["node", "edge", "item"] | None = None
    target_id: str
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


class DiagramTransitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[DiagramTransitionActionModel]


class DiagramStepModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    order_index: int
    title: str
    teaching_note: str
    entry_state: DiagramStateModel
    exit_state: DiagramStateModel
    transition: DiagramTransitionModel


class DiagramSceneModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    session_id: str | None = None
    parent_scene_id: str | None = None
    base_step_id: str | None = None
    inherit_mode: Literal["new", "extend", "revise", "branch"] = "new"
    title: str
    diagram_kind: DiagramKind
    layout: DiagramLayoutMode = "freeform"
    teaching_goal: str
    default_step_id: str | None = None
    status: Literal["draft", "verified", "archived"] = "draft"
    source_turn_index: int = 0
    steps: list[DiagramStepModel]


class DiagramTeachingBundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preface: str
    inline_payload: DiagramInlinePayloadModel
    followup: str
    scene: DiagramSceneModel


class DiagramVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    reason: str
    scene: dict[str, Any] | None = None


class DiagramPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle: DiagramTeachingBundleModel | None
    verification: DiagramVerificationResult
