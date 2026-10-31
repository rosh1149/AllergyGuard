"""Request ids, security headers and structured (JSON) event logs without personal data."""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
event_log = logging.getLogger("allergyguard.events")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def log_event(event: str, **fields) -> None:
    """One JSON line per event. Callers pass counts and statuses only: never names, emails,
    allergy profiles, ingredient text or photos."""
    event_log.info(json.dumps({"event": event, "request_id": request_id_var.get(), **fields}, default=str))


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            # Responses can contain allergy profiles and results: never store them in shared caches.
            response.headers.setdefault("Cache-Control", "no-store")
        return response
