"""U听说 CLI 的底层客户端（真实接口，无任何模拟）。

只做四件事：登录/续期、读周进度、取卷、交卷读分。
所有 path 都来自公开抓包记录，已在本机活体验证（401 而非 404）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

SSO = "https://sso.unipus.cn"
SSO_LOGIN = "/sso/0.1/sso/cip/login"
SSO_REFRESH = "/sso/4.0/sso/refresh_jwt"
SSO_SERVICE = "https://ucloud.unipus.cn/"

UCLOUD = "https://ucloud.unipus.cn"
ADAPTIVE = "https://uadaptive.unipus.cn"

P_STATUS = "/api/uls/user/getUserStatusForApp"
P_USER_STATUS = "/api/uls/user/getUserStatus"
P_LOAD_PAPER = "/api/uls/user/loadPaper"
P_SUBMIT = "/api/uls/user/submitAnswer"
P_GRADED = "/api/uls/user/loadGradedQuestions"
P_CHECK = "/api/uls/report/checkStatus"
P_HISTORY = "/api/uls/user/getHistoryTrainingTasks"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

HOME = Path(os.environ.get("UTING_HOME", Path.home() / ".uting"))
PROFILES = HOME / "accounts"          # 每个账号一个目录
ACTIVE = HOME / "active"              # 当前使用的账号
STATE_FILE = HOME / "state.json"      # 兼容旧版单账号
ANSWER_FILE = HOME / "answers.json"   # 题库：全局共享（同一篇卷所有账号复用）


def resolve_profile(profile: str | None = None) -> str:
    if profile:
        return profile.strip()
    env = os.environ.get("UTING_USER", "").strip()
    if env:
        return env
    if ACTIVE.exists():
        try:
            name = ACTIVE.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass
    if PROFILES.exists():
        dirs = [p.name for p in PROFILES.iterdir() if (p / "state.json").exists()]
        if len(dirs) == 1:
            return dirs[0]
    return ""


def profile_dir(profile: str | None = None) -> Path:
    name = resolve_profile(profile)
    return (PROFILES / name) if name else HOME


def state_file(profile: str | None = None) -> Path:
    return profile_dir(profile) / "state.json"


class UtingError(Exception):
    def __init__(self, kind: str, message: str, *, code: int | None = None):
        self.kind = kind          # auth|captcha|network|business|parse|needs_placement|already
        self.code = code
        super().__init__(message)


# --------------------------------------------------------------------------- 本地状态
def load_state(profile: str | None = None) -> dict[str, Any]:
    for path in (state_file(profile), STATE_FILE):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return {}


def save_state(state: dict[str, Any], profile: str | None = None) -> None:
    path = state_file(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    name = resolve_profile(profile)
    if name and name != state.get("phone"):
        pass
    if name:
        ACTIVE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE.write_text(name, encoding="utf-8")


def load_answers() -> dict[str, Any]:
    if ANSWER_FILE.exists():
        try:
            return json.loads(ANSWER_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_answers(bank: dict[str, Any]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    ANSWER_FILE.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 加密
def encrypt_field(plain: str) -> str:
    """sso.unipus.cn 的账号密码加密（AES-128-CBC，key/iv 是前端公开常量）。"""
    key = bytes.fromhex("8AD70B641C024C7ADA2ECD082EC0334F")
    iv = bytes.fromhex("0102030405060708090A0B0C0D0E0F10")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return (enc.update(plain.encode()) + enc.finalize()).hex().upper()
    except ImportError:
        import sys

        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from app.upstream.unipus import encrypt_sso_field_fallback  # 复用纯 Python 实现

        return encrypt_sso_field_fallback(plain)


def jwt_exp(jwt: str) -> float:
    try:
        import base64

        part = jwt.split(".")[1]
        raw = part + "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return float(data.get("exp") or 0)
    except (IndexError, ValueError, json.JSONDecodeError):
        return 0.0


# --------------------------------------------------------------------------- 客户端
class Client:
    def __init__(self, profile: str | None = None) -> None:
        self.profile = resolve_profile(profile)
        self.state = load_state(self.profile)
        if self.profile and self.state.get("phone") and self.state["phone"] != self.profile:
            # 目录名与内容不一致时以目录为准（避免串号）
            self.state["phone"] = self.profile
        self._client = httpx.AsyncClient(
            timeout=30, follow_redirects=False,
            headers={"accept": "application/json, text/plain, */*", "user-agent": UA},
        )

    def _persist(self) -> None:
        """保存到该账号自己的目录；首次登录时把 profile 定为手机号。"""
        phone = str(self.state.get("phone") or "")
        if phone:
            self.profile = phone
        save_state(self.state, self.profile)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- 会话
    @property
    def jwt(self) -> str:
        return str(self.state.get("jwt") or "")

    @property
    def phone(self) -> str:
        return str(self.state.get("phone") or "")

    def _auth(self) -> dict[str, str]:
        return {"authorization": self.jwt}          # 裸 JWT，不能加 Bearer

    async def _req(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                   params: dict[str, Any] | None = None, body: dict[str, Any] | None = None,
                   retries: int = 2) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = await self._client.request(method, url, headers=headers, params=params, json=body)
                if r.status_code >= 500 and attempt < retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                if r.status_code == 401:
                    raise UtingError("auth", "JWT 无效或已过期（401）")
                if r.status_code >= 400:
                    raise UtingError("network", f"HTTP {r.status_code}: {r.text[:200]}")
                try:
                    return r.json()
                except ValueError as exc:
                    raise UtingError("parse", f"响应不是 JSON：{r.text[:200]}") from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise UtingError("network", f"网络失败：{exc}") from exc
        raise UtingError("network", f"网络失败：{last}")

    @staticmethod
    def _check_biz(data: dict[str, Any], action: str) -> dict[str, Any]:
        code = data.get("code")
        try:
            n = int(code) if code is not None else None
        except (TypeError, ValueError):
            n = None
        if n in (0, 1, 200) or code is None:
            return data
        msg = data.get("msg") or data.get("message") or ""
        if n == 4021:
            raise UtingError("business", "4021 请勿多设备同时作答（token 冲突）", code=n)
        if n == 4013:
            raise UtingError("already", "4013 该训练已提交过", code=n)
        if n == 4295:
            raise UtingError("business", "4295 作答小题数与试卷不匹配", code=n)
        raise UtingError("business", f"{action} 失败 code={n} {msg}", code=n)

    # ---------------------------------------------------------------- 登录
    async def login_password(self, phone: str, password: str) -> None:
        body = {"service": SSO_SERVICE,
                "username": encrypt_field(phone), "password": encrypt_field(password)}
        data = await self._req("POST", f"{SSO}{SSO_LOGIN}", body=body, headers={
            "content-type": "application/json", "origin": SSO, "referer": f"{SSO}/sso/login"})
        code = str(data.get("code", ""))
        if code == "1506":
            raise UtingError("captcha", "SSO 要求滑块验证：请用浏览器登录后导入 JWT（见 README）")
        if code != "0":
            raise UtingError("auth", data.get("msg") or "账号或密码错误")
        rs = data.get("rs") or {}
        jwt = str(rs.get("jwt") or "")
        if not jwt:
            raise UtingError("auth", "登录成功但没有拿到 jwt")
        self.state.update({
            "phone": phone, "jwt": jwt, "rt": str(rs.get("rt") or ""),
            "jwt_exp": jwt_exp(jwt) or time.time() + 48 * 3600,
            "rt_exp": time.time() + 30 * 86400, "saved_at": time.time(),
        })
        self._persist()

    def import_token(self, phone: str, jwt: str, rt: str = "") -> None:
        """接受多种粘贴形式：裸 jwt / 'Bearer xxx' / 'authorization: xxx' / 'jwt=xxx'。"""
        raw = (jwt or "").strip().strip('"').strip("'")
        for prefix in ("authorization:", "Authorization:", "authorization=", "jwt=", "Bearer ", "bearer "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
        raw = raw.split("\n")[0].strip()
        if not raw:
            raise UtingError("auth", "jwt 为空")
        if not raw.startswith("eyJ"):
            raise UtingError("auth", f"这不像 JWT（应以 eyJ 开头，实际是 {raw[:12]}…）")
        self.state.update({
            "phone": phone or self.state.get("phone", ""), "jwt": raw, "rt": rt.strip(),
            "jwt_exp": jwt_exp(raw) or time.time() + 48 * 3600,
            "rt_exp": time.time() + 30 * 86400, "saved_at": time.time(),
        })
        self._persist()

    async def ensure_token(self) -> None:
        if not self.jwt:
            raise UtingError("auth", "还没有登录：先 `python -m uting login`")
        if self.state.get("jwt_exp", 0) > time.time() + 60:
            return
        rt = str(self.state.get("rt") or "")
        if not rt:
            raise UtingError("auth", "jwt 已过期且没有 refresh token：请重新 login / 导入 JWT")
        data = await self._req("POST", f"{SSO}{SSO_REFRESH}", body={"rt": rt}, headers={
            "content-type": "application/json", "origin": SSO, "referer": f"{SSO}/sso/login"})
        if str(data.get("code", "")) != "0":
            raise UtingError("auth", "续期失败：请重新登录")
        rs = data.get("rs") or {}
        self.state["jwt"] = str(rs.get("jwt") or "")
        self.state["rt"] = str(rs.get("rt") or rt)
        self.state["jwt_exp"] = jwt_exp(self.state["jwt"]) or time.time() + 48 * 3600
        self._persist()

    # ---------------------------------------------------------------- 进度
    async def week_status(self, flow: str = "listen") -> dict[str, Any]:
        await self.ensure_token()
        data = await self._req("GET", f"{UCLOUD}{P_STATUS}", headers={**self._auth(), "u-app-id": "116"},
                               params={"flowType": flow})
        self._check_biz(data, "读取周进度")
        value = data.get("value") or data.get("data") or data
        return {
            "flow": flow,
            "type": str(value.get("type") or ""),
            "done": value.get("weekDoneTaskCount"),
            "target": value.get("weekFrequency"),
            "level": value.get("currentLevel") or value.get("level") or "",
            "raw": value,
        }

    async def current_task(self, flow: str = "listen") -> dict[str, Any] | None:
        await self.ensure_token()
        data = await self._req("GET", f"{ADAPTIVE}{P_USER_STATUS}", headers=self._auth(),
                               params={"flowType": flow})
        self._check_biz(data, "读取当前任务")
        value = data.get("value") or data.get("data") or data
        task_id = value.get("taskId")
        if not task_id:
            return None
        return {"taskId": str(task_id), "ansVersion": int(value.get("ansVersion") or 1),
                "type": str(value.get("type") or ""), "status": value.get("status"),
                "level": value.get("level") or ""}

    async def load_paper(self, task_id: str, ans_version: int) -> dict[str, Any]:
        await self.ensure_token()
        data = await self._req("POST", f"{ADAPTIVE}{P_LOAD_PAPER}", headers={**self._auth(),
                                                                           "content-type": "application/json"},
                               body={"taskId": task_id, "ansVersion": ans_version})
        self._check_biz(data, "取卷")
        return data.get("value") or data.get("data") or data

    async def submit(self, task_id: str, ans_version: int, token: str,
                     user_data: list[dict[str, Any]], duration: int) -> dict[str, Any]:
        await self.ensure_token()
        body = {"taskId": task_id, "ansVersion": ans_version, "token": token,
                "duration": max(1, duration), "userData": user_data}
        data = await self._req("POST", f"{ADAPTIVE}{P_SUBMIT}", headers={**self._auth(),
                                                                        "content-type": "application/json"},
                               body=body)
        return self._check_biz(data, "交卷")

    async def graded(self, task_id: str, ans_version: int) -> list[dict[str, Any]]:
        data = await self._req("POST", f"{ADAPTIVE}{P_GRADED}",
                               headers={**self._auth(), "u-app-id": "116", "content-type": "application/json"},
                               body={"taskId": task_id, "ansVersion": ans_version})
        self._check_biz(data, "读分")
        value = data.get("value") or data.get("data") or []
        return value if isinstance(value, list) else []

    async def check_status(self, task_id: str, ans_version: int) -> str:
        data = await self._req("POST", f"{ADAPTIVE}{P_CHECK}",
                               headers={**self._auth(), "u-app-id": "116", "content-type": "application/json"},
                               body={"taskId": task_id, "ansVersion": ans_version})
        self._check_biz(data, "报告状态")
        return str(data.get("value"))

    async def history(self, flow: str = "listen", page: int = 1) -> list[dict[str, Any]]:
        data = await self._req("POST", f"{UCLOUD}{P_HISTORY}",
                               headers={**self._auth(), "u-app-id": "116", "content-type": "application/json"},
                               body={"pageSize": 20, "page": page, "flowType": flow})
        self._check_biz(data, "历史列表")
        value = data.get("value") or data.get("data") or []
        if isinstance(value, dict):
            value = value.get("list") or value.get("records") or []
        return value if isinstance(value, list) else []
