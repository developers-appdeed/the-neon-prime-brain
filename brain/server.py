from __future__ import annotations
import os
import networkx as nx
from brain.graphs import GraphRegistry
from brain.memory import MemoryStore
from brain.logging import (
    configure_observability,
    get_logger,
    span,
    shutdown as observability_shutdown,
)
from brain.observability import RequestContextMiddleware, configure_otel


def _tokenize(question: str) -> list[str]:
    """Split a question into lowercase search terms (len > 1)."""
    return [t for t in question.lower().replace("?", "").split() if len(t) > 1]


class BrainServer:
    """Implements the 11 brain tools as plain methods. The MCP/HTTP layer wraps these."""

    def __init__(self, registry: GraphRegistry, memory: MemoryStore):
        self.reg = registry
        self.mem = memory

    def _resolve(self, label: str, repo: str | None) -> tuple[str, list[str]] | None:
        """Find a node by label, scoped to one repo if given, else across all repos."""
        if repo:
            nids = self.reg.find_node(repo, label)
            if nids:
                return repo, nids
            # fall through to a cross-repo search if not found in the named repo
        for r in self.reg.repos():
            nids = self.reg.find_node(r, label)
            if nids:
                return r, nids
        return None

    def query_graph(self, question: str, repo: str | None = None,
                    mode: str = "bfs", depth: int = 3, token_budget: int = 2000) -> str:
        import time
        from brain import metrics
        t0 = time.monotonic()
        try:
            with span("brain.query_graph", repo=repo or "all", mode=mode):
                return self._query_graph_impl(question, repo, mode, depth, token_budget)
        finally:
            metrics.observe_query(
                repo=repo or "all",
                tool="query_graph",
                seconds=time.monotonic() - t0,
            )

    def _query_graph_impl(self, question: str, repo: str | None = None,
                          mode: str = "bfs", depth: int = 3,
                          token_budget: int = 2000) -> str:
        terms = _tokenize(question)
        if repo:
            if repo not in self.reg.graphs:
                return f"Unknown repo '{repo}'. Known: {self.reg.repos()}"
            return self.reg.query_one(repo, terms, depth, token_budget)
        return self.reg.query_all(terms, depth, token_budget)

    def explain(self, label: str, repo: str | None = None) -> str:
        target = self._resolve(label, repo)
        if not target:
            return f"Node '{label}' not found in any graph."
        r, nids = target
        nodes, edges = self.reg.bfs(r, nids, 1)
        return f"[{r}] " + self.reg.subgraph_text(r, nodes, edges, 1000)

    def get_node(self, label: str, repo: str | None = None) -> str:
        target = self._resolve(label, repo)
        if not target:
            return f"Node '{label}' not found."
        r, nids = target
        d = self.reg.graphs[r].nodes[nids[0]]
        return (f"[{r}] {d.get('label')} | src={d.get('source_file')} "
                f"loc={d.get('source_location')} community={d.get('community')}")

    def get_neighbors(self, label: str, repo: str | None = None,
                      relation_filter: str | None = None) -> str:
        target = self._resolve(label, repo)
        if not target:
            return f"Node '{label}' not found."
        r, nids = target
        G = self.reg.graphs[r]
        lines = []
        for n in G.neighbors(nids[0]):
            edge = G[nids[0]][n]
            rel = edge.get("relation", "")
            if relation_filter and relation_filter.lower() not in rel.lower():
                continue
            lines.append(f"  --{rel}--> {G.nodes[n].get('label', n)} [{G.nodes[n].get('source_file','')}]")
        return f"[{r}] {G.nodes[nids[0]].get('label')} neighbors:\n" + "\n".join(lines)

    def get_community(self, community_id: int, repo: str | None = None) -> str:
        repos = [repo] if repo else self.reg.repos()
        lines = []
        for r in repos:
            members = self.reg.communities.get(r, {}).get(community_id, [])
            for nid in members[:50]:
                d = self.reg.graphs[r].nodes[nid]
                lines.append(f"[{r}] {d.get('label')} [{d.get('source_file')}]")
        return "\n".join(lines) or f"Community {community_id} empty/unknown."

    def god_nodes(self, repo: str | None = None, top_n: int = 10) -> str:
        repos = [repo] if repo else self.reg.repos()
        lines = []
        for r in repos:
            G = self.reg.graphs[r]
            ranked = sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)[:top_n]
            for n in ranked:
                d = G.nodes[n]
                lines.append(f"[{r}] ({G.degree(n)}) {d.get('label')} [{d.get('source_file')}]")
        return "\n".join(lines)

    def graph_stats(self, repo: str | None = None) -> str:
        repos = [repo] if repo else self.reg.repos()
        lines = []
        for r in repos:
            G = self.reg.graphs[r]
            comms = len(self.reg.communities.get(r, {}))
            lines.append(f"[{r}] {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {comms} communities")
        return "\n".join(lines)

    def shortest_path(self, source: str, target: str,
                      repo: str | None = None, max_hops: int = 8) -> str:
        repos = [repo] if repo else self.reg.repos()
        for r in repos:
            srcs = self.reg.find_node(r, source)
            tgts = self.reg.find_node(r, target)
            if not srcs or not tgts:
                continue
            try:
                path = nx.shortest_path(self.reg.graphs[r], srcs[0], tgts[0])
            except nx.NetworkXNoPath:
                continue
            if len(path) - 1 <= max_hops:
                labels = [self.reg.graphs[r].nodes[n].get("label", n) for n in path]
                return f"[{r}] " + " -> ".join(labels)
        return f"No path between '{source}' and '{target}' within {max_hops} hops."

    def list_repos(self) -> list[dict]:
        out = []
        for r, G in self.reg.graphs.items():
            out.append({
                "name": r,
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "communities": len(self.reg.communities.get(r, {})),
            })
        return out

    def _graph_gauge_rows(self) -> list[dict]:
        """Build the list of {repo, nodes, memory_entries} for the metrics gauges.

        Node count comes from the loaded graph; memory_entries is the number of
        ``*.md`` files in the repo's memory directory (0 if absent).
        """
        rows = []
        for repo_info in self.list_repos():
            repo = repo_info["name"]
            mem_dir = self.mem.base / repo
            memory_entries = len(list(mem_dir.glob("*.md"))) if mem_dir.exists() else 0
            rows.append({
                "repo": repo,
                "nodes": repo_info.get("nodes", 0),
                "memory_entries": memory_entries,
            })
        return rows

    def remember(self, question: str, answer: str, repo: str,
                 nodes: list[str] | None = None, mtype: str = "query") -> str:
        return self.mem.remember(repo, question, answer, nodes, mtype)

    def refresh(self, repo: str, source: str = "manual") -> dict:
        import time
        from brain import metrics
        t0 = time.monotonic()
        log = get_logger()
        with span("brain.refresh", repo=repo, source=source):
            result = self._refresh_impl(repo, source)
        if isinstance(result, dict) and result.get("error"):
            metrics.observe_refresh_failed(repo=repo)
            log.warn("brain refresh failed", repo=repo, source=source,
                     error=result["error"])
        else:
            metrics.observe_refresh(repo=repo, source=source,
                                    seconds=time.monotonic() - t0)
            # After a successful refresh, update the gauges so the dashboard
            # reflects the new node count immediately.
            try:
                metrics.set_graph_gauges(self._graph_gauge_rows())
            except Exception:
                pass  # metrics must never break a successful refresh
            log.info("brain refresh ok", repo=repo, source=source)
        return result

    def _refresh_impl(self, repo: str, source: str = "manual") -> dict:
        import subprocess
        from brain.config import load_config
        cfg = load_config()
        if repo not in cfg.repos:
            return {"error": f"Unknown repo '{repo}'"}
        path = cfg.repos[repo]["path"]
        before = self.reg.graphs.get(repo)
        before_n = before.number_of_nodes() if before else 0
        try:
            subprocess.run(["graphify", "update", path], check=True,
                           capture_output=True, text=True, timeout=300)
        except subprocess.CalledProcessError as e:
            return {"error": f"graphify update failed: {e.stderr[:200]}"}
        except FileNotFoundError:
            return {"error": "graphify CLI not found in PATH"}
        graph_path = cfg.repos[repo]["graph"]
        self.reg.reload(repo, graph_path)
        after_n = self.reg.graphs[repo].number_of_nodes()
        return {"repo": repo, "nodes_before": before_n, "nodes_after": after_n,
                "delta": after_n - before_n, "source": source}


def create_app():
    """Build the ASGI app: MCP tools over streamable-http + /health."""
    service = os.environ.get("OTEL_SERVICE_NAME", "brain")
    environment = os.environ.get("OBSERVABILITY_ENV", "production")
    configure_observability(service=service, environment=environment)
    configure_otel(service_name=service, environment=environment)
    log = get_logger()
    log.info("brain starting")
    from brain.config import load_config
    cfg = load_config()
    reg = GraphRegistry()
    for repo, spec in cfg.repos.items():
        try:
            reg.load(repo, spec["graph"])
        except SystemExit:
            print(f"[brain] WARNING: graph for '{repo}' not found at {spec['graph']} - skipping", flush=True)
    mem = MemoryStore(base_dir=os.environ.get("BRAIN_MEMORY_DIR", "/graphs/memory"))
    brain = BrainServer(registry=reg, memory=mem)

    from mcp.server.fastmcp import FastMCP
    from mcp.server.streamable_http_manager import TransportSecuritySettings
    # Allow docker-internal hostnames (brain:8000) — DNS rebinding protection
    # would reject them since they're not public domains.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["brain:8000", "brain", "localhost", "localhost:8000",
                       "127.0.0.1", "127.0.0.1:8000"],
    )
    mcp = FastMCP("graphify-brain", transport_security=security)
    mcp.tool()(brain.query_graph)
    mcp.tool()(brain.explain)
    mcp.tool()(brain.get_node)
    mcp.tool()(brain.get_neighbors)
    mcp.tool()(brain.get_community)
    mcp.tool()(brain.god_nodes)
    mcp.tool()(brain.graph_stats)
    mcp.tool()(brain.shortest_path)
    mcp.tool()(brain.list_repos)
    mcp.tool()(brain.remember)
    mcp.tool()(brain.refresh)
    app = mcp.streamable_http_app()

    from starlette.routing import Route
    from starlette.responses import JSONResponse as StarletteJSONResponse
    from starlette.responses import PlainTextResponse as StarlettePlainTextResponse
    from brain import metrics

    async def health(_req):
        return StarletteJSONResponse({"status": "ok", "repos": brain.list_repos()})

    # /metrics — Prometheus text format. Open path (auth middleware allows it).
    # Prometheus scrapes over the internal docker network with no Bearer token.
    async def metrics_endpoint(_req):
        return StarlettePlainTextResponse(
            metrics.render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    app.router.routes.insert(0, Route("/metrics", metrics_endpoint, methods=["GET"]))

    # Set the graph/memory gauges once at startup so they're non-zero before the
    # first refresh. (brain_graph_nodes for a degraded/missing repo is stale,
    # which is the intended signal — see metrics.set_graph_gauges docstring.)
    try:
        metrics.set_graph_gauges(brain._graph_gauge_rows())
    except Exception:
        pass  # metrics must never block startup

    from brain.auth import BearerAuthMiddleware
    app.add_middleware(BearerAuthMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Flush pending OTel spans on SIGTERM/SIGINT so traces aren't dropped on a
    # graceful shutdown. Wrapped defensively: signal.signal only works in the
    # main thread (some test runners call create_app() off-main-thread).
    import signal as _signal

    def _flush_on_exit(*_args):
        observability_shutdown()

    for _sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            _signal.signal(_sig, _flush_on_exit)
        except (ValueError, OSError):
            pass
    from opentelemetry.instrumentation.starlette import StarletteInstrumentor
    StarletteInstrumentor.instrument_app(app)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("BRAIN_HOST", "0.0.0.0"),
                port=int(os.environ.get("BRAIN_PORT", "8000")))
