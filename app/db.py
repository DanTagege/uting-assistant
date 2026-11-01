"""SQLite 持久层：账号 / 托管 / 任务 / 日志 / 题库 / 配置。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  username      TEXT PRIMARY KEY,           -- U校园手机号（11 位）
  password      TEXT NOT NULL,              -- 本地演示用：见 docs 安全说明（可接 KMS/加密列）
  name          TEXT DEFAULT '',
  school        TEXT DEFAULT '',
  is_admin      INTEGER DEFAULT 0,
  status        TEXT DEFAULT 'active',      -- active|relogin_required|rt_expired|credentials_missing
  uc_jwt        TEXT DEFAULT '',
  uc_rt         TEXT DEFAULT '',
  uc_jwt_exp    REAL DEFAULT 0,
  uc_rt_exp     REAL DEFAULT 0,
  hosted_at     REAL DEFAULT 0,
  created_at    REAL DEFAULT 0,
  last_seen_at  REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hosting (
  username  TEXT PRIMARY KEY,
  active    INTEGER DEFAULT 0,
  slot      INTEGER DEFAULT 0,              -- 0=周一 … 6=周日
  updated_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account      TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  task_name    TEXT DEFAULT '',
  trigger      TEXT DEFAULT 'user',         -- user|cron|hosting
  status       TEXT DEFAULT 'queued',       -- queued|running|succeeded|failed
  source       TEXT DEFAULT 'manual',
  message      TEXT DEFAULT '',
  error        TEXT DEFAULT '',
  score        REAL,
  paper_hash   TEXT DEFAULT '',
  ans_version  INTEGER DEFAULT 0,
  delay_s      INTEGER DEFAULT 0,
  audio_visual INTEGER DEFAULT 0,
  origin_ip    TEXT DEFAULT '',
  browser_id   TEXT DEFAULT '',
  created_at   REAL DEFAULT 0,
  started_at   REAL DEFAULT 0,
  finished_at  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_account ON jobs(account, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);

CREATE TABLE IF NOT EXISTS job_logs (
  job_id INTEGER NOT NULL,
  seq    INTEGER NOT NULL,
  line   TEXT NOT NULL,
  ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON job_logs(job_id, seq);






CREATE TABLE IF NOT EXISTS mock_state (
  account TEXT PRIMARY KEY,
  payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
  paper_hash  TEXT PRIMARY KEY,
  task_id     TEXT DEFAULT '',
  payload     TEXT NOT NULL,
  fetched_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS answer_bank (
  paper_hash  TEXT NOT NULL,
  ans_version INTEGER NOT NULL,
  payload     TEXT NOT NULL,
  source      TEXT DEFAULT 'seed',
  updated_at  REAL NOT NULL,
  PRIMARY KEY (paper_hash, ans_version)
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def q(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        cur = conn().execute(sql, tuple(args))
        return cur.fetchall()


def one(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: Iterable[Any] = ()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, tuple(args))
        c.commit()
        return cur.lastrowid or cur.rowcount


def json_col(row: sqlite3.Row | None, name: str, default: Any = None) -> Any:
    if row is None:
        return default
    raw = row[name] if name in row.keys() else None
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def now() -> float:
    return time.time()
