"""上游（U校园 / U听说）抽象层。

业务层只依赖 `Upstream` 协议 + `UpstreamCtx` 上下文，不关心是本地 mock 还是真上游。
- `MockUpstream`：离线可跑，完整模拟视听说题库、判分、周配额。
- `UnipusAIUpstream`：真上游适配，端点/字段来自 data/upstream.json（tools/har_extract.py 生成）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class UpstreamError(Exception):
    """reason 会映射成 job 的失败分类与账号状态。"""

    def __init__(self, reason: str, message: str = "", *, fatal: bool = False):
        self.reason = reason   # auth_failed|captcha|rt_expired|network|not_configured|rate_limited|upstream
        self.fatal = fatal     # True => 需要用户重新登录
        super().__init__(message or reason)


@dataclass
class UpstreamCtx:
    """一次上游调用的上下文：账号 + 该账号的上游令牌。"""
    account: str
    jwt: str = ""
    rt: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpstreamSession:
    jwt: str
    rt: str
    jwt_exp: float = 0.0
    rt_exp: float = 0.0
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitResult:
    score: float | None
    graded: int
    total: int
    raw: dict[str, Any] = field(default_factory=dict)


LogFn = Callable[[str], Awaitable[None]]


class Upstream(ABC):
    name = "base"

    @abstractmethod
    async def login(self, username: str, password: str) -> UpstreamSession: ...

    @abstractmethod
    async def refresh(self, ctx: UpstreamCtx) -> UpstreamSession: ...

    @abstractmethod
    async def overview(self, ctx: UpstreamCtx) -> dict[str, Any]:
        """返回 {adaptive:{week,current,tasks}, assigned:{tasks}, history:{tasks}}"""

    @abstractmethod
    async def paper(self, ctx: UpstreamCtx, task_id: str) -> dict[str, Any]:
        """返回 {paperHash, paperTitle, audioVisual, questions:[...], questionCount}"""

    @abstractmethod
    async def submit(
        self,
        ctx: UpstreamCtx,
        task_id: str,
        answers: list[dict[str, Any]],
        *,
        delay_seconds: float = 0.0,
        log: LogFn | None = None,
    ) -> SubmitResult:
        """逐题提交（skip/oral/probe），返回判分结果。"""
