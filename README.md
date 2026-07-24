# the-neon-prime-brain

The digital brain — a graphify multi-graph MCP server that provides graph-grounded
code knowledge across all The Neon Prime repositories.

## What it does

Loads graphify knowledge graphs for each repo into memory and exposes them as
11 MCP tools over HTTP/SSE:

- `query_graph` — natural-language code search with file:line citations
- `explain` / `get_node` / `get_neighbors` — node detail and relationships
- `shortest_path` — trace how A connects to B
- `get_community` / `god_nodes` / `graph_stats` — structural overview
- `list_repos` — which repos are indexed + stats
- `remember` — save Q&A/fixes/notes as permanent memory (feedback loop)
- `refresh` — rebuild a repo's graph after code changes

## Quick start

```bash
pip install -e ".[dev]"
BRAIN_PORT=8000 python -m brain.server
curl http://localhost:8000/health
```

See [DEPLOY.md](./DEPLOY.md) for production deployment on Coolify.

## Architecture

Part of the Digital Brain Platform. See the design doc:
`docs/superpowers/specs/2026-07-24-digital-brain-platform-design.md`
