"""用真实令牌取一次卷，打印解析后的题目（重点看多子题/排序题的 subs.options）。"""
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


async def main(phone: str, task_id: str, ans_version: int) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt, uc_rt FROM accounts WHERE username=?", (phone,))
    if not row:
        raise SystemExit("账号不在库里")
    up = UnipusUpstream()
    ctx = UpstreamCtx(account=phone, jwt=row["uc_jwt"], rt=row["uc_rt"] or "", meta={})
    paper = await up.paper(ctx, task_id)
    await up.aclose()
    print(f"paper {paper.get('paperHash')} 共 {len(paper['questions'])} 项")
    for q in paper["questions"]:
        subs = q.get("subs") or []
        print(f"\n#{q['startNo']} {q['type']} action={q['action']} subs={len(subs)} "
              f"options={[o['value'] for o in q.get('options') or []]}")
        for i, s in enumerate(subs[:4]):
            print(f"   sub{i + 1}: prompt={s['prompt'][:40]!r} opts={[o['value'] for o in s['options']]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
