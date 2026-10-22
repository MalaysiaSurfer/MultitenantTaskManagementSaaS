import hashlib
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from redis.exceptions import BusyLoadingError, ClusterDownError, MasterDownError, ReadOnlyError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings

logger = logging.getLogger(__name__)
REDIS_UNAVAILABLE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    BusyLoadingError,
    ClusterDownError,
    MasterDownError,
    ReadOnlyError,
    OSError,
)

_RESERVE_ATTEMPT = """
local pair_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ip_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if pair_count >= tonumber(ARGV[1]) then
    return {0, redis.call('TTL', KEYS[1])}
end
if ip_count >= tonumber(ARGV[2]) then
    return {0, redis.call('TTL', KEYS[2])}
end
pair_count = redis.call('INCR', KEYS[1])
if pair_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
ip_count = redis.call('INCR', KEYS[2])
if ip_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[3]) end
return {1, pair_count, ip_count}
"""

_COMPLETE_SUCCESS = """
redis.call('DEL', KEYS[1])
local ip_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if ip_count > 0 then
    ip_count = redis.call('DECR', KEYS[2])
    if ip_count <= 0 then redis.call('DEL', KEYS[2]) end
end
return ip_count
"""


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def login_rate_limit_keys(ip: str, email: str) -> tuple[str, str]:
    normalized_email = email.strip().lower()
    email_digest = hashlib.sha256(normalized_email.encode()).hexdigest()
    return f"login:ip:{ip}", f"login:pair:{ip}:{email_digest}"


async def login_rate_limit(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    ip = request.client.host if request.client else "unknown"
    ip_key, pair_key = login_rate_limit_keys(ip, form.username)
    try:
        allowed, *values = await redis.eval(
            _RESERVE_ATTEMPT,
            2,
            pair_key,
            ip_key,
            settings.login_rate_limit_attempts,
            settings.login_rate_limit_attempts * settings.login_rate_limit_ip_multiplier,
            settings.login_rate_limit_window_seconds,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts",
                headers={"Retry-After": str(max(int(values[0]), 1))},
            )
    except HTTPException:
        raise
    except REDIS_UNAVAILABLE_ERRORS:
        logger.warning("Login rate limiter unavailable; allowing request", exc_info=True)


async def complete_login_success(redis: Redis, request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    ip_key, pair_key = login_rate_limit_keys(ip, email)
    try:
        await redis.eval(_COMPLETE_SUCCESS, 2, pair_key, ip_key)
    except REDIS_UNAVAILABLE_ERRORS:
        logger.warning("Could not clear successful login reservation", exc_info=True)
