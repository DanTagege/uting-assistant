"""鉴权 / 会话 / CSRF 与本平台自有请求约定。

约定：
  X-UCampus-Request     所有非 GET 请求必带，缺失 403
  X-UCampus-Session     标签页级会话 id，不匹配 409
  X-UCampus-Device-Info platform / timezone / screen 的 JSON（URL 编码）
  X-UCampus-Fingerprint `5:<visitorId>` 形式的浏览器指纹
  Cookie                ucampus_jwt / ucampus_rt / ucampus_identity（HttpOnly + SameSite=Lax）

返回错误：
  409 SESSION_CHANGED / 422 校验失败 / 401 未登录
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Request, Response

from . import db
from .config import settings


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, headers: dict[str, str] | None = None):
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers or {}
        super().__init__(message)


# --------------------------------------------------------------------------- token

def make_token(username: str, sid: str, typ: str, ttl: int) -> str:
    payload = {
        "sub": username,
        "sid": sid,
        "typ": typ,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_alg)


def decode_token(token: str, typ: str = "access") -> dict[str, Any] | None:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_alg])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != typ:
        return None
    return data


def new_sid() -> str:
    return secrets.token_hex(16)


# --------------------------------------------------------------------------- 设备

@dataclass
class DeviceCtx:
    key: str
    browser_id: str
    fingerprint: str
    platform: str
    tz: str
    screen: str
    ua: str
    ip: str


def _parse_device_info(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        from urllib.parse import unquote

        data = json.loads(unquote(raw))
        if isinstance(data, dict):
            return {k: str(v)[:64] for k, v in data.items() if k in ("platform", "timezone", "screen")}
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    return {}


def device_from_request(request: Request) -> DeviceCtx:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "0.0.0.0")
    browser_id = request.cookies.get("ucampus_did", "") or str(request.scope.get("ucampus_did", ""))
    fp = request.headers.get("x-ucampus-fingerprint", "")[:128]
    info = _parse_device_info(request.headers.get("x-ucampus-device-info", ""))
    # 主键 = 浏览器实例（cookie 级），指纹只做辅助关联
    key = browser_id or f"anon:{ip}:{request.headers.get('user-agent','')[:40]}"
    return DeviceCtx(
        key=key,
        browser_id=browser_id,
        fingerprint=fp,
        platform=info.get("platform", ""),
        tz=info.get("timezone", ""),
        screen=info.get("screen", ""),
        ua=request.headers.get("user-agent", "")[:256],
        ip=ip,
    )


# --------------------------------------------------------------------------- 登录态

@dataclass
class Ctx:
    username: str
    sid: str
    account: Any
    dev: DeviceCtx
    is_admin: bool


def _client_sid(request: Request) -> str:
    return request.headers.get("x-ucampus-session", "")


def set_auth_cookies(response: Response, username: str, sid: str) -> None:
    access = make_token(username, sid, "access", settings.jwt_ttl_seconds)
    refresh = make_token(username, sid, "refresh", settings.rt_ttl_seconds)
    identity = make_token(username, sid, "identity", settings.rt_ttl_seconds)
    common = dict(httponly=True, samesite="lax", secure=False, path="/")  # 本地 HTTP 调试用；上线改 secure=True + HTTPS
    response.set_cookie("ucampus_jwt", access, max_age=settings.jwt_ttl_seconds, **common)
    response.set_cookie("ucampus_rt", refresh, max_age=settings.rt_ttl_seconds, **common)
    response.set_cookie("ucampus_identity", identity, max_age=settings.rt_ttl_seconds, **common)


def clear_auth_cookies(response: Response) -> None:
    for name in ("ucampus_jwt", "ucampus_rt", "ucampus_identity"):
        response.delete_cookie(name, path="/")


def resolve_ctx(request: Request, *, allow_anonymous: bool = False) -> Ctx | None:
    dev = device_from_request(request)
    token = request.cookies.get("ucampus_jwt") or request.cookies.get("ucampus_rt")
    if not token:
        if allow_anonymous:
            return None
        raise ApiError(401, "请先登录")
    typ = "access" if request.cookies.get("ucampus_jwt") else "refresh"
    data = decode_token(token, typ)
    if data is None:
        if allow_anonymous:
            return None
        raise ApiError(401, "请先登录")
    username = str(data["sub"])
    row = db.one("SELECT * FROM accounts WHERE username=?", (username,))
    if row is None:
        if allow_anonymous:
            return None
        raise ApiError(401, "请先登录")
    client_sid = _client_sid(request)
    if client_sid and client_sid != data["sid"]:
        # 会话被替换：前端应刷新页面重新鉴权
        raise ApiError(409, "登录状态已变化，请刷新页面", code="SESSION_CHANGED",
                       headers={"X-UCampus-Session-Changed": "1"})
    return Ctx(username=username, sid=data["sid"], account=row, dev=dev, is_admin=bool(row["is_admin"]))


def require_admin(ctx: Ctx | None) -> Ctx:
    if ctx is None:
        raise ApiError(401, "请先登录")
    if not ctx.is_admin:
        raise ApiError(403, "没有管理员权限")
    return ctx


def check_csrf(request: Request) -> None:
    if not settings.require_csrf_header:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("x-ucampus-request") != "1":
        raise ApiError(403, "请从本站页面发起操作")
