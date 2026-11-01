"""DeepSeek（OpenAI 兼容）答题器：读卷面原文 → 输出每题答案。

配置存在 ~/.uting/llm.json（0600），CLI 与网页共用；可用环境变量覆盖：
    UTING_LLM_API_KEY / UTING_LLM_MODEL / UTING_LLM_BASE_URL
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
CFG_PATH = Path(os.environ.get("UTING_HOME", Path.home() / ".uting")) / "llm.json"

SYSTEM = (
    "你是英语听力/视听说卷子的答题器。用户会给你卷面原文（听力文稿或字幕）和题目列表。\n"
    "只输出一个 JSON 对象，键是题目 instanceId，值是答案：\n"
    "- 单选题：单个大写字母，如 \"B\"\n"
    "- 一个实例含多道小题：按小题顺序给出数组，如 [\"C\",\"A\",\"B\"]\n"
    "- 填空题：用 | 分隔各空，如 \"nine|morning\"\n"
    "- 判断题：T 或 F\n"
    "必须依据原文作答；原文没有明说时选最合理的。禁止输出 JSON 以外的任何内容。\n"
    "注意：若某题含多个小题/步骤且共用同一组选项（如排序题），按顺序给出等长的字母数组，字母不要重复；"
    "若各小题有各自选项，则每项只填该小题的字母。"
)


class LLMError(Exception):
    pass


# --------------------------------------------------------------------------- 配置
def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if CFG_PATH.exists():
        try:
            cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    env_key = os.environ.get("UTING_LLM_API_KEY", "").strip()
    if env_key:
        cfg["api_key"] = env_key
    cfg["base_url"] = (os.environ.get("UTING_LLM_BASE_URL") or cfg.get("base_url") or DEFAULT_BASE).rstrip("/")
    cfg["model"] = os.environ.get("UTING_LLM_MODEL") or cfg.get("model") or DEFAULT_MODEL
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    return cfg


def save_config(api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    if api_key is not None:
        cfg["api_key"] = api_key.strip()
    if model:
        cfg["model"] = model.strip()
    if base_url:
        cfg["base_url"] = base_url.rstrip("/")
    cfg["updated_at"] = time.time()
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(json.dumps({k: cfg[k] for k in ("api_key", "model", "base_url", "updated_at")},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CFG_PATH.chmod(0o600)
    except OSError:
        pass
    return cfg


def mask(api_key: str) -> str:
    k = (api_key or "").strip()
    if not k:
        return ""
    return f"{k[:4]}…{k[-4:]}" if len(k) > 10 else "已设置"


def configured() -> bool:
    return bool(load_config().get("api_key"))


# --------------------------------------------------------------------------- 调用
async def _chat(messages: list[dict[str, str]], *, max_tokens: int = 3000, timeout: float = 180,
                json_mode: bool = True, _retry: int = 0) -> str:
    cfg = load_config()
    key = cfg.get("api_key", "")
    if not key:
        raise LLMError("还没有配置 API Key")
    model = cfg["model"]
    # reasoner 会把"思考 token"也算进 max_tokens，预算给小了 content 直接是空
    if "reasoner" in model.lower():
        max_tokens = max(max_tokens, 6000)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{cfg['base_url']}/chat/completions",
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                             json=payload)
            if r.status_code == 400 and json_mode and "json" in r.text.lower():
                # 有些模型/网关不接受 response_format，退回普通文本模式
                payload.pop("response_format", None)
                r = await c.post(f"{cfg['base_url']}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 json=payload)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise LLMError(f"连接 DeepSeek 失败：{exc}") from exc
    if r.status_code == 401:
        raise LLMError("API Key 无效（401）")
    if r.status_code == 402:
        raise LLMError("账户余额不足（402）")
    if r.status_code == 429:
        raise LLMError("触发限流（429），稍后再试")
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content", "") or ""
    finish = choice.get("finish_reason")

    # 空回复 / 被截断：加大预算重试一次（reasoner 常见）
    if (not content.strip() or finish == "length") and _retry == 0:
        bigger = min(8000, max_tokens * 3)
        return await _chat(messages, max_tokens=bigger, timeout=timeout,
                           json_mode=json_mode, _retry=1)
    if not content.strip():
        usage = data.get("usage") or {}
        raise LLMError(f"模型返回空内容（finish={finish}，reasoning_tokens={usage.get('completion_tokens_details', {}).get('reasoning_tokens')}）；"
                       f"若是 deepseek-reasoner，请把模型换成 deepseek-chat 或重试")
    return content


async def test_key() -> dict[str, Any]:
    t0 = time.time()
    text = await _chat([{"role": "user", "content": '只输出 json：{"ok":true}'}], max_tokens=30, timeout=30)
    return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": text[:80]}


# --------------------------------------------------------------------------- 答题
def _brief(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for q in questions:
        if q.get("action") != "probe":
            continue
        item: dict[str, Any] = {"instanceId": q["instanceId"], "type": q.get("type") or "", "name": q.get("name") or ""}
        subs = q.get("subs") or []
        if subs:
            shared = len(subs) > 1 and all(
                [o["value"] for o in s.get("options", [])] == [o["value"] for o in subs[0].get("options", [])]
                for s in subs)
            item["subQuestions"] = [
                {"no": i + 1, "prompt": s.get("prompt") or "", "options": [f"{o['value']}. {o['label']}" for o in s.get("options", [])]}
                for i, s in enumerate(subs)
            ]
            if shared:
                item["hint"] = f"这 {len(subs)} 个空共用同一组选项，请按顺序给出 {len(subs)} 个不同字母"
        else:
            item["prompt"] = q.get("prompt") or ""
            item["options"] = [f"{o['value']}. {o['label']}" for o in q.get("options", [])]
        out.append(item)
    return out


def _extract_json(text: str) -> dict[str, Any] | None:
    """尽量从模型输出里抠出 JSON：代码块、前后说明、被截断都试着救。"""
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    start = t.find("{")
    cands: list[str] = [t]
    if start >= 0:
        cands.append(t[start:])
        end = t.rfind("}")
        if end > start:
            cands.append(t[start:end + 1])
    for cand in cands:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    if start >= 0:                      # 截断补救：去掉尾部并补括号
        frag = t[start:]
        for trim in range(0, min(400, len(frag))):
            s = frag[: len(frag) - trim]
            for suffix in ("}", "]}", "}]}", '"}', '"]}', '"]}}'):
                try:
                    data = json.loads(s + suffix)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data:
                    return data
    return None


async def solve(questions: list[dict[str, Any]], material: str) -> dict[str, Any]:
    """返回 {instanceId: "A" | ["C","A"] }，已按选项校验过滤非法值。"""
    items = _brief(questions)
    if not items:
        return {}

    def build(mat: str) -> list[dict[str, str]]:
        user = (
            "卷面原文/字幕：\n" + (mat or "（无原文，只能依据题面推断）")[:24000] +
            "\n\n需要作答的题目：\n" + json.dumps(items, ensure_ascii=False, indent=1) +
            "\n\n只输出一个 json 对象：{instanceId: 答案}，不要任何解释或代码块。"
        )
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    raw: dict[str, Any] | None = None
    last = ""
    for attempt, mat in enumerate((material, material[:6000])):
        last = await _chat(build(mat), max_tokens=3000)
        raw = _extract_json(last)
        if raw:
            break
    if not raw:
        raise LLMError(f"模型没有返回可用 JSON（原始输出前 120 字：{last[:120]!r}）")

    by_id = {q["instanceId"]: q for q in questions if q.get("action") == "probe"}
    out: dict[str, Any] = {}
    for iid, ans in raw.items():
        q = by_id.get(str(iid))
        if q is None:
            continue
        subs = q.get("subs") or []
        if subs:
            vals = ans if isinstance(ans, list) else [x for x in re.split(r"[,\s]+", str(ans)) if x]
            vals = [str(v).strip().upper() for v in vals]
            if len(vals) < len(subs):
                continue
            ok = True
            for i, sub in enumerate(subs):
                letters = {o["value"].upper() for o in sub.get("options", [])}
                if letters and any(ch not in letters for ch in vals[i].replace(" ", "")):
                    ok = False
                    break
            if ok:
                out[str(iid)] = vals[:len(subs)]
        else:
            val = str(ans).strip()
            letters = {o["value"].upper() for o in q.get("options", [])}
            if letters and ("blank" not in (q.get("type") or "").lower()):
                up = val.upper().replace(" ", "")
                if not up or any(ch not in letters for ch in up):
                    continue
                out[str(iid)] = up
            elif val:
                out[str(iid)] = val
    return out