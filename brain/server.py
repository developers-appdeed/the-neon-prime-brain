from __future__ import annotations
import os
import networkx as nx
from brain.graphs import GraphRegistry
from brain.memory import MemoryStore


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

    def remember(self, question: str, answer: str, repo: str,
                 nodes: list[str] | None = None, mtype: str = "query") -> str:
        return self.mem.remember(repo, question, answer, nodes, mtype)

    def refresh(self, repo: str, source: str = "manual") -> dict:
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

    async def health(_req):
        return StarletteJSONResponse({"status": "ok", "repos": brain.list_repos()})

    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))

    from brain.auth import BearerAuthMiddleware
    app.add_middleware(BearerAuthMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("BRAIN_HOST", "0.0.0.0"),
                port=int(os.environ.get("BRAIN_PORT", "8000")))
