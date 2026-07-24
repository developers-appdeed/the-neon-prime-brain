"""Prometheus metrics for the brain MCP server.

Exposed at ``/metrics`` (Prometheus text format). Counters/histograms are
labelled so the Platform dashboard (Grafana) can break queries/refreshes down
by repo (+ tool) and refreshes by repo (+ source).

Metric inventory (matches the Stage 6 coordinator brief):

* ``brain_query_total``         counter    labels: repo, tool
* ``brain_query_seconds``       histogram  labels: repo, tool
* ``brain_refresh_total``       counter    labels: repo, source
* ``brain_refresh_failed_total`` counter   labels: repo
* ``brain_graph_nodes``         gauge      labels: repo
* ``brain_memory_entries``      gauge      labels: repo

This module owns a private :class:`CollectorRegistry` so it never collides
with any other ``prometheus_client`` user in the same process.
``reset_registry()`` rebuilds it; it is called once on import (prod path) and
in test setup (fresh counters per test).
"""
from __future__ import annotations

from typing import Iterable

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_REGISTRY: CollectorRegistry | None = None
_QUERY_TOTAL: Counter | None = None
_QUERY_SECONDS: Histogram | None = None
_REFRESH_TOTAL: Counter | None = None
_REFRESH_FAILED_TOTAL: Counter | None = None
_GRAPH_NODES: Gauge | None = None
_MEMORY_ENTRIES: Gauge | None = None


def reset_registry() -> None:
    """Recreate the registry + all collectors.

    Safe in prod (called once on import). Also used by tests to get fresh
    counters per test so cumulative values don't leak across cases.
    """
    global _REGISTRY, _QUERY_TOTAL, _QUERY_SECONDS, _REFRESH_TOTAL
    global _REFRESH_FAILED_TOTAL, _GRAPH_NODES, _MEMORY_ENTRIES
    _REGISTRY = CollectorRegistry()
    _QUERY_TOTAL = Counter(
        "brain_query_total",
        "Total brain tool calls (query_graph/explain/...).",
        ["repo", "tool"],
        registry=_REGISTRY,
    )
    _QUERY_SECONDS = Histogram(
        "brain_query_seconds",
        "Brain tool latency in seconds.",
        ["repo", "tool"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=_REGISTRY,
    )
    _REFRESH_TOTAL = Counter(
        "brain_refresh_total",
        "Total refresh attempts (success path).",
        ["repo", "source"],
        registry=_REGISTRY,
    )
    _REFRESH_FAILED_TOTAL = Counter(
        "brain_refresh_failed_total",
        "Total refresh failures.",
        ["repo"],
        registry=_REGISTRY,
    )
    _GRAPH_NODES = Gauge(
        "brain_graph_nodes",
        "Node count per loaded graph.",
        ["repo"],
        registry=_REGISTRY,
    )
    _MEMORY_ENTRIES = Gauge(
        "brain_memory_entries",
        "Memory entries per repo.",
        ["repo"],
        registry=_REGISTRY,
    )


def observe_query(*, repo: str, tool: str, seconds: float) -> None:
    """Record one brain tool call (e.g. ``query_graph``)."""
    assert _QUERY_TOTAL is not None and _QUERY_SECONDS is not None, (
        "registry not initialised — call reset_registry()"
    )
    _QUERY_TOTAL.labels(repo=repo, tool=tool).inc()
    _QUERY_SECONDS.labels(repo=repo, tool=tool).observe(seconds)


def observe_refresh(*, repo: str, source: str, seconds: float) -> None:
    """Record one refresh call that succeeded."""
    assert _REFRESH_TOTAL is not None, "registry not initialised"
    _REFRESH_TOTAL.labels(repo=repo, source=source).inc()


def observe_refresh_failed(*, repo: str) -> None:
    """Record one refresh failure."""
    assert _REFRESH_FAILED_TOTAL is not None, "registry not initialised"
    _REFRESH_FAILED_TOTAL.labels(repo=repo).inc()


def set_graph_gauges(rows: Iterable[dict]) -> None:
    """Set node + memory gauges from a list of ``{repo, nodes, memory_entries}``.

    Called by ``server.py`` after loading graphs and after each refresh. We set
    every known repo to its current value; repos not in ``rows`` keep their
    last value (a missing graph is surfaced by ``brain_graph_nodes`` being
    stale or by the separate degraded flag in ``list_repos`` — not by zeroing
    here).
    """
    assert _GRAPH_NODES is not None and _MEMORY_ENTRIES is not None
    for row in rows:
        _GRAPH_NODES.labels(repo=row["repo"]).set(row["nodes"])
        _MEMORY_ENTRIES.labels(repo=row["repo"]).set(row["memory_entries"])


def render_metrics() -> str:
    """Return the full ``/metrics`` body as Prometheus text format (UTF-8 str)."""
    assert _REGISTRY is not None, "registry not initialised — call reset_registry()"
    return generate_latest(_REGISTRY).decode("utf-8")


# Initialise the singleton registry on import (prod path).
reset_registry()
