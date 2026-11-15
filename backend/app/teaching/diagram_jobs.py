"""Persist and reconcile background diagram work for a conversation turn."""
from __future__ import annotations

from datetime import datetime
import hashlib

from sqlmodel import Session, select

from app.models import AgentDiagramJob


def diagram_job_key(session_uuid: str, source_turn_index: int, diagram_goal: str) -> str:
    return hashlib.sha256(f"{session_uuid}\0{source_turn_index}\0{diagram_goal}".encode()).hexdigest()


def start_diagram_job(
    db: Session, *, session_uuid: str, session_id: int, user_id: int,
    run_uuid: str | None, workspace_id: int | None, source_message_id: int | None,
    source_turn_index: int, diagram_goal: str,
) -> AgentDiagramJob:
    key = diagram_job_key(session_uuid, source_turn_index, diagram_goal)
    job = db.exec(select(AgentDiagramJob).where(AgentDiagramJob.idempotency_key == key)).first()
    if job is None:
        job = AgentDiagramJob(
            job_uuid="diagram-job-" + key[:24], idempotency_key=key, run_uuid=run_uuid,
            user_id=user_id, session_id=session_id, workspace_id=workspace_id,
            source_message_id=source_message_id, source_turn_index=source_turn_index,
            diagram_goal=diagram_goal,
        )
    else:
        if job.user_id != user_id or job.session_id != session_id:
            raise ValueError("绘图任务归属不匹配")
        job.run_uuid = run_uuid
        job.workspace_id = workspace_id
        job.source_message_id = source_message_id
        job.status = "running"
        job.scene_uuid = None
        job.error_code = None
    job.update_time = datetime.utcnow()
    db.add(job)
    db.flush()
    return job


def finish_diagram_job(
    db: Session, job: AgentDiagramJob, *, status: str,
    scene_uuid: str | None = None, producer_version: str | None = None,
    renderer_version: str | None = None, error_code: str | None = None,
) -> None:
    if status not in {"ready", "failed", "stopped"}:
        raise ValueError("绘图任务状态无效")
    if job.status == "stopped" and status != "stopped":
        return
    job.status = status
    job.scene_uuid = scene_uuid
    job.producer_version = producer_version
    job.renderer_version = renderer_version
    job.error_code = error_code
    job.update_time = datetime.utcnow()
    db.add(job)
