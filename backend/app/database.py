"""Database utilities using SQLModel."""
from contextlib import contextmanager
from datetime import datetime
import sqlite3

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import inspect, text

from .config import get_settings
from .models import (
    AgentAcceptanceRun,
    AgentBlackboardEntry,
    AgentBoardNode,
    AgentBoardUpdateAction,
    AgentBoardwork,
    AgentDiagramJob,
    AgentDiagramReference,
    AgentDiagramScene,
    AgentDiagramStep,
    AgentExecutionTrace,
    AgentMemoryItem,
    AgentMessage,
    AgentMessageImage,
    AgentProblemWorkspace,
    AgentProviderCallTrace,
    AgentSession,
    AgentSessionFact,
    AgentTeachingEvent,
    AgentTeachingPlan,
    AgentTeachingStep,
    AgentToolCallTrace,
    AgentTurnRun,
    AgentTurnRunEvent,
    AgentUserMemorySettings,
    AgentUserPreferences,
    ExamProblemAnswerTiming,
    ImageAsset,
    User,
)

_settings = get_settings()


def build_engine(database_url: str, *, app_env: str):
    engine_kwargs = {"echo": app_env == "dev", "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": 30,
        }
    engine = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        _configure_sqlite_engine(engine, database_url)
    return engine


def _configure_sqlite_engine(engine, database_url: str) -> None:
    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        _set_sqlite_pragmas(dbapi_connection, database_url)


def _set_sqlite_pragmas(dbapi_connection, database_url: str) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout = 30000")
        if database_url not in {"sqlite://", "sqlite:///:memory:"}:
            try:
                cursor.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


_engine = build_engine(_settings.database_url, app_env=_settings.app_env)


def init_db() -> None:
    """Create all application tables automatically in dev/test environments."""

    SQLModel.metadata.create_all(_engine)
    _ensure_agent_boardwork_columns()
    _ensure_agent_user_preferences_columns()


def init_agent_db() -> None:
    """Create the Agent schema used by the application."""

    SQLModel.metadata.create_all(
        _engine,
        tables=[
            AgentSession.__table__,
            AgentMessage.__table__,
            AgentMessageImage.__table__,
            AgentDiagramScene.__table__,
            AgentDiagramJob.__table__,
            AgentDiagramStep.__table__,
            AgentDiagramReference.__table__,
            AgentSessionFact.__table__,
            ExamProblemAnswerTiming.__table__,
            ImageAsset.__table__,
            AgentBoardwork.__table__,
            AgentBoardNode.__table__,
            AgentBoardUpdateAction.__table__,
            AgentProblemWorkspace.__table__,
            AgentTeachingPlan.__table__,
            AgentTeachingStep.__table__,
            AgentTeachingEvent.__table__,
            AgentTurnRun.__table__,
            AgentTurnRunEvent.__table__,
            AgentBlackboardEntry.__table__,
            AgentMemoryItem.__table__,
            AgentUserMemorySettings.__table__,
            AgentUserPreferences.__table__,
            AgentExecutionTrace.__table__,
            AgentAcceptanceRun.__table__,
            AgentProviderCallTrace.__table__,
            AgentToolCallTrace.__table__,
            User.__table__,
        ],
    )
    _ensure_agent_boardwork_columns()
    _ensure_agent_turn_run_columns()
    _ensure_agent_user_preferences_columns()
    if _settings.app_env in {"dev", "test"}:
        _seed_local_test_users()


def get_engine():
    return _engine


def _ensure_agent_boardwork_columns() -> None:
    inspector = inspect(_engine)
    existing_tables = set(inspector.get_table_names())
    if "agent_boardworks" not in existing_tables:
        AgentBoardwork.__table__.create(_engine, checkfirst=True)

    column_specs = {
        "agent_board_nodes": {
            "boardwork_id": "INTEGER",
            "teaching_step_id": "INTEGER",
            "trace_payload": "TEXT",
        },
        "agent_board_update_actions": {
            "boardwork_id": "INTEGER",
        },
        "agent_diagram_scenes": {
            "workspace_id": "INTEGER",
            "teaching_step_id": "INTEGER",
            "parent_scene_uuid": "VARCHAR(128)",
            "base_step_id": "VARCHAR(128)",
            "inherit_mode": "VARCHAR(32) DEFAULT 'new'",
            "layout": "VARCHAR(32) DEFAULT 'freeform'",
            "metadata_json": "TEXT",
        },
        "agent_boardworks": {
            "workspace_id": "INTEGER",
        },
    }

    with _engine.begin() as connection:
        refreshed = inspect(connection)
        for table_name, additions in column_specs.items():
            if table_name not in existing_tables and table_name not in refreshed.get_table_names():
                continue
            existing_columns = {column["name"] for column in refreshed.get_columns(table_name)}
            for column_name, column_sql in additions.items():
                if column_name in existing_columns:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


def _ensure_agent_turn_run_columns() -> None:
    inspector = inspect(_engine)
    if "agent_turn_runs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_turn_runs")}
    with _engine.begin() as connection:
        if "client_request_id" not in columns:
            connection.execute(text("ALTER TABLE agent_turn_runs ADD COLUMN client_request_id VARCHAR(128)"))
            connection.execute(
                text("UPDATE agent_turn_runs SET client_request_id = run_uuid WHERE client_request_id IS NULL")
            )
        index_names = {index["name"] for index in inspect(connection).get_indexes("agent_turn_runs")}
        if "uq_agent_turn_run_session_request" not in index_names:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_agent_turn_run_session_request "
                    "ON agent_turn_runs (session_id, client_request_id)"
                )
            )


def _ensure_agent_user_preferences_columns() -> None:
    inspector = inspect(_engine)
    if "agent_user_preferences" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_user_preferences")}
    if "font_size" not in columns:
        with _engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE agent_user_preferences ADD COLUMN font_size VARCHAR(16) NOT NULL DEFAULT 'small'")
            )



def _seed_local_test_users() -> None:
    seed_users = [
        User(id=1, username="test-user-1@oj.local", nickname="测试用户1", create_time=datetime.utcnow(), update_time=datetime.utcnow()),
        User(id=2, username="test-user-2@oj.local", nickname="测试用户2", create_time=datetime.utcnow(), update_time=datetime.utcnow()),
        User(id=4, username="test-user-4@oj.local", nickname="Test user 4", create_time=datetime.utcnow(), update_time=datetime.utcnow()),
        User(id=19, username="test-user-19@oj.local", nickname="Test user 19", create_time=datetime.utcnow(), update_time=datetime.utcnow()),
    ]

    with Session(_engine) as session:
        existing_ids = set(session.exec(select(User.id)).all())
        created = False
        for user in seed_users:
            if user.id in existing_ids:
                continue
            session.add(user)
            created = True
        if created:
            session.commit()


@contextmanager
def session_context() -> Session:
    """Synchronous session context manager."""

    with Session(_engine) as session:
        yield session


def get_session() -> Session:
    """FastAPI dependency for a scoped session."""

    with Session(_engine) as session:
        yield session
