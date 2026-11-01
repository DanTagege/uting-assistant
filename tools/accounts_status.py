"""列出平台里每个账号的真实周进度（用它自己存的 JWT 去问 U校园）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.upstream.unipus import UnipusUpstream  # noqa: E402
from app.upstream.base import UpstreamCtx, UpstreamError  # noqa: E402


def mask(p: str) -> str:
    return p[:3] + "****" + p[-4:] if len(p) == 11 else p


async def main() -> None:
    db.conn()
    rows = db.q("SELECT username,status,uc_jwt,uc_rt,uc_jwt_exp FROM accounts ORDER BY username")
    up = UnipusUpstream()
    for r in rows:
        print(f"\n=== {mask(r['username'])}  status={r['status']}  jwt={'有' if r['uc_jwt'] else '无'} ===")
        if not r["uc_jwt"]:
            print("  无令牌，跳过")
            continue
        ctx = UpstreamCtx(account=r["username"], jwt=r["uc_jwt"], rt=r["uc_rt"] or "")
        try:
            for flow in ("listen", "speak"):
                w = await up._week(ctx, flow)
                print(f"  {flow}: {w['done']}/{w['total']}  type={w['type']}  level={w['level']}")
            t = await up._task(ctx, "listen", 0)
            print(f"  当前听力任务: {t['taskId'] if t else '无'}")
        except UpstreamError as exc:
            print(f"  读取失败: {exc.reason} {exc}")
    await up.aclose()


if __name__ == "__main__":
    asyncio.run(main())
