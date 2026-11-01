"""上游工厂：按 UC_UPSTREAM 选择实现，进程内单例。

  mock      —— 离线可跑的假 U听说（默认）
  unipus    —— 真实 U听说（ucloud / uadaptive / sso 网关，契约来自公开抓包）
  unipus_ai —— 通用模板型适配器（data/upstream.json 驱动，留给其它学校/版本）
"""
from __future__ import annotations

from ..config import settings
from .base import Upstream, UpstreamCtx, UpstreamError, UpstreamSession, SubmitResult
from .mock import MockUpstream
from .unipus import UnipusUpstream
from .unipus_ai import UnipusAIUpstream

__all__ = ["get_upstream", "Upstream", "UpstreamCtx", "UpstreamError", "UpstreamSession", "SubmitResult"]

_instance: Upstream | None = None


def get_upstream() -> Upstream:
    global _instance
    if _instance is None:
        if settings.upstream == "unipus":
            _instance = UnipusUpstream()
        elif settings.upstream == "unipus_ai":
            _instance = UnipusAIUpstream()
        else:
            _instance = MockUpstream()
    return _instance


def reset_upstream() -> None:
    """切换 UC_UPSTREAM 后重新加载（测试/CLI 用）。"""
    global _instance
    _instance = None
