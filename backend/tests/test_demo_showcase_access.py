import os

os.environ.setdefault("LLM_TYPE", "api")
os.environ.setdefault("EXTERNAL_LLM_API_KEY", "test-key")
os.environ.setdefault("TOKEN_SECRET", "test-secret")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agent_store import AgentStore
from app.config import Settings
from app.models import AgentMessage, AgentSession, User
from app.models import AgentProblemWorkspace
from app.main import app
from app.database import get_session
from app.agents.router import get_current_user_id_and_token
from app.api.routes.teaching_v2 import get_v2_user_id
from app.dependencies import get_agent_manager
from app.config import get_settings


def test_showcase_is_readable_to_all_demo_visitors_but_other_sessions_remain_private():
    from app.session_access import can_read_agent_session, can_write_agent_session

    settings = Settings(
        demo_mode=True,
        demo_showcase_session_uuid="showcase-example",
        database_url="sqlite:///:memory:",
        token_secret="test-secret",
        external_llm_api_key="test-key",
    )
    showcase = AgentSession(session_uuid="showcase-example", user_id=1)
    visitor_a = AgentSession(session_uuid="visitor-a", user_id=2)
    visitor_b = AgentSession(session_uuid="visitor-b", user_id=3)
    assert can_read_agent_session(showcase, 2, settings)
    assert not can_write_agent_session(showcase, 2, settings)
    assert can_read_agent_session(visitor_a, 2, settings)
    assert can_write_agent_session(visitor_a, 2, settings)
    assert not can_read_agent_session(visitor_b, 2, settings)

    production = settings.model_copy(update={"demo_mode": False})
    assert not can_read_agent_session(showcase, 2, production)


def test_demo_session_listing_adds_only_the_shared_example():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[User.__table__, AgentSession.__table__, AgentMessage.__table__])
    with Session(engine) as db:
        for user_id in (1, 2, 3):
            db.add(User(id=user_id, nickname="Demo visitor"))
        db.add(AgentSession(session_uuid="showcase-example", user_id=1))
        db.add(AgentSession(session_uuid="visitor-a", user_id=2))
        db.add(AgentSession(session_uuid="visitor-b", user_id=3))
        db.commit()
        sessions = AgentStore(db).list_user_sessions(2, showcase_uuid="showcase-example")
        assert {item["session_id"] for item in sessions} == {"showcase-example", "visitor-a"}
        assert next(item for item in sessions if item["session_id"] == "showcase-example")["is_shared_demo"] is True
        assert next(item for item in sessions if item["session_id"] == "visitor-a")["is_shared_demo"] is False


def test_history_and_workspace_routes_expose_only_the_shared_example(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for user_id in (1, 2, 3):
            db.add(User(id=user_id, nickname="Demo visitor"))
        db.add(AgentSession(id=1, session_uuid="showcase-example", user_id=1))
        db.add(AgentSession(id=2, session_uuid="visitor-a", user_id=2))
        db.add(AgentSession(id=3, session_uuid="visitor-b", user_id=3))
        db.add(AgentProblemWorkspace(id=1, session_id=1, user_id=1, title="Example board"))
        db.add(AgentProblemWorkspace(id=2, session_id=3, user_id=3, title="Private board"))
        db.commit()

    class FakeManager:
        def get_session_history(self, *args, **kwargs):
            return []

        def get_session_history_meta(self, *args, **kwargs):
            return {"count": 0}

    settings = Settings(
        demo_mode=True,
        demo_showcase_session_uuid="showcase-example",
        database_url="sqlite://",
        token_secret="test-secret",
        external_llm_api_key="test-key",
    )

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user_id_and_token] = lambda: ("2", "test-token")
    app.dependency_overrides[get_v2_user_id] = lambda: 2
    app.dependency_overrides[get_agent_manager] = FakeManager
    monkeypatch.setattr("app.agents.router.session_context", lambda: Session(engine))
    monkeypatch.setattr("app.agents.router.settings", settings)
    try:
        client = TestClient(app)
        assert client.get("/api/agents/history", params={"session_id": "showcase-example"}).status_code == 200
        assert client.get("/api/agents/history", params={"session_id": "visitor-b"}).status_code == 404
        assert client.get("/api/agents/v2/sessions/showcase-example/workspaces").status_code == 200
        assert client.get("/api/agents/v2/workspaces/1/blackboard").status_code == 200
        assert client.get("/api/agents/v2/sessions/visitor-b/workspaces").status_code == 404
        assert client.get("/api/agents/v2/workspaces/2/blackboard").status_code == 404
        assert client.get("/api/agents/v2/users/3/memory").status_code == 404
        assert client.patch("/api/agents/v2/workspaces/1/guidance-depth", json={"guidance_depth": "direct"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
