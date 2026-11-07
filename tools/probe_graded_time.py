"""看"已判分题目"响应里有没有每题用时/作答时间等字段。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.upstream.base import UpstreamCtx  # noqa: E402
from app.upstream.unipus import UnipusUpstream  # noqa: E402

HINT = ("time", "duration", "cost", "spend", "elapsed", "used", "second", "start", "end")


def scan(node, prefix="", depth=0, out=None):
    out = out if out is not None else []
    if depth > 3:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            low = k.lower()
            if any(h in low for h in HINT) and not isinstance(v, (dict, list)):
                out.append((prefix + k, v))
            elif isinstance(v, (dict, list)):
                scan(v, prefix + k + ".", depth + 1, out)
    elif isinstance(node, list) and node:
        scan(node[0], prefix + "[0].", depth + 1, out)
    return out


async def main(phone: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt, uc_rt FROM accounts WHERE username=?", (phone,))
    up = UnipusUpstream()
    ctx = UpstreamCtx(account=phone, jwt=row["uc_jwt"], rt=row["uc_rt"] or "", meta={})
    tasks = await up._history(ctx, "listen")
    tasks = tasks if isinstance(tasks, list) else []
    if not tasks:
        raise SystemExit("该账号没有历史")
    task = tasks[0]
    task_id = str(task.get("taskId"))
    print(f"样本任务 {task_id} score={task.get('score')}")
    print("history 原始项:", json.dumps(task, ensure_ascii=False)[:400])
    try:
        graded = await up._read_score(ctx, task_id, int(task.get("ansVersion") or 1), None)
        print("read_score 返回:", graded)
    except Exception as exc:  # noqa: BLE001
        print("read_score 失败:", exc)
    await up.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
