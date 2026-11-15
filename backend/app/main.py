"""FastAPI entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, status
from sqlmodel import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .agents.router import router as agent_router
from .api_errors import build_error_payload
from .api.routes.teaching_v2 import router as teaching_v2_router
from .api.routes.teaching_v3 import router as teaching_v3_router
from .config import get_settings
from .database import get_engine, init_agent_db
from .en_demo import router as en_demo_router
from .models import User
from sqlmodel import SQLModel
from sqlalchemy import text
from .en_demo import demo_problem_catalog
from .repositories.teaching import TeachingRepository

settings = get_settings()

app = FastAPI(title="Agno Student Agent API", version="0.1.0")
origins = settings.cors_origins or ["*"]
allow_credentials = origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Pydantic's input/ctx and exception text can include full conversations or
    # credentials. Return only field locations and error categories.
    errors = [
        {"loc": error.get("loc", ()), "type": error.get("type", "value_error"), "msg": "字段值不合法"}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=build_error_payload(
            "VALIDATION_ERROR",
            "请求参数不合法",
            {"errors": errors},
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
    else:
        content = build_error_payload("HTTP_ERROR", str(exc.detail), {})
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

app.include_router(agent_router, prefix="/api/agents", tags=["agents"])
app.include_router(teaching_v2_router, prefix="/api/agents/v2", tags=["agents-v2"])
app.include_router(teaching_v3_router, prefix="/api/agents/v3", tags=["agents-v3"])
if settings.demo_mode:
    app.include_router(en_demo_router, prefix="/api/en-demo", tags=["en-demo"])


@app.on_event("startup")
def on_startup() -> None:
    Path(settings.media_upload_dir).mkdir(parents=True, exist_ok=True)
    init_agent_db()
    if settings.demo_mode:
        SQLModel.metadata.create_all(get_engine(), tables=[User.__table__])
        with get_engine().begin() as connection:
            connection.execute(text("CREATE TABLE IF NOT EXISTS problem (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"))
            for problem in demo_problem_catalog().values():
                connection.execute(
                    text("INSERT OR REPLACE INTO problem (id, title) VALUES (:id, :title)"),
                    {"id": problem["id"], "title": problem["title"]},
                )
    with Session(get_engine()) as db:
        TeachingRepository(db).interrupt_stale_turn_runs()
        db.commit()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
