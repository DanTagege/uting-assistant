"""跑一次真实的 job 流水线（不经过 HTTP 层），用来看上游适配器在整条链路上的表现。

典型用途：导入 JWT 之后，先确认「取卷 → 组答案 → 交卷」走到哪一步、错在哪一步。

用法：
    set UC_UPSTREAM=unipus
    .venv\\Scripts\\python.exe tools\\check_pipeline.py --username 13800000000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, jobs  # noqa: E402
from app.config import settings  # noqa: E402
from app.upstream import reset_upstream  # noqa: E402


async def main(username: str) -> None:
    db.conn()
    account = db.one("SELECT username,status,uc_jwt_exp,uc_rt_exp FROM accounts WHERE username=?", (username,))
    if account is None:
        raise SystemExit(f"账号 {username} 不存在：先 tools/import_jwt.py 或网页登录一次")
    print(f"账号 {username}: status={account['status']} jwt_exp={account['uc_jwt_exp']} rt_exp={account['uc_rt_exp']}")
    print(f"上游 = {settings.upstream}   real_submit={settings.real_submit}")

    reset_upstream()
    await jobs.start_workers(1)
    job_id = jobs.enqueue(username, {"taskId": "__auto__", "name": "自动选题", "paperHash": "", "audioVisual": 0},
                          trigger="user", source="check")
    print(f"已投递 job #{job_id}，等待…")
    for _ in range(300):
        await asyncio.sleep(0.5)
        job = jobs.get_job(job_id) or {}
        if job.get("status") in ("succeeded", "failed"):
            break
    job = jobs.get_job(job_id) or {}
    print(f"\n结果: status={job.get('status')} score={job.get('score')} message={job.get('message')}")
    print(f"error: {job.get('error')}")
    print("日志：")
    for line in jobs.job_logs(job_id):
        print("   " + line)
    await jobs.stop_workers()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    args = ap.parse_args()
    asyncio.run(main(args.username))
