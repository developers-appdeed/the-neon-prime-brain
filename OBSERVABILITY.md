# Brain Observability

How the brain MCP server is observed: metrics, logs, and traces. This documents
the brain-side implementation of Stage 6; the platform-wide (Prometheus scrape
jobs, alert rules, dashboards, Loki/Tempo stack) lives in
`ds6c/docs/OBSERVABILITY.md`.

## TL;DR

| Signal | Endpoint / path | Format |
|---|---|---|
| Metrics | `GET /metrics` (unauthenticated) | Prometheus text format |
| Logs | stdout (JSON) → Promtail → Loki | pino-compatible JSON |
| Traces | stdout (traceId) → optional OTLP → Tempo | W3C trace context |

`/metrics` and `/health` are exempt from `BearerAuthMiddleware` (Prometheus
scrapes with no token over the internal docker network). Everything else under
`/mcp` still requires the Bearer token.

---

## Metrics

Module: `brain/metrics.py` (private `prometheus_client.CollectorRegistry`).

| Metric | Type | Labels | What it means |
|---|---|---|---|
| `brain_query_total` | counter | `repo`, `tool` | Total brain tool calls (`query_graph`, ...) |
| `brain_query_seconds` | histogram | `repo`, `tool` | Brain tool latency (buckets 0.05→10s) |
| `brain_refresh_total` | counter | `repo`, `source` | Successful refresh attempts |
| `brain_refresh_failed_total` | counter | `repo` | Refresh failures |
| `brain_graph_nodes` | gauge | `repo` | Node count per loaded graph |
| `brain_memory_entries` | gauge | `repo` | `*.md` memory files per repo |

Instrumentation points (in `brain/server.py`):
- `BrainServer.query_graph` is wrapped: every call increments
  `brain_query_total{repo,tool="query_graph"}` and observes
  `brain_query_seconds`. The real work moved to `_query_graph_impl`.
- `BrainServer.refresh` is wrapped: success → `brain_refresh_total{repo,source}`
  + gauge refresh; failure (returns `{"error": ...}`) →
  `brain_refresh_failed_total{repo}`. Real work is in `_refresh_impl`.
- Gauges are set once at `create_app()` startup and after every successful
  refresh via `BrainServer._graph_gauge_rows()`.

`render_metrics()` returns the exposition body; the `/metrics` route serves it
as `text/plain; version=0.0.4`.

---

## Logs (pino-compatible JSON → Loki)

Module: `brain/logging.py`. Emits one JSON object per line to stdout:

```json
{"level":"info","time":"2026-07-24T16:27:12.000Z","service":"brain",
 "environment":"production","msg":"brain starting",
 "requestId":"req-456","traceId":"0af7651916cd43dd8448eb211c80319c",
 "spanId":"b7ad6b7169203331","repo":"api"}
```

This matches the fastify pino logger shape (`base: {service, environment}`,
`messageKey: "msg"`, ISO time) so Promtail's existing json pipeline ingests
brain logs identically.

### Loki label model (IMPORTANT)

**Only `environment` and `service` are Loki index labels** (set by Promtail on
the scrape config, identical to fastify). Everything else — `requestId`,
`traceId`, `spanId`, `repo`, `msg`, `level` — is a **JSON field inside the log
line**, NOT a label. This keeps label cardinality bounded (no per-request or
per-repo series).

### LogQL query patterns

Filter by the indexed labels, then parse JSON fields:

```logql
# All brain logs in production
{environment="production", service="brain"}

# Brain errors only
{environment="production", service="brain"} | json | level="error"

# All log lines for one request (across brain + fastify, same trace)
{environment="production"} | json | traceId="0af7651916cd43dd8448eb211c80319c"

# Brain refresh failures for a repo
{environment="production", service="brain"}
  | json |= "brain refresh failed" | json | repo="api"

# Slow queries (count by repo over 5m)
sum by (repo) (count_over_time(
  {environment="production", service="brain"} | json | msg="brain query slow" [5m]
))
```

Never put `requestId` / `traceId` / `repo` in a Promtail label or in a
`|=~` regex over the raw line — use `| json | field="value"` so the parser
extracts them efficiently.

---

## Traces (W3C trace context → Tempo)

The goal: **app → fastify → brain is one Tempo trace**.

### How the join works

1. fastify's OpenTelemetry SDK (`src/shared/observability/tracing.ts`) opens a
   request span and its `PinoInstrumentation` mixes `traceId`/`spanId` into
   every fastify log line within that span.
2. When fastify calls the brain over HTTP (undici), undici's auto-instrumentation
   injects the **W3C `traceparent` header**:
   `traceparent: 00-<32hex traceId>-<16hex spanId>-<flags>`.
3. The brain receives `traceparent` on the inbound request. The MCP layer /
   middleware calls `brain.logging.set_request_context(traceparent=..., requestId=...)`,
   which parses the header and stores `traceId` + `spanId` in a `contextvars`
   context. Every brain log line within that request then carries the **same
   `traceId`** as the fastify request span.
4. Click the `traceId` in a Loki log line → Tempo opens the full trace
   (fastify request span + nested brain spans). One-click correlation, exactly
   like fastify↔Loki today.

### Optional OTel span EXPORT

The `span(name, **attrs)` context manager emits real OTel spans to Tempo
**only when** `OTEL_EXPORTER_OTLP_ENDPOINT` is set (e.g.
`http://otel-collector:4318`) and the optional `otel` extra is installed:

```bash
pip install -e ".[otel]"
# env (set on the brain Coolify app via the REST API):
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_SERVICE_NAME=brain
```

Without the extra or the endpoint, `span()` is a no-op context manager and
trace context still flows into logs via `traceparent` parsing — so the
Tempo↔Loki join works even with OTel span export off. `query_graph` and
`refresh` are wrapped in `span("brain.query_graph", ...)` /
`span("brain.refresh", ...)`.

`SIGTERM`/`SIGINT` flush pending spans via `observability_shutdown()`.

---

## Wiring summary (`brain/server.py::create_app`)

1. `configure_observability(service, environment)` + `log.info("brain starting")`.
2. Load graphs; build `BrainServer`.
3. Register MCP tools (query_graph/refresh are instrumented wrappers).
4. Add `/health` and `/metrics` Starlette routes (both unauthenticated).
5. `metrics.set_graph_gauges(...)` so gauges are non-zero before first refresh.
6. Add `BearerAuthMiddleware` (exempts `/health` + `/metrics`).
7. Register SIGTERM/SIGINT → flush OTel spans.

## Testing

- `tests/test_metrics.py` — 5 tests: counters/histogram/gauges + required
  metric inventory.
- `tests/test_logging.py` — 5 tests: pino shape, extra fields, traceparent
  injection, nested context restore, `LOG_LEVEL` gating.
- Run: `python -m pytest tests/ -v` (23 tests total; the 13 pre-Stage-6 tests
  are unchanged and still pass).
