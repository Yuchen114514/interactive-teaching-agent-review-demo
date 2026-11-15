from __future__ import annotations

import time
from uuid import uuid4

from fastapi import Depends
from redis.asyncio import Redis
import logging

from app.api_errors import ApiError
from app.config import get_settings
from app.dependencies import get_redis

logger = logging.getLogger("uvicorn.error")


def _key(scope: str, user_id: str) -> str:
    return f"agent-rate-limit:{scope}:{user_id}"


async def _check_window(
    redis: Redis,
    *,
    scope: str,
    user_id: str,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> int | None:
    current = now or time.time()
    key = _key(scope, user_id)
    window_start = current - window_seconds
    try:
        await redis.zremrangebyscore(key, float("-inf"), window_start)
        count = await redis.zcard(key)
        if count >= limit:
            return max(1, int(window_seconds - (current - window_start)))

        member = f"{current}:{uuid4().hex}"
        await redis.zadd(key, {member: current})
        await redis.expire(key, window_seconds)
        return None
    except Exception as exc:
        logger.warning("Rate limit check failed for %s/%s: %s", scope, user_id, exc)
        return None


async def enforce_scope_rate_limits(
    redis: Redis,
    *,
    user_id: str,
    scope: str,
) -> None:
    settings = get_settings()
    if scope == "chat":
        limits = (
            ("chat_burst", settings.chat_burst_limit, settings.chat_burst_window_seconds),
            ("chat_window", settings.chat_window_limit, settings.chat_window_seconds),
        )
    else:
        limits = (
            ("image_burst", settings.image_burst_limit, settings.image_burst_window_seconds),
            ("image_window", settings.image_window_limit, settings.image_window_seconds),
        )

    for limit_scope, limit, window_seconds in limits:
        retry_after = await _check_window(
            redis,
            scope=limit_scope,
            user_id=user_id,
            limit=limit,
            window_seconds=window_seconds,
        )
        if retry_after is not None:
            raise ApiError(
                status_code=429,
                code="RATE_LIMITED",
                message="请求过于频繁，请稍后再试",
                details={
                    "scope": limit_scope,
                    "retry_after_seconds": retry_after,
                },
            )


def rate_limit_dependency(scope: str, user_dependency):
    async def _dependency(
        user_data: tuple[str, str] = Depends(user_dependency),
        redis: Redis = Depends(get_redis),
    ) -> None:
        user_id, _ = user_data
        await enforce_scope_rate_limits(redis, user_id=str(user_id), scope=scope)

    return _dependency
