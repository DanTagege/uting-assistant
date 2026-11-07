"""探测计时服务：连 socket.io（kick-v2/stop）→ 查服务端累计时长。

只做"连接 + 上报"，不取卷、不交卷，可反复运行。
用法：python tools/probe_timing_socket.py <手机号> <taskId> [ansVersion] [保持秒数]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import socketio  # noqa: E402

from app import db  # noqa: E402
from app.upstream.base import UpstreamCtx  # noqa: E402
from app.upstream.unipus import ADAPTIVE_ORIGIN, UnipusUpstream  # noqa: E402

SOCKET_HOST = "https://ucontent.unipus.cn"
NS = "/userActivities"
SIO_PATH = "/unipusio"
DURATION_API = "https://ucontentapi.unipus.cn/course/api/v4/statistic/uls/user/total/duration"


async def find_open_id(up: UnipusUpstream, ctx: UpstreamCtx, task_id: str) -> str:
    try:
        resp = await up._request(
            "POST", f"{ADAPTIVE_ORIGIN}/api/uls/user/loadGradedQuestions",
            headers=up._headers(ctx), body={"taskId": task_id, "ansVersion": 1})
        data = up._check(resp, "loadGradedQuestions")
        val = data.get("value") or []
        if isinstance(val, list) and val and val[0].get("userId"):
            return str(val[0]["userId"])
    except Exception as exc:  # noqa: BLE001
        print("  loadGradedQuestions 失败:", exc)
    return ""


async def query_duration(jwt: str, module_group: str, open_id: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(DURATION_API,
                             headers={"Content-Type": "application/json", "Authorization": jwt},
                             json={"moduleGroup": module_group, "openIds": [open_id]})
            print(f"  查时长 -> {r.status_code} {r.text[:260]}")
    except Exception as exc:  # noqa: BLE001
        print("  查时长失败:", exc)


async def main() -> None:
    phone = sys.argv[1]
    task_id = sys.argv[2]
    ans_version = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    hold = int(sys.argv[4]) if len(sys.argv) > 4 else 15

    db.conn()
    row = db.one("SELECT uc_jwt, uc_rt FROM accounts WHERE username=?", (phone,))
    if not row:
        raise SystemExit("账号不在库里")
    jwt = row["uc_jwt"]
    up = UnipusUpstream()
    ctx = UpstreamCtx(account=phone, jwt=jwt, rt=row["uc_rt"] or "", meta={})

    open_id = await find_open_id(up, ctx, task_id)
    await up.aclose()

    module_group = f"uls:{task_id}_{ans_version}"
    print(f"账号 {phone}  task={task_id}  ansVersion={ans_version}")
    print(f"openId = {open_id or '(未找到)'}  moduleGroup = {module_group}")
    await query_duration(jwt, module_group, open_id)

    payload = {
        "openId": open_id,
        "module": f"uls_{task_id}",
        "moduleGroup": module_group,
        "type": "train",
        "enterStatus": "ans",
        "apptype": "listen",
        "anstype": "ans",
        "usertype": "student",
        "sendTimestamp": int(time.time() * 1000),
    }

    sio = socketio.AsyncClient(reconnection=False)
    try:
        qs = urllib.parse.urlencode({"uuid": open_id, "token": jwt})
        await sio.connect(f"{SOCKET_HOST}{NS}?{qs}", transports=["websocket"], socketio_path=SIO_PATH,
                          namespaces=[NS], wait_timeout=20, wait=True,
                          headers={"Authorization": jwt})
    except Exception as exc:  # noqa: BLE001
        print("  连接异常:", type(exc).__name__, exc)

    await asyncio.sleep(3)
    print("  connected =", sio.connected, " sid =", getattr(sio, "sid", None))

    try:
        ack = await sio.call("kick-v2", payload, namespace=NS, timeout=15)
        print("  kick-v2 回执:", json.dumps(ack, ensure_ascii=False)[:300])
    except Exception as exc:  # noqa: BLE001
        print("  kick-v2 异常:", type(exc).__name__, exc)

    print(f"  保持 {hold}s（模拟作答中）…")
    await asyncio.sleep(hold)
    await query_duration(jwt, module_group, open_id)

    try:
        ack = await sio.call("stop", {**payload, "sendTimestamp": int(time.time() * 1000)},
                             namespace=NS, timeout=15)
        print("  stop 回执:", json.dumps(ack, ensure_ascii=False)[:300])
    except Exception as exc:  # noqa: BLE001
        print("  stop 异常:", type(exc).__name__, exc)

    await query_duration(jwt, module_group, open_id)
    if sio.connected:
        await sio.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
