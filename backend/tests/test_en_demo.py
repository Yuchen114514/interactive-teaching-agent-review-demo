from pathlib import Path
import json
import pytest

from app.teaching.v3_visible_stream import _visible_stream_request_payload
from app.config import Settings


def test_demo_recovery_restores_the_same_guest_without_creating_another_user():
    import jwt
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, SQLModel, create_engine, select
    from app.en_demo import _issue_demo_tokens, _restore_demo_guest
    from app.jwt_auth import decode_java_compatible_jwt, get_java_compatible_signing_key
    from app.models import User

    settings = Settings(demo_mode=True, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key")
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[User.__table__])
    with Session(engine) as db:
        guest = User(username=None, nickname="Demo visitor")
        db.add(guest)
        db.commit()
        db.refresh(guest)
        access_token, recovery_token = _issue_demo_tokens(guest.id, settings)
        assert decode_java_compatible_jwt(access_token, settings.token_secret)["id"] == guest.id
        with pytest.raises(jwt.InvalidSignatureError):
            decode_java_compatible_jwt(recovery_token, settings.token_secret)
        assert _restore_demo_guest(db, settings, recovery_token=recovery_token, previous_token=None).id == guest.id

        expired_access = jwt.encode(
            {"id": guest.id, "sub": str(guest.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            get_java_compatible_signing_key(settings.token_secret),
            algorithm="HS256",
        )
        assert _restore_demo_guest(db, settings, recovery_token=None, previous_token=expired_access).id == guest.id
        assert _restore_demo_guest(db, settings, recovery_token="invalid", previous_token=None) is None
        assert _restore_demo_guest(db, settings, recovery_token=None, previous_token=None) is None
        assert len(db.exec(select(User)).all()) == 1


def test_demo_guest_cookie_restores_one_browser_without_sharing_history_with_another():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlalchemy.pool import StaticPool
    from app.config import get_settings
    from app.database import get_session
    from app.en_demo import router
    from app.models import User

    settings = Settings(demo_mode=True, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[User.__table__])

    def test_session():
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/en-demo/api/en-demo")
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as first_browser, TestClient(app) as second_browser:
        first_response = first_browser.post("/en-demo/api/en-demo/guest")
        assert "httponly" in first_response.headers["set-cookie"].lower()
        assert "path=/en-demo" in first_response.headers["set-cookie"].lower()
        first = first_response.json()
        restored = first_browser.post("/en-demo/api/en-demo/guest/restore").json()
        second = second_browser.post("/en-demo/api/en-demo/guest").json()
        assert first["visitor_id"] == restored["visitor_id"]
        assert first["visitor_id"] != second["visitor_id"]
        with Session(engine) as db:
            assert len(db.exec(select(User)).all()) == 2


def test_demo_catalog_uses_sequential_reserved_ids_and_english_content():
    catalog = json.loads((Path(__file__).parents[2] / "demo" / "english_problems.json").read_text())
    ids = [entry["id"] for entry in catalog]
    assert len(ids) >= 3
    assert ids == list(range(ids[0], ids[0] + len(ids)))
    assert ids[0] >= 1_000_000
    for entry in catalog:
        assert entry["title"] and entry["statement"] and entry["examples"]
        assert all(ord(char) < 0x2E80 for char in json.dumps(entry, ensure_ascii=False))


def test_demo_variant_statements_hide_the_named_data_structures():
    catalog = json.loads((Path(__file__).parents[2] / "demo" / "english_problems.json").read_text())
    variants = [entry for entry in catalog if entry["id"] in {1000004, 1000005}]
    assert len(variants) == 2
    for entry in variants:
        visible_text = json.dumps(entry, ensure_ascii=False).lower()
        assert not any(name in visible_text for name in ("hanoi", "red-black", "red black", "汉诺塔", "红黑树"))
        assert entry["examples"] and all(example["input"] and example["output"] for example in entry["examples"])


def test_demo_visible_stream_prompt_requires_english():
    settings = Settings(demo_mode=True, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key")
    payload = _visible_stream_request_payload(settings, {"guidance_contract": {"guidance_depth": "direct"}})
    system_prompt = payload["messages"][0]["content"]
    assert "English" in system_prompt
    assert "只输出给学生看的简洁中文回答" not in system_prompt


def test_demo_guest_endpoint_disabled_by_default():
    from app.en_demo import ensure_demo_enabled
    try:
        ensure_demo_enabled(Settings(demo_mode=False, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("guest endpoint must be disabled outside demo mode")


def test_demo_rejects_non_catalog_problem_ids():
    from app.en_demo import require_demo_problem_id
    assert require_demo_problem_id(1000002) == 1000002
    assert require_demo_problem_id(1000004) == 1000004
    assert require_demo_problem_id(1000005) == 1000005
    try:
        require_demo_problem_id(42)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("production problem IDs must not resolve in the demo")


def test_demo_agent_receives_full_statement_without_replacing_student_question():
    from app.en_demo import demo_agent_message
    question = "Can you give me a hint?"
    context = demo_agent_message(1000001, question)
    assert "Two Sum" in context
    assert "Constraints:" in context
    assert "Example 1:" in context
    assert context.endswith(f"Student question: {question}")
    assert demo_agent_message(None, question) == question


def test_demo_does_not_allow_dev_header_auth_bypass():
    import asyncio
    from app.api.routes.teaching_v3 import get_v3_user_id
    settings = Settings(demo_mode=True, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key")
    try:
        asyncio.run(get_v3_user_id(x_test_user_id=19, x_accoding_session=None, settings=settings))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("demo users must have a signed visitor token")


def test_demo_requires_isolated_sqlite_database():
    try:
        Settings(demo_mode=True, app_env="prod", database_url="mysql+pymysql://user:pass@localhost/oj", token_secret="test-secret", external_llm_api_key="test-key")
    except ValueError as exc:
        assert "isolated SQLite" in str(exc)
    else:
        raise AssertionError("demo must never share the production OJ database")


def test_demo_agent_rejects_turns_until_model_is_verified():
    from app.en_demo import ensure_demo_agent_ready
    settings = Settings(demo_mode=True, demo_agent_ready=False, database_url="sqlite:///:memory:", token_secret="test-secret", external_llm_api_key="test-key")
    try:
        ensure_demo_agent_ready(settings)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("unverified model cannot serve demo turns")
