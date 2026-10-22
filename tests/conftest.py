import os

TEST_SECRET_KEY = "pytest-secret-key-that-is-long-enough"
os.environ["SECRET_KEY"] = TEST_SECRET_KEY
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("INVITATION_EXPIRE_DAYS", "7")
os.environ.setdefault("LOGIN_RATE_LIMIT_ATTEMPTS", "5")
os.environ.setdefault("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")
os.environ.setdefault("LOGIN_RATE_LIMIT_IP_MULTIPLIER", "5")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from tests.helpers import TEST_CLIENT_IP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(items):
    """Allow a reversed-order run to audit test isolation."""
    if os.getenv("PYTEST_REVERSE_ORDER") == "1":
        items.reverse()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(
        "postgres:16", username="postgres", password="postgres", dbname="test"
    ) as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        env = os.environ | {"DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
        )
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture(scope="session")
async def redis_client(redis_url: str):
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture(scope="session")
async def test_engine(database_url: str):
    # NullPool avoids carrying asyncpg connections between event-loop uses.
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def clean_database(test_engine, redis_client):
    yield
    from app.db.base import Base

    table_list = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
    await redis_client.flushdb()


@pytest_asyncio.fixture
async def client(app, session_factory, redis_client, clean_database) -> AsyncIterator[AsyncClient]:
    from app.api.rate_limit import get_redis
    from app.db.session import get_db

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis_client
    transport = ASGITransport(app=app, client=(TEST_CLIENT_IP, 123))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_redis, None)


@pytest.fixture
def user_factory(client):
    from tests.helpers import create_actor

    async def factory(email: str, organization_name: str):
        return await create_actor(client, email, organization_name)

    return factory
