import io
import json
import sys

from brain import logging as brain_logging


def _capture(monkeypatch, capfd):
    """Force a fresh module state + capture stdout."""
    brain_logging.configure_observability(service="brain", environment="test")
    return capfd


def test_log_line_is_pino_compatible_json(capfd):
    capfd = _capture(None, capfd)
    log = brain_logging.get_logger()
    log.info("brain starting")
    out, _ = capfd.readouterr()
    line = out.strip().splitlines()[-1]
    rec = json.loads(line)
    # pino shape: level (lowercase word), time (ISO), msg, service, environment
    assert rec["level"] == "info"
    assert rec["service"] == "brain"
    assert rec["environment"] == "test"
    assert rec["msg"] == "brain starting"
    assert "T" in rec["time"] and rec["time"].endswith("Z")


def test_log_line_carries_extra_fields(capfd):
    capfd = _capture(None, capfd)
    log = brain_logging.get_logger()
    log.warn("refresh slow", requestId="req-123", repo="api")
    out, _ = capfd.readouterr()
    rec = json.loads(out.strip().splitlines()[-1])
    assert rec["level"] == "warn"
    assert rec["requestId"] == "req-123"
    assert rec["repo"] == "api"


def test_set_request_context_injects_traceparent_fields(capfd):
    capfd = _capture(None, capfd)
    # W3C traceparent: version-traceid-spanid-flags
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    token = brain_logging.set_request_context(traceparent=tp, requestId="r1")
    try:
        log = brain_logging.get_logger()
        log.info("inside request")
    finally:
        brain_logging.clear_request_context(token)
    out, _ = capfd.readouterr()
    rec = json.loads(out.strip().splitlines()[-1])
    assert rec["requestId"] == "r1"
    assert rec["traceId"] == "0af7651916cd43dd8448eb211c80319c"
    assert rec["spanId"] == "b7ad6b7169203331"


def test_clear_request_context_restores_prior_state(capfd):
    capfd = _capture(None, capfd)
    # set an outer context, then a nested one, then clear back to outer
    outer = brain_logging.set_request_context(requestId="outer")
    inner = brain_logging.set_request_context(requestId="inner")
    brain_logging.clear_request_context(inner)
    log = brain_logging.get_logger()
    log.info("after clear")
    brain_logging.clear_request_context(outer)
    out, _ = capfd.readouterr()
    rec = json.loads(out.strip().splitlines()[-1])
    assert rec["requestId"] == "outer"


def test_log_level_respects_env(monkeypatch, capfd):
    monkeypatch.setenv("LOG_LEVEL", "WARN")
    brain_logging.configure_observability(service="brain", environment="test")
    log = brain_logging.get_logger()
    log.info("should be suppressed")
    log.error("should appear")
    out, _ = capfd.readouterr()
    lines = [l for l in out.strip().splitlines() if l.strip()]
    msgs = [json.loads(l)["msg"] for l in lines]
    assert "should be suppressed" not in msgs
    assert "should appear" in msgs
