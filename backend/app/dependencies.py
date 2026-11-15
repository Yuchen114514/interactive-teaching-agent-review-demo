"""FastAPI dependencies used across routers."""
from redis.asyncio import Redis, from_url

from .agents.manager import AgentManager, manager
from .config import get_settings
from .database import get_session

_settings = get_settings()
_redis = from_url(_settings.redis_url, decode_responses=True)

async def get_redis() -> Redis:
    return _redis

def get_agent_manager() -> AgentManager:
    return manager

__all__ = ["get_session", "get_redis", "get_agent_manager"]
