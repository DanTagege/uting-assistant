"""只读探测：拿各账号的定级报告，摸清"分数 → 级别"的映射。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.upstream.unipus import ADAPTIVE_ORIGIN, UCLOUD_ORIGIN  # noqa: E402

CANDIDATES = [
    ("POST", f"{ADAPTIVE_ORIGIN}/api/uls/report/levelReport", None),
    ("POST", f"{ADAPTIVE_ORIGIN}/api/uls/report/oralLevelReport", None),
    ("GET", f"{ADAPTIVE_ORIGIN}/api/uls/user/getUserStatus?flowType=listen", None),
    ("GET", f"{UCLOUD_ORIGIN}/api/uls/user/getUserStatusForApp?flowType=listen", None),
]


async def probe(phone: str, jwt: str) -> None:
    print(f"\n=== {phone} ===")
    headers = {"authorization": jwt, "u-app-id": "116", "content-type": "application/json",
               "accept": "application/json, text/plain, */*"}
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as c:
        for method, url, body in CANDIDATES:
            try:
                if method == "POST":
                    r = await c.post(url, headers=headers, json=body or {})
                else:
                    r = await c.get(url, headers=headers)
                text = r.text.strip().replace("\n", " ")
                print(f"  {method:4} {url.split('/api')[1][:46]:<46} {r.status_code}  {text[:260]}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {method:4} {url[:40]} ERR {exc}")


async def main() -> None:
    db.conn()
    rows = db.q("SELECT username, uc_jwt FROM accounts ORDER BY username")
    for r in rows:
        if not r["uc_jwt"]:
            continue
        await probe(r["username"], r["uc_jwt"])


if __name__ == "__main__":
    asyncio.run(main())
