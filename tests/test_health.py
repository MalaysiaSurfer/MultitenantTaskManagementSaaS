import pytest
from app.api.rate_limit import get_redis
from redis.exceptions import ConnectionError as RedisConnectionError


@pytest.mark.asyncio
async def test_readiness_returns_200_when_database_and_redis_are_ready(client):
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_redis_is_unavailable(client, app):
    class BrokenRedis:
        async def ping(self):
            raise RedisConnectionError("Redis unavailable")

    app.dependency_overrides[get_redis] = lambda: BrokenRedis()
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not ready"}


@pytest.mark.asyncio
async def test_unhandled_exception_keeps_request_id_and_returns_safe_500(client, app, caplog):
    async def broken_endpoint():
        raise RuntimeError("private diagnostic detail")

    app.add_api_route("/_test/broken", broken_endpoint, methods=["GET"], include_in_schema=False)
    route = next(
        route for route in app.router.routes if getattr(route, "path", None) == "/_test/broken"
    )
    try:
        response = await client.get("/_test/broken", headers={"X-Request-ID": "req-test-123"})
    finally:
        app.router.routes.remove(route)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["X-Request-ID"] == "req-test-123"
    messages = [record.getMessage() for record in caplog.records]
    assert any("Unhandled request exception" in message for message in messages)
    access_record = next(
        record for record in caplog.records if "path=/_test/broken" in record.getMessage()
    )
    assert "status=500" in access_record.getMessage()
    assert access_record.request_id == "req-test-123"


@pytest.mark.asyncio
async def test_request_id_header_is_sanitized(client):
    response = await client.get("/health", headers={"X-Request-ID": "evil/header"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "evil/header"
