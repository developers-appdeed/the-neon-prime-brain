from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:
    """Per-repo markdown memory: remember() writes, load() searches by keyword."""

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)

    def remember(self, repo: str, question: str, answer: str,
                 nodes: list[str] | None = None, mtype: str = "query") -> str:
        repo_dir = self.base / repo
        repo_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        fname = f"{ts}_{mtype}.md"
        nodes_yaml = "\n".join(f"  - {n}" for n in (nodes or [])) or "  []"
        body = f"""---
repo: {repo}
type: {mtype}
nodes:
{nodes_yaml}
ts: {ts}
---

# Q: {question}

{answer}
"""
        (repo_dir / fname).write_text(body, encoding="utf-8")
        return str(repo_dir / fname)

    def load(self, repo: str, terms: list[str]) -> list[str]:
        """Return answer bodies of memory files whose question/answer match any term."""
        repo_dir = self.base / repo
        if not repo_dir.exists():
            return []
        norm = [t.lower() for t in terms]
        hits = []
        for f in sorted(repo_dir.glob("*.md"), reverse=True):
            text = f.read_text(encoding="utf-8").lower()
            if any(t in text for t in norm):
                parts = f.read_text(encoding="utf-8").split("\n\n", 2)
                hits.append(parts[-1].strip() if len(parts) > 2 else text)
        return hits[:20]
