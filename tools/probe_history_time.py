"""在所有账号的历史里找一条 startTime/endTime 非空的记录，看真实格式。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.upstream.base import UpstreamCtx  # noqa: E402
from app.upstream.unipus import UnipusUpstream  # noqa: E402


async def main() -> None:
    db.conn()
    rows = db.q("SELECT username, uc_jwt, uc_rt FROM accounts")
    up = UnipusUpstream()
    found = 0
    for row in rows:
        phone = row["username"]
        ctx = UpstreamCtx(account=phone, jwt=row["uc_jwt"], rt=row["uc_rt"] or "", meta={})
        try:
            tasks = await up._history(ctx, "listen")
        except Exception as exc:  # noqa: BLE001
            print(f"{phone}: 读取失败 {exc}")
            continue
        tasks = tasks if isinstance(tasks, list) else []
        for t in tasks:
            st, et = t.get("startTime"), t.get("endTime")
            if st or et:
                found += 1
                print(f"{phone}  {t.get('taskId')}  score={t.get('score')}  "
                      f"startTime={st!r}  endTime={et!r}  qc={t.get('questionCount')} gc={t.get('gradedCount')}")
        print(f"{phone}: {len(tasks)} 条历史，其中带时间的 {sum(1 for t in tasks if t.get('startTime') or t.get('endTime'))} 条")
    await up.aclose()
    print(f"\n共找到 {found} 条带时间的记录")


if __name__ == "__main__":
    asyncio.run(main())
