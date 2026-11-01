"""打印平台账号的令牌有效期（不改任何数据）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402


def main() -> None:
    db.conn()
    now = time.time()
    rows = db.q("SELECT username, uc_jwt_exp, uc_rt_exp, status FROM accounts ORDER BY username")
    if not rows:
        print("平台里还没有账号（先在网页登录一次）")
        return
    for r in rows:
        jwt_ok = float(r["uc_jwt_exp"] or 0) > now
        rt_ok = float(r["uc_rt_exp"] or 0) > now
        print(f"  {r['username']}  "
              f"jwt={'有效' if jwt_ok else '已过期'}"
              f"（{time.strftime('%m-%d %H:%M', time.localtime(float(r['uc_jwt_exp'] or 0)))}）  "
              f"rt={'可续期' if rt_ok else '已过期'}  status={r['status']}")


if __name__ == "__main__":
    main()
