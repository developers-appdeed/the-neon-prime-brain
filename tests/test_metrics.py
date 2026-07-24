import sys
from pathlib import Path

# Ensure the brain package is importable when run from the brain/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain import metrics


def setup_function():
    """Each test gets a fresh registry so counters don't leak across tests."""
    metrics.reset_registry()


def test_observe_query_increments_counter_with_tool_label():
    metrics.observe_query(repo="api", tool="query_graph", seconds=0.12)
    metrics.observe_query(repo="api", tool="query_graph", seconds=0.30)
    metrics.observe_query(repo="api", tool="explain", seconds=0.05)
    out = metrics.render_metrics()
    assert 'brain_query_total{repo="api",tool="query_graph"} 2.0' in out
    assert 'brain_query_total{repo="api",tool="explain"} 1.0' in out
    # histogram bucket present for the observed latencies
    assert "brain_query_seconds_bucket" in out


def test_observe_refresh_tracks_success_and_failure():
    metrics.observe_refresh(repo="api", source="webhook", seconds=4.2)
    metrics.observe_refresh_failed(repo="api")
    out = metrics.render_metrics()
    assert 'brain_refresh_total{repo="api",source="webhook"} 1.0' in out
    assert 'brain_refresh_failed_total{repo="api"} 1.0' in out


def test_set_graph_gauges_report_node_and_memory_counts():
    metrics.set_graph_gauges([
        {"repo": "api", "nodes": 2168, "memory_entries": 14},
        {"repo": "store", "nodes": 1500, "memory_entries": 3},
    ])
    out = metrics.render_metrics()
    assert 'brain_graph_nodes{repo="api"} 2168.0' in out
    assert 'brain_graph_nodes{repo="store"} 1500.0' in out
    assert 'brain_memory_entries{repo="api"} 14.0' in out
    assert 'brain_memory_entries{repo="store"} 3.0' in out


def test_render_metrics_is_prometheus_text_format():
    metrics.observe_query(repo="api", tool="query_graph", seconds=0.1)
    out = metrics.render_metrics()
    # Prometheus exposition format always emits HELP and TYPE lines
    assert out.startswith("# HELP ") or "# TYPE " in out
    # Ends with a trailing newline (Prometheus parser expectation)
    assert out.endswith("\n")


def test_all_required_metric_names_present():
    """The coordinator brief enumerates the six required metric names."""
    metrics.observe_query(repo="api", tool="query_graph", seconds=0.1)
    metrics.observe_refresh(repo="api", source="webhook", seconds=1.0)
    metrics.observe_refresh_failed(repo="api")
    metrics.set_graph_gauges([{"repo": "api", "nodes": 5, "memory_entries": 2}])
    out = metrics.render_metrics()
    for name in (
        "brain_query_total",
        "brain_query_seconds",
        "brain_refresh_total",
        "brain_refresh_failed_total",
        "brain_graph_nodes",
        "brain_memory_entries",
    ):
        assert name in out, f"missing metric family: {name}"
