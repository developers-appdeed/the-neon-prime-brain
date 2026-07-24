from __future__ import annotations
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = os.environ.get("BRAIN_BEARER_TOKEN", "")
        # /health is the liveness probe; /metrics is scraped by Prometheus over
        # the internal docker network (no Bearer token). Both are open paths;
        # all other paths require the Bearer token.
        if request.url.path in ("/health", "/metrics"):
            return await call_next(request)
        if token:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
