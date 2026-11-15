"""Isolated anonymous identity for the English paper demo."""
from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

import jwt
from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.api_errors import ApiError
from app.config import Settings, get_settings
from app.database import get_session
from app.jwt_auth import get_java_compatible_signing_key
from app.models import User

router = APIRouter()
_lock = Lock()
_guest_attempts: dict[str, deque[float]] = defaultdict(deque)
_chat_attempts: dict[str, deque[float]] = defaultdict(deque)
DEMO_RECOVERY_COOKIE = "accoding_en_demo_recovery"
DEMO_RECOVERY_SECONDS = 365 * 24 * 3600


class DemoVisitRequest(BaseModel):
    previous_token: str | None = None


def _demo_recovery_key(settings: Settings) -> bytes:
    return hmac.new(
        get_java_compatible_signing_key(settings.token_secret),
        b"accoding-en-demo-recovery-v1",
        hashlib.sha256,
    ).digest()


def _issue_demo_tokens(user_id: int, settings: Settings) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    access = jwt.encode(
        {"id": int(user_id), "sub": str(user_id), "purpose": "demo_access", "exp": now + timedelta(hours=8)},
        get_java_compatible_signing_key(settings.token_secret),
        algorithm="HS256",
    )
    recovery = jwt.encode(
        {"id": int(user_id), "sub": str(user_id), "purpose": "demo_recovery", "exp": now + timedelta(seconds=DEMO_RECOVERY_SECONDS)},
        _demo_recovery_key(settings),
        algorithm="HS256",
    )
    return access, recovery


def _restore_demo_guest(
    db: Session,
    settings: Settings,
    *,
    recovery_token: str | None,
    previous_token: str | None,
) -> User | None:
    now = datetime.now(timezone.utc).timestamp()
    candidates = (
        (recovery_token, _demo_recovery_key(settings), "demo_recovery", True),
        (previous_token, get_java_compatible_signing_key(settings.token_secret), "demo_access", False),
    )
    for token, key, purpose, verify_exp in candidates:
        if not token:
            continue
        try:
            payload = jwt.decode(token, key, algorithms=["HS256"], options={"verify_exp": verify_exp})
            if payload.get("purpose") not in ({purpose, None} if purpose == "demo_access" else {purpose}):
                continue
            expiry = payload.get("exp")
            if not isinstance(expiry, (int, float)) or expiry < now - DEMO_RECOVERY_SECONDS:
                continue
            guest = db.get(User, int(payload["id"]))
            if guest and guest.username is None and guest.nickname == "Demo visitor":
                return guest
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            continue
    return None


def _demo_guest_response(request: Request, response: Response, guest: User, settings: Settings) -> dict:
    assert guest.id is not None
    token, recovery = _issue_demo_tokens(guest.id, settings)
    response.set_cookie(
        DEMO_RECOVERY_COOKIE,
        recovery,
        max_age=DEMO_RECOVERY_SECONDS,
        path="/en-demo",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return {
        "token": token,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        "visitor_id": guest.id,
    }


@lru_cache(maxsize=1)
def demo_problem_catalog() -> dict[int, dict]:
    catalog_path = Path(__file__).parents[2] / "demo" / "english_problems.json"
    return {int(problem["id"]): problem for problem in json.loads(catalog_path.read_text(encoding="utf-8"))}


def require_demo_problem_id(problem_id: int) -> int:
    if problem_id not in demo_problem_catalog():
        raise ApiError(status_code=404, code="PROBLEM_NOT_FOUND", message="Demo problem not found")
    return problem_id


def demo_agent_message(problem_id: int | None, user_message: str) -> str:
    """Supply the isolated English statement to the model without changing the visible user message."""
    if problem_id is None:
        return user_message
    problem = demo_problem_catalog().get(problem_id)
    if problem is None:
        require_demo_problem_id(problem_id)
    assert problem is not None
    examples = "\n".join(
        f"Example {index}: input {example['input']}; output {example['output']}; explanation {example.get('explanation', '')}"
        for index, example in enumerate(problem["examples"], 1)
    )
    return (
        f"English demo problem {problem_id}: {problem['title']}\n"
        f"Statement: {problem['statement']}\nInput: {problem['input']}\n"
        f"Output: {problem['output']}\nConstraints: {problem['constraints']}\n"
        f"{examples}\n\nStudent question: {user_message}"
    )


def ensure_demo_enabled(settings: Settings) -> None:
    if not settings.demo_mode:
        raise ApiError(status_code=404, code="NOT_FOUND", message="Not found")


def ensure_demo_agent_ready(settings: Settings) -> None:
    if settings.demo_mode and not settings.demo_agent_ready:
        raise ApiError(status_code=503, code="DEMO_AGENT_PENDING", message="The English Agent connection is being prepared")


def _limit_attempts(bucket: dict[str, deque[float]], key: str, limit: int, window: float) -> None:
    now = monotonic()
    with _lock:
        times = bucket[key]
        while times and now - times[0] >= window:
            times.popleft()
        if len(times) >= limit:
            raise ApiError(status_code=429, code="RATE_LIMIT", message="Please wait before trying again.")
        times.append(now)
        if len(bucket) > 10000:
            for stale_key in list(bucket):
                if not bucket[stale_key] or now - bucket[stale_key][-1] >= window:
                    del bucket[stale_key]


def limit_demo_chat(user_id: int) -> None:
    _limit_attempts(_chat_attempts, str(user_id), 12, 60)


@router.get("/status")
def demo_status(settings: Settings = Depends(get_settings)) -> dict:
    ensure_demo_enabled(settings)
    return {"agent_ready": settings.demo_agent_ready}


@router.post("/guest")
def create_demo_guest(
    request: Request,
    response: Response,
    visit: DemoVisitRequest | None = Body(default=None),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    ensure_demo_enabled(settings)
    guest = _restore_demo_guest(
        db,
        settings,
        recovery_token=request.cookies.get(DEMO_RECOVERY_COOKIE),
        previous_token=visit.previous_token if visit else None,
    )
    if guest is not None:
        return _demo_guest_response(request, response, guest, settings)
    peer = request.headers.get("X-Real-IP") if request.client and request.client.host in {"127.0.0.1", "::1"} else None
    peer = peer or (request.client.host if request.client else "unknown")
    _limit_attempts(_guest_attempts, peer, 10, 3600)
    guest = User(username=None, nickname="Demo visitor")
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return _demo_guest_response(request, response, guest, settings)


@router.post("/guest/restore")
def restore_demo_guest(
    request: Request,
    response: Response,
    visit: DemoVisitRequest | None = Body(default=None),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    ensure_demo_enabled(settings)
    guest = _restore_demo_guest(
        db,
        settings,
        recovery_token=request.cookies.get(DEMO_RECOVERY_COOKIE),
        previous_token=visit.previous_token if visit else None,
    )
    if guest is None:
        raise ApiError(status_code=401, code="DEMO_VISIT_NOT_FOUND", message="This demo visit cannot be restored")
    return _demo_guest_response(request, response, guest, settings)
