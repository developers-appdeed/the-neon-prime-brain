# Brain Server — Deployment Guide

Deployed: 2026-07-24 on the Coolify server (45.195.159.80).

## What's deployed

The brain MCP server runs as a **standalone docker-compose service** at `/opt/brain/` on the server
(not a Coolify-managed app — Coolify's public API lacks app-creation endpoints). It joins the
`coolify` external docker network so the fastify container can reach it at `http://brain:8000`.

| Repo | Nodes | Edges | Communities |
|------|-------|-------|-------------|
| store (the-neon-prime) | 10,192 | 15,920 | 692 |
| api (the-neon-prime-fastify) | 5,335 | 13,713 | 270 |
| admin (the-neon-prime-admin) | 2,854 | 9,212 | 168 |
| flutter (the-neon-prime-ops) | 816 | 1,219 | 56 |

## Server layout

```
/opt/brain/
  docker-compose.yml          # the production compose (coolify network)
  ds6c/                       # cloned repo (branch feat/stage1-brain)
    infra/brain/              # the brain source
  repos/                      # git clones of the indexed repos (persistent)
    the-neon-prime/
    the-neon-prime-fastify/
    the-neon-prime-admin/
    the-neon-prime-ops/
  graphs/                     # graph.json + memory (persistent, backed up)
    repos.json                # the corrected config (mounted into container)
    store/graphify-out/
    api/graphify-out/
    admin/graphify-out/
    flutter/graphify-out/
    memory/                   # per-repo learned Q&A
```

## How it was deployed

```bash
# 1. Clone the brain code
ssh root@45.195.159.80
mkdir -p /opt/brain && cd /opt/brain
git clone --branch feat/stage1-brain https://github.com/developers-appdeed/ds6c.git

# 2. Generate a bearer token
BRAIN_TOKEN=$(openssl rand -hex 32)

# 3. Write the production docker-compose (see /opt/brain/docker-compose.yml)
#    Key: networks.coolify.external: true (so fastify can reach brain:8000)

# 4. Build + start
cd /opt/brain && docker compose build && docker compose up -d

# 5. Clone repos + build initial graphs
mkdir -p /opt/brain/repos /opt/brain/graphs/{store,api,admin,flutter}/graphify-out
cd /opt/brain/repos
git clone --branch dev https://github.com/developers-appdeed/the-neon-prime.git
# ... repeat for each repo
docker exec brain graphify update /repos/the-neon-prime
# copy graph.json to /graphs/<key>/graphify-out/

# 6. Write the corrected repos.json (paths → /graphs/, branches → dev/prod)
#    and mount it into the container

# 7. Set BRAIN_URL + BRAIN_BEARER_TOKEN on the fastify app via Coolify API
curl -X POST "https://coolify.appdeed.com/api/v1/applications/<UUID>/envs" \
  -H "Authorization: Bearer <COOLIFY_API_KEY>" \
  -d '{"key":"BRAIN_URL","value":"http://brain:8000"}'
```

## Key findings (load-bearing — read before redeploying)

### 1. MCP streamable-http requires a session handshake
The brain's `/mcp` endpoint is NOT stateless. Clients must:
1. `POST /mcp` with `initialize` → capture `mcp-session-id` from response headers
2. `POST /mcp` with `notifications/initialized` (fire-and-forget)
3. `POST /mcp` with `tools/call` including the `mcp-session-id` header
4. Responses are SSE: `event: message\ndata: {jsonrpc...}` — parse the `data:` line

The `BrainClient` (fastify) handles this automatically (lazy session init, reuse, expiry retry).

### 2. DNS rebinding protection must be disabled for docker-internal access
The MCP server validates the `Host` header and rejects docker-internal hostnames
like `brain:8000` (returns 421 "Invalid Host header"). Fixed by passing
`TransportSecuritySettings(enable_dns_rebinding_protection=False, allowed_hosts=[...])`
to FastMCP. See `server.py:create_app()`.

### 3. Branches are `dev`/`prod`, not `develop`/`main`
The initial `repos.json` assumed `develop`/`main`. The actual branch names are `dev`/`prod`.

### 4. Brain is NOT a Coolify app
Coolify's public API can manage existing apps (deploy, env, status) but cannot create new ones.
The brain runs as a standalone docker-compose service (like the monitoring stack at `/opt/monitoring/`).
Updates: `cd /opt/brain/ds6c && git pull && cd /opt/brain && docker compose build && docker compose up -d`.

### 5. The webhook refresh path
When a Coolify deploy finishes (e.g. tnp-fastify-dev), the Coolify webhook → fastify
`ops-console.webhooks.ts` → `brain.refresh(repo)` → `graphify update` + reload graph.
The webhook handler maps Coolify app names to repo keys via `mapAppNameToRepo()`.

## Operations

```bash
# Check health
docker exec brain curl -sf http://localhost:8000/health

# Update brain code
cd /opt/brain/ds6c && git pull origin feat/stage1-brain
cd /opt/brain && docker compose build && docker compose up -d

# Rebuild a single repo's graph manually
docker exec brain graphify update /repos/the-neon-prime-fastify
docker exec brain sh -c "cp -r /repos/the-neon-prime-fastify/graphify-out/* /graphs/api/graphify-out/"

# View logs
docker logs brain --tail 50

# Backup (the valuable state is /opt/brain/graphs/ — memory + graph.json)
tar czf brain-graphs-backup-$(date +%Y%m%d).tar.gz /opt/brain/graphs/
```
