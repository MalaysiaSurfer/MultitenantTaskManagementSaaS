import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import auth, health
from app.db.session import async_session, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session() as session:
        result = await session.execute(text("SELECT 1"))
        logger.info("DB connection ok: %s", result.scalar())
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
