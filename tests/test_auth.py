import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.api.rate_limit import login_rate_limit_keys
from app.core.config import settings
from app.core.security import create_token
from app.db.base import Organization, User
from sqlalchemy import delete, func, select

from tests.helpers import TEST_CLIENT_IP, auth_headers, login, register_user


@pytest.mark.asyncio
async def test_health_and_register_create_owner(client):
    assert (await client.get("/health")).status_code == 200
    response = await register_user(client)
    assert response.status_code == 201
    assert response.json()["user"]["email"] == "user@example.com"
    assert response.json()["role"] == "owner"


@pytest.mark.asyncio
async def test_duplicate_registration_creates_no_orphan_organization(client, session_factory):
    first = await register_user(client, "same@example.com", "Original org")
    assert first.status_code == 201
    async with session_factory() as db:
        count_before = await db.scalar(select(func.count()).select_from(Organization))
        users_before = await db.scalar(select(func.count()).select_from(User))
    assert count_before == users_before == 1
    assert (await register_user(client, "same@example.com", "Duplicate org")).status_code == 409
    assert (
        await register_user(client, "SAME@example.com", "Duplicate case org")
    ).status_code == 409
    async with session_factory() as db:
        count_after = await db.scalar(select(func.count()).select_from(Organization))
        users_after = await db.scalar(select(func.count()).select_from(User))
        assert count_after == count_before == 1
        assert users_after == users_before == 1


@pytest.mark.asyncio
async def test_register_rejects_short_password(client):
    response = await client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "short", "organization_name": "Org"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_normalizes_username(client):
    registration = await register_user(client, "person@example.com", "Person org")
    assert registration.status_code == 201
    response = await login(client, "  PERSON@EXAMPLE.COM  ")
    assert response.status_code == 200
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_login_wrong_password_and_unknown_email_have_same_response(client):
    await register_user(client, "person@example.com", "Person org")
    wrong_password = await login(client, "person@example.com", "incorrect password")
    unknown_email = await login(client, "missing@example.com")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


@pytest.mark.asyncio
async def test_inactive_user_cannot_login_or_use_existing_token(
    client, user_factory, session_factory
):
    actor = await user_factory("inactive@example.com", "Inactive org")
    access_token = actor.token
    async with session_factory() as db:
        user = await db.get(User, actor.user_id)
        user.is_active = False
        await db.commit()
    assert (await login(client, "inactive@example.com")).status_code == 401
    assert (await client.get("/auth/me", headers=auth_headers(access_token))).status_code == 401
    assert (await client.get("/projects", headers=actor.headers)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("token_kind", ["expired", "refresh", "garbage"])
async def test_access_endpoint_rejects_invalid_access_tokens(client, user_factory, token_kind):
    actor = await user_factory("tokens@example.com", "Tokens")
    if token_kind == "expired":
        token = create_token(actor.user_id, "access", timedelta(seconds=-1))
    elif token_kind == "refresh":
        token = create_token(actor.user_id, "refresh", timedelta(minutes=5))
    else:
        token = "not.a.jwt"
    assert (await client.get("/auth/me", headers=auth_headers(token))).status_code == 401


@pytest.mark.asyncio
async def test_access_endpoint_requires_token(client):
    assert (await client.get("/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_signature_and_subject_validation_have_valid_token_positive_control(
    client, user_factory
):
    actor = await user_factory("claims@example.com", "Claims")
    assert (await client.get("/auth/me", headers=auth_headers(actor.token))).status_code == 200
    claims = {
        "sub": str(actor.user_id),
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "jti": "test-jti",
    }
    valid_claims = jwt.encode(claims, settings.secret_key, algorithm="HS256")
    invalid_subject = jwt.encode(claims | {"sub": "abc"}, settings.secret_key, algorithm="HS256")
    invalid_signature = jwt.encode(
        claims, "another-signing-key-that-is-long-enough", algorithm="HS256"
    )
    assert (await client.get("/auth/me", headers=auth_headers(valid_claims))).status_code == 200
    assert (await client.get("/auth/me", headers=auth_headers(invalid_subject))).status_code == 401
    assert (
        await client.get("/auth/me", headers=auth_headers(invalid_signature))
    ).status_code == 401


@pytest.mark.asyncio
async def test_refresh_issues_a_working_token_pair(client, user_factory):
    await user_factory("refresh@example.com", "Refresh")
    refresh = (await login(client, "refresh@example.com")).json()["refresh_token"]
    pair = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert pair.status_code == 200
    assert (
        await client.get("/auth/me", headers=auth_headers(pair.json()["access_token"]))
    ).status_code == 200


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(client, user_factory):
    actor = await user_factory("access-refresh@example.com", "Refresh")
    access = create_token(actor.user_id, "access", timedelta(minutes=5))
    response = await client.post("/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_inactive_user(client, user_factory, session_factory):
    actor = await user_factory("inactive-refresh@example.com", "Refresh")
    refresh = (await login(client, "inactive-refresh@example.com")).json()["refresh_token"]
    async with session_factory() as db:
        user = await db.get(User, actor.user_id)
        user.is_active = False
        await db.commit()
    assert (await client.post("/auth/refresh", json={"refresh_token": refresh})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_deleted_user(client, user_factory, session_factory):
    actor = await user_factory("deleted-refresh@example.com", "Refresh")
    refresh = (await login(client, "deleted-refresh@example.com")).json()["refresh_token"]
    async with session_factory() as db:
        await db.execute(delete(User).where(User.id == actor.user_id))
        await db.commit()
    assert (await client.post("/auth/refresh", json={"refresh_token": refresh})).status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_is_revoked_after_refresh(client, user_factory):
    await user_factory("revocation@example.com", "Revocation")
    refresh = (await login(client, "revocation@example.com")).json()["refresh_token"]
    assert (await client.post("/auth/refresh", json={"refresh_token": refresh})).status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": refresh})).status_code == 401


@pytest.mark.asyncio
async def test_reused_refresh_token_revokes_other_existing_sessions(client, user_factory):
    await user_factory("reuse-sessions@example.com", "Reuse sessions")
    first = (await login(client, "reuse-sessions@example.com")).json()["refresh_token"]
    second = (await login(client, "reuse-sessions@example.com")).json()["refresh_token"]
    rotated = await client.post("/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": first})).status_code == 401
    assert (
        await client.post("/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]})
    ).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": second})).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_only_the_provided_refresh_session(client, user_factory):
    await user_factory("logout-sessions@example.com", "Logout sessions")
    current = (await login(client, "logout-sessions@example.com")).json()["refresh_token"]
    other = (await login(client, "logout-sessions@example.com")).json()["refresh_token"]
    assert (await client.post("/auth/logout", json={"refresh_token": current})).status_code == 204
    assert (await client.post("/auth/refresh", json={"refresh_token": current})).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": other})).status_code == 200


@pytest.mark.asyncio
async def test_login_rate_limit_returns_retry_after(client, redis_client):
    from app.core.config import settings

    for _ in range(settings.login_rate_limit_attempts):
        assert (await login(client, "blocked@example.com", "wrong password")).status_code == 401
    limited = await login(client, "blocked@example.com", "wrong password")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_login_rate_limit_keeps_separate_email_pair_counters(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "login_rate_limit_ip_multiplier", 100)
    for _ in range(settings.login_rate_limit_attempts):
        assert (await login(client, "first@example.com", "wrong password")).status_code == 401
    assert (await login(client, "first@example.com", "wrong password")).status_code == 429
    for _ in range(settings.login_rate_limit_attempts):
        assert (await login(client, "second@example.com", "wrong password")).status_code == 401
    assert (await login(client, "second@example.com", "wrong password")).status_code == 429


@pytest.mark.asyncio
async def test_login_rate_limit_ip_counter_catches_many_emails(client):
    from app.core.config import settings

    for email_index in range(settings.login_rate_limit_ip_multiplier):
        for _ in range(settings.login_rate_limit_attempts):
            response = await login(client, f"bot{email_index}@example.com", "wrong password")
            assert response.status_code == 401
    limited = await login(client, "new-email@example.com", "wrong password")
    assert limited.status_code == 429


@pytest.mark.asyncio
async def test_login_rate_limit_window_can_expire(client, redis_client):
    from app.core.config import settings

    for _ in range(settings.login_rate_limit_attempts):
        await login(client, "window@example.com", "wrong password")
    ip_key, pair_key = login_rate_limit_keys(TEST_CLIENT_IP, "window@example.com")
    await redis_client.expire(pair_key, -1)
    await redis_client.expire(ip_key, -1)
    response = await login(client, "window@example.com", "wrong password")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rate_limit_sets_ttl_on_first_failure(client, redis_client):
    from app.core.config import settings

    await login(client, "ttl@example.com", "wrong password")
    _, pair_key = login_rate_limit_keys(TEST_CLIENT_IP, "ttl@example.com")
    ttl = await redis_client.ttl(pair_key)
    assert 0 < ttl <= settings.login_rate_limit_window_seconds


@pytest.mark.asyncio
async def test_concurrent_login_attempts_are_reserved_atomically(client):
    from app.core.config import settings

    attempts = settings.login_rate_limit_attempts
    responses = await asyncio.gather(
        *(login(client, "parallel@example.com", "wrong password") for _ in range(attempts * 4))
    )
    assert sum(response.status_code == 401 for response in responses) == attempts
    assert sum(response.status_code == 429 for response in responses) == attempts * 3


@pytest.mark.asyncio
async def test_login_rate_limit_is_fail_open_when_redis_is_unavailable(client, app):
    from app.api.rate_limit import get_redis

    class BrokenRedis:
        async def eval(self, *args):
            from redis.exceptions import ConnectionError as RedisConnectionError

            raise RedisConnectionError("Redis unavailable")

    app.dependency_overrides[get_redis] = lambda: BrokenRedis()
    response = await login(client, "outage@example.com", "wrong password")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rate_limiter_does_not_hide_redis_script_errors(client, app):
    from app.api.rate_limit import get_redis
    from redis.exceptions import ResponseError

    class BrokenRedis:
        async def eval(self, *args):
            raise ResponseError("script has an invalid argument")

    app.dependency_overrides[get_redis] = lambda: BrokenRedis()
    response = await login(client, "script-error@example.com", "wrong password")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


@pytest.mark.asyncio
async def test_successful_login_clears_pair_counter(client, user_factory, redis_client):
    await user_factory("reset@example.com", "Reset")
    ip_key, pair_key = login_rate_limit_keys(TEST_CLIENT_IP, "reset@example.com")
    assert await redis_client.get(ip_key) is None
    response = await login(client, "reset@example.com", "incorrect password")
    assert response.status_code == 401
    assert await redis_client.get(pair_key) == "1"
    assert (await login(client, "reset@example.com")).status_code == 200
    assert await redis_client.get(pair_key) is None
    assert await redis_client.get(ip_key) == "1"


@pytest.mark.asyncio
async def test_refresh_rejects_non_integer_subject(client, user_factory):
    await user_factory("bad-subject@example.com", "Bad subject")
    claims = {
        "sub": "abc",
        "type": "refresh",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "jti": "bad-sub-refresh",
    }
    token = jwt.encode(claims, settings.secret_key, algorithm="HS256")
    response = await client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_parallel_refresh_only_rotates_token_once(client, user_factory):
    await user_factory("parallel-refresh@example.com", "Parallel refresh")
    refresh = (await login(client, "parallel-refresh@example.com")).json()["refresh_token"]
    responses = await asyncio.gather(
        *(client.post("/auth/refresh", json={"refresh_token": refresh}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [200, 401]


@pytest.mark.asyncio
async def test_refresh_fails_closed_when_redis_is_unavailable(client, app, user_factory):
    from app.api.rate_limit import get_redis
    from redis.exceptions import ConnectionError as RedisConnectionError

    await user_factory("refresh-outage@example.com", "Refresh outage")
    refresh = (await login(client, "refresh-outage@example.com")).json()["refresh_token"]

    class BrokenRedis:
        async def get(self, key):
            raise RedisConnectionError("Redis unavailable")

    app.dependency_overrides[get_redis] = lambda: BrokenRedis()
    response = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 503
