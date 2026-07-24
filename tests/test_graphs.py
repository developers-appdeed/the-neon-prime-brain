import json
import sys
from pathlib import Path

# Ensure the brain package is importable when run from the brain/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.graphs import GraphRegistry

def _make_fixture(tmp: Path) -> Path:
    """Tiny 3-node graph fixture mimicking graphify's schema."""
    g = {
        "directed": False, "multigraph": False, "graph": {},
        "nodes": [
            {"label": "calculateCartTotal()", "id": "calc_cart", "file_type": "code",
             "source_file": "/repo/src/cart.ts", "source_location": "L42",
             "community": 1, "norm_label": "calculatecarttotal"},
            {"label": "createOrder()", "id": "create_order", "file_type": "code",
             "source_file": "/repo/src/orders.ts", "source_location": "L7",
             "community": 1, "norm_label": "createorder"},
            {"label": "orders.insert()", "id": "orders_insert", "file_type": "code",
             "source_file": "/repo/src/db.ts", "source_location": "L3",
             "community": 2, "norm_label": "ordersinsert"},
        ],
        "links": [
            {"source": "calc_cart", "target": "create_order", "relation": "calls", "confidence": 1.0},
            {"source": "create_order", "target": "orders_insert", "relation": "calls", "confidence": 1.0},
        ],
    }
    p = tmp / "graph.json"
    p.write_text(json.dumps(g))
    return p

def test_load_single_graph(tmp_path):
    reg = GraphRegistry()
    gp = _make_fixture(tmp_path)
    reg.load("api", str(gp))
    assert "api" in reg.graphs
    assert reg.graphs["api"].number_of_nodes() == 3

def test_score_and_bfs_finds_cart(tmp_path):
    reg = GraphRegistry()
    reg.load("api", str(_make_fixture(tmp_path)))
    scored = reg.score("api", ["cart", "total"])
    assert scored[0][1] == "calc_cart"
    nodes, edges = reg.bfs("api", ["calc_cart"], depth=2)
    assert "create_order" in nodes
    assert "orders_insert" in nodes

def test_query_all_fans_out_and_tags(tmp_path):
    reg = GraphRegistry()
    reg.load("api", str(_make_fixture(tmp_path)))
    reg.load("store", str(_make_fixture(tmp_path)))
    result = reg.query_all(["cart"], depth=2, budget=2000)
    assert "[api]" in result and "[store]" in result
