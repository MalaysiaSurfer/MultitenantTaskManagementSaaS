import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth, health, organization, projects, tasks
from app.core.constants import NOT_FOUND
from app.core.exceptions import NotFoundException
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


async def not_found_exception_handler(
    request: Request,
    exc: NotFoundException,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": NOT_FOUND},
    )


app = FastAPI(lifespan=lifespan)

app.add_exception_handler(
    NotFoundException,
    not_found_exception_handler,
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(organization.router)
app.include_router(projects.router)
app.include_router(tasks.router)
