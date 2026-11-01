"""导入 JWT / RT（浏览器登录后拿到的令牌），避免在服务里存密码。

为什么要这个：U校园 SSO 登录可能触发滑块（业务码 1506），
此时用浏览器登录 https://uadaptive.unipus.cn，从 localStorage / Network 里取出 jwt，
直接写进账号档案即可，平台后续用 refresh_jwt 自动续期。

用法：
    python tools/import_jwt.py --username 13800000000 --jwt eyJ... [--rt xxx] [--admin]
    python tools/import_jwt.py --username 13800000000 --jwt-file jwt.txt
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.upstream.unipus import decode_jwt_payload  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True, help="U校园手机号（11 位）")
    ap.add_argument("--jwt", default="", help="裸 JWT（不要带 Bearer ）")
    ap.add_argument("--jwt-file", default="", help="从文件读取 JWT（可含首尾空白）")
    ap.add_argument("--rt", default="", help="refresh token（可选，能自动续期）")
    ap.add_argument("--admin", action="store_true", help="顺带授予管理员")
    args = ap.parse_args()

    tok = args.jwt.strip()
    if not tok and args.jwt_file:
        tok = Path(args.jwt_file).read_text(encoding="utf-8").strip()
    tok = tok.replace("Bearer", "").strip()
    if not tok:
        raise SystemExit("缺少 --jwt 或 --jwt-file")

    payload = decode_jwt_payload(tok)
    exp = float(payload.get("exp") or 0)
    ts = time.time()
    status = "active" if exp == 0 or exp > ts else "relogin_required"

    db.conn()
    db.ex("""INSERT INTO accounts(username,password,name,school,is_admin,status,uc_jwt,uc_rt,
                                 uc_jwt_exp,uc_rt_exp,created_at,last_seen_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(username) DO UPDATE SET
               uc_jwt=excluded.uc_jwt, uc_rt=excluded.uc_rt, uc_jwt_exp=excluded.uc_jwt_exp,
               uc_rt_exp=excluded.uc_rt_exp, status=excluded.status, last_seen_at=excluded.last_seen_at,
               is_admin=MAX(accounts.is_admin, excluded.is_admin)""",
          (args.username, "", str(payload.get("name") or ""), str(payload.get("schoolName") or ""),
           1 if args.admin else 0, status, tok, args.rt, exp or ts + 48 * 3600,
           ts + 30 * 86400, ts, ts))

    left = (exp - ts) / 3600 if exp else float("nan")
    print(f"已写入账号 {args.username}：status={status} jwt_exp_in={left:.1f}h rt={'有' if args.rt else '无'}")
    print("提示：把 UC_UPSTREAM=unipus 写进 .env，然后重启服务，即可复用这个令牌。")


if __name__ == "__main__":
    main()
