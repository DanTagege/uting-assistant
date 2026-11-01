"""运行时开关（存 kv 表，改完立即生效，不需要重启）。"""
from __future__ import annotations

import random

from . import db
from .config import settings

K_REAL_SUBMIT = "real_submit"
K_DURATION_MIN = "paper_duration_minutes"
K_DURATION_JITTER = "paper_duration_jitter"


def get_real_submit() -> bool:
    """网页里点的「真实提交」开关；未设置时用 .env 的 UC_REAL_SUBMIT。"""
    row = db.one("SELECT v FROM kv WHERE k=?", (K_REAL_SUBMIT,))
    if row is not None:
        return str(row["v"]) == "1"
    return bool(settings.real_submit)


def set_real_submit(enabled: bool) -> bool:
    db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (K_REAL_SUBMIT, "1" if enabled else "0"))
    return enabled


# --------------------------------------------------------------------------- 作答时长
def get_duration_minutes() -> float:
    row = db.one("SELECT v FROM kv WHERE k=?", (K_DURATION_MIN,))
    try:
        val = float(row["v"]) if row is not None else 0.0
    except (TypeError, ValueError):
        val = 0.0
    return val if val > 0 else 0.0          # 0 = 不模拟，立即交卷


def set_duration_minutes(minutes: float) -> float:
    minutes = max(0.0, min(60.0, float(minutes)))
    db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (K_DURATION_MIN, f"{minutes:g}"))
    return minutes


def get_jitter_percent() -> int:
    row = db.one("SELECT v FROM kv WHERE k=?", (K_DURATION_JITTER,))
    try:
        return max(0, min(60, int(row["v"]))) if row is not None else 20
    except (TypeError, ValueError):
        return 20


def set_jitter_percent(pct: int) -> int:
    pct = max(0, min(60, int(pct)))
    db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (K_DURATION_JITTER, str(pct)))
    return pct


def pick_duration_seconds(minutes: float | None = None) -> int:
    """算一次"本次作答"的秒数（带随机抖动，避免每篇时长一模一样）。"""
    minutes = get_duration_minutes() if minutes is None else minutes
    if minutes <= 0:
        return 0
    base = minutes * 60
    jitter = get_jitter_percent() / 100.0
    factor = 1.0 + random.uniform(-jitter, jitter)
    return max(20, int(base * factor))

