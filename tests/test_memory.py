import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.memory import MemoryStore

def test_remember_writes_markdown_file(tmp_path):
    store = MemoryStore(base_dir=str(tmp_path))
    store.remember(
        repo="api", question="how does cart work?",
        answer="cart.ts:42 calculateCartTotal()",
        nodes=["calc_cart"], mtype="query",
    )
    files = list((tmp_path / "api").glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "how does cart work?" in content
    assert "repo: api" in content

def test_load_returns_relevant_memory(tmp_path):
    store = MemoryStore(base_dir=str(tmp_path))
    store.remember(repo="api", question="cart total bug",
                   answer="rounded at L42", nodes=["calc_cart"], mtype="fix")
    hits = store.load(repo="api", terms=["cart", "total"])
    assert len(hits) >= 1
    assert "rounded at L42" in hits[0]

def test_load_empty_when_no_match(tmp_path):
    store = MemoryStore(base_dir=str(tmp_path))
    store.remember(repo="api", question="cart", answer="a",
                   nodes=[], mtype="query")
    hits = store.load(repo="api", terms=["checkout", "payment"])
    assert hits == []
