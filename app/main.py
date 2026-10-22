import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.routes import auth, health, invitations, organization, projects, tasks
from app.core.config import settings
from app.core.constants import NOT_FOUND
from app.core.exceptions import NotFoundException
from app.core.log_context import organization_id_var, request_id_var, user_id_var
from app.db.session import async_session, engine


class RequestFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": record.request_id,
            "user_id": record.user_id,
            "organization_id": record.organization_id,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", request_id_var.get())
        record.user_id = getattr(record, "user_id", user_id_var.get())
        record.organization_id = getattr(record, "organization_id", organization_id_var.get())
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    if settings.log_json:
        handler.setFormatter(RequestFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s"
            )
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    if settings.environment == "prod":
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
    )
    try:
        async with async_session() as session:
            result = await session.execute(text("SELECT 1"))
            logger.info("Database connection ready: %s", result.scalar())
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()


async def not_found_exception_handler(request: Request, exc: NotFoundException) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": NOT_FOUND})


app = FastAPI(
    lifespan=lifespan,
    docs_url=None if settings.environment == "prod" else "/docs",
    redoc_url=None if settings.environment == "prod" else "/redoc",
    openapi_url=None if settings.environment == "prod" else "/openapi.json",
)
app.add_exception_handler(NotFoundException, not_found_exception_handler)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    candidate_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        candidate_request_id
        if len(candidate_request_id) <= 128
        and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate_request_id)
        else str(uuid.uuid4())
    )
    token = request_id_var.set(request_id)
    user_token = user_id_var.set("-")
    organization_token = organization_id_var.set("-")
    started = time.perf_counter()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    try:
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            logger.error(
                "Unhandled request exception",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal server error"},
            )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            status_code,
            (time.perf_counter() - started) * 1000,
            extra={
                "user_id": request.scope.get("state", {}).get("user_id", "-"),
                "organization_id": request.scope.get("state", {}).get("organization_id", "-"),
            },
        )
        return response
    finally:
        request_id_var.reset(token)
        user_id_var.reset(user_token)
        organization_id_var.reset(organization_token)


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(organization.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(invitations.router)
