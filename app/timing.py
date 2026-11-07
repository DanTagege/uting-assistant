"""训练计时会话。

U校园 的「训练时长」不是 submitAnswer 的 duration，而是由一个独立的计时服务统计：
    连接 socket.io  https://ucontent.unipus.cn  命名空间 /userActivities  路径 /unipusio
    事件 kick-v2（进入房间 / 心跳）与 stop（离开房间）
    服务端按 moduleGroup = "uls:<taskId>_<ansVersion>" 累计秒数，
    可通过 POST https://ucontentapi.unipus.cn/course/api/v4/statistic/uls/user/total/duration 查询。

本模块负责在作答期间维持这个会话，让平台记录的时长与我们真实的作答时长一致。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import socketio

SOCKET_HOST = "https://ucontent.unipus.cn"
NAMESPACE = "/userActivities"
SIO_PATH = "/unipusio"
DURATION_API = "https://ucontentapi.unipus.cn/course/api/v4/statistic/uls/user/total/duration"
HEARTBEAT_SECONDS = 20


def open_id_from_jwt(jwt: str) -> str:
    """从 U校园 JWT 里取 openId（计时服务的用户主键）。"""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return str(data.get("openId") or "")
    except Exception:  # noqa: BLE001
        return ""


class TimingSession:
    """一次作答对应的计时会话；失败不抛异常，只记录状态。"""

    def __init__(self, jwt: str, task_id: str, ans_version: int, *,
                 modules: list[str] | None = None, module: str = "",
                 apptype: str = "listen") -> None:
        self.jwt = jwt
        self.open_id = open_id_from_jwt(jwt)
        self.task_id = str(task_id)
        self.ans_version = int(ans_version or 1)
        mods = [m for m in (modules or []) if m]
        self.modules: list[str] = mods or ([module] if module else [f"uls_{self.task_id}"])
        self.module = self.modules[0]
        self.apptype = apptype
        self.sio: socketio.AsyncClient | None = None
        self.connected = False
        self.kick_ack: dict[str, Any] | None = None
        self.module_acks: list[Any] = []
        self.error = ""
        self._beat: asyncio.Task | None = None
        self._started_at = 0.0

    @property
    def module_group(self) -> str:
        return f"uls:{self.task_id}_{self.ans_version}"

    def _payload(self, module: str | None = None) -> dict[str, Any]:
        return {
            "openId": self.open_id,
            "module": module or self.modules[0],
            "moduleGroup": self.module_group,
            "type": "train",
            "enterStatus": "ans",
            "apptype": self.apptype,
            "anstype": "ans",
            "usertype": "student",
            "sendTimestamp": int(time.time() * 1000),
        }

    async def start(self) -> bool:
        if not self.open_id:
            self.error = "缺少 openId"
            return False
        try:
            self.sio = socketio.AsyncClient(reconnection=False)
            qs = f"uuid={self.open_id}&token={self.jwt}"
            await self.sio.connect(f"{SOCKET_HOST}{NAMESPACE}?{qs}",
                                   transports=["websocket"], socketio_path=SIO_PATH,
                                   namespaces=[NAMESPACE], wait_timeout=20, wait=True,
                                   headers={"Authorization": self.jwt})
            acks: list[Any] = []
            for mod in self.modules:
                acks.append(await self.sio.call("kick-v2", self._payload(mod),
                                                namespace=NAMESPACE, timeout=15))
            self.module_acks = acks
            self.kick_ack = acks[0] if isinstance(acks[0], dict) else {"raw": str(acks[0])}
            code = int(self.kick_ack.get("code") or 0)
            self.connected = code == 0
            if not self.connected:
                self.error = str(self.kick_ack)[:200]
                return False
            self._started_at = time.time()
            self._beat = asyncio.create_task(self._heartbeat())
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            self.connected = False
            return False

    async def _heartbeat(self) -> None:
        while self.connected and self.sio is not None:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not self.connected or self.sio is None:
                return
            try:
                for mod in self.modules:
                    await self.sio.call("kick-v2", self._payload(mod),
                                        namespace=NAMESPACE, timeout=10)
            except Exception:  # noqa: BLE001 - 心跳失败不影响作答
                return

    async def stop(self) -> None:
        if self._beat is not None:
            self._beat.cancel()
            self._beat = None
        if self.sio is None:
            return
        try:
            if self.connected:
                for mod in self.modules:
                    await self.sio.call("stop", self._payload(mod),
                                        namespace=NAMESPACE, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.connected = False

    def elapsed(self) -> int:
        return int(time.time() - self._started_at) if self._started_at else 0


async def query_duration(jwt: str, module_group: str, open_id: str) -> int | None:
    """查服务端累计的训练秒数（用于自检/日志）。"""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(DURATION_API,
                             headers={"Content-Type": "application/json", "Authorization": jwt},
                             json={"moduleGroup": module_group, "openIds": [open_id]})
            data = r.json()
            stat = ((data.get("data") or [{}])[0].get("statistic") or [{}])[0]
            val = stat.get("duration")
            return int(val) if val is not None else None
    except Exception:  # noqa: BLE001
        return None
