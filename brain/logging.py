"""Structured logging + trace-context propagation for the brain.

Emits pino-compatible JSON to stdout so Promtail's json pipeline (the same one
that ships fastify logs to Loki) ingests brain logs with an identical label
shape: ``{level, time, msg, service, environment, requestId?, traceId?, spanId?}``.
The Loki index labels are still just ``environment`` + ``service`` (set on the
Promtail scrape) — ``requestId``/``traceId`` are JSON fields, queried with
LogQL json filters (see OBSERVABILITY.md).

Trace context: fastify's undici auto-instrumentation injects the W3C
``traceparent`` header on its outbound brain MCP call. We parse it via
``set_request_context(traceparent=...)`` and mix ``traceId``/``spanId`` into
every log line within that request — so a brain log line shares the fastify
``traceId``, giving one-click Tempo <-> Loki correlation just like fastify's
PinoInstrumentation.

Optional OpenTelemetry span EXPORT is supported: when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set and the ``opentelemetry-sdk`` +
``opentelemetry-exporter-otlp`` packages are installed, ``span(...)`` emits
real OTLP spans to the existing otel-collector. Without those deps the
``span()`` context manager is a no-op (logs + trace context still work).

This module depends only on the Python stdlib at runtime so the brain image
stays light. OTel is an optional extra (see ``[project.optional-dependencies]``
in pyproject.toml).
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator

# ---- module-level config ----

_SERVICE = "brain"
_ENV = "production"
_LOG_LEVEL = "INFO"

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def configure_observability(
    *, service: str = "brain", environment: str = "production"
) -> None:
    """Set module-level service/environment and the log-level gate.

    Idempotent. Reads ``LOG_LEVEL`` from the environment on each call so tests
    can flip it. OTel export is started lazily by :func:`span` when an exporter
    endpoint is configured (no eager import of OTel SDK here).
    """
    global _SERVICE, _ENV, _LOG_LEVEL
    _SERVICE = service
    _ENV = environment
    _LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


# ---- per-request trace context (contextvars) ----


@dataclass
class _RequestCtx:
    fields: dict[str, Any] = field(default_factory=dict)


_CURRENT_CTX: ContextVar[_RequestCtx | None] = ContextVar(
    "brain_request_ctx", default=None
)


def _parse_traceparent(tp: str) -> dict[str, str]:
    """Extract traceId/spanId from a W3C traceparent header value.

    Format: ``00-<32 hex traceId>-<16 hex spanId>-<2 hex flags>``. Returns {}
    if the value is malformed (we never want a bad header to crash a log call).
    """
    parts = tp.strip().split("-")
    if len(parts) != 4 or len(parts[1]) != 32 or len(parts[2]) != 16:
        return {}
    return {"traceId": parts[1], "spanId": parts[2]}


def set_request_context(**fields: Any) -> Token:
    """Push per-request fields (requestId, traceparent, ...) onto the context stack.

    Pass the inbound ``traceparent`` header and any ``requestId``. The returned
    :class:`~contextvars.Token` must be passed to :func:`clear_request_context`
    in a ``finally`` to restore the prior context (supports nesting).

    A ``traceparent`` value is auto-parsed into ``traceId`` + ``spanId`` fields
    so every subsequent log line within the request carries them.
    """
    parent_ctx = _CURRENT_CTX.get()
    merged: dict[str, Any] = dict(parent_ctx.fields) if parent_ctx else {}
    tp = fields.pop("traceparent", None)
    if tp:
        merged.update(_parse_traceparent(tp))
    merged.update(fields)
    return _CURRENT_CTX.set(_RequestCtx(fields=merged))


def clear_request_context(token: Token) -> None:
    """Restore the context to its state before the matching set_request_context."""
    _CURRENT_CTX.reset(token)


# ---- emit + logger ----


def _emit(level: str, msg: str, **fields: Any) -> None:
    if _LEVELS.get(level, 20) < _LEVELS.get(_LOG_LEVEL, 20):
        return
    record: dict[str, Any] = {
        "level": level.lower(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "service": _SERVICE,
        "environment": _ENV,
        "msg": msg,
    }
    ctx = _CURRENT_CTX.get()
    if ctx is not None:
        record.update(ctx.fields)
    record.update(fields)
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()


class _Logger:
    """Tiny pino-shaped logger: ``log.info(msg, **fields)`` etc."""

    def debug(self, msg: str, **f: Any) -> None:
        _emit("DEBUG", msg, **f)

    def info(self, msg: str, **f: Any) -> None:
        _emit("INFO", msg, **f)

    def warn(self, msg: str, **f: Any) -> None:
        _emit("WARN", msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        _emit("ERROR", msg, **f)


def get_logger() -> _Logger:
    return _Logger()


# ---- optional OTel span export ----

_TRACER = None
_PROVIDER = None


def _maybe_start_tracer():
    """Lazily create an OTel TracerProvider if OTLP endpoint + deps are present.

    Returns the tracer or None. Imported lazily so the brain never hard-depends
    on opentelemetry-* at runtime.
    """
    global _TRACER, _PROVIDER
    if _TRACER is not None or _PROVIDER is not None:
        return _TRACER
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        return None  # OTel optional — logging still works

    resource = Resource.create(
        {"service.name": _SERVICE, "deployment.environment": _ENV}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(_SERVICE)
    _PROVIDER = provider
    return _TRACER


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open an OTel span if OTel is configured; otherwise a no-op context.

    Attributes are set on the span. The span joins the inbound W3C trace
    (extracted from the active OTel context, which StarletteInstrumentor or an
    equivalent propagator populates from the ``traceparent`` header) when
    present. When OTel is not installed/configured this is a plain context
    manager so callers never need to branch.
    """
    tracer = _maybe_start_tracer()
    if tracer is None:
        yield None
        return
    try:
        from opentelemetry import trace as _trace
    except ImportError:
        yield None
        return
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            try:
                s.set_attribute(k, str(v))
            except Exception:
                pass
        yield s


def shutdown() -> None:
    """Flush pending OTel spans on exit. Safe to call unconditionally."""
    global _PROVIDER
    if _PROVIDER is not None:
        try:
            _PROVIDER.force_flush()
        except Exception:
            pass
