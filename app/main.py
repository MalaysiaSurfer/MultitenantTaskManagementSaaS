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
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes import auth, health, invitations, organization, projects, tasks
from app.core.config import Settings, settings
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


def configure_logging(config: Settings = settings) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    if config.log_json:
        handler.setFormatter(RequestFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s"
            )
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(config.log_level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    if config.environment == "prod":
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


configure_logging()
logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = dict(scope.get("headers", []))
        candidate = request_headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = (
            candidate
            if len(candidate) <= 128 and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate)
            else str(uuid.uuid4())
        )
        state = scope.setdefault("state", {})
        token = request_id_var.set(request_id)
        user_token = user_id_var.set("-")
        organization_token = organization_id_var.set("-")
        started = time.perf_counter()
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        response_started = False

        async def send_with_request_id(message):
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = message | {"headers": headers}
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as exc:
                logger.error(
                    "Unhandled request exception",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                if not response_started:
                    response = JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"detail": "Internal server error"},
                    )
                    await response(scope, receive, send_with_request_id)
                else:
                    raise
            logger.info(
                "HTTP request method=%s path=%s status=%s duration_ms=%.2f",
                scope["method"],
                scope["path"],
                status_code,
                (time.perf_counter() - started) * 1000,
                extra={
                    "user_id": state.get("user_id", "-"),
                    "organization_id": state.get("organization_id", "-"),
                },
            )
        finally:
            request_id_var.reset(token)
            user_id_var.reset(user_token)
            organization_id_var.reset(organization_token)


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


def create_app(config: Settings = settings) -> FastAPI:
    application = FastAPI(
        lifespan=lifespan,
        docs_url=None if config.environment == "prod" else "/docs",
        redoc_url=None if config.environment == "prod" else "/redoc",
        openapi_url=None if config.environment == "prod" else "/openapi.json",
    )
    application.add_exception_handler(NotFoundException, not_found_exception_handler)

    application.add_middleware(RequestLoggingMiddleware)

    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(organization.router)
    application.include_router(projects.router)
    application.include_router(tasks.router)
    application.include_router(invitations.router)
    return application


app = create_app()
