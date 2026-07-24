FROM python:3.11-slim

# graphify needs git for `graphify update` (git pull); curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (graphifyy pulls networkx; mcp pulls starlette/uvicorn)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy the application
COPY brain/ ./brain/
COPY repos.json ./
COPY tests/ ./tests/

# Volumes mounted by Coolify: /repos (git clones), /graphs (graph.json + memory)
VOLUME ["/repos", "/graphs"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python", "-m", "brain.server"]
