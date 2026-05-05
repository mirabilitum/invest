"""Shared initialization — load .env, init DB, return Repo.

Used by all entry points (OpenClaw, CLI, API).
"""

import os
import sys
import sqlite3
from pathlib import Path


def find_project_root() -> Path:
    """Auto-detect project root (where config.yaml lives)."""
    # Walk up from this file's location
    current = Path(__file__).resolve().parent.parent
    while current != current.parent:
        if (current / "config.yaml").exists():
            return current
        current = current.parent
    # Fallback: two levels up from src/init.py
    return Path(__file__).resolve().parent.parent


def load_env():
    """Load .env file if present."""
    root = find_project_root()
    env_file = root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ[key] = value


def init_db() -> tuple[sqlite3.Connection, "Repo"]:
    """Initialize database, create tables, return (conn, repo)."""
    from src.db.schema import create_tables
    from src.db.repo import Repo
    from src.config import DB_PATH

    root = find_project_root()
    db_dir = root / "data"
    db_dir.mkdir(exist_ok=True)
    db_path = root / DB_PATH

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_tables(conn)

    repo = Repo(conn)
    return conn, repo


def ensure_build_up(repo: "Repo"):
    """Ensure build-up state is initialized."""
    state = repo.get_build_up_state()
    if not state:
        repo.init_build_up()
