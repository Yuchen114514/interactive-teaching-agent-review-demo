"""Runtime configuration for the Agno backend."""
from functools import lru_cache
from typing import Literal
import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-level settings loaded from env/.env."""
    app_env: Literal["dev", "prod", "test"] = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 9001
    database_url: str = ""
    redis_url: str = "redis://localhost:6380/0"

    # Internal LLM Config
    local_llm_base_url: str = "http://127.0.0.1:8080/v1"
    local_llm_api_key: str = "not-set"
    local_llm_model: str = "local-model"
    llm_temperature: float = 0.2

    # External LLM Config
    llm_type: Literal["local", "api"] = "api"
    external_llm_base_url: str = "https://api.openai.com/v1"
    external_llm_api_key: str = ""
    external_llm_model: str = "doubao-seed-1-6-250615"
    external_llm_timeout_seconds: float = 20.0
    external_llm_max_retries: int = 0

    max_context_messages: int = 8
    max_memory_results: int = 5
    teaching_agent_providers_enabled: bool = False
    teaching_agent_provider_backend: Literal["disabled", "agno"] = "disabled"
    teaching_agent_provider_agents: str = ""
    teaching_agent_provider_timeout_seconds: float = 40.0
    teaching_agent_provider_max_tokens: int = 1200
    teaching_agent_provider_json_mode: bool = False
    teaching_agent_provider_extra_body_json: str = ""
    teaching_supervisor_provider_timeout_seconds: float = 0.25
    teaching_optional_specialist_result_timeout_seconds: float = 0.25
    teaching_memory_consolidation_timeout_seconds: float = 0.5
    teaching_v2_require_model_provider: bool = True
    teaching_algorithm_taxonomy_path: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    token_secret: str = ""
    frontend_origin: str = "*"
    media_upload_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    media_public_base_url: str = "http://localhost:9001"
    media_max_upload_bytes: int = 10 * 1024 * 1024
    chat_burst_limit: int = 8
    chat_burst_window_seconds: int = 10
    chat_window_limit: int = 30
    chat_window_seconds: int = 60
    image_burst_limit: int = 3
    image_burst_window_seconds: int = 10
    image_window_limit: int = 10
    image_window_seconds: int = 60

    # MCP Settings
    mcp_enabled: bool = True
    mcp_mode: Literal["http", "stdio"] = "http"
    mcp_http_url: str = "http://127.0.0.1:3000/mcp"
    mcp_http_proxy: str = ""
    submission_demo_mode: bool = False
    demo_mode: bool = False
    demo_agent_ready: bool = False
    demo_showcase_session_uuid: str = ""
    # Use wrapper script to filter extraneous logs
    # Script is in backend/mcp_wrapper.sh
    # This file is backend/app/config.py -> dirname -> backend/app -> dirname -> backend
    mcp_stdio_command: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_wrapper.sh")
    mcp_stdio_args: list[str] = ["npx", "-y", "accoding-mcp-server-test-new@0.1.0"]
    # Path to local nodejs bin directory to prepend to PATH (e.g. for npx/node)
    mcp_node_bin_dir: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        if not (self.database_url or "").strip():
            raise ValueError("DATABASE_URL is required")
        if self.demo_mode and not self.database_url.strip().startswith("sqlite"):
            raise ValueError("English demo requires an isolated SQLite database")
        if self.app_env == "prod" and not self.demo_mode and self.database_url.strip().startswith("sqlite"):
            raise ValueError("DATABASE_URL must point to the remote OJ MySQL database")
        if self.llm_type == "api" and not (self.external_llm_api_key or "").strip():
            raise ValueError("EXTERNAL_LLM_API_KEY is required when LLM_TYPE=api")
        if not (self.token_secret or "").strip():
            raise ValueError("TOKEN_SECRET is required")
        return self

    @property
    def cors_origins(self) -> list[str]:
        raw_origins = (self.frontend_origin or "").strip()
        if not raw_origins:
            return []
        parsed = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        if "*" in parsed:
            return ["*"]
        return parsed


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance to avoid re-parsing .env."""
    return Settings()
