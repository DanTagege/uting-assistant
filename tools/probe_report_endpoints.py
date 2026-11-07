"""探测"训练报告/答题详情"类接口，找用时字段（只读，不改数据）。"""
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
    ("POST", "/api/uls/report/paperReport", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/report/taskReport", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/report/paperDetail", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/user/getUserAnswerSheet", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/user/getAnswerSheet", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/user/getAnswerDetail", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/user/getTaskDetail", {"taskId": "{tid}", "ansVersion": 1}),
    ("POST", "/api/uls/user/loadGradedQuestions", {"taskId": "{tid}", "ansVersion": 1}),
]


async def main(phone: str, task_id: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt FROM accounts WHERE username=?", (phone,))
    headers = {"authorization": row["uc_jwt"], "u-app-id": "116",
               "content-type": "application/json", "accept": "application/json, text/plain, */*"}
    async with httpx.AsyncClient(timeout=25) as c:
        for method, path, tpl in CANDIDATES:
            body = {k: (v.replace("{tid}", task_id) if isinstance(v, str) else v) for k, v in tpl.items()}
            url = ADAPTIVE_ORIGIN + path
            try:
                r = await c.post(url, headers=headers, json=body) if method == "POST" else await c.get(url, headers=headers)
                text = r.text.strip().replace("\n", " ")
                snippet = text[:220]
                print(f"{path:<44} {r.status_code}  {snippet}")
            except Exception as exc:  # noqa: BLE001
                print(f"{path:<44} ERR {exc}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
