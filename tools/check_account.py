"""看某个账号"定级之后"的真实状态：报告 + 周进度 + 当前任务。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.upstream.base import UpstreamCtx, UpstreamError  # noqa: E402
from app.upstream.unipus import UnipusUpstream  # noqa: E402


async def main(phone: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt, uc_rt FROM accounts WHERE username=?", (phone,))
    if not row:
        raise SystemExit("账号不在库里")
    up = UnipusUpstream()
    ctx = UpstreamCtx(account=phone, jwt=row["uc_jwt"], rt=row["uc_rt"] or "", meta={})

    print(f"=== {phone} ===")
    try:
        st = await up.status(ctx, "listen")
        print("getUserStatus:", {k: st.get(k) for k in ("type", "taskId", "ansVersion", "status",
                                                        "initLevel", "currentLevel", "targetLevel")})
    except UpstreamError as exc:
        print("getUserStatus 失败:", exc)

    try:
        rep = await up.level_report(ctx)
        print("levelReport:", rep)
    except UpstreamError as exc:
        print("levelReport:", exc.reason, exc)

    for flow in ("listen", "speak"):
        try:
            w = await up._week(ctx, flow)
            print(f"week[{flow}]:", {k: w.get(k) for k in ("done", "total", "type", "level", "placement")})
        except UpstreamError as exc:
            print(f"week[{flow}] 失败:", exc)

    try:
        ov = await up.overview(ctx)
        print("overview.adaptive:", ov["adaptive"]["week"], "current=",
              (ov["adaptive"]["current"] or {}).get("taskId"))
        print("overview.assigned:", len(ov["assigned"]["tasks"]), "history:", len(ov["history"]["tasks"]))
    except UpstreamError as exc:
        print("overview 失败:", exc)

    await up.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
