from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
organization_id_var: ContextVar[str] = ContextVar("organization_id", default="-")
