import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.graphs import GraphRegistry
from brain.memory import MemoryStore
from brain.server import BrainServer

def _fixture(tmp_path):
    g = {"directed": False, "multigraph": False, "graph": {},
         "nodes": [{"label":"calculateCartTotal()","id":"c","file_type":"code",
                    "source_file":"/r/cart.ts","source_location":"L1","community":1,"norm_label":"calculatecarttotal"},
                   {"label":"createOrder()","id":"o","file_type":"code",
                    "source_file":"/r/orders.ts","source_location":"L2","community":1,"norm_label":"createorder"}],
         "links": [{"source":"c","target":"o","relation":"calls","confidence":1.0}]}
    p = tmp_path / "graph.json"; p.write_text(json.dumps(g)); return p

def _server(tmp_path):
    reg = GraphRegistry(); reg.load("api", str(_fixture(tmp_path)))
    mem = MemoryStore(base_dir=str(tmp_path / "mem"))
    return BrainServer(registry=reg, memory=mem)

def test_list_repos(tmp_path):
    s = _server(tmp_path)
    out = s.list_repos()
    assert len(out) == 1
    assert out[0]["name"] == "api"
    assert out[0]["nodes"] == 2

def test_query_graph_single_repo(tmp_path):
    s = _server(tmp_path)
    out = s.query_graph(question="cart total", repo="api")
    assert "calculateCartTotal" in out

def test_query_graph_all_repos(tmp_path):
    s = _server(tmp_path)
    out = s.query_graph(question="cart total")  # no repo = fan-out
    assert "[api]" in out

def test_get_node(tmp_path):
    s = _server(tmp_path)
    out = s.get_node(label="calculateCartTotal", repo="api")
    assert "cart.ts" in out

def test_shortest_path(tmp_path):
    s = _server(tmp_path)
    out = s.shortest_path(source="cart", target="order", repo="api")
    assert "calculateCartTotal" in out
    assert "createOrder" in out

def test_graph_stats(tmp_path):
    s = _server(tmp_path)
    out = s.graph_stats(repo="api")
    assert "2 nodes" in out

def test_remember_returns_path(tmp_path):
    s = _server(tmp_path)
    out = s.remember(question="q", answer="a", repo="api", nodes=["c"])
    assert "api" in out
