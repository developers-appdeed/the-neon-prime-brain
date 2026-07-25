"""Tests for brain.observability — request context middleware + OTel config."""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from brain.observability import RequestContextMiddleware
from brain.logging import _CURRENT_CTX


async def _echo(request):
    # The middleware should have populated the request context by now.
    ctx = _CURRENT_CTX.get()
    fields = ctx.fields if ctx else {}
    return JSONResponse(fields)


def _build_app() -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo)])
    app.add_middleware(RequestContextMiddleware)
    return app


def test_middleware_captures_all_three_headers():
    app = _build_app()
    client = TestClient(app)
    response = client.get(
        "/echo",
        headers={
            "x-correlation-id": "cid_abc",
            "x-request-id": "req-123",
            "traceparent": "00-aaaa1111aaaa1111aaaa1111aaaa1111-bbbb2222bbbb2222-01",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("correlationId") == "cid_abc"
    assert body.get("requestId") == "req-123"
    assert body.get("traceId") == "aaaa1111aaaa1111aaaa1111aaaa1111"
    assert body.get("spanId") == "bbbb2222bbbb2222"


def test_middleware_handles_missing_headers():
    app = _build_app()
    client = TestClient(app)
    response = client.get("/echo")
    assert response.status_code == 200
    body = response.json()
    # No headers sent → context stays empty.
    assert body == {}


def test_middleware_clears_context_after_response():
    app = _build_app()
    client = TestClient(app)
    client.get(
        "/echo",
        headers={"x-correlation-id": "cid_xyz"},
    )
    # After the response, the context var should be reset.
    assert _CURRENT_CTX.get() is None


def test_middleware_echoes_headers_on_response():
    app = _build_app()
    client = TestClient(app)
    response = client.get(
        "/echo",
        headers={"x-correlation-id": "cid_echo", "x-request-id": "req-echo"},
    )
    assert response.headers.get("x-correlation-id") == "cid_echo"
    assert response.headers.get("x-request-id") == "req-echo"


def test_configure_otel_is_idempotent():
    """Calling configure_otel twice must not re-initialize the provider."""
    from brain.observability import configure_otel, _OTEL_INITIALIZED
    from opentelemetry import trace

    # Reset for test isolation (the flag is module-global).
    import brain.observability as obs
    original_flag = obs._OTEL_INITIALIZED
    obs._OTEL_INITIALIZED = False

    try:
        configure_otel(service_name="brain-test", environment="test")
        first_provider = trace.get_tracer_provider()
        # Second call should be a no-op.
        configure_otel(service_name="brain-test-2", environment="test-2")
        second_provider = trace.get_tracer_provider()
        assert first_provider is second_provider, "configure_otel re-initialized the provider on second call"
    finally:
        obs._OTEL_INITIALIZED = original_flag


def test_configure_otel_sets_provider_without_exporter_when_endpoint_absent(monkeypatch):
    """When OTEL_EXPORTER_OTLP_ENDPOINT is unset, provider is set but no exporter is wired."""
    from brain.observability import configure_otel
    from opentelemetry import trace
    import brain.observability as obs

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    original_flag = obs._OTEL_INITIALIZED
    obs._OTEL_INITIALIZED = False

    try:
        configure_otel(service_name="brain-no-exporter", environment="test")
        provider = trace.get_tracer_provider()
        # The provider should be set (not the default ProxyTracerProvider).
        # We can't easily assert "no exporter" without reaching into internals,
        # but we can assert the provider is a real TracerProvider.
        from opentelemetry.sdk.trace import TracerProvider
        assert isinstance(provider, TracerProvider), f"expected TracerProvider, got {type(provider).__name__}"
    finally:
        obs._OTEL_INITIALIZED = original_flag
