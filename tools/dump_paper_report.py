"""打印 paperReport 的字段结构，定位"用时/训练时长"字段名与格式。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.upstream.unipus import ADAPTIVE_ORIGIN  # noqa: E402

HINT = ("time", "duration", "cost", "spend", "elapsed", "used", "second", "start", "end", "count")


def show(node, prefix="", depth=0, lines=None):
    lines = lines if lines is not None else []
    if depth > 2:
        return lines
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k} = <{type(v).__name__} len={len(v)}>")
                show(v, prefix + k + ".", depth + 1, lines)
            else:
                mark = " <<<" if any(h in k.lower() for h in HINT) else ""
                sval = str(v)
                lines.append(f"{prefix}{k} = {sval[:60]}{mark}")
    elif isinstance(node, list) and node:
        show(node[0], prefix + "[0].", depth + 1, lines)
    return lines


async def main(phone: str, task_id: str) -> None:
    db.conn()
    row = db.one("SELECT uc_jwt FROM accounts WHERE username=?", (phone,))
    headers = {"authorization": row["uc_jwt"], "u-app-id": "116", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(ADAPTIVE_ORIGIN + "/api/uls/report/paperReport",
                         headers=headers, json={"taskId": task_id, "ansVersion": 1})
    data = r.json()
    value = data.get("value") or {}
    print(f"=== paperReport({task_id}) 顶层键 ===")
    print(sorted(value.keys()))
    print("\n=== 结构（只看前两层）===")
    for line in show(value)[:60]:
        print(" ", line)
    # 逐题答案里是否带用时
    arr = value.get("questions") or value.get("gradedQuestions") or value.get("answers") or []
    if isinstance(arr, list) and arr:
        print("\n=== 单题字段 ===")
        print(sorted(arr[0].keys()))
        print(json.dumps(arr[0], ensure_ascii=False)[:400])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
