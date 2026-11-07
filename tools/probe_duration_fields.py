"""看平台到底记录了什么"训练时长"：history 里每个字段都打印，找时长相关键。"""
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

TIME_HINT = ("duration", "time", "cost", "seconds", "spend", "elapsed", "used")


async def main(phone: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt, uc_rt FROM accounts WHERE username=?", (phone,))
    if not row:
        raise SystemExit("账号不在库里")
    up = UnipusUpstream()
    ctx = UpstreamCtx(account=phone, jwt=row["uc_jwt"], rt=row["uc_rt"] or "", meta={})
    hist = await up._history(ctx, "listen")
    tasks = hist if isinstance(hist, list) else (hist.get("tasks") or [])
    print(f"=== {phone} 历史 {len(tasks)} 条 ===")
    for t in tasks[:6]:
        keys = sorted(t.keys())
        print(f"\n- taskId={t.get('taskId')} title={t.get('title') or t.get('paperTitle')} score={t.get('score')}")
        for k in keys:
            v = t[k]
            mark = "  <<<" if any(h in k.lower() for h in TIME_HINT) else ""
            sval = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            print(f"    {k} = {sval[:70]}{mark}")
    await up.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
