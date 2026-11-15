from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlmodel import Session, delete, select

from app.models import (
    AgentBoardNode,
    AgentBoardNodeStatus,
    AgentBoardUpdateAction,
    AgentBoardUpdateActionType,
    AgentBoardwork,
    AgentBoardworkStatus,
    AgentDiagramReference,
    AgentDiagramJob,
    AgentDiagramScene,
    AgentDiagramStep,
    AgentMessage,
    AgentMessageImage,
    AgentMessageRole,
    AgentMessageType,
    AgentMode,
    AgentMemoryItem,
    AgentSession,
    AgentSessionFact,
    AgentSessionFactType,
    AgentSessionSource,
    AgentSessionStatus,
    AgentTurnRun,
    AgentBlackboardEntry,
    ExamProblemAnswerTiming,
)
from app.session_titles import DEFAULT_SESSION_TITLE, display_session_title, generate_session_title


def _activity_sort_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


class AgentStore:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_session(
        self,
        session_uuid: str,
        user_id: int,
        mode: AgentMode,
        *,
        source: AgentSessionSource | None = None,
        problem_id: int | None = None,
        submission_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
    ) -> AgentSession:
        session = self.get_session(session_uuid)
        if session is not None:
            updated = False
            if session.mode != mode:
                session.mode = mode
                updated = True
            if source is not None and session.source != source:
                session.source = source
                updated = True
            for field_name, value in {
                "submission_id": submission_id,
                "course_id": course_id,
                "class_id": class_id,
                "exam_id": exam_id,
            }.items():
                if value is not None and getattr(session, field_name) != value:
                    setattr(session, field_name, value)
                    updated = True
            if updated:
                session.update_time = datetime.utcnow()
                self.db.add(session)
                self.db.flush()
            return session

        now = datetime.utcnow()
        session = AgentSession(
            session_uuid=session_uuid,
            user_id=user_id,
            mode=mode,
            status=AgentSessionStatus.ACTIVE,
            source=source or AgentSessionSource.WEB,
            title=None,
            problem_id=problem_id,
            submission_id=submission_id,
            course_id=course_id,
            class_id=class_id,
            exam_id=exam_id,
            create_time=now,
            update_time=now,
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_session(self, session_uuid: str) -> AgentSession | None:
        statement = select(AgentSession).where(
            AgentSession.session_uuid == session_uuid,
            AgentSession.status != AgentSessionStatus.DELETED,
        )
        return self.db.exec(statement).first()

    def bind_session_problem(self, session_uuid: str, user_id: int, problem_id: int) -> bool:
        session = self.get_session(session_uuid)
        if session is None or session.user_id != user_id:
            return False
        if session.problem_id == problem_id:
            return True
        if session.problem_id is not None:
            return False

        substantive_messages = self.db.exec(
            select(func.count(AgentMessage.id)).where(
                AgentMessage.session_id == session.id,
                AgentMessage.visible_to_user.is_(True),
                AgentMessage.role.in_([AgentMessageRole.USER, AgentMessageRole.ASSISTANT]),
            )
        ).one()
        if substantive_messages:
            return False

        session.problem_id = problem_id
        session.update_time = datetime.utcnow()
        self.db.add(session)
        self.db.flush()
        return True

    def get_exam_problem_answer_timing(
        self,
        *,
        user_id: int,
        exam_id: int,
        problem_id: int,
    ) -> ExamProblemAnswerTiming | None:
        statement = select(ExamProblemAnswerTiming).where(
            ExamProblemAnswerTiming.user_id == user_id,
            ExamProblemAnswerTiming.exam_id == exam_id,
            ExamProblemAnswerTiming.problem_id == problem_id,
        )
        return self.db.exec(statement).first()

    def _recalculate_exam_problem_answer_timing(self, timing: ExamProblemAnswerTiming) -> None:
        def _elapsed_seconds(started_at: datetime | None, event_at: datetime | None) -> int | None:
            if started_at is None or event_at is None:
                return None
            elapsed = int((event_at - started_at).total_seconds())
            return max(elapsed, 0)

        timing.first_submission_elapsed_seconds = _elapsed_seconds(timing.started_at, timing.first_submission_at)
        timing.last_submission_elapsed_seconds = _elapsed_seconds(timing.started_at, timing.last_submission_at)
        timing.total_answer_seconds = timing.last_submission_elapsed_seconds

    def record_exam_problem_answer_start(
        self,
        *,
        user_id: int,
        exam_id: int,
        problem_id: int,
        session_id: int | None = None,
        started_at: datetime | None = None,
    ) -> ExamProblemAnswerTiming:
        now = started_at or datetime.utcnow()
        timing = self.get_exam_problem_answer_timing(
            user_id=user_id,
            exam_id=exam_id,
            problem_id=problem_id,
        )
        if timing is None:
            timing = ExamProblemAnswerTiming(
                user_id=user_id,
                exam_id=exam_id,
                problem_id=problem_id,
                session_id=session_id,
                started_at=now,
                create_time=now,
                update_time=now,
            )
            self.db.add(timing)
            self.db.flush()
            return timing

        updated = False
        if session_id is not None and timing.session_id is None:
            timing.session_id = session_id
            updated = True
        if started_at is not None and started_at < timing.started_at:
            timing.started_at = started_at
            updated = True
        if updated:
            timing.update_time = now
            self._recalculate_exam_problem_answer_timing(timing)
            self.db.add(timing)
            self.db.flush()
        return timing

    def add_message(
        self,
        session: AgentSession,
        role: AgentMessageRole,
        content: str,
        *,
        turn_index: int,
        message_type: AgentMessageType = AgentMessageType.TEXT,
        visible_to_user: bool = True,
        token_count: int | None = None,
    ) -> AgentMessage:
        now = datetime.utcnow()
        message = AgentMessage(
            session_id=session.id,
            role=role,
            message_type=message_type,
            content=content.strip(),
            visible_to_user=visible_to_user,
            turn_index=turn_index,
            token_count=token_count,
            create_time=now,
        )
        self.db.add(message)
        if role == AgentMessageRole.USER and not session.title:
            session.title = generate_session_title(content)
        session.last_message_at = now
        session.update_time = now
        self.db.add(session)
        self.db.flush()
        return message

    def upsert_diagram_scene(self, session: AgentSession, scene_payload: dict[str, Any]) -> AgentDiagramScene:
        scene_uuid = str(scene_payload["scene_id"])
        statement = select(AgentDiagramScene).where(
            AgentDiagramScene.session_id == session.id,
            AgentDiagramScene.scene_uuid == scene_uuid,
        )
        diagram_scene = self.db.exec(statement).first()
        now = datetime.utcnow()

        if diagram_scene is None:
            diagram_scene = AgentDiagramScene(
                session_id=session.id,
                scene_uuid=scene_uuid,
                create_time=now,
                update_time=now,
            )
        else:
            self.db.exec(delete(AgentDiagramStep).where(AgentDiagramStep.diagram_scene_id == diagram_scene.id))
            diagram_scene.update_time = now

        diagram_scene.title = str(scene_payload["title"])
        diagram_scene.diagram_kind = str(scene_payload["diagram_kind"])
        diagram_scene.layout = str(scene_payload.get("layout") or "freeform")
        diagram_scene.workspace_id = scene_payload.get("workspace_id")
        diagram_scene.teaching_step_id = scene_payload.get("teaching_step_id")
        diagram_scene.parent_scene_uuid = scene_payload.get("parent_scene_id")
        diagram_scene.base_step_id = scene_payload.get("base_step_id")
        diagram_scene.inherit_mode = str(scene_payload.get("inherit_mode") or "new")
        diagram_scene.teaching_goal = str(scene_payload["teaching_goal"])
        diagram_scene.default_step_id = scene_payload.get("default_step_id")
        diagram_scene.status = str(scene_payload.get("status") or "verified")
        diagram_scene.source_turn_index = int(scene_payload.get("source_turn_index") or 0)
        metadata_json = scene_payload.get("metadata_json") or {}
        diagram_scene.metadata_json = metadata_json if isinstance(metadata_json, dict) else {}
        self.db.add(diagram_scene)
        self.db.flush()

        for step_payload in sorted(scene_payload.get("steps", []), key=lambda item: item["order_index"]):
            self.db.add(
                AgentDiagramStep(
                    diagram_scene_id=diagram_scene.id,
                    step_uuid=str(step_payload["step_id"]),
                    order_index=int(step_payload["order_index"]),
                    title=str(step_payload["title"]),
                    teaching_note=str(step_payload["teaching_note"]),
                    entry_state=step_payload["entry_state"],
                    exit_state=step_payload["exit_state"],
                    transition_payload=step_payload["transition"],
                    create_time=now,
                    update_time=now,
                )
            )

        self.db.flush()
        return diagram_scene

    def get_diagram_scene_payload(self, session_uuid: str, scene_uuid: str) -> dict[str, Any] | None:
        session = self.get_session(session_uuid)
        if session is None or session.id is None:
            return None

        scene = self.db.exec(
            select(AgentDiagramScene).where(
                AgentDiagramScene.session_id == session.id,
                AgentDiagramScene.scene_uuid == scene_uuid,
            )
        ).first()
        if scene is None or scene.id is None:
            return None

        steps = list(
            self.db.exec(
                select(AgentDiagramStep)
                .where(AgentDiagramStep.diagram_scene_id == scene.id)
                .order_by(AgentDiagramStep.order_index, AgentDiagramStep.id)
            )
        )
        return {
            "scene_id": scene.scene_uuid,
            "session_id": session_uuid,
            "workspace_id": scene.workspace_id,
            "teaching_step_id": scene.teaching_step_id,
            "parent_scene_id": scene.parent_scene_uuid,
            "base_step_id": scene.base_step_id,
            "inherit_mode": scene.inherit_mode,
            "title": scene.title,
            "diagram_kind": scene.diagram_kind,
            "layout": scene.layout,
            "teaching_goal": scene.teaching_goal,
            "default_step_id": scene.default_step_id,
            "status": scene.status,
            "source_turn_index": scene.source_turn_index,
            "metadata_json": scene.metadata_json or {},
            "steps": [
                {
                    "step_id": step.step_uuid,
                    "order_index": step.order_index,
                    "title": step.title,
                    "teaching_note": step.teaching_note,
                    "entry_state": step.entry_state,
                    "exit_state": step.exit_state,
                    "transition": step.transition_payload,
                }
                for step in steps
            ],
        }

    def list_recent_diagram_scene_payloads(self, session_uuid: str, limit: int = 3) -> list[dict[str, Any]]:
        session = self.get_session(session_uuid)
        if session is None or session.id is None:
            return []

        scenes = list(
            self.db.exec(
                select(AgentDiagramScene)
                .where(AgentDiagramScene.session_id == session.id)
                .order_by(AgentDiagramScene.source_turn_index.desc(), AgentDiagramScene.id.desc())
                .limit(limit)
            )
        )
        payloads: list[dict[str, Any]] = []
        for scene in reversed(scenes):
            payload = self.get_diagram_scene_payload(session_uuid, scene.scene_uuid)
            if payload is not None:
                payloads.append(payload)
        return payloads

    def replace_diagram_references(
        self,
        diagram_scene: AgentDiagramScene,
        references: list[dict[str, Any]],
    ) -> list[AgentDiagramReference]:
        self.db.exec(
            delete(AgentDiagramReference).where(AgentDiagramReference.diagram_scene_id == diagram_scene.id)
        )
        created: list[AgentDiagramReference] = []
        now = datetime.utcnow()
        for reference_payload in references:
            reference = AgentDiagramReference(
                diagram_scene_id=diagram_scene.id,
                diagram_step_id=reference_payload.get("diagram_step_id"),
                reference_type=str(reference_payload["reference_type"]),
                role=str(reference_payload.get("role") or "origin"),
                target_id=reference_payload.get("target_id"),
                source_message_id=reference_payload.get("source_message_id"),
                source_turn_index=int(reference_payload.get("source_turn_index") or 0),
                create_time=now,
            )
            self.db.add(reference)
            created.append(reference)
        self.db.flush()
        return created

    def list_diagram_scenes_for_turn(self, session_id: int, source_turn_index: int) -> list[AgentDiagramScene]:
        return list(
            self.db.exec(
                select(AgentDiagramScene)
                .where(
                    AgentDiagramScene.session_id == session_id,
                    AgentDiagramScene.source_turn_index == source_turn_index,
                )
                .order_by(AgentDiagramScene.id)
            )
        )

    def replace_board_node_diagram_references(
        self,
        node: AgentBoardNode,
        diagram_scenes: list[AgentDiagramScene],
    ) -> list[AgentDiagramReference]:
        self.db.exec(
            delete(AgentDiagramReference).where(
                AgentDiagramReference.reference_type == "board_node",
                AgentDiagramReference.target_id == str(node.id),
            )
        )
        created: list[AgentDiagramReference] = []
        now = datetime.utcnow()
        for scene in diagram_scenes:
            step = None
            if scene.default_step_id:
                step = self.db.exec(
                    select(AgentDiagramStep).where(
                        AgentDiagramStep.diagram_scene_id == scene.id,
                        AgentDiagramStep.step_uuid == scene.default_step_id,
                    )
                ).first()
            reference = AgentDiagramReference(
                diagram_scene_id=scene.id,
                diagram_step_id=step.id if step is not None else None,
                reference_type="board_node",
                role="summary_ref",
                target_id=str(node.id),
                source_turn_index=node.source_turn_index,
                create_time=now,
            )
            self.db.add(reference)
            created.append(reference)
        self.db.flush()
        return created

    def set_session_summary(self, session: AgentSession, summary: str | None) -> AgentSession:
        session.summary = summary.strip() if summary and summary.strip() else None
        session.update_time = datetime.utcnow()
        self.db.add(session)
        self.db.flush()
        return session

    def upsert_session_fact(
        self,
        *,
        session_id: int | None,
        user_id: int,
        fact_type: AgentSessionFactType,
        content: str,
        source_message_id: int | None = None,
        confidence: float | None = None,
    ) -> AgentSessionFact:
        if session_id is None:
            raise ValueError("session_id is required")

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("content is required")

        statement = (
            select(AgentSessionFact)
            .where(
                AgentSessionFact.session_id == session_id,
                AgentSessionFact.fact_type == fact_type,
                AgentSessionFact.is_active.is_(True),
            )
            .order_by(AgentSessionFact.update_time.desc(), AgentSessionFact.id.desc())
            .limit(1)
        )
        fact = self.db.exec(statement).first()
        now = datetime.utcnow()
        if fact is None:
            fact = AgentSessionFact(
                user_id=user_id,
                session_id=session_id,
                fact_type=fact_type,
                content=normalized_content,
                source_message_id=source_message_id,
                confidence=confidence,
                is_active=True,
                create_time=now,
                update_time=now,
            )
        else:
            fact.content = normalized_content
            fact.source_message_id = source_message_id
            fact.confidence = confidence
            fact.is_active = True
            fact.update_time = now
        self.db.add(fact)
        self.db.flush()
        return fact

    def list_session_facts(
        self,
        session_id: int | None,
        *,
        fact_type: AgentSessionFactType | None = None,
        limit: int | None = None,
    ) -> list[AgentSessionFact]:
        if session_id is None:
            return []

        statement = (
            select(AgentSessionFact)
            .where(
                AgentSessionFact.session_id == session_id,
                AgentSessionFact.is_active.is_(True),
            )
            .order_by(AgentSessionFact.update_time.desc(), AgentSessionFact.id.desc())
        )
        if fact_type is not None:
            statement = statement.where(AgentSessionFact.fact_type == fact_type)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.db.exec(statement))

    def create_board_node(
        self,
        *,
        session_id: int | None,
        title: str,
        summary: str,
        source_turn_index: int,
        status: AgentBoardNodeStatus = AgentBoardNodeStatus.EXPLORING,
        trace_payload: dict[str, Any] | None = None,
        coach_note: str | None = None,
        sort_order: int | None = None,
        is_active: bool = True,
        teaching_step_id: int | None = None,
    ) -> AgentBoardNode:
        if session_id is None:
            raise ValueError("session_id is required")

        boardwork = self.get_or_create_active_boardwork(
            session_id=session_id,
            title=title.strip(),
            source_turn_index=source_turn_index,
        )

        if sort_order is None:
            current = self.db.exec(
                select(AgentBoardNode.sort_order)
                .where(AgentBoardNode.session_id == session_id)
                .where(AgentBoardNode.boardwork_id == boardwork.id)
                .order_by(AgentBoardNode.sort_order.desc(), AgentBoardNode.id.desc())
                .limit(1)
            ).first()
            sort_order = 0 if current is None else int(current) + 1

        if is_active:
            active_nodes = list(
                self.db.exec(
                    select(AgentBoardNode).where(
                        AgentBoardNode.session_id == session_id,
                        AgentBoardNode.boardwork_id == boardwork.id,
                        AgentBoardNode.is_active.is_(True),
                    )
                )
            )
            for existing in active_nodes:
                existing.is_active = False
                existing.update_time = datetime.utcnow()
                self.db.add(existing)

        now = datetime.utcnow()
        node = AgentBoardNode(
            session_id=session_id,
            boardwork_id=boardwork.id,
            teaching_step_id=teaching_step_id,
            title=title.strip(),
            summary=summary.strip(),
            status=status,
            trace_payload=trace_payload,
            coach_note=coach_note.strip() if coach_note else None,
            sort_order=sort_order,
            is_active=is_active,
            source_turn_index=source_turn_index,
            create_time=now,
            update_time=now,
        )
        self.db.add(node)
        self.db.flush()
        return node

    def list_boardworks(self, session_id: int | None) -> list[AgentBoardwork]:
        if session_id is None:
            return []
        statement = (
            select(AgentBoardwork)
            .where(AgentBoardwork.session_id == session_id)
            .order_by(AgentBoardwork.update_time.desc(), AgentBoardwork.id.desc())
        )
        return list(self.db.exec(statement))

    def get_active_boardwork(self, session_id: int | None) -> AgentBoardwork | None:
        if session_id is None:
            return None
        statement = (
            select(AgentBoardwork)
            .where(
                AgentBoardwork.session_id == session_id,
                AgentBoardwork.status == AgentBoardworkStatus.ACTIVE,
            )
            .order_by(AgentBoardwork.update_time.desc(), AgentBoardwork.id.desc())
            .limit(1)
        )
        return self.db.exec(statement).first()

    def get_or_create_active_boardwork(
        self,
        *,
        session_id: int | None,
        title: str,
        source_turn_index: int,
        workspace_id: int | None = None,
    ) -> AgentBoardwork:
        if session_id is None:
            raise ValueError("session_id is required")

        existing = self.get_active_boardwork(session_id)
        if existing is not None:
            if (
                workspace_id is not None
                and existing.workspace_id is not None
                and existing.workspace_id != workspace_id
            ):
                self.close_active_boardwork(
                    session_id=session_id,
                    status=AgentBoardworkStatus.PAUSED,
                    closed_turn_index=source_turn_index,
                )
                existing = self.db.exec(
                    select(AgentBoardwork)
                    .where(
                        AgentBoardwork.session_id == session_id,
                        AgentBoardwork.workspace_id == workspace_id,
                    )
                    .order_by(AgentBoardwork.update_time.desc(), AgentBoardwork.id.desc())
                    .limit(1)
                ).first()

        if existing is not None:
            if existing.status != AgentBoardworkStatus.ACTIVE:
                existing.status = AgentBoardworkStatus.ACTIVE
                existing.closed_turn_index = None
                existing.update_time = datetime.utcnow()
                self.db.add(existing)
                self.db.flush()
            if workspace_id is not None and existing.workspace_id is None:
                existing.workspace_id = workspace_id
                existing.update_time = datetime.utcnow()
                self.db.add(existing)
                self.db.flush()
            if not existing.title.strip():
                existing.title = title.strip() or "当前题目白板"
                existing.update_time = datetime.utcnow()
                self.db.add(existing)
                self.db.flush()
            return existing

        now = datetime.utcnow()
        boardwork = AgentBoardwork(
            session_id=session_id,
            workspace_id=workspace_id,
            title=title.strip() or "当前题目白板",
            status=AgentBoardworkStatus.ACTIVE,
            source_turn_index=source_turn_index,
            create_time=now,
            update_time=now,
        )
        self.db.add(boardwork)
        self.db.flush()
        return boardwork

    def close_active_boardwork(
        self,
        *,
        session_id: int | None,
        status: AgentBoardworkStatus,
        closed_turn_index: int | None = None,
    ) -> AgentBoardwork | None:
        if session_id is None:
            return None
        boardwork = self.get_active_boardwork(session_id)
        if boardwork is None:
            return None

        boardwork.status = status
        boardwork.closed_turn_index = closed_turn_index
        boardwork.update_time = datetime.utcnow()
        self.db.add(boardwork)

        active_nodes = list(
            self.db.exec(
                select(AgentBoardNode).where(
                    AgentBoardNode.session_id == session_id,
                    AgentBoardNode.boardwork_id == boardwork.id,
                    AgentBoardNode.is_active.is_(True),
                )
            )
        )
        for node in active_nodes:
            node.is_active = False
            node.update_time = datetime.utcnow()
            self.db.add(node)

        self.db.flush()
        return boardwork

    def start_new_boardwork(
        self,
        *,
        session_id: int | None,
        title: str,
        source_turn_index: int,
        workspace_id: int | None = None,
    ) -> AgentBoardwork:
        if session_id is None:
            raise ValueError("session_id is required")
        self.close_active_boardwork(
            session_id=session_id,
            status=AgentBoardworkStatus.PAUSED,
            closed_turn_index=source_turn_index,
        )
        return self.get_or_create_active_boardwork(
            session_id=session_id,
            title=title,
            source_turn_index=source_turn_index,
            workspace_id=workspace_id,
        )

    def list_board_nodes(self, session_uuid: str) -> list[AgentBoardNode]:
        session = self.get_session(session_uuid)
        if session is None or session.id is None:
            return []

        active_boardwork = self.get_active_boardwork(session.id)
        if active_boardwork is None or active_boardwork.id is None:
            return []

        statement = (
            select(AgentBoardNode)
            .where(
                AgentBoardNode.session_id == session.id,
                AgentBoardNode.boardwork_id == active_boardwork.id,
            )
            .order_by(AgentBoardNode.sort_order.asc(), AgentBoardNode.id.asc())
        )
        return list(self.db.exec(statement))

    def get_active_board_node(self, session_id: int | None) -> AgentBoardNode | None:
        if session_id is None:
            return None
        active_boardwork = self.get_active_boardwork(session_id)
        if active_boardwork is None or active_boardwork.id is None:
            return None
        statement = (
            select(AgentBoardNode)
            .where(
                AgentBoardNode.session_id == session_id,
                AgentBoardNode.boardwork_id == active_boardwork.id,
                AgentBoardNode.is_active.is_(True),
            )
            .order_by(AgentBoardNode.sort_order.desc(), AgentBoardNode.id.desc())
            .limit(1)
        )
        return self.db.exec(statement).first()

    def update_board_node(
        self,
        node: AgentBoardNode,
        *,
        summary: str | None = None,
        status: AgentBoardNodeStatus | None = None,
        trace_payload: dict[str, Any] | None = None,
        coach_note: str | None = None,
        is_active: bool | None = None,
    ) -> AgentBoardNode:
        if summary is not None:
            node.summary = summary.strip()
        if status is not None:
            node.status = status
        if trace_payload is not None:
            node.trace_payload = trace_payload
        if coach_note is not None:
            node.coach_note = coach_note.strip() if coach_note else None
        if is_active is not None:
            node.is_active = is_active
        node.update_time = datetime.utcnow()
        self.db.add(node)
        self.db.flush()
        return node

    def record_board_action(
        self,
        *,
        session_id: int | None,
        node_id: int | None,
        action_type: AgentBoardUpdateActionType,
        reason: str | None,
        source_turn_index: int,
    ) -> AgentBoardUpdateAction:
        if session_id is None:
            raise ValueError("session_id is required")

        action = AgentBoardUpdateAction(
            session_id=session_id,
            boardwork_id=self.get_active_boardwork(session_id).id if self.get_active_boardwork(session_id) else None,
            node_id=node_id,
            action_type=action_type,
            reason=reason.strip() if reason else None,
            source_turn_index=source_turn_index,
        )
        self.db.add(action)
        self.db.flush()
        return action

    def next_turn_index(self, session_id: int) -> int:
        statement = (
            select(AgentMessage.turn_index)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.turn_index.desc(), AgentMessage.id.desc())
            .limit(1)
        )
        latest_turn = self.db.exec(statement).first()
        if latest_turn is None:
            return 0
        return int(latest_turn) + 1

    def attach_images(self, message_id: int, image_ids: list[str], user_id: int) -> None:
        unique_ids = list(dict.fromkeys(image_ids))
        for image_id in unique_ids:
            self.db.add(
                AgentMessageImage(
                    message_id=message_id,
                    image_id=image_id,
                    user_id=user_id,
                )
            )
        self.db.flush()

    def get_session_history(
        self,
        session_uuid: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        after_message_id: int | None = None,
    ) -> list[dict[str, Any]]:
        session = self.get_session(session_uuid)
        if session is None or session.id is None:
            return []

        statement = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session.id,
                AgentMessage.visible_to_user.is_(True),
            )
            .order_by(AgentMessage.id.asc())
        )
        if after_message_id is not None:
            statement = statement.where(AgentMessage.id > after_message_id)
        messages = list(self.db.exec(statement))
        image_map = self._message_image_map([message.id for message in messages if message.id is not None])

        history: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {
                "message_id": message.id,
                "role": message.role.value,
                "message_type": message.message_type.value,
                "content": message.content,
                "turn_index": message.turn_index,
                "created_at": message.create_time,
            }
            if message.role == AgentMessageRole.USER:
                entry["images"] = image_map.get(message.id, [])
            history.append(entry)

        if limit is None:
            return history
        if after_message_id is not None:
            return history[:limit]

        total = len(history)
        if offset >= total:
            return []
        end = total - offset
        start = max(0, end - limit)
        return history[start:end]

    def get_session_history_meta(self, session_uuid: str) -> dict[str, Any]:
        session = self.get_session(session_uuid)
        if session is None or session.id is None:
            return {
                "count": 0,
                "max_message_id": None,
                "last_message_at": None,
            }

        count, max_message_id = self.db.exec(
            select(func.count(AgentMessage.id), func.max(AgentMessage.id)).where(
                AgentMessage.session_id == session.id,
                AgentMessage.visible_to_user.is_(True),
            )
        ).one()
        return {
            "count": int(count or 0),
            "max_message_id": max_message_id,
            "last_message_at": session.last_message_at,
        }

    def list_user_sessions(self, user_id: int, *, showcase_uuid: str | None = None) -> list[dict[str, Any]]:
        visible_to_user = AgentSession.user_id == user_id
        if showcase_uuid:
            visible_to_user = or_(visible_to_user, AgentSession.session_uuid == showcase_uuid)
        statement = (
            select(AgentSession)
            .where(
                visible_to_user,
                AgentSession.status != AgentSessionStatus.DELETED,
            )
            .order_by(
                func.coalesce(AgentSession.last_message_at, AgentSession.create_time).desc(),
                AgentSession.id.desc(),
            )
        )
        sessions = list(self.db.exec(statement))

        payload: list[dict[str, Any]] = []
        for session in sessions:
            preview = self._session_preview(session.id)
            payload.append(
                {
                    "session_id": session.session_uuid,
                    "user_id": str(session.user_id),
                    "is_shared_demo": bool(showcase_uuid and session.session_uuid == showcase_uuid),
                    "created_at": session.create_time,
                    "updated_at": session.update_time,
                    "last_message_at": session.last_message_at,
                    "preview": preview,
                    "title": display_session_title(session.title or DEFAULT_SESSION_TITLE, preview),
                    "source": session.source.value,
                    "problem_id": session.problem_id,
                    "submission_id": session.submission_id,
                    "course_id": session.course_id,
                    "class_id": session.class_id,
                    "exam_id": session.exam_id,
                }
            )
        return payload

    def list_user_session_groups(self, user_id: int, *, showcase_uuid: str | None = None) -> dict[str, list[dict[str, Any]]]:
        sessions = self.list_user_sessions(user_id, showcase_uuid=showcase_uuid)
        problem_groups: dict[int, dict[str, Any]] = {}
        standalone_sessions: list[dict[str, Any]] = []

        for item in sessions:
            problem_id = item.get("problem_id")
            activity = item.get("last_message_at") or item.get("created_at")
            if problem_id is None:
                standalone_sessions.append(item)
                continue
            group = problem_groups.setdefault(
                int(problem_id),
                {
                    "problem_id": int(problem_id),
                    "problem_title": self._problem_title(int(problem_id)),
                    "last_active_at": activity,
                    "sessions": [],
                },
            )
            group["sessions"].append(item)
            if _activity_sort_value(activity) > _activity_sort_value(group["last_active_at"]):
                group["last_active_at"] = activity

        ordered_groups = sorted(
            problem_groups.values(),
            key=lambda group: (_activity_sort_value(group["last_active_at"]), group["problem_id"]),
            reverse=True,
        )
        return {
            "problem_groups": ordered_groups,
            "standalone_sessions": standalone_sessions,
        }

    def _problem_title(self, problem_id: int) -> str:
        try:
            from sqlalchemy import text

            row = self.db.execute(
                text("SELECT title FROM problem WHERE id = :problem_id LIMIT 1"),
                {"problem_id": problem_id},
            ).first()
        except Exception:
            return f"题目 {problem_id}"
        return str(row[0]).strip() if row and row[0] else f"题目 {problem_id}"

    def rename_session(self, session_uuid: str, user_id: int, new_title: str) -> bool:
        session = self.get_session(session_uuid)
        if session is None or session.user_id != user_id:
            return False
        session.title = new_title.strip()
        self.db.add(session)
        self.db.flush()
        return True

    def _detach_message_references(self, message_ids: list[int]) -> None:
        if not message_ids:
            return

        self.db.exec(delete(AgentMessageImage).where(AgentMessageImage.message_id.in_(message_ids)))

        for run in self.db.exec(
            select(AgentTurnRun).where(
                (AgentTurnRun.user_message_id.in_(message_ids))
                | (AgentTurnRun.assistant_message_id.in_(message_ids))
            )
        ):
            if run.user_message_id in message_ids:
                run.user_message_id = None
            if run.assistant_message_id in message_ids:
                run.assistant_message_id = None
            self.db.add(run)

        for fact in self.db.exec(
            select(AgentSessionFact).where(AgentSessionFact.source_message_id.in_(message_ids))
        ):
            fact.source_message_id = None
            self.db.add(fact)

        for reference in self.db.exec(
            select(AgentDiagramReference).where(AgentDiagramReference.source_message_id.in_(message_ids))
        ):
            reference.source_message_id = None
            self.db.add(reference)

        for entry in self.db.exec(
            select(AgentBlackboardEntry).where(AgentBlackboardEntry.source_message_id.in_(message_ids))
        ):
            entry.source_message_id = None
            self.db.add(entry)

        for memory in self.db.exec(
            select(AgentMemoryItem).where(AgentMemoryItem.source_message_id.in_(message_ids))
        ):
            memory.source_message_id = None
            self.db.add(memory)

        self.db.flush()

    def _hide_empty_session(self, session: AgentSession) -> None:
        session.status = AgentSessionStatus.DELETED
        session.update_time = datetime.utcnow()
        session.last_message_at = None
        self.db.add(session)

    def delete_session(self, session_uuid: str, user_id: int) -> bool:
        session = self.get_session(session_uuid)
        if session is None or session.user_id != user_id or session.id is None:
            return False

        message_ids = list(
            self.db.exec(select(AgentMessage.id).where(AgentMessage.session_id == session.id)).all()
        )
        self._detach_message_references([message_id for message_id in message_ids if message_id is not None])
        self.db.exec(delete(AgentMessage).where(AgentMessage.session_id == session.id))
        self._hide_empty_session(session)
        self.db.commit()
        return True

    def undo_last_turn(self, session_uuid: str, user_id: int) -> bool:
        session = self.get_session(session_uuid)
        if session is None or session.user_id != user_id or session.id is None:
            return False

        statement = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session.id,
                AgentMessage.visible_to_user.is_(True),
            )
            .order_by(AgentMessage.turn_index.desc(), AgentMessage.id.desc())
        )
        messages = list(self.db.exec(statement))
        if not messages:
            return False

        last_turn_index = messages[0].turn_index
        turn_messages = list(
            self.db.exec(
                select(AgentMessage).where(
                    AgentMessage.session_id == session.id,
                    AgentMessage.turn_index == last_turn_index,
                )
            )
        )
        turn_message_ids = [message.id for message in turn_messages if message.id is not None]
        self._detach_message_references(turn_message_ids)
        for message in turn_messages:
            self.db.delete(message)

        remaining = list(
            self.db.exec(
                select(AgentMessage)
                .where(
                    AgentMessage.session_id == session.id,
                    AgentMessage.visible_to_user.is_(True),
                )
                .order_by(AgentMessage.id.desc())
            )
        )
        session.last_message_at = remaining[0].create_time if remaining else None
        session.update_time = datetime.utcnow()
        self.db.add(session)
        self.db.commit()
        return True

    def delete_message(self, message_id: int, user_id: int) -> bool:
        message = self.db.get(AgentMessage, message_id)
        if message is None:
            return False

        session = self.db.get(AgentSession, message.session_id)
        if session is None or session.user_id != user_id or session.id is None:
            return False

        self._detach_message_references([message_id])
        self.db.delete(message)
        self.db.flush()

        remaining = self.db.exec(
            select(AgentMessage.id).where(AgentMessage.session_id == session.id).limit(1)
        ).first()
        if remaining is None:
            self._hide_empty_session(session)

        self.db.commit()
        return True

    def delete_response_group(self, message_id: int, user_id: int) -> dict[str, Any]:
        """Remove one assistant turn's text, followup and diagrams in one transaction."""
        message = self.db.get(AgentMessage, message_id)
        if message is None or message.role != AgentMessageRole.ASSISTANT:
            return {"deleted": False}
        session = self.db.get(AgentSession, message.session_id)
        if session is None or session.user_id != user_id or session.id is None:
            return {"deleted": False}

        turn_index = int(message.turn_index)
        responses = list(self.db.exec(
            select(AgentMessage).where(
                AgentMessage.session_id == session.id,
                AgentMessage.turn_index == turn_index,
                AgentMessage.role == AgentMessageRole.ASSISTANT,
            ).order_by(AgentMessage.id)
        ))
        message_ids = [item.id for item in responses if item.id is not None]
        scenes = self.list_diagram_scenes_for_turn(session.id, turn_index)
        scene_ids = [scene.scene_uuid for scene in scenes]
        self._detach_message_references(message_ids)
        for job in self.db.exec(select(AgentDiagramJob).where(
            AgentDiagramJob.session_id == session.id,
            AgentDiagramJob.source_turn_index == turn_index,
        )):
            self.db.delete(job)
        self.db.flush()
        for scene in scenes:
            if scene.id is None:
                continue
            self.db.exec(delete(AgentDiagramReference).where(AgentDiagramReference.diagram_scene_id == scene.id))
            self.db.exec(delete(AgentDiagramStep).where(AgentDiagramStep.diagram_scene_id == scene.id))
            self.db.delete(scene)
        for response in responses:
            self.db.delete(response)
        self.db.flush()

        latest = self.db.exec(
            select(AgentMessage).where(
                AgentMessage.session_id == session.id,
                AgentMessage.visible_to_user.is_(True),
            ).order_by(AgentMessage.id.desc()).limit(1)
        ).first()
        if latest is None:
            self._hide_empty_session(session)
        else:
            session.last_message_at = latest.create_time
            session.update_time = datetime.utcnow()
            self.db.add(session)
        self.db.commit()
        return {"deleted": True, "message_ids": message_ids, "scene_ids": scene_ids}

    def delete_turns_from_message(self, message_id: int, user_id: int) -> dict[str, Any]:
        message = self.db.get(AgentMessage, message_id)
        if message is None:
            return {"deleted": False}

        session = self.db.get(AgentSession, message.session_id)
        if session is None or session.user_id != user_id or session.id is None:
            return {"deleted": False}

        start_turn_index = int(message.turn_index)
        messages = list(
            self.db.exec(
                select(AgentMessage).where(
                    AgentMessage.session_id == session.id,
                    AgentMessage.turn_index >= start_turn_index,
                )
            )
        )
        message_ids = [item.id for item in messages if item.id is not None]
        deleted_image_ids: list[str] = []
        if message_ids:
            refs = list(
                self.db.exec(
                    select(AgentMessageImage)
                    .where(AgentMessageImage.message_id.in_(message_ids))
                    .order_by(AgentMessageImage.id.asc())
                )
            )
            deleted_image_ids = list(dict.fromkeys(ref.image_id for ref in refs))
            self._detach_message_references(message_ids)
        for item in messages:
            self.db.delete(item)

        remaining = list(
            self.db.exec(
                select(AgentMessage)
                .where(
                    AgentMessage.session_id == session.id,
                    AgentMessage.visible_to_user.is_(True),
                )
                .order_by(AgentMessage.id.desc())
            )
        )
        if remaining:
            session.last_message_at = remaining[0].create_time
            session.update_time = datetime.utcnow()
            self.db.add(session)
        else:
            self._hide_empty_session(session)
        self.db.commit()
        return {
            "deleted": True,
            "session_uuid": session.session_uuid,
            "start_turn_index": start_turn_index,
            "deleted_image_ids": deleted_image_ids,
        }

    def _session_preview(self, session_id: int | None) -> str:
        if session_id is None:
            return ""
        statement = (
            select(AgentMessage.content)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.visible_to_user.is_(True),
                AgentMessage.message_type != AgentMessageType.SUMMARY,
                AgentMessage.message_type != AgentMessageType.DIAGRAM_CARD,
                AgentMessage.message_type != AgentMessageType.DIAGRAM_INLINE,
            )
            .order_by(AgentMessage.id.desc())
            .limit(1)
        )
        content = self.db.exec(statement).first() or ""
        return content[:60] + "..." if len(content) > 60 else content

    def _message_image_map(self, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not message_ids:
            return {}

        statement = (
            select(AgentMessageImage)
            .where(AgentMessageImage.message_id.in_(message_ids))
            .order_by(AgentMessageImage.id.asc())
        )
        refs = list(self.db.exec(statement))
        if not refs:
            return {}

        from app.models import ImageAsset

        assets = {
            asset.id: asset
            for asset in self.db.exec(select(ImageAsset).where(ImageAsset.id.in_([ref.image_id for ref in refs])))
        }

        image_map: dict[int, list[dict[str, Any]]] = {}
        for ref in refs:
            asset = assets.get(ref.image_id)
            if asset is None:
                continue
            image_map.setdefault(ref.message_id, []).append(
                {
                    "image_id": asset.id,
                    "url": asset.public_url,
                    "thumbnail_url": f"{asset.public_url}/thumbnail",
                    "filename": asset.filename,
                }
            )
        return image_map
