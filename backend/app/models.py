"""SQLModel ORM tables."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Enum as SAEnum, Integer, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlmodel import Field, SQLModel


UINT_TYPE = mysql.INTEGER(unsigned=True).with_variant(Integer, "sqlite")
UBIGINT_TYPE = mysql.BIGINT(unsigned=True).with_variant(Integer, "sqlite")


def enum_type(enum_cls: type[Enum]) -> SAEnum:
    return SAEnum(
        enum_cls,
        values_callable=lambda enum_members: [member.value for member in enum_members],
        native_enum=True,
    )


class AgentMode(str, Enum):
    NORMAL = "normal"
    SOCRATIC = "socratic"


class AgentSessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AgentSessionSource(str, Enum):
    WEB = "web"
    PROBLEM_HELP = "problem_help"
    SUBMISSION_DEBUG = "submission_debug"
    COURSE_TUTOR = "course_tutor"
    EXAM_ASSISTANT = "exam_assistant"


class AgentMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUMMARY = "summary"
    DIAGRAM_CARD = "diagram_card"
    DIAGRAM_INLINE = "diagram_inline"


class AgentSessionFactType(str, Enum):
    SUMMARY = "summary"
    PROFILE = "profile"
    PREFERENCE = "preference"
    LEARNING_STATE = "learning_state"
    LONG_TERM_MEMORY = "long_term_memory"


class AgentBoardNodeStatus(str, Enum):
    EXPLORING = "exploring"
    FORMED = "formed"
    IMPLEMENTED = "implemented"
    BLOCKED = "blocked"


class AgentBoardworkStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class AgentBoardUpdateActionType(str, Enum):
    NOOP = "noop"
    ANNOTATE = "annotate"
    REFINE = "refine"
    ADVANCE = "advance"


class GuidanceDepthValue(str, Enum):
    DIRECT = "direct"
    COACHED = "coached"
    SOCRATIC = "socratic"
    MINIMAL = "minimal"


class ProblemWorkspaceStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class TeachingPlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class TeachingStepStatus(str, Enum):
    LOCKED = "locked"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"
    BRANCHED = "branched"
    NEEDS_REVISIT = "needs_revisit"


class MemoryScope(str, Enum):
    USER_PROFILE = "user_profile"
    SESSION = "session"
    WORKSPACE = "workspace"
    COURSE = "course"
    PROBLEM = "problem"


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: int = Field(primary_key=True, sa_type=UINT_TYPE)
    username: str | None = None
    nickname: str | None = None
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None


class AgentSession(SQLModel, table=True):
    __tablename__ = "agent_sessions"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_uuid: str = Field(index=True, unique=True, max_length=64)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, sa_type=UINT_TYPE)
    mode: AgentMode = Field(default=AgentMode.NORMAL, nullable=False, sa_type=enum_type(AgentMode))
    status: AgentSessionStatus = Field(
        default=AgentSessionStatus.ACTIVE,
        nullable=False,
        sa_type=enum_type(AgentSessionStatus),
    )
    source: AgentSessionSource = Field(
        default=AgentSessionSource.WEB,
        nullable=False,
        sa_type=enum_type(AgentSessionSource),
    )
    title: Optional[str] = Field(default=None, max_length=255)
    summary: Optional[str] = Field(default=None, sa_type=Text())
    problem_id: Optional[int] = Field(default=None, index=True, sa_type=UINT_TYPE)
    submission_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    course_id: Optional[int] = Field(default=None, index=True, sa_type=UINT_TYPE)
    class_id: Optional[int] = Field(default=None, index=True, sa_type=UINT_TYPE)
    exam_id: Optional[int] = Field(default=None, index=True, sa_type=UINT_TYPE)
    last_message_at: Optional[datetime] = Field(default=None, sa_type=DateTime())
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class ExamProblemAnswerTiming(SQLModel, table=True):
    __tablename__ = "exam_problem_answer_timings"
    __table_args__ = (
        UniqueConstraint("user_id", "exam_id", "problem_id", name="uq_exam_problem_answer_timings_user_exam_problem"),
    )

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    user_id: int = Field(index=True, nullable=False, sa_type=UINT_TYPE)
    exam_id: int = Field(index=True, nullable=False, sa_type=UINT_TYPE)
    problem_id: int = Field(index=True, nullable=False, sa_type=UINT_TYPE)
    session_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    started_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    first_submission_at: Optional[datetime] = Field(default=None, sa_type=DateTime())
    last_submission_at: Optional[datetime] = Field(default=None, sa_type=DateTime())
    first_submission_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    last_submission_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    submission_count: int = Field(default=0, nullable=False)
    first_submission_elapsed_seconds: Optional[int] = Field(default=None)
    last_submission_elapsed_seconds: Optional[int] = Field(default=None)
    total_answer_seconds: Optional[int] = Field(default=None)
    solved_at: Optional[datetime] = Field(default=None, sa_type=DateTime())
    is_solved: bool = Field(default=False, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentMessage(SQLModel, table=True):
    __tablename__ = "agent_messages"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    role: AgentMessageRole = Field(nullable=False, sa_type=enum_type(AgentMessageRole))
    message_type: AgentMessageType = Field(
        default=AgentMessageType.TEXT,
        nullable=False,
        sa_type=enum_type(AgentMessageType),
    )
    content: str = Field(nullable=False, sa_type=Text())
    visible_to_user: bool = Field(default=True, nullable=False)
    turn_index: int = Field(default=0, index=True, nullable=False)
    token_count: Optional[int] = Field(default=None)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentDiagramScene(SQLModel, table=True):
    __tablename__ = "agent_diagram_scenes"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    teaching_step_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_teaching_steps.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    scene_uuid: str = Field(index=True, max_length=128, nullable=False)
    parent_scene_uuid: Optional[str] = Field(default=None, max_length=128, index=True)
    base_step_id: Optional[str] = Field(default=None, max_length=128)
    inherit_mode: str = Field(default="new", max_length=32, nullable=False)
    title: str = Field(max_length=255, nullable=False)
    diagram_kind: str = Field(max_length=32, nullable=False)
    layout: str = Field(default="freeform", max_length=32, nullable=False)
    teaching_goal: str = Field(nullable=False, sa_type=Text())
    default_step_id: Optional[str] = Field(default=None, max_length=128)
    status: str = Field(default="verified", max_length=32, nullable=False)
    source_turn_index: int = Field(default=0, nullable=False)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentDiagramJob(SQLModel, table=True):
    __tablename__ = "agent_diagram_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_diagram_job_key"),)

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    job_uuid: str = Field(index=True, max_length=64, nullable=False)
    idempotency_key: str = Field(max_length=128, nullable=False)
    run_uuid: Optional[str] = Field(default=None, index=True, max_length=64)
    user_id: int = Field(index=True, nullable=False, sa_type=UINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(default=None, foreign_key="agent_problem_workspaces.id", index=True, sa_type=UBIGINT_TYPE)
    source_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    source_turn_index: int = Field(index=True, nullable=False)
    status: str = Field(default="running", max_length=24, nullable=False)
    scene_uuid: Optional[str] = Field(default=None, max_length=128)
    diagram_goal: str = Field(default="", nullable=False, sa_type=Text())
    producer_version: Optional[str] = Field(default=None, max_length=64)
    renderer_version: Optional[str] = Field(default=None, max_length=64)
    error_code: Optional[str] = Field(default=None, max_length=64)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentDiagramStep(SQLModel, table=True):
    __tablename__ = "agent_diagram_steps"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    diagram_scene_id: int = Field(
        foreign_key="agent_diagram_scenes.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    step_uuid: str = Field(max_length=128, nullable=False)
    order_index: int = Field(default=0, nullable=False)
    title: str = Field(max_length=255, nullable=False)
    teaching_note: str = Field(nullable=False, sa_type=Text())
    entry_state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    exit_state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    transition_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentDiagramReference(SQLModel, table=True):
    __tablename__ = "agent_diagram_references"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    diagram_scene_id: int = Field(
        foreign_key="agent_diagram_scenes.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    diagram_step_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_diagram_steps.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    reference_type: str = Field(max_length=32, nullable=False)
    role: str = Field(default="origin", max_length=32, nullable=False)
    target_id: Optional[str] = Field(default=None, max_length=255)
    source_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    source_turn_index: int = Field(default=0, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class ImageAsset(SQLModel, table=True):
    __tablename__ = "image_assets"

    id: str = Field(primary_key=True, index=True, max_length=64)
    filename: str = Field(max_length=255)
    storage_path: str = Field(max_length=1024)
    public_url: str = Field(max_length=1024)
    content_type: str = Field(max_length=128)
    size_bytes: int = Field(nullable=False)
    uploader_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True, sa_type=UINT_TYPE)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentMessageImage(SQLModel, table=True):
    __tablename__ = "agent_message_images"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    message_id: int = Field(foreign_key="agent_messages.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    image_id: str = Field(foreign_key="image_assets.id", index=True, max_length=64, nullable=False)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, sa_type=UINT_TYPE)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentSessionFact(SQLModel, table=True):
    __tablename__ = "agent_session_facts"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, sa_type=UINT_TYPE)
    session_id: Optional[int] = Field(default=None, foreign_key="agent_sessions.id", index=True, sa_type=UBIGINT_TYPE)
    fact_type: AgentSessionFactType = Field(nullable=False, sa_type=enum_type(AgentSessionFactType))
    content: str = Field(nullable=False, sa_type=Text())
    source_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    confidence: Optional[float] = Field(default=None)
    is_active: bool = Field(default=True, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentBoardNode(SQLModel, table=True):
    __tablename__ = "agent_board_nodes"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    boardwork_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_boardworks.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    teaching_step_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_teaching_steps.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    title: str = Field(max_length=255, nullable=False)
    summary: str = Field(default="", nullable=False, sa_type=Text())
    status: AgentBoardNodeStatus = Field(
        default=AgentBoardNodeStatus.EXPLORING,
        nullable=False,
        sa_type=enum_type(AgentBoardNodeStatus),
    )
    trace_payload: Optional[dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    coach_note: Optional[str] = Field(default=None, sa_type=Text())
    sort_order: int = Field(default=0, nullable=False)
    is_active: bool = Field(default=False, nullable=False)
    debug_ready: bool = Field(default=False, nullable=False)
    source_turn_index: int = Field(default=0, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentBoardUpdateAction(SQLModel, table=True):
    __tablename__ = "agent_board_update_actions"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    boardwork_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_boardworks.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    node_id: Optional[int] = Field(default=None, foreign_key="agent_board_nodes.id", index=True, sa_type=UBIGINT_TYPE)
    action_type: AgentBoardUpdateActionType = Field(
        nullable=False,
        sa_type=enum_type(AgentBoardUpdateActionType),
    )
    reason: Optional[str] = Field(default=None, sa_type=Text())
    source_turn_index: int = Field(default=0, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentBoardwork(SQLModel, table=True):
    __tablename__ = "agent_boardworks"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    title: str = Field(max_length=255, nullable=False)
    status: AgentBoardworkStatus = Field(
        default=AgentBoardworkStatus.ACTIVE,
        nullable=False,
        sa_type=enum_type(AgentBoardworkStatus),
    )
    source_turn_index: int = Field(default=0, nullable=False)
    closed_turn_index: Optional[int] = Field(default=None)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentProblemWorkspace(SQLModel, table=True):
    __tablename__ = "agent_problem_workspaces"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, sa_type=UINT_TYPE)
    problem_key: Optional[str] = Field(default=None, max_length=255, index=True)
    problem_id: Optional[int] = Field(default=None, index=True, sa_type=UINT_TYPE)
    submission_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    source: Optional[str] = Field(default=None, max_length=64)
    title: str = Field(default="当前题目", max_length=255, nullable=False)
    problem_summary: Optional[str] = Field(default=None, sa_type=Text())
    status: ProblemWorkspaceStatus = Field(
        default=ProblemWorkspaceStatus.ACTIVE,
        nullable=False,
        sa_type=enum_type(ProblemWorkspaceStatus),
    )
    active_plan_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    current_step_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    guidance_depth: GuidanceDepthValue = Field(
        default=GuidanceDepthValue.DIRECT,
        nullable=False,
        sa_type=enum_type(GuidanceDepthValue),
    )
    depth_override: bool = Field(default=False, nullable=False)
    algorithm_family: Optional[str] = Field(default=None, max_length=64)
    confidence: Optional[float] = Field(default=None)
    started_turn_index: int = Field(default=0, nullable=False)
    last_active_turn_index: int = Field(default=0, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentTeachingPlan(SQLModel, table=True):
    __tablename__ = "agent_teaching_plans"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    workspace_id: int = Field(
        foreign_key="agent_problem_workspaces.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    version: int = Field(default=1, nullable=False)
    status: TeachingPlanStatus = Field(
        default=TeachingPlanStatus.ACTIVE,
        nullable=False,
        sa_type=enum_type(TeachingPlanStatus),
    )
    planner_agent: Optional[str] = Field(default=None, max_length=128)
    plan_summary: Optional[str] = Field(default=None, sa_type=Text())
    branch_from_plan_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentTeachingStep(SQLModel, table=True):
    __tablename__ = "agent_teaching_steps"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    plan_id: int = Field(foreign_key="agent_teaching_plans.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: int = Field(
        foreign_key="agent_problem_workspaces.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    parent_step_id: Optional[int] = Field(default=None, index=True, sa_type=UBIGINT_TYPE)
    order_index: int = Field(default=0, nullable=False)
    step_key: str = Field(max_length=128, nullable=False)
    title: str = Field(max_length=255, nullable=False)
    teaching_goal: str = Field(default="", nullable=False, sa_type=Text())
    expected_student_action: Optional[str] = Field(default=None, sa_type=Text())
    mastery_criteria: Optional[str] = Field(default=None, sa_type=Text())
    status: TeachingStepStatus = Field(
        default=TeachingStepStatus.READY,
        nullable=False,
        sa_type=enum_type(TeachingStepStatus),
    )
    mastery_state: Optional[str] = Field(default=None, max_length=64)
    repeat_count: int = Field(default=0, nullable=False)
    last_prompt_turn_index: Optional[int] = Field(default=None)
    confirmed_turn_index: Optional[int] = Field(default=None)
    min_guidance_depth: GuidanceDepthValue = Field(
        default=GuidanceDepthValue.DIRECT,
        nullable=False,
        sa_type=enum_type(GuidanceDepthValue),
    )
    max_guidance_depth: GuidanceDepthValue = Field(
        default=GuidanceDepthValue.MINIMAL,
        nullable=False,
        sa_type=enum_type(GuidanceDepthValue),
    )
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentTeachingEvent(SQLModel, table=True):
    __tablename__ = "agent_teaching_events"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    workspace_id: int = Field(
        foreign_key="agent_problem_workspaces.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    turn_index: int = Field(default=0, index=True, nullable=False)
    event_type: str = Field(max_length=64, nullable=False)
    actor: str = Field(max_length=128, nullable=False)
    step_id: Optional[int] = Field(default=None, foreign_key="agent_teaching_steps.id", index=True, sa_type=UBIGINT_TYPE)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    confidence: Optional[float] = Field(default=None)
    visible_to_user: bool = Field(default=False, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentTurnRun(SQLModel, table=True):
    __tablename__ = "agent_turn_runs"
    __table_args__ = (UniqueConstraint("session_id", "client_request_id", name="uq_agent_turn_run_session_request"),)

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    run_uuid: str = Field(index=True, max_length=128, nullable=False)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    client_request_id: str = Field(default_factory=lambda: uuid4().hex, max_length=128, nullable=False)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    turn_index: int = Field(default=0, index=True, nullable=False)
    user_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    assistant_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    status: str = Field(default="running", max_length=32, index=True, nullable=False)
    response_preview: Optional[str] = Field(default=None, sa_type=Text())
    error_code: Optional[str] = Field(default=None, max_length=128)
    error_message: Optional[str] = Field(default=None, sa_type=Text())
    event_count: int = Field(default=0, nullable=False)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentTurnRunEvent(SQLModel, table=True):
    __tablename__ = "agent_turn_run_events"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    run_id: int = Field(foreign_key="agent_turn_runs.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    event_index: int = Field(default=0, nullable=False)
    event_type: str = Field(max_length=128, index=True, nullable=False)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentBlackboardEntry(SQLModel, table=True):
    __tablename__ = "agent_blackboard_entries"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    workspace_id: int = Field(
        foreign_key="agent_problem_workspaces.id",
        index=True,
        nullable=False,
        sa_type=UBIGINT_TYPE,
    )
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    step_id: Optional[int] = Field(default=None, foreign_key="agent_teaching_steps.id", index=True, sa_type=UBIGINT_TYPE)
    entry_type: str = Field(max_length=64, nullable=False)
    content: str = Field(nullable=False, sa_type=Text())
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    writer_agent: str = Field(max_length=128, nullable=False)
    source_event_id: Optional[int] = Field(default=None, foreign_key="agent_teaching_events.id", sa_type=UBIGINT_TYPE)
    source_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    confidence: Optional[float] = Field(default=None)
    visibility: str = Field(default="internal", max_length=32, nullable=False)
    status: str = Field(default="active", max_length=32, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentMemoryItem(SQLModel, table=True):
    __tablename__ = "agent_memory_items"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, sa_type=UINT_TYPE)
    scope: MemoryScope = Field(default=MemoryScope.USER_PROFILE, nullable=False, sa_type=enum_type(MemoryScope))
    entity_type: Optional[str] = Field(default=None, max_length=64)
    entity_id: Optional[str] = Field(default=None, max_length=255)
    memory_type: str = Field(max_length=64, nullable=False)
    content: str = Field(nullable=False, sa_type=Text())
    summary: Optional[str] = Field(default=None, sa_type=Text())
    embedding: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    source_message_id: Optional[int] = Field(default=None, foreign_key="agent_messages.id", sa_type=UBIGINT_TYPE)
    confidence: Optional[float] = Field(default=None)
    importance: Optional[float] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None, sa_type=DateTime())
    is_active: bool = Field(default=True, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentUserMemorySettings(SQLModel, table=True):
    __tablename__ = "agent_user_memory_settings"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, unique=True, sa_type=UINT_TYPE)
    long_term_memory_enabled: bool = Field(default=True, nullable=False)
    personalization_enabled: bool = Field(default=True, nullable=False)
    updated_by: Optional[str] = Field(default=None, max_length=128)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentUserPreferences(SQLModel, table=True):
    __tablename__ = "agent_user_preferences"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False, unique=True, sa_type=UINT_TYPE)
    mascot_skin: str = Field(default="base", max_length=32, nullable=False)
    font_size: str = Field(default="small", max_length=16, nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
    update_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentAcceptanceRun(SQLModel, table=True):
    __tablename__ = "agent_acceptance_runs"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    scenario_id: str = Field(max_length=128, index=True, nullable=False)
    status: str = Field(max_length=32, index=True, nullable=False)
    mode: str = Field(max_length=64, index=True, nullable=False)
    model: Optional[str] = Field(default=None, max_length=128)
    runtime_equivalent: Optional[bool] = Field(default=None)
    turn_count: int = Field(default=0, nullable=False)
    checks: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    failed_checks: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    performance: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    real_provider: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentProviderCallTrace(SQLModel, table=True):
    __tablename__ = "agent_provider_call_traces"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    turn_index: int = Field(default=0, index=True, nullable=False)
    agent_name: str = Field(max_length=128, index=True, nullable=False)
    capability: str = Field(max_length=128, index=True, nullable=False)
    provider_backend: str = Field(max_length=64, index=True, nullable=False)
    outcome: str = Field(max_length=64, index=True, nullable=False)
    accepted: bool = Field(default=False, index=True, nullable=False)
    latency_ms: int = Field(default=0, nullable=False)
    prompt_size_bytes: Optional[int] = Field(default=None)
    payload_size_bytes: Optional[int] = Field(default=None)
    fallback_reason: Optional[str] = Field(default=None, max_length=128)
    error_type: Optional[str] = Field(default=None, max_length=128)
    redaction_policy: str = Field(default="summary_only", max_length=64, nullable=False)
    sanitized: bool = Field(default=True, nullable=False)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentToolCallTrace(SQLModel, table=True):
    __tablename__ = "agent_tool_call_traces"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    turn_index: int = Field(default=0, index=True, nullable=False)
    owner_agent: str = Field(max_length=128, index=True, nullable=False)
    tool_backend: str = Field(max_length=64, index=True, nullable=False)
    tool_name: str = Field(max_length=128, index=True, nullable=False)
    status: str = Field(max_length=32, index=True, nullable=False)
    latency_ms: int = Field(default=0, nullable=False)
    request_summary: Optional[str] = Field(default=None, sa_type=Text())
    response_summary: Optional[str] = Field(default=None, sa_type=Text())
    error_type: Optional[str] = Field(default=None, max_length=128)
    redaction_policy: str = Field(default="summary_only", max_length=64, nullable=False)
    sanitized: bool = Field(default=True, nullable=False)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())


class AgentExecutionTrace(SQLModel, table=True):
    __tablename__ = "agent_execution_traces"

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=UBIGINT_TYPE)
    session_id: int = Field(foreign_key="agent_sessions.id", index=True, nullable=False, sa_type=UBIGINT_TYPE)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="agent_problem_workspaces.id",
        index=True,
        sa_type=UBIGINT_TYPE,
    )
    turn_index: int = Field(default=0, index=True, nullable=False)
    agent_name: str = Field(max_length=128, nullable=False)
    status: str = Field(max_length=32, nullable=False)
    input_summary: str = Field(default="", nullable=False, sa_type=Text())
    output_summary: str = Field(default="", nullable=False, sa_type=Text())
    latency_ms: int = Field(default=0, nullable=False)
    blackboard_write_count: int = Field(default=0, nullable=False)
    memory_write_count: int = Field(default=0, nullable=False)
    error_message: Optional[str] = Field(default=None, sa_type=Text())
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    create_time: datetime = Field(default_factory=datetime.utcnow, nullable=False, sa_type=DateTime())
