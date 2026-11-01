"""定级后（train_plan）探测：看哪个接口能把计划/首篇推出来。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.upstream.unipus import ADAPTIVE_ORIGIN, UCLOUD_ORIGIN  # noqa: E402

CALLS = [
    ("GET", f"{ADAPTIVE_ORIGIN}/api/uls/plan/current?trainType=listen", None),
    ("GET", f"{ADAPTIVE_ORIGIN}/api/uls/plan/current?trainType=1", None),
    ("GET", f"{ADAPTIVE_ORIGIN}/api/uls/user/getUserStatus?flowType=listen", None),
    ("GET", f"{UCLOUD_ORIGIN}/api/uls/user/getUserStatusForApp?flowType=listen", None),
    ("POST", f"{ADAPTIVE_ORIGIN}/api/uls/report/levelReport", None),
    ("POST", f"{ADAPTIVE_ORIGIN}/api/uls/report/paperReport", None),
]


async def main(phone: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt FROM accounts WHERE username=?", (phone,))
    if not row:
        raise SystemExit("账号不在库里")
    headers = {"authorization": row["uc_jwt"], "u-app-id": "116",
               "content-type": "application/json", "accept": "application/json, text/plain, */*"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
        for method, url, body in CALLS:
            try:
                r = await (c.post(url, headers=headers, json=body or {}) if method == "POST"
                           else c.get(url, headers=headers))
                text = r.text.strip().replace("\n", " ")
                print(f"{method:4} {url.split('/api')[1][:44]:<44} {r.status_code}  {text[:300]}")
            except Exception as exc:  # noqa: BLE001
                print(f"{method:4} {url[:50]} ERR {exc}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
