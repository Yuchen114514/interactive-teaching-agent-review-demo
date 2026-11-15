"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict

from app.models import AgentBoardNodeStatus, AgentBoardworkStatus


class GuidanceDepth(str, Enum):
    DIRECT = "direct"
    COACHED = "coached"
    SOCRATIC = "socratic"
    MINIMAL = "minimal"


class TeachingTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    client_request_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    session_id: Optional[str] = None
    mode: Optional[str] = None
    guidance_depth: Optional[GuidanceDepth] = None
    depth_override: bool = False
    workspace_id: Optional[int] = None
    source: Optional[str] = None
    problem_id: Optional[int] = None
    submission_id: Optional[int] = None
    course_id: Optional[int] = None
    class_id: Optional[int] = None
    images: Optional[List[Annotated[str, Field(max_length=2048)]]] = Field(default=None, max_length=8)
    audio: Optional[List[str]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.guidance_depth is None:
            self.guidance_depth = GuidanceDepth.DIRECT if self.mode == "normal" else GuidanceDepth.SOCRATIC


class GuidanceDepth(str, Enum):
    DIRECT = "direct"
    COACHED = "coached"
    SOCRATIC = "socratic"
    MINIMAL = "minimal"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    stream: bool = Field(default=True)
    socratic_mode: bool = False
    session_id: Optional[str] = None  # Added support for specific session
    source: Optional[str] = None
    problem_id: Optional[int] = None
    submission_id: Optional[int] = None
    course_id: Optional[int] = None
    class_id: Optional[int] = None
    exam_id: Optional[int] = None
    images: Optional[List[str]] = None
    audio: Optional[List[str]] = None


class TeachingTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    client_request_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    session_id: Optional[str] = None
    mode: Optional[str] = None
    guidance_depth: Optional[GuidanceDepth] = None
    depth_override: bool = False
    workspace_id: Optional[int] = None
    source: Optional[str] = None
    problem_id: Optional[int] = None
    submission_id: Optional[int] = None
    course_id: Optional[int] = None
    class_id: Optional[int] = None
    images: Optional[List[Annotated[str, Field(max_length=2048)]]] = Field(default=None, max_length=8)
    audio: Optional[List[str]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.guidance_depth is None:
            self.guidance_depth = GuidanceDepth.DIRECT if self.mode == "normal" else GuidanceDepth.SOCRATIC


class TeachingStateResponse(BaseModel):
    problem_title: Optional[str] = None
    workspace_status: str
    current_step_index: int
    total_reached_steps: int
    total_plan_steps: int = 0
    current_step_title: str
    current_step_status: str = "in_progress"
    current_step_mastery_state: Optional[str] = None
    guidance_depth: GuidanceDepth
    can_advance: bool = False


class AgentTraceResponse(BaseModel):
    agent: str
    status: str
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: int = 0
    blackboard_writes: int = 0
    memory_writes: int = 0
    error_message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class TeachingTurnResponse(BaseModel):
    session_id: str
    workspace_id: Optional[int] = None
    response: str
    teaching_state: TeachingStateResponse
    events: List[Dict[str, Any]] = Field(default_factory=list)
    agent_trace: List[AgentTraceResponse] = Field(default_factory=list)
    boardwork_refresh: bool = False
    diagram_scene_id: Optional[str] = None


class MemorySettingsRequest(BaseModel):
    long_term_memory_enabled: bool
    personalization_enabled: Optional[bool] = None


class MemorySettingsResponse(BaseModel):
    user_id: int
    long_term_memory_enabled: bool
    personalization_enabled: bool
    updated_at: Optional[datetime] = None


MascotSkin = Literal["base", "terminal", "keycap", "server"]
AgentFontSize = Literal["small", "medium", "large"]


class AgentUserPreferencesRequest(BaseModel):
    mascot_skin: Optional[MascotSkin] = None
    font_size: Optional[AgentFontSize] = None


class AgentUserPreferencesResponse(BaseModel):
    mascot_skin: MascotSkin
    font_size: AgentFontSize = "small"


class MemoryItemResponse(BaseModel):
    memory_id: int
    scope: str
    memory_type: str
    content: str
    confidence: Optional[float] = None
    importance: Optional[float] = None
    updated_at: datetime


class ChatResponse(BaseModel):
    user_id: int
    session_id: str
    answer: str
    latency_ms: int
    context_messages: List[ChatMessage]


class ExamProblemTimingStartRequest(BaseModel):
    exam_id: int = Field(gt=0)
    problem_id: int = Field(gt=0)
    session_id: Optional[str] = None


class ExamProblemTimingResponse(BaseModel):
    id: Optional[int] = None
    user_id: int
    exam_id: int
    problem_id: int
    session_id: Optional[int] = None
    started_at: datetime
    first_submission_at: Optional[datetime] = None
    last_submission_at: Optional[datetime] = None
    first_submission_id: Optional[int] = None
    last_submission_id: Optional[int] = None
    submission_count: int = 0
    first_submission_elapsed_seconds: Optional[int] = None
    last_submission_elapsed_seconds: Optional[int] = None
    total_answer_seconds: Optional[int] = None
    solved_at: Optional[datetime] = None
    is_solved: bool = False
    create_time: datetime
    update_time: datetime

    model_config = ConfigDict(from_attributes=True)


class HistoryItem(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class HistoryResponse(BaseModel):
    user_id: int
    session_id: str
    items: List[HistoryItem]


class StudentSummary(BaseModel):
    id: UUID
    display_name: str
    system_prompt: str | None = None
    tool_flags: dict | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# New Session Schemas
class SessionSchema(BaseModel):
    session_id: str
    user_id: str
    is_shared_demo: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_message_at: datetime | None = None
    preview: Optional[str] = None # Last message or summary
    title: Optional[str] = None # Auto-generated title
    source: Optional[str] = None
    problem_id: Optional[int] = None
    submission_id: Optional[int] = None
    course_id: Optional[int] = None
    class_id: Optional[int] = None
    exam_id: Optional[int] = None

class CreateSessionResponse(BaseModel):
    session_id: str


class CreateAgentSessionRequest(BaseModel):
    problem_id: Optional[int] = Field(default=None, gt=0)
    
class DeleteSessionResponse(BaseModel):
    success: bool
    message: str

class HistoryRequest(BaseModel):
    session_id: Optional[str] = None
    limit: int = 10
    offset: int = 0

class DeleteSessionRequest(BaseModel):
    session_id: str

class UpdateSessionRequest(BaseModel):
    title: str = Field(min_length=1)


class DeleteTurnsFromMessageResponse(BaseModel):
    success: bool
    message: str
    session_id: Optional[str] = None
    start_turn_index: Optional[int] = None


class ImageUploadResponse(BaseModel):
    image_id: str
    url: str
    thumbnail_url: str
    filename: str
    mime_type: str
    size: int
    created_at: datetime


class BoardNodeCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    summary: str = Field(default="", min_length=1)
    source_turn_index: int = 0


class BoardCloseRequest(BaseModel):
    outcome: AgentBoardworkStatus


class BoardStartRequest(BaseModel):
    title: str = Field(default="当前题目白板", min_length=1)
    source_turn_index: int = 0


class BoardNodeResponse(BaseModel):
    node_id: int
    title: str
    summary: str
    status: AgentBoardNodeStatus
    trace_payload: Optional[Dict[str, Any]] = None
    coach_note: Optional[str] = None
    sort_order: int
    is_active: bool
    source_turn_index: int
    created_at: datetime
    updated_at: datetime


class BoardStateResponse(BaseModel):
    session_id: str
    session_summary: Optional[str] = None
    learning_state: Optional[str] = None
    boardwork: Optional["BoardworkResponse"] = None
    nodes: List[BoardNodeResponse]


class BoardworkResponse(BaseModel):
    boardwork_id: int
    title: str
    status: AgentBoardworkStatus
    source_turn_index: int
    closed_turn_index: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class BoardCloseResponse(BaseModel):
    session_id: str
    boardwork_id: int
    status: AgentBoardworkStatus


class DiagramNodeResponse(BaseModel):
    node_id: str
    label: str
    x: float
    y: float
    state: Optional[str] = None
    shape: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None


class DiagramEdgeResponse(BaseModel):
    edge_id: str
    from_: str = Field(alias="from")
    to: str
    label: Optional[str] = None
    state: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class DiagramItemResponse(BaseModel):
    item_id: str
    label: str
    x: float
    y: float
    width: float
    height: float
    kind: str
    state: Optional[str] = None
    secondary_label: Optional[str] = None


class DiagramStateResponse(BaseModel):
    nodes: List[DiagramNodeResponse]
    edges: List[DiagramEdgeResponse]
    items: List[DiagramItemResponse] = Field(default_factory=list)


class DiagramTransitionActionResponse(BaseModel):
    kind: str
    target: Optional[str] = None
    target_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


class DiagramTransitionResponse(BaseModel):
    actions: List[DiagramTransitionActionResponse]


class DiagramStepResponse(BaseModel):
    step_id: str
    order_index: int
    title: str
    teaching_note: str
    entry_state: DiagramStateResponse
    exit_state: DiagramStateResponse
    transition: DiagramTransitionResponse


class DiagramSceneResponse(BaseModel):
    scene_id: str
    session_id: str
    parent_scene_id: Optional[str] = None
    base_step_id: Optional[str] = None
    inherit_mode: Optional[str] = None
    title: str
    diagram_kind: str
    layout: Optional[str] = None
    teaching_goal: str
    default_step_id: Optional[str] = None
    status: str
    source_turn_index: int
    steps: List[DiagramStepResponse]
