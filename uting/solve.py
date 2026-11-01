"""答题：先查本地题库，再（可选）让 DeepSeek 作答，都不行就明确告诉缺什么。"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- 提交体


def _letters(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().upper() for v in value]
    return [x for x in re.split(r"[,\s|]+", str(value or "")) if x]


def payload_for(question: dict[str, Any], value: Any) -> dict[str, Any]:
    """把答案变成 submitAnswer 需要的 answer JSON 对象。"""
    if isinstance(value, dict):
        return value                                  # 题库里直接写完整结构，原样提交

    subs = question.get("subs") or []
    if subs:                                          # 一个实例带多道小题：children 对齐
        vals = _letters(value)
        children = []
        for i in range(len(subs)):
            v = vals[i] if i < len(vals) else ""
            children.append({"value": [v] if v else []})
        return {"value": [], "children": children, "record": None}

    text = str(value or "").strip()
    qtype = (question.get("type") or "").lower()

    if "blank" in qtype or "fill" in qtype or "dictation" in qtype:
        parts = [p for p in re.split(r"[|/;；]", text) if p.strip()] or [text]
        return {"value": [], "children": [{"value": [p.strip()]} for p in parts], "record": None}
    if "multi" in qtype:
        return {"value": _letters(text), "children": [], "record": None}
    return {"value": [text], "children": [], "record": None}


EMPTY_ANSWER = {"value": [], "children": [], "record": None}


def build_user_data(paper: dict[str, Any], answers: dict[str, Any],
                    include_non_objective: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    """按试卷顺序生成 userData；返回 (userData, 缺答案的 instanceId 列表)。"""
    user_data: list[dict[str, Any]] = []
    missing: list[str] = []
    for q in paper["questions"]:
        iid = q["instanceId"]
        if q["action"] != "probe":
            if include_non_objective:                 # 4295 兜底：补占位凑齐实例
                user_data.append({
                    "instanceId": iid,
                    "answer": json.dumps(EMPTY_ANSWER, ensure_ascii=False),
                    "answerVersion": int(paper.get("ansVersion") or 1),
                    "context": json.dumps({"state": "done"}),
                    "contextVersion": 1,
                })
            continue
        if iid not in answers:
            missing.append(iid)
            continue
        if q.get("subs") and len(_letters(answers[iid])) < len(q["subs"]):
            missing.append(iid)
            continue
        user_data.append({
            "instanceId": iid,
            "answer": json.dumps(payload_for(q, answers[iid]), ensure_ascii=False),
            "answerVersion": int(paper.get("ansVersion") or 1),
            "context": json.dumps({"state": "done"}),
            "contextVersion": 1,
        })
    return user_data, missing


# --------------------------------------------------------------------------- 题库
def bank_lookup(paper: dict[str, Any], bank: dict[str, Any]) -> dict[str, Any]:
    node = bank.get(str(paper["taskId"])) or {}
    answers = node.get("answers") if isinstance(node, dict) else None
    return dict(answers) if isinstance(answers, dict) else {}


def bank_store(paper: dict[str, Any], answers: dict[str, Any], bank: dict[str, Any]) -> None:
    bank[str(paper["taskId"])] = {"title": paper["title"], "answers": answers, "updated": time.time()}


# --------------------------------------------------------------------------- LLM
LLM_ENV = ("UTING_LLM_BASE_URL", "UTING_LLM_MODEL", "UTING_LLM_API_KEY")


def llm_config() -> dict[str, str] | None:
    """优先读 ~/.uting/llm.json（网页里填的那份），其次环境变量。"""
    cfg_file = Path(os.environ.get("UTING_HOME", Path.home() / ".uting")) / "llm.json"
    saved: dict[str, str] = {}
    if cfg_file.exists():
        try:
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
    base = os.environ.get(LLM_ENV[0], "").strip() or str(saved.get("base_url") or "").strip()
    model = os.environ.get(LLM_ENV[1], "").strip() or str(saved.get("model") or "").strip()
    key = os.environ.get(LLM_ENV[2], "").strip() or str(saved.get("api_key") or "").strip()
    if not key:
        return None
    return {"base": (base or "https://api.deepseek.com/v1").rstrip("/"),
            "model": model or "deepseek-chat", "key": key}


async def llm_solve(paper: dict[str, Any], cfg: dict[str, str] | None, material: str) -> dict[str, Any]:
    """统一走 app.llm.solve —— 它懂"一个实例挂多道小题"（subs），命令行与网页同一实现。"""
    try:
        from app.llm import solve as _solve
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 app.llm（请在 D:\\Ucampus 根目录运行）") from exc
    return await _solve(paper["questions"], material)
