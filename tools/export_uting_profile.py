"""把平台里某个账号的令牌导出成 uting 的 state.json（这样 CLI 能直接操作它）。

用法：python tools/export_uting_profile.py --phone 18321531958 --dir D:\\Ucampus\\data\\uting_b
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from uting.client import jwt_exp  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True)
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    db.conn()
    row = db.one("SELECT username,uc_jwt,uc_rt,uc_jwt_exp,uc_rt_exp FROM accounts WHERE username=?", (args.phone,))
    if row is None or not row["uc_jwt"]:
        raise SystemExit(f"平台里没有 {args.phone} 的令牌（先在网页登录一次）")

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    state = {
        "phone": row["username"],
        "jwt": row["uc_jwt"],
        "rt": row["uc_rt"] or "",
        "jwt_exp": float(row["uc_jwt_exp"] or jwt_exp(row["uc_jwt"]) or time.time() + 48 * 3600),
        "rt_exp": float(row["uc_rt_exp"] or time.time() + 30 * 86400),
        "saved_at": time.time(),
    }
    (out / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出到 {out / 'state.json'}（账号 {row['username']}）")
    print(f"用法：$env:UTING_HOME='{out}'; python -m uting verify")


if __name__ == "__main__":
    main()
