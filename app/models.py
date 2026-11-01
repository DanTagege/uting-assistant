"""请求 / 响应模型：字段名与约束。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(min_length=1, max_length=11, description="U校园手机号")
    password: str = Field(default="", max_length=256)
    challenge: str = Field(default="", max_length=2048)
    validate_: str = Field(default="", alias="validate", max_length=2048)
    seccode: str = Field(default="", max_length=2048)
    # 可选：直接贴浏览器里拿到的令牌（绕过上游密码登录/滑块）
    jwt: str = Field(default="", max_length=4096)
    rt: str = Field(default="", max_length=2048)

    @field_validator("username")
    @classmethod
    def _digits(cls, v: str) -> str:
        v = v.strip()
        if not (v.isdigit() and len(v) == 11 and v.startswith("1")):
            raise ValueError("手机号格式不正确")
        return v

    @field_validator("jwt")
    @classmethod
    def _strip_prefix(cls, v: str) -> str:
        v = (v or "").strip().strip('"').strip("'")
        for prefix in ("authorization:", "Authorization:", "Bearer ", "bearer ", "jwt="):
            if v.startswith(prefix):
                v = v[len(prefix):].strip()
        return v


class CompleteRequest(BaseModel):
    ansVersion: int = Field(ge=0, description="答案版本号，用于挑选题库中的第 N 套答案")
    questionDelaySeconds: int = Field(default=0, ge=0, le=60, description="每题之间的停顿秒数")


class SchedulerUpdate(BaseModel):
    enabled: bool = True
    weekday: int = Field(default=0, ge=0, le=6)
    hour: int = Field(default=12, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    parallelism: int = Field(default=8, ge=1, le=32)


class AnswerItem(BaseModel):
    """answer_bank 中的一条答案，供引擎与题库导入工具共用。"""
    instanceId: str
    action: Literal["skip", "oral", "probe"] = "probe"
    answer: str | list[str] = ""
    confidence: float = 1.0
