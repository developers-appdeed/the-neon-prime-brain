from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BrainConfig:
    repos: dict
    coolify_uuid_map: dict
    bearer_token: str
    host: str
    port: int
    repos_file: Path


def load_config() -> BrainConfig:
    repos_file = Path(os.environ.get("BRAIN_REPOS_FILE", "repos.json"))
    data = json.loads(repos_file.read_text(encoding="utf-8"))
    return BrainConfig(
        repos=data["repos"],
        coolify_uuid_map=data.get("coolify_uuid_map", {}),
        bearer_token=os.environ.get("BRAIN_BEARER_TOKEN", ""),
        host=os.environ.get("BRAIN_HOST", "0.0.0.0"),
        port=int(os.environ.get("BRAIN_PORT", "8000")),
        repos_file=repos_file,
    )
