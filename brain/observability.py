"""OpenTelemetry configuration and request-context middleware for brain.

This module wires the inbound HTTP headers (x-correlation-id, x-request-id,
traceparent) into brain's logger context (brain.logging.set_request_context)
and configures the OTel SDK so traces export to the collector.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from brain.logging import clear_request_context, set_request_context

_OTEL_INITIALIZED = False
_logger = logging.getLogger(__name__)


def configure_otel(*, service_name: str = "brain", environment: str = "production") -> None:
    """Initialize the OTel SDK with an OTLP exporter.

    Idempotent: safe to call multiple times. Reads OTEL_EXPORTER_OTLP_ENDPOINT
    from the environment. If unset, traces are processed but not exported
    (still useful for in-process context propagation).
    """
    global _OTEL_INITIALIZED
    if _OTEL_INITIALIZED:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource)
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _OTEL_INITIALIZED = True
    _logger.debug("otel configured: service=%s env=%s exporter=%s", service_name, environment, bool(endpoint))


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Capture inbound correlation headers and push them into the logger context.

    Reads x-correlation-id, x-request-id, traceparent; calls
    brain.logging.set_request_context(**fields). The logger auto-splits
    traceparent into traceId + spanId. Token is cleared in `finally`.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        fields: dict[str, Any] = {}
        cid = request.headers.get("x-correlation-id")
        if cid:
            fields["correlationId"] = cid
        rid = request.headers.get("x-request-id")
        if rid:
            fields["requestId"] = rid
        tp = request.headers.get("traceparent")
        if tp:
            fields["traceparent"] = tp

        token = set_request_context(**fields) if fields else None
        try:
            response = await call_next(request)
        finally:
            if token is not None:
                clear_request_context(token)

        # Echo back so callers (and support) can correlate.
        if cid:
            response.headers["X-Correlation-Id"] = cid
        if rid:
            response.headers["X-Request-Id"] = rid
        return response
