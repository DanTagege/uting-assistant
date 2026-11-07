"""U 听说（U听力 / U口语）真实上游适配器。

接口约定（以线上实际行为为准，本文件不发明任何 path）：
  k4f7/unipus-mcp · docs/api-notes.md + src/{config,http,sso-login,submit-answer,week-progress}.ts
  抓包时间点：2026-09-21 ~ 2026-10-14（App 2.8.4 / H5 SPA）

已实测（本机 2026-10-26，无凭证探测）：
  GET  ucloud/api/uls/user/getUserStatusForApp  -> 401 {"message":"Missing JWT token in request"}
  POST uadaptive/api/uls/user/loadPaper         -> 401 {"message":"Missing JWT token in request"}
  POST sso/sso/0.1/sso/cip/login                -> 200 {"code":"2012","msg":"用户名或者密码错误"}
      （AES-CBC 加密体被服务端正常解密，说明密钥/格式正确）

关键约定：
  · Authorization 是**裸 JWT**，不能加 "Bearer "
  · 大部分 uls 接口成功业务码 = 1；conversation/ebcp = 200；login/SSO = "0"
  · getUserStatusForApp 需要头 `u-app-id: 116`
  · loadPaper 返回的 token 必须带回 submitAnswer，否则 4021「请勿多设备同时作答」
  · 4021 多端冲突 / 4013 已提交 / 4295 userData 与小题数不匹配
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from typing import Any

import httpx

from .. import db
from ..config import settings
from .. import runtime
from .base import LogFn, SubmitResult, Upstream, UpstreamCtx, UpstreamError, UpstreamSession
from .paper_parse import normalize

# --------------------------------------------------------------------------- 常量
SSO_ORIGIN = "https://sso.unipus.cn"
SSO_LOGIN_PATH = "/sso/0.1/sso/cip/login"
SSO_REFRESH_PATH = "/sso/4.0/sso/refresh_jwt"
SSO_SERVICE = "https://ucloud.unipus.cn/"

UCLOUD_ORIGIN = "https://ucloud.unipus.cn"
ADAPTIVE_ORIGIN = "https://uadaptive.unipus.cn"

P_STATUS_FOR_APP = "/api/uls/user/getUserStatusForApp"     # GET ?flowType=listen|speak
P_USER_STATUS = "/api/uls/user/getUserStatus"             # GET ?flowType=
P_LOAD_PAPER = "/api/uls/user/loadPaper"                  # POST {taskId, ansVersion}
P_PART_GET = "/api/uls/part/get"                          # GET ?taskId=&ansVersion=
P_SUBMIT_ANSWER = "/api/uls/user/submitAnswer"            # POST {taskId,ansVersion,token,duration,userData}
P_SAVE_SNAPSHOT = "/api/uls/user/saveSnapshot"            # POST 同上（作答中快照）
P_LOAD_GRADED = "/api/uls/user/loadGradedQuestions"       # POST {taskId,ansVersion}
P_HISTORY = "/api/uls/user/getHistoryTrainingTasks"       # POST {pageSize,page,flowType}
P_REPORT_CHECK = "/api/uls/report/checkStatus"            # POST

# sso.unipus.cn 前端 helper.js 里的公开常量（不是账号密钥）
SSO_AES_KEY = bytes.fromhex("8AD70B641C024C7ADA2ECD082EC0334F")
SSO_AES_IV = bytes.fromhex("0102030405060708090A0B0C0D0E0F10")

OK_CODES = {0, 1, 200}
CODE_MULTI_DEVICE = 4021
CODE_ALREADY_SUBMITTED = 4013
CODE_COVERAGE_MISMATCH = 4295
CODE_CAPTCHA = 1506


# --------------------------------------------------------------------------- 工具
def encrypt_sso_field(plain: str) -> str:
    """AES-128-CBC + 大写 HEX（与 sso 前端 helper.js 一致）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # type: ignore

    encryptor = Cipher(algorithms.AES(SSO_AES_KEY), modes.CBC(SSO_AES_IV)).encryptor()
    data = encryptor.update(plain.encode("utf-8")) + encryptor.finalize()
    return data.hex().upper()


def encrypt_sso_field_fallback(plain: str) -> str:
    """无 cryptography 时的纯 Python 实现（AES-128-CBC）。"""
    try:
        return encrypt_sso_field(plain)
    except Exception:  # noqa: BLE001 - 走纯 Python 回退
        return _aes_cbc_hex(plain)


def _aes_cbc_hex(plain: str) -> str:
    # 极简 AES-128 实现（仅 CBC 加密一个/多个块），避免额外依赖
    key = SSO_AES_KEY
    iv = SSO_AES_IV
    data = plain.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _aes_encrypt_block(block, key)
        out += prev
    return out.hex().upper()


def _aes_encrypt_block(block: bytes, key: bytes) -> bytes:
    s = list(block)
    rk = _expand_key(key)
    _add_round_key(s, rk, 0)
    for rnd in range(1, 11):
        s = [_sbox[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]          # ShiftRows
        if rnd != 10:
            s = _mix_columns(s)
        _add_round_key(s, rk, rnd)
    return bytes(s)


def _expand_key(key: bytes) -> list[list[int]]:
    nb, nk, nr = 4, 4, 10
    w = [list(key[i * 4:(i + 1) * 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, nb * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_sbox[b] for b in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(nr + 1)]


def _add_round_key(s: list[int], rk: list[list[int]], rnd: int) -> None:
    for i in range(16):
        s[i] ^= rk[rnd][i]


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _mix_columns(s: list[int]) -> list[int]:
    out = [0] * 16
    for c in range(4):
        col = s[c * 4:c * 4 + 4]
        out[c * 4 + 0] = _gmul(col[0], 2) ^ _gmul(col[1], 3) ^ col[2] ^ col[3]
        out[c * 4 + 1] = col[0] ^ _gmul(col[1], 2) ^ _gmul(col[2], 3) ^ col[3]
        out[c * 4 + 2] = col[0] ^ col[1] ^ _gmul(col[2], 2) ^ _gmul(col[3], 3)
        out[c * 4 + 3] = _gmul(col[0], 3) ^ col[1] ^ col[2] ^ _gmul(col[3], 2)
    return out


_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
_sbox = _SBOX


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """不验签地读 JWT payload（只用来取 exp / openId / userId）。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _dig(data: Any, *keys: str, default: Any = None) -> Any:
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# --------------------------------------------------------------------------- 适配器
class UnipusUpstream(Upstream):
    """U听说真实上游。strict=True：题库没有答案时拒绝提交，绝不猜题。"""

    name = "unipus"
    strict = True
    supports_oral = False      # 口语/跟读需要 Clio 打分 + 七牛上传，本仓库未实现
    supports_skip = False      # 纯学习段不提交

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ HTTP
    @property
    def client(self) -> httpx.AsyncClient:
        """按"当前运行的事件循环"复用客户端。

        关键：httpx.AsyncClient 绑定创建它的事件循环。同步路由里用 asyncio.run()
        会每次新建循环，复用旧客户端就会 `RuntimeError: Event loop is closed`。
        所以这里检测循环变化并重建。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._client is None or self._client_loop is not loop:
            if self._client is not None and not self._client.is_closed:
                try:
                    self._client._transport = None  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            self._client = httpx.AsyncClient(
                timeout=settings.upstream_timeout,
                follow_redirects=False,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "user-agent": settings.upstream_user_agent,
                },
            )
            self._client_loop = loop
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass                     # 循环已关闭，交给 GC
        self._client = None
        self._client_loop = None

    async def _request(self, method: str, url: str, *, headers: dict[str, str],
                       params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self.client.request(method, url, headers=headers, params=params, json=body)
                if resp.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
        raise UpstreamError("network", f"连接上游失败：{last}")

    def _headers(self, ctx: UpstreamCtx | None, *, app_id: bool = False,
                 with_open_id: bool = True) -> dict[str, str]:
        h: dict[str, str] = {}
        if ctx is not None and ctx.jwt:
            h["authorization"] = ctx.jwt             # 裸 JWT，无 Bearer
        if app_id:
            h["u-app-id"] = settings.u_app_id
        if with_open_id:
            oid = str((ctx.meta.get("openId") if ctx else "") or "")
            if oid:
                h["openId"] = oid
        return h

    def _check(self, resp: httpx.Response, action: str) -> dict[str, Any]:
        if resp.status_code == 401:
            raise UpstreamError("rt_expired", f"{action} 401：JWT 无效或已过期", fatal=True)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise UpstreamError("upstream", f"{action} HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise UpstreamError("upstream", f"{action} 响应非 JSON") from exc
        code = data.get("code")
        try:
            n = int(code) if code is not None else None
        except (TypeError, ValueError):
            n = None
        if n is not None and n not in OK_CODES:
            msg = data.get("msg") or data.get("message") or ""
            if n == CODE_MULTI_DEVICE:
                raise UpstreamError("upstream", "4021 请勿多设备同时作答（token 冲突）")
            if n == CODE_ALREADY_SUBMITTED:
                raise UpstreamError("already_submitted", "4013 训练已经提交")
            if n == CODE_COVERAGE_MISMATCH:
                raise UpstreamError("upstream", "4295 作答小题数与试卷不匹配（userData 未覆盖全部题目）")
            raise UpstreamError("upstream", f"{action} 业务失败 code={n} {msg}")
        return data

    # ------------------------------------------------------------------ 登录
    async def login(self, username: str, password: str) -> UpstreamSession:
        body = {
            "service": SSO_SERVICE,
            "username": encrypt_sso_field_fallback(username),
            "password": encrypt_sso_field_fallback(password),
        }
        resp = await self._request("POST", f"{SSO_ORIGIN}{SSO_LOGIN_PATH}", headers={
            "content-type": "application/json",
            "origin": SSO_ORIGIN,
            "referer": f"{SSO_ORIGIN}/sso/login",
        }, body=body)
        data = resp.json() if resp.content else {}
        code = str(data.get("code", ""))
        if code == str(CODE_CAPTCHA):
            raise UpstreamError("captcha", "SSO 要求滑块验证：请用浏览器登录后用 tools/import_jwt.py 导入 jwt", fatal=True)
        if code != "0":
            raise UpstreamError("auth_failed", data.get("msg") or "SSO 登录失败")
        rs = data.get("rs") or {}
        jwt = str(rs.get("jwt") or "")
        if not jwt:
            raise UpstreamError("auth_failed", "SSO 未返回 jwt")
        return UpstreamSession(
            jwt=jwt,
            rt=str(rs.get("rt") or ""),
            jwt_exp=float(rs.get("jwtExpire") or 0) / (1000 if rs.get("jwtExpire") else 1) or time.time() + 48 * 3600,
            rt_exp=time.time() + settings.rt_ttl_seconds,
            profile=self._profile(username, jwt),
        )

    def _profile(self, account: str, jwt: str) -> dict[str, Any]:
        payload = decode_jwt_payload(jwt)
        return {
            "account": account,
            "name": str(payload.get("name") or payload.get("userName") or ""),
            "schoolName": str(payload.get("schoolName") or payload.get("school") or ""),
            "openId": str(payload.get("openId") or payload.get("openid") or ""),
        }

    async def refresh(self, ctx: UpstreamCtx) -> UpstreamSession:
        if not ctx.rt:
            raise UpstreamError("rt_expired", "缺少 refresh token，需要重新登录", fatal=True)
        resp = await self._request("POST", f"{SSO_ORIGIN}{SSO_REFRESH_PATH}", headers={
            "content-type": "application/json",
            "origin": SSO_ORIGIN,
            "referer": f"{SSO_ORIGIN}/sso/login",
        }, body={"rt": ctx.rt})
        data = resp.json() if resp.content else {}
        if str(data.get("code", "")) != "0":
            raise UpstreamError("rt_expired", data.get("msg") or "刷新失败", fatal=True)
        rs = data.get("rs") or {}
        jwt = str(rs.get("jwt") or "")
        if not jwt:
            raise UpstreamError("rt_expired", "刷新未返回 jwt", fatal=True)
        return UpstreamSession(jwt=jwt, rt=str(rs.get("rt") or ctx.rt),
                               jwt_exp=time.time() + 48 * 3600,
                               rt_exp=time.time() + settings.rt_ttl_seconds,
                               profile=self._profile(ctx.account, jwt))

    # ------------------------------------------------------------------ 总览
    async def status(self, ctx: UpstreamCtx, flow: str = "listen") -> dict[str, Any]:
        """原始 getUserStatus：type / taskId / ansVersion / initLevel / currentLevel / targetLevel。"""
        resp = await self._request("GET", f"{settings.adaptive_origin}{P_USER_STATUS}",
                                   headers=self._headers(ctx), params={"flowType": flow})
        data = self._check(resp, f"getUserStatus({flow})")
        return data.get("value") or data.get("data") or data

    async def level_report(self, ctx: UpstreamCtx) -> dict[str, Any]:
        """定级报告：{level: 14, levelName: "S14"}；未定级时业务码 4012。"""
        resp = await self._request("POST", f"{settings.adaptive_origin}/api/uls/report/levelReport",
                                   headers=self._headers(ctx, app_id=True), body={})
        data = resp.json() if resp.content else {}
        code = data.get("code")
        try:
            n = int(code) if code is not None else None
        except (TypeError, ValueError):
            n = None
        if n == 4012:
            raise UpstreamError("not_placed", "用户尚未完成定级", fatal=False)
        return self._check(resp, "levelReport").get("value") or {}

    async def _week(self, ctx: UpstreamCtx, flow: str) -> dict[str, Any]:
        resp = await self._request(
            "GET", f"{settings.ucloud_origin}{P_STATUS_FOR_APP}",
            headers=self._headers(ctx, app_id=True), params={"flowType": flow})
        data = self._check(resp, f"getUserStatusForApp({flow})")
        value = data.get("value") or data.get("data") or data
        done = value.get("weekDoneTaskCount")
        total = value.get("weekFrequency")
        stype = value.get("type") or ""
        return {
            "flow": flow,
            "type": stype,
            "done": int(done) if isinstance(done, (int, float)) else None,
            "total": int(total) if isinstance(total, (int, float)) else None,
            "level": value.get("currentLevel") or value.get("level") or "",
            # 只有 type=grade 才是"未定级"；train_plan/grade_profile 表示定级已完成、计划待生成
            "placement": stype == "grade",
            "planPending": stype in ("train_plan", "grade_profile"),
            "levelList": value.get("levelList") or [],
        }

    async def _task(self, ctx: UpstreamCtx, flow: str, audio_visual: int) -> dict[str, Any] | None:
        resp = await self._request(
            "GET", f"{settings.adaptive_origin}{P_USER_STATUS}",
            headers=self._headers(ctx), params={"flowType": flow})
        data = self._check(resp, f"getUserStatus({flow})")
        value = data.get("value") or data.get("data") or data
        task_id = str(value.get("taskId") or "")
        if not task_id:
            return None
        ans_version = int(value.get("ansVersion") or 1)
        ctx.meta.setdefault("ans", {})[task_id] = ans_version
        ctx.meta.setdefault("flow", {})[task_id] = flow
        return {
            "taskId": task_id,
            "taskStudentId": str(value.get("tsId") or value.get("flowId") or ""),
            "name": f"{'U听力' if flow == 'listen' else 'U口语'} · {'定级' if value.get('type') == 'grade' else '周训练'}",
            "kind": flow,
            "audioVisual": audio_visual,
            "level": str(value.get("level") or ""),
            "genre": "",
            "topicCategory": flow,
            "paperHash": "",
            "ansVersion": ans_version,
            "locked": False,
            "completed": False,
            "startTime": "",
            "endTime": "",
            "score": None,
            "questionCount": 0,
            "gradedCount": 0,
        }

    async def _history(self, ctx: UpstreamCtx, flow: str) -> list[dict[str, Any]]:
        try:
            resp = await self._request("POST", f"{settings.ucloud_origin}{P_HISTORY}",
                                       headers=self._headers(ctx, app_id=True),
                                       body={"pageSize": 20, "page": 1, "flowType": flow})
            data = self._check(resp, f"getHistoryTrainingTasks({flow})")
        except UpstreamError:
            return []
        items = data.get("value") or data.get("data") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("records") or []
        out: list[dict[str, Any]] = []
        for it in items if isinstance(items, list) else []:
            out.append({
                "taskId": str(it.get("taskId") or ""),
                "taskStudentId": "",
                "name": str(it.get("paperName") or it.get("taskName") or "历史训练"),
                "kind": flow,
                "audioVisual": 1 if flow == "listen" and str(it.get("skill") or "").lower().startswith("video") else 0,
                "level": "", "genre": "", "topicCategory": flow,
                "paperHash": str(it.get("paperId") or ""),
                "ansVersion": int(it.get("ansVersion") or 1),
                "completed": True, "locked": False,
                "startTime": str(it.get("submitTime") or it.get("createTime") or ""),
                "endTime": "",
                "score": it.get("score") if it.get("scoreIsOut") else None,
                "questionCount": 0, "gradedCount": 0,
            })
        return out

    async def overview(self, ctx: UpstreamCtx) -> dict[str, Any]:
        listen_week = await self._week(ctx, "listen")
        try:
            speak_week = await self._week(ctx, "speak")
        except UpstreamError:
            speak_week = {"flow": "speak", "done": None, "total": None, "level": "", "placement": False}

        listen_task = None if listen_week["placement"] else await self._task(ctx, "listen", 0)
        speak_task = None if speak_week.get("placement") else await self._task(ctx, "speak", 1)

        history = await self._history(ctx, "listen")
        history += await self._history(ctx, "speak")

        def week_of(w: dict[str, Any], task: dict[str, Any] | None) -> dict[str, int]:
            raw_done, raw_total = w.get("done"), w.get("total")
            if raw_done is None and raw_total is None:
                # 服务端还没给计数（定级后 train_plan 阶段）：明确返回空，别伪装成 0/3
                return {"done": None, "quota": None, "pending": None}
            done = raw_done or 0
            quota = raw_total or 0
            if quota <= 0:
                quota = max(done, 3)
            return {"done": done, "quota": quota, "pending": max(0, quota - done)}

        return {
            "adaptive": {"week": week_of(listen_week, listen_task),
                         "current": listen_task,
                         "tasks": [listen_task] if listen_task else []},
            "assigned": {"tasks": [speak_task] if speak_task else []},
            "history": {"tasks": history[:20]},
            "meta": {
                "listenWeek": listen_week,
                "speakWeek": speak_week,
                "placementRequired": bool(listen_week["placement"] or speak_week.get("placement")),
                "planPending": bool(listen_week.get("planPending")),
                "upstream": "unipus",
            },
        }

    # ------------------------------------------------------------------ 取卷
    async def paper(self, ctx: UpstreamCtx, task_id: str) -> dict[str, Any]:
        ans_version = int(ctx.meta.get("ans", {}).get(task_id, 1) or 1)
        resp = await self._request("POST", f"{settings.adaptive_origin}{P_LOAD_PAPER}",
                                   headers=self._headers(ctx),
                                   body={"taskId": task_id, "ansVersion": ans_version})
        data = self._check(resp, "loadPaper")
        value = data.get("value") or data.get("data") or data
        token = str(value.get("token") or value.get("paperToken") or "")
        if token:
            ctx.meta.setdefault("tokens", {})[task_id] = token
        paper = normalize(value, task_id)
        paper["ansVersion"] = ans_version
        return paper

    # ------------------------------------------------------------------ 交卷
    @staticmethod
    def _answer_json(item: dict[str, Any]) -> dict[str, Any]:
        qtype = str(item.get("type") or "").lower()
        value = item.get("answer")
        subs = int(item.get("sub_count") or 0)

        if isinstance(value, (dict, list)) and subs <= 0:
            return value if isinstance(value, dict) else {"value": value, "children": [], "record": None}

        # 一个实例挂多道小题：children 段数必须与小题数对齐，否则 4295
        if subs > 0:
            if isinstance(value, list):
                vals = [str(v).strip().upper() for v in value]
            else:
                vals = [x for x in str(value or "").replace(",", " ").split() if x]
            children = []
            for i in range(subs):
                v = vals[i] if i < len(vals) else ""
                children.append({"value": [v] if v else []})
            return {"value": [], "children": children, "record": None}

        text_value = str(value or "").strip()
        if "blank" in qtype or "fill" in qtype or "dictation" in qtype:
            return {"value": [], "children": [{"value": [text_value]}], "record": None}
        if "multi" in qtype:
            return {"value": [v.strip() for v in text_value.split(",") if v.strip()], "children": [], "record": None}
        return {"value": [text_value], "children": [], "record": None}

    async def save_snapshot(self, ctx: UpstreamCtx, task_id: str,
                            user_data: list[dict[str, Any]], *,
                            duration: int = 0) -> dict[str, Any]:
        """作答中快照（模拟真人边做边存；不做也不影响最终判分）。"""
        token = str(ctx.meta.get("tokens", {}).get(task_id) or "")
        if not token:
            raise UpstreamError("upstream", "缺少 loadPaper token")
        ans_version = int(ctx.meta.get("ans", {}).get(task_id, 1) or 1)
        body = {"taskId": task_id, "ansVersion": ans_version, "token": token,
                "duration": max(0, int(duration)), "userData": user_data}
        resp = await self._request("POST", f"{settings.adaptive_origin}{P_SAVE_SNAPSHOT}",
                                   headers=self._headers(ctx), body=body)
        self._check(resp, "saveSnapshot")
        return {"ok": True, "saved": len(user_data)}

    async def submit_userdata(self, ctx: UpstreamCtx, task_id: str,
                              user_data: list[dict[str, Any]], *,
                              duration: int = 30) -> dict[str, Any]:
        """直接用组装好的 userData 提交（定级卷要求全卷覆盖时用）。"""
        token = str(ctx.meta.get("tokens", {}).get(task_id) or "")
        if not token:
            raise UpstreamError("upstream", "缺少 loadPaper token：请先取卷")
        ans_version = int(ctx.meta.get("ans", {}).get(task_id, 1) or 1)
        body = {"taskId": task_id, "ansVersion": ans_version, "token": token,
                "duration": max(1, duration), "userData": user_data}
        if not runtime.get_real_submit():
            raise UpstreamError("dry_run", "当前是预演模式（未开启真实提交）")
        resp = await self._request("POST", f"{settings.adaptive_origin}{P_SUBMIT_ANSWER}",
                                   headers=self._headers(ctx), body=body)
        self._check(resp, "submitAnswer")
        return {"ok": True, "instances": len(user_data)}

    async def submit(self, ctx: UpstreamCtx, task_id: str, answers: list[dict], *,
                     delay_seconds: float = 0.0, log: LogFn | None = None) -> SubmitResult:
        token = str(ctx.meta.get("tokens", {}).get(task_id) or "")
        if not token:
            raise UpstreamError("upstream", "缺少 loadPaper token：请先取卷（否则会被判多端冲突 4021）")
        ans_version = int(ctx.meta.get("ans", {}).get(task_id, 1) or 1)

        user_data: list[dict[str, Any]] = []
        for item in answers:
            if item["action"] == "oral":
                raise UpstreamError("unsupported", "口语/跟读需要音频上传与 Clio 打分，未实现；已拒交")
            if item["action"] == "skip":
                continue
            payload = self._answer_json(item)
            user_data.append({
                "instanceId": str(item["instanceId"]),          # snowflake，必须保持字符串
                "answer": json.dumps(payload, ensure_ascii=False),
                "answerVersion": ans_version,
                "context": json.dumps({"state": "done"}),
                "contextVersion": 1,
            })
            if log:
                await log(f"INFO answer {item['instanceId']} -> {payload}")
            if delay_seconds:
                await asyncio.sleep(min(delay_seconds, 60))

        if not user_data:
            raise UpstreamError("unsupported", "没有可提交的客观题答案")

        body = {"taskId": task_id, "ansVersion": ans_version, "token": token,
                "duration": runtime.to_payload_duration(max(1, len(user_data) * 20)),
                "userData": user_data}

        if not runtime.get_real_submit():
            if log:
                await log(f"WARNING dry-run：已组装 {len(user_data)} 条答案，未提交"
                          f"（页面顶部开关或 .env 的 UC_REAL_SUBMIT 可开启真实提交）")
            return SubmitResult(score=None, graded=0, total=len(user_data),
                                raw={"dryRun": True, "payload": body})

        resp = await self._request("POST", f"{settings.adaptive_origin}{P_SUBMIT_ANSWER}",
                                   headers=self._headers(ctx), body=body)
        self._check(resp, "submitAnswer")
        if log:
            await log(f"INFO submitted instances={len(user_data)}")

        score, graded = await self._read_score(ctx, task_id, ans_version, log)
        return SubmitResult(score=score, graded=graded, total=len(user_data),
                            raw={"paperToken": token})

    async def _read_score(self, ctx: UpstreamCtx, task_id: str, ans_version: int,
                          log: LogFn | None = None) -> tuple[float | None, int]:
        """交卷后读分：loadGradedQuestions（有分）→ 失败则 checkStatus 轮询。"""
        try:
            resp = await self._request("POST", f"{settings.adaptive_origin}{P_LOAD_GRADED}",
                                       headers=self._headers(ctx, app_id=True),
                                       body={"taskId": task_id, "ansVersion": ans_version})
            data = self._check(resp, "loadGradedQuestions")
            items = data.get("value") or data.get("data") or []
            scores = [float(it["score"]) for it in items
                      if isinstance(it, dict) and isinstance(it.get("score"), (int, float))]
            if scores:
                return round(sum(scores) / len(scores), 1), len(items)
        except UpstreamError as exc:
            if log:
                await log(f"WARNING 读分失败：{exc}")

        for _ in range(6):
            await asyncio.sleep(3)
            try:
                resp = await self._request("POST", f"{settings.adaptive_origin}{P_REPORT_CHECK}",
                                           headers=self._headers(ctx, app_id=True),
                                           body={"taskId": task_id, "ansVersion": ans_version})
                data = self._check(resp, "checkStatus")
                if str(data.get("value")) == "3":
                    if log:
                        await log("INFO 报告已出分")
                    return None, 0
            except UpstreamError:
                break
        return None, 0

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def fingerprint(paper: dict[str, Any]) -> str:
        raw = json.dumps(paper.get("questions") or [], ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
