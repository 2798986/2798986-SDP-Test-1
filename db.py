"""RAT - database helpers.

Connection-per-call helpers, schema initialisation and path anchoring.
All filesystem paths are anchored to this file's directory (the repository
root) or to DATA_DIR - never to the process working directory.
"""

import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source TEXT NOT NULL,              -- 'url' | 'zip' | 'sample'
  path TEXT NOT NULL,                -- relative to DATA_DIR
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|ready|error
  error TEXT,
  ref_hash TEXT,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS authors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  canonical_id INTEGER,              -- NULL = itself; else id of canonical author row
  UNIQUE(repo_id, name, email)
);
CREATE TABLE IF NOT EXISTS commits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL,
  hash TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,               -- committer date, UNIX seconds
  subject TEXT
);
CREATE TABLE IF NOT EXISTS files (
  repo_id INTEGER NOT NULL,
  commit_id INTEGER NOT NULL,
  path TEXT NOT NULL,                -- final path (renames attributed to new path)
  added INTEGER NOT NULL,
  removed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER,
  kind TEXT NOT NULL,                -- 'ingest'
  status TEXT NOT NULL DEFAULT 'running',  -- running|done|error
  progress REAL NOT NULL DEFAULT 0,
  message TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_commit ON files(repo_id, commit_id);
CREATE INDEX IF NOT EXISTS idx_files_path   ON files(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_commits_ts   ON commits(repo_id, ts);
CREATE INDEX IF NOT EXISTS idx_commits_hash ON commits(repo_id, hash);
"""


def data_dir() -> Path:
    """DATA_DIR env override, else <repo>/data."""
    env = os.environ.get("DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return ROOT / "data"


def repos_dir() -> Path:
    return data_dir() / "repos"


def connect() -> sqlite3.Connection:
    """One connection per caller. WAL keeps readers alive during ingest writes."""
    dd = data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dd / "rat.db"), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def now() -> int:
    return int(time.time())
