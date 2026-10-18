import os

TEST_SECRET_KEY = "pytest-secret-key-that-is-long-enough"
os.environ["SECRET_KEY"] = TEST_SECRET_KEY
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("INVITATION_EXPIRE_DAYS", "7")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/test")

import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

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


@pytest_asyncio.fixture(autouse=True)
async def clean_database(test_engine):
    yield
    from app.db.base import Base

    table_list = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def client(app, session_factory) -> AsyncIterator[AsyncClient]:
    from app.db.session import get_db

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_factory(client):
    from tests.helpers import create_actor

    async def factory(email: str, organization_name: str):
        return await create_actor(client, email, organization_name)

    return factory
