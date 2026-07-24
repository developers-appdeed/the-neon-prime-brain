from __future__ import annotations
from threading import Lock

import networkx as nx
from graphify.serve import (
    _load_graph, _communities_from_graph, _score_nodes,
    _bfs, _dfs, _subgraph_to_text, _find_node,
)


class GraphRegistry:
    """Holds all repo graphs in memory, separate; supports single-repo + fan-out."""

    def __init__(self):
        self.graphs: dict[str, nx.Graph] = {}
        self.communities: dict[str, dict[int, list[str]]] = {}
        self._locks: dict[str, Lock] = {}

    def load(self, repo: str, graph_path: str) -> None:
        G = _load_graph(graph_path)
        self.graphs[repo] = G
        self.communities[repo] = _communities_from_graph(G)
        self._locks[repo] = Lock()

    def reload(self, repo: str, graph_path: str) -> None:
        """Atomic swap: load new G, then replace under lock."""
        G = _load_graph(graph_path)
        C = _communities_from_graph(G)
        with self._locks[repo]:
            self.graphs[repo] = G
            self.communities[repo] = C

    def repos(self) -> list[str]:
        return list(self.graphs.keys())

    def score(self, repo: str, terms: list[str]):
        return _score_nodes(self.graphs[repo], terms)

    def bfs(self, repo: str, starts: list[str], depth: int):
        return _bfs(self.graphs[repo], starts, depth)

    def dfs(self, repo: str, starts: list[str], depth: int):
        return _dfs(self.graphs[repo], starts, depth)

    def find_node(self, repo: str, label: str):
        return _find_node(self.graphs[repo], label)

    def subgraph_text(self, repo: str, nodes, edges, budget=2000) -> str:
        return _subgraph_to_text(self.graphs[repo], nodes, edges, budget)

    def query_all(self, terms: list[str], depth: int = 3, budget: int = 2000) -> str:
        """Fan-out: score every graph, BFS the top repos, merge + tag."""
        per_repo = []
        for repo, G in self.graphs.items():
            scored = _score_nodes(G, terms)
            if not scored:
                continue
            top = [scored[0][1]]
            nodes, edges = _bfs(G, top, depth)
            text = _subgraph_to_text(G, nodes, edges, budget)
            agg = sum(s[0] for s in scored[:3])
            per_repo.append((repo, agg, text))
        per_repo.sort(key=lambda r: r[1], reverse=True)
        chunks = []
        for repo, _, text in per_repo:
            for line in text.splitlines():
                chunks.append(f"[{repo}] {line}")
        out = "\n".join(chunks)
        char_budget = budget * 3
        return out[:char_budget] + (f"\n... (truncated ~{budget} tokens)" if len(out) > char_budget else "")

    def query_one(self, repo: str, terms: list[str], depth: int = 3, budget: int = 2000) -> str:
        scored = _score_nodes(self.graphs[repo], terms)
        if not scored:
            return f"No graph coverage for these terms in repo '{repo}'."
        nodes, edges = _bfs(self.graphs[repo], [scored[0][1]], depth)
        return _subgraph_to_text(self.graphs[repo], nodes, edges, budget)
