"""演示数据：造几个账号 + 托管 + 题库版本，便于直接看到调度/答题全链路。

用法：
    .venv\\Scripts\\python.exe tools\\seed_demo.py          # 只造数据
    .venv\\Scripts\\python.exe tools\\seed_demo.py --run    # 造完数据后立刻跑一轮托管
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import answer_bank, db, scheduler  # noqa: E402
from app.config import settings  # noqa: E402
from app.upstream.mock import answer_key, paper_hash_of, task_id_for  # noqa: E402

DEMO = [
    ("13800000001", "李小明", "示例大学"),
    ("13800000002", "王小雨", "上海立信会计金融学院"),
    ("13800000003", "赵小刚", "某某学院"),
]
ADMIN = "13900000000"


def seed() -> None:
    db.conn()
    ts = time.time()
    for i, (user, name, school) in enumerate(DEMO):
        db.ex("""INSERT INTO accounts(username,password,name,school,is_admin,status,uc_jwt,uc_rt,
                                     uc_jwt_exp,uc_rt_exp,created_at,last_seen_at)
                 VALUES(?,?,?,?,0,'active',?,?,?,?,?,?)
                 ON CONFLICT(username) DO UPDATE SET name=excluded.name, school=excluded.school""",
              (user, "demo1234", name, school,
               "mock-jwt-" + secrets.token_hex(12), "mock-rt-" + secrets.token_hex(12),
               ts + 7200, ts + settings.rt_ttl_seconds, ts, ts))
        slot = i % 7
        db.ex("""INSERT INTO hosting(username,active,slot,updated_at) VALUES(?,1,?,?)
                 ON CONFLICT(username) DO UPDATE SET active=1, slot=excluded.slot, updated_at=excluded.updated_at""",
              (user, slot, ts))
        db.ex("UPDATE accounts SET hosted_at=? WHERE username=?", (ts, user))

    db.ex("""INSERT INTO accounts(username,password,name,school,is_admin,status,uc_jwt,uc_rt,
                                 uc_jwt_exp,uc_rt_exp,created_at,last_seen_at)
             VALUES(?,?,?,?,1,'active',?,?,?,?,?,?)
             ON CONFLICT(username) DO UPDATE SET is_admin=1""",
          (ADMIN, "admin1234", "管理员", "示例大学",
           "mock-jwt-" + secrets.token_hex(12), "mock-rt-" + secrets.token_hex(12),
           ts + 7200, ts + settings.rt_ttl_seconds, ts, ts))

    # 为每个账号的当周任务准备 3 套答案（v0 全对，v1/v2 各错 1/2 题）
    seeded = 0
    for user, _, _ in DEMO:
        for i in range(1, settings.weekly_quota + 1):
            tid = task_id_for(user, i, "adaptive")
            ph = paper_hash_of(tid)
            answer_bank.seed_demo_versions(ph, [], answer_key(ph))
            seeded += 1
        for i in range(1, 4):
            tid = task_id_for(user, i, "teacher")
            ph = paper_hash_of(tid)
            answer_bank.seed_demo_versions(ph, [], answer_key(ph))
            seeded += 1

    print(f"已写入 {len(DEMO)} 个演示账号 + 1 个管理员（{ADMIN} / admin1234），题库 {seeded} 张卷 x 3 版本")
    print("演示账号密码统一为 demo1234，登录任意一个即可看到工作台")


async def run_once() -> None:
    from app import jobs

    await jobs.start_workers(settings.default_parallelism)
    ids = await scheduler.run_batch([u for u, _, _ in DEMO], trigger="hosting")
    print(f"已投递 {len(ids)} 个托管任务，等待完成…")
    for _ in range(600):
        await asyncio.sleep(0.2)
        done = [jobs.get_job(i) for i in ids]
        if all(d and d["status"] in ("succeeded", "failed") for d in done):
            break
    for i in ids:
        j = jobs.get_job(i) or {}
        print(f"  job #{i} {j.get('account')} -> {j.get('status')} score={j.get('score')}")
    await jobs.stop_workers()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="造数据后立即跑一轮托管")
    args = ap.parse_args()
    seed()
    if args.run:
        asyncio.run(run_once())
