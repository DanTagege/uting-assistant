"""调度器：周维度削峰排班 + 到点批量托管。

排班规则：
  - 托管开启时把账号分配到"一周中人数较少的一天"（slot 0..6）
  - 全局配置 {enabled, weekday, hour, minute, parallelism}
  - 管理端可立即手动跑一轮，并按日期查看每账号 succeeded/needed
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from . import db, jobs
from .config import settings

try:  # Windows 上可能缺 IANA 时区库，退回固定 +08:00
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(settings.timezone)
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=8))
KEYS = {"config": "sched:config", "state": "sched:state", "last": "sched:last_run"}


# --------------------------------------------------------------------------- 配置
def default_config() -> dict[str, Any]:
    return {
        "enabled": settings.scheduler_enabled,
        "weekday": settings.scheduler_weekday,
        "hour": settings.scheduler_hour,
        "minute": settings.scheduler_minute,
        "parallelism": settings.default_parallelism,
    }


def _kv_get(key: str) -> Any:
    row = db.one("SELECT v FROM kv WHERE k=?", (key,))
    if not row:
        return None
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return None


def _kv_set(key: str, value: Any) -> None:
    db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (key, json.dumps(value, ensure_ascii=False)))


def get_config() -> dict[str, Any]:
    cfg = default_config()
    cfg.update(_kv_get(KEYS["config"]) or {})
    return cfg


def get_state() -> dict[str, Any]:
    return _kv_get(KEYS["state"]) or {"running": False, "lastStatus": "idle", "lastMessage": "", "lastRunAt": 0}


def public_config() -> dict[str, Any]:
    cfg = get_config()
    cfg.update(get_state())
    cfg["slot_counts"] = slot_counts()
    cfg["server_time"] = db.now()
    cfg["today"] = datetime.now(TZ).strftime("%Y-%m-%d")
    return cfg


def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    for key in ("enabled", "weekday", "hour", "minute", "parallelism"):
        if key in payload and payload[key] is not None:
            cfg[key] = payload[key]
    _kv_set(KEYS["config"], cfg)
    return public_config()


# --------------------------------------------------------------------------- 槽位
def slot_counts() -> list[int]:
    rows = db.q("SELECT slot, COUNT(*) AS c FROM hosting WHERE active=1 GROUP BY slot")
    counts = [0] * 7
    for r in rows:
        s = int(r["slot"])
        if 0 <= s <= 6:
            counts[s] = int(r["c"])
    return counts


def assign_slot(username: str) -> int:
    """分到人数最少的一天；并列取最小 weekday（确定性、可复现）。"""
    current = db.one("SELECT slot FROM hosting WHERE username=? AND active=1", (username,))
    if current:
        return int(current["slot"])
    counts = slot_counts()
    best = min(range(7), key=lambda i: (counts[i], i))
    return best


# --------------------------------------------------------------------------- 选账号
def _active_accounts(weekday: int | None = None) -> list[str]:
    if weekday is None:
        rows = db.q("SELECT username FROM hosting WHERE active=1 ORDER BY username")
    else:
        rows = db.q("SELECT username FROM hosting WHERE active=1 AND slot=? ORDER BY username", (weekday,))
    return [r["username"] for r in rows]


# --------------------------------------------------------------------------- 执行
async def run_batch(accounts: list[str], trigger: str = "hosting") -> list[int]:
    ids: list[int] = []
    for acc in accounts:
        task = {"taskId": "__auto__", "name": "自动选题", "paperHash": "", "audioVisual": 0}
        ids.append(jobs.enqueue(acc, task, trigger=trigger, source=trigger))
    _kv_set(KEYS["state"], {"running": True, "lastStatus": "running",
                            "lastMessage": f"已投递 {len(ids)} 个账号", "lastRunAt": db.now()})
    if ids:
        asyncio.create_task(_watch(ids))
    else:
        _kv_set(KEYS["state"], {"running": False, "lastStatus": "succeeded",
                                "lastMessage": "没有需要执行的账号", "lastRunAt": db.now()})
    return ids


async def _watch(ids: list[int]) -> None:
    pending = set(ids)
    deadline = asyncio.get_running_loop().time() + 30 * 60
    while pending and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(1.0)
        for jid in list(pending):
            job = jobs.get_job(jid)
            if job and job["status"] in ("succeeded", "failed"):
                pending.discard(jid)
    ok = sum(1 for jid in ids if (jobs.get_job(jid) or {}).get("status") == "succeeded")
    bad = len(ids) - ok
    _kv_set(KEYS["state"], {
        "running": False,
        "lastStatus": "succeeded" if bad == 0 else ("failed" if ok == 0 else "partial"),
        "lastMessage": f"完成 {ok}，失败 {bad}",
        "lastRunAt": db.now(),
    })


async def run_now(trigger: str = "cron") -> dict[str, Any]:
    cfg = get_config()
    today = datetime.now(TZ).weekday()
    accounts = _active_accounts(cfg["weekday"])
    if not accounts:
        accounts = _active_accounts(today)
    if not accounts:
        accounts = _active_accounts(None)
    await run_batch(accounts, trigger=trigger)
    return public_config()


async def loop() -> None:
    """后台循环：到点即跑，按日期去重。"""
    while True:
        try:
            cfg = get_config()
            now = datetime.now(TZ)
            today = now.strftime("%Y-%m-%d")
            last = _kv_get(KEYS["last"]) or {}
            due = (
                cfg["enabled"]
                and now.weekday() == int(cfg["weekday"])
                and now.hour == int(cfg["hour"])
                and abs(now.minute - int(cfg["minute"])) <= 1
                and last.get("date") != today
            )
            if due and not get_state().get("running"):
                _kv_set(KEYS["last"], {"date": today, "at": db.now()})
                await run_now(trigger="cron")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 调度器不能因为一次异常停摆
            _kv_set(KEYS["state"], {"running": False, "lastStatus": "failed",
                                    "lastMessage": f"调度循环异常：{exc}", "lastRunAt": db.now()})
        await asyncio.sleep(settings.scheduler_tick_seconds)


# --------------------------------------------------------------------------- 报表
def scheduler_accounts(date: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    day = date or datetime.now(TZ).strftime("%Y-%m-%d")
    rows = db.q(
        """SELECT a.username AS account, a.name AS username, a.school AS school_name,
                  COALESCE(h.slot, 0) AS slot, COALESCE(a.hosted_at, 0) AS hosted_at,
                  (SELECT COUNT(*) FROM jobs j WHERE j.account=a.username
                     AND j.status='succeeded' AND date(j.finished_at,'unixepoch','+8 hours')=?) AS succeeded,
                  (SELECT COUNT(*) FROM jobs j WHERE j.account=a.username
                     AND j.trigger IN ('hosting','cron') AND date(j.created_at,'unixepoch','+8 hours')=?) AS needed
             FROM accounts a LEFT JOIN hosting h ON h.username=a.username
            WHERE h.active=1
            ORDER BY a.username LIMIT ? OFFSET ?""",
        (day, day, limit, offset),
    )
    total = db.one("SELECT COUNT(*) AS c FROM hosting WHERE active=1")["c"]
    return {"date": day, "total": int(total), "limit": limit, "offset": offset,
            "accounts": [dict(r) for r in rows]}
