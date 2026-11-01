"""真上游适配器（U校园 AI 版 / U听说）。

⚠️ 端点与字段一律来自 `data/upstream.json`，本文件不含任何硬编码的真实地址。
获取方式：登录一次 U校园 AI 版网页/APP，导出 HAR，然后

    python tools/har_extract.py capture.har --out data/upstream.json

脚本会自动脱敏并生成端点草稿，人工补齐 `{{...}}` 变量后即可切换：

    set UC_UPSTREAM=unipus_ai

未配置时本适配器会抛 `not_configured`，不会发出任何请求。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .base import LogFn, SubmitResult, Upstream, UpstreamCtx, UpstreamError, UpstreamSession

PLACEHOLDER_MARKS = ("REPLACE_ME", "example.com", "127.0.0.1:0")


def _render(node: Any, variables: dict[str, Any]) -> Any:
    """递归替换 {{var}} 占位。"""
    if isinstance(node, str):
        out = node
        for k, v in variables.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out
    if isinstance(node, list):
        return [_render(x, variables) for x in node]
    if isinstance(node, dict):
        return {k: _render(v, variables) for k, v in node.items()}
    return node


def _dig(data: Any, path: str, default: Any = None) -> Any:
    """按 `data.items.0.id` 形式取嵌套值。"""
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return default
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return default
    return default if cur is None else cur


class UnipusAIUpstream(Upstream):
    name = "unipus_ai"

    def __init__(self, cfg_path: Path | None = None):
        self.cfg_path = cfg_path or settings.upstream_config
        self.cfg: dict[str, Any] = {}
        self._client: httpx.AsyncClient | None = None
        self._load()

    # ------------------------------------------------------------------ 配置
    def _load(self) -> None:
        if self.cfg_path.exists():
            try:
                self.cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise UpstreamError("not_configured", f"upstream.json 解析失败：{exc}") from exc
        else:
            self.cfg = {}

    def configured(self) -> bool:
        base = str(self.cfg.get("base_url", ""))
        eps = self.cfg.get("endpoints") or {}
        if not base or any(m in base for m in PLACEHOLDER_MARKS):
            return False
        return "login" in eps and "overview" in eps

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json", **{k: str(v) for k, v in (self.cfg.get("headers") or {}).items()}}
            self._client = httpx.AsyncClient(
                base_url=self.cfg.get("base_url", ""),
                timeout=settings.upstream_timeout,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ HTTP
    def _auth_headers(self, ctx: UpstreamCtx) -> dict[str, str]:
        auth = self.cfg.get("auth") or {}
        if not ctx.jwt:
            return {}
        kind = auth.get("type", "bearer")
        header = auth.get("header", "Authorization")
        if kind == "bearer":
            return {header: f"{auth.get('prefix', 'Bearer ')}{ctx.jwt}"}
        if kind == "header":
            return {header: ctx.jwt}
        if kind == "cookie":
            return {"Cookie": f"{auth.get('cookie_name', 'token')}={ctx.jwt}"}
        return {}

    async def _call(self, name: str, ctx: UpstreamCtx | None, variables: dict[str, Any]) -> Any:
        if not self.configured():
            raise UpstreamError("not_configured",
                                f"上游未配置：请在 {self.cfg_path} 中填入真实 base_url 与 endpoints（见 tools/har_extract.py）")
        eps = self.cfg.get("endpoints") or {}
        spec = eps.get(name)
        if not spec:
            raise UpstreamError("not_configured", f"endpoints 缺少 `{name}`")
        method = str(spec.get("method", "GET")).upper()
        path = _render(str(spec.get("path", "/")), variables)
        params = _render(spec.get("params") or {}, variables) or None
        payload = _render(spec.get("json") or spec.get("body"), variables)
        headers = dict(self._auth_headers(ctx) if ctx else {})
        headers.update({k: str(v) for k, v in _render(spec.get("headers") or {}, variables).items()})
        retry = int((self.cfg.get("retry") or {}).get("times", 2))
        backoff = float((self.cfg.get("retry") or {}).get("backoff", 0.8))

        last: Exception | None = None
        for attempt in range(retry + 1):
            try:
                resp = await self.client.request(method, path, params=params,
                                                 json=payload if payload is not None else None,
                                                 headers=headers)
                if resp.status_code in (401, 403):
                    raise UpstreamError("rt_expired", f"上游鉴权失败 {resp.status_code}", fatal=True)
                if resp.status_code == 429:
                    raise UpstreamError("rate_limited", "上游限流")
                if resp.status_code >= 500:
                    raise UpstreamError("upstream", f"上游 {resp.status_code}")
                if resp.status_code >= 400:
                    raise UpstreamError("upstream", f"上游 {resp.status_code}: {resp.text[:200]}")
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < retry:
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                raise UpstreamError("network", f"连接上游失败：{exc}") from exc
        raise UpstreamError("network", f"连接上游失败：{last}")

    # ------------------------------------------------------------------ 协议实现
    async def login(self, username: str, password: str) -> UpstreamSession:
        data = await self._call("login", None, {"username": username, "password": password})
        auth = self.cfg.get("auth") or {}
        jwt = _dig(data, auth.get("token_path", "data.accessToken"), "")
        rt = _dig(data, auth.get("refresh_path", "data.refreshToken"), "")
        if not jwt:
            raise UpstreamError("auth_failed", "上游未返回访问令牌，请检查 auth.token_path")
        prof_src = _dig(data, self.cfg.get("profile_path", "data.profile"), {}) or {}
        profile = {
            "account": username,
            "name": str(prof_src.get("name") or prof_src.get("userName") or ""),
            "schoolName": str(prof_src.get("schoolName") or prof_src.get("school") or ""),
        }
        now = time.time()
        return UpstreamSession(jwt=jwt, rt=rt, jwt_exp=now + int(self.cfg.get("token_ttl", 7200)),
                              rt_exp=now + settings.rt_ttl_seconds, profile=profile)

    async def refresh(self, ctx: UpstreamCtx) -> UpstreamSession:
        data = await self._call("refresh", ctx, {})
        auth = self.cfg.get("auth") or {}
        jwt = _dig(data, auth.get("token_path", "data.accessToken"), "")
        if not jwt:
            raise UpstreamError("rt_expired", "刷新未返回令牌", fatal=True)
        now = time.time()
        return UpstreamSession(jwt=jwt, rt=ctx.rt, jwt_exp=now + int(self.cfg.get("token_ttl", 7200)),
                              rt_exp=now + settings.rt_ttl_seconds)

    async def overview(self, ctx: UpstreamCtx) -> dict[str, Any]:
        data = await self._call("overview", ctx, {})
        mapping = self.cfg.get("mapping") or {}
        adaptive = _dig(data, mapping.get("adaptive", "data.adaptive"), {})
        assigned = _dig(data, mapping.get("assigned", "data.assigned"), {})
        history = _dig(data, mapping.get("history", "data.history"), {})
        return {
            "adaptive": {"week": adaptive.get("week") or {"done": 0, "quota": 0, "pending": 0},
                         "current": adaptive.get("current"), "tasks": adaptive.get("tasks") or []},
            "assigned": {"tasks": assigned.get("tasks") or []},
            "history": {"tasks": history.get("tasks") or []},
        }

    async def paper(self, ctx: UpstreamCtx, task_id: str) -> dict[str, Any]:
        data = await self._call("paper", ctx, {"task_id": task_id})
        mapping = self.cfg.get("mapping") or {}
        payload = _dig(data, mapping.get("paper", "data"), {}) or {}
        if isinstance(payload, dict) and "questions" not in payload:
            # 兼容上游把题目放在 paperDetail / questions 下
            for key in ("paperDetail", "detail", "questions"):
                cand = payload.get(key)
                if isinstance(cand, dict) and cand.get("questions"):
                    payload = cand
                    break
                if isinstance(cand, list):
                    payload = {"questions": cand}
                    break
        if not payload.get("questions"):
            raise UpstreamError("upstream", "上游试卷缺少 questions 字段，请检查 mapping.paper")
        return payload

    async def submit(self, ctx: UpstreamCtx, task_id: str, answers: list[dict], *,
                     delay_seconds: float = 0.0, log: LogFn | None = None) -> SubmitResult:
        eps = self.cfg.get("endpoints") or {}
        per_item = "answer" in eps
        for item in answers:
            if log:
                await log(f"INFO submit {item['instanceId']} action={item['action']} answer={item.get('answer')!r}")
            if per_item:
                await self._call("answer", ctx, {
                    "task_id": task_id,
                    "instanceId": item["instanceId"],
                    "instance_id": item["instanceId"],
                    "action": item["action"],
                    "answer": item.get("answer", ""),
                    "answer_text": item.get("answer", ""),
                })
            if delay_seconds:
                await asyncio.sleep(min(delay_seconds, 60))
        if not per_item:
            await self._call("submit", ctx, {
                "task_id": task_id,
                "answers": [{"instanceId": a["instanceId"], "action": a["action"], "answer": a.get("answer", "")}
                            for a in answers],
            })
        mapping = self.cfg.get("mapping") or {}
        score = None
        graded = 0
        total = len(answers)
        if "result" in eps:
            res = await self._call("result", ctx, {"task_id": task_id})
            score = _dig(res, mapping.get("score", "data.score"))
            graded = int(_dig(res, mapping.get("graded", "data.gradedCount"), 0) or 0)
            total = int(_dig(res, mapping.get("total", "data.questionCount"), total) or total)
        return SubmitResult(score=float(score) if score is not None else None,
                            graded=graded, total=total, raw={"paperHash": ""})
