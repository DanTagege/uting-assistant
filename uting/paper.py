"""试卷解析（按 U听说 真实 paperJson 结构）。

真实结构（2026-10 实测，S13 视听说卷）：
  chr[]                                  ← 章：pre-listening / while-listening / post-listening
    └ chr[]                              ← 题目单元
        ├ q_qinstid      : 雪花 id（JSON 数字！必须转字符串，否则精度丢失）
        ├ q_idx          : 题号
        ├ nm             : 中文名（听力_单选_客观题 / 单词跟读 / …）
        ├ q_template_name: 题型（single-choice_objective / vocabulary / …）
        ├ is_objective   : 是否客观题
        └ data
            ├ contents[] : {text(HTML 原文/字幕), path(.wav), name, id}
            ├ options[]  : 常见于 data.children[0].options（{name,text,value}，text 带 HTML）
            ├ quesText / direction.text : 题面
            └ replyType  : singlechoice / outline / …

所以"是不是要作答"看 is_objective，"答什么"看 options，"听到什么"看 contents[].text。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

HOME = Path(os.environ.get("UTING_HOME", Path.home() / ".uting"))
PAPER_DIR = HOME / "papers"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

ORAL_HINTS = ("跟读", "口语", "朗读", "record", "speak", "oral", "pronounce")
LEARN_HINTS = ("学习", "导读", "讲解", "词汇", "vocabulary", "learn", "guide")


def clean(s: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", s or "")).strip()


# --------------------------------------------------------------------------- 遍历
def _iter_units(node: Any, part: str = "") -> Iterator[dict[str, Any]]:
    """产出所有带 q_qinstid 的题目单元，并带上所属章名。"""
    if isinstance(node, list):
        for it in node:
            yield from _iter_units(it, part)
        return
    if not isinstance(node, dict):
        return

    if "q_qinstid" in node:
        yield {"node": node, "part": part}
        return

    # 章节节点：用 nm 作为 part 名
    here = part
    nm = node.get("nm")
    if isinstance(nm, str) and nm.strip() and "chr" in node:
        here = nm.strip()

    for k, v in node.items():
        if isinstance(v, (dict, list)):
            yield from _iter_units(v, here)


def _find_options(data: Any) -> list[dict[str, str]]:
    """在 data 子树里找第一个非空 options 列表。"""
    if isinstance(data, dict):
        opts = data.get("options")
        if isinstance(opts, list) and opts:
            out = []
            for i, o in enumerate(opts):
                if isinstance(o, str):
                    out.append({"value": o, "label": o})
                    continue
                if not isinstance(o, dict):
                    continue
                value = str(o.get("value") or o.get("name") or chr(65 + i))
                label = clean(str(o.get("text") or o.get("label") or o.get("content") or value))
                out.append({"value": value, "label": label})
            if out:
                return out
        for v in data.values():
            found = _find_options(v)
            if found:
                return found
    elif isinstance(data, list):
        for it in data:
            found = _find_options(it)
            if found:
                return found
    return []


def _contents_text(data: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(data, dict):
        for key in ("quesText", "text", "nm", "name"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                texts.append(clean(v))
        if "direction" in data and isinstance(data["direction"], dict):
            d = data["direction"]
            for key in ("text", "mText", "pcText"):
                if isinstance(d.get(key), str) and d[key].strip():
                    texts.append(clean(d[key]))
        for v in data.values():
            if isinstance(v, (dict, list)):
                texts.extend(_contents_text(v))
    elif isinstance(data, list):
        for it in data:
            texts.extend(_contents_text(it))
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _classify(unit: dict[str, Any]) -> str:
    node = unit["node"]
    if bool(node.get("is_objective")):
        return "probe"
    label = f"{node.get('nm') or ''} {node.get('q_template_name') or ''}".lower()
    if any(h in label for h in ORAL_HINTS):
        return "oral"
    if any(h in label for h in LEARN_HINTS):
        return "learn"
    return "probe" if _find_options(node.get("data")) else "learn"


def _int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def normalize(value: dict[str, Any], task_id: str) -> dict[str, Any]:
    paper_json = value.get("paperJson")
    if isinstance(paper_json, str):
        try:
            paper_json = json.loads(paper_json)
        except json.JSONDecodeError:
            paper_json = {}

    questions: list[dict[str, Any]] = []
    for unit in _iter_units(paper_json):
        node = unit["node"]
        data = node.get("data") or {}
        instance = str(node.get("q_qinstid")).strip()
        if instance.endswith(".0"):
            instance = instance[:-2]
        options = _find_options(data)
        texts = _contents_text(data)

        # 一个实例下挂多道小题（如 while-listening 一篇 3 问）
        subs: list[dict[str, Any]] = []
        for ch in data.get("children") or []:
            if not isinstance(ch, dict):
                continue
            sub_opts = _find_options(ch)
            if not sub_opts:
                continue
            sub_text = ""
            for c in ch.get("contents") or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip():
                    sub_text = clean(c["text"])
                    break
            subs.append({"prompt": sub_text, "options": sub_opts})

        headline = ""
        for key in ("quesText",):
            if isinstance(data.get(key), str) and clean(data[key]):
                headline = clean(data[key])
                break
        if not headline:
            cand = [t for t in texts if len(t) > 8]
            headline = max(cand, key=len) if cand else (texts[0] if texts else "")
        if subs and not headline:
            headline = subs[0]["prompt"]

        questions.append({
            "instanceId": instance,
            "partName": unit["part"],
            "startNo": _int(node.get("q_idx")),
            "type": str(node.get("q_template_name") or node.get("nm") or ""),
            "name": str(node.get("nm") or ""),
            "objective": bool(node.get("is_objective")),
            "action": _classify(unit),
            "prompt": headline,
            "texts": texts,
            "options": options,
            "subs": subs,
        })
    questions.sort(key=lambda q: (q.get("startNo") or 0))

    return {
        "taskId": str(value.get("taskId") or task_id),
        "title": str(value.get("paperName") or value.get("title") or f"听力训练 {task_id}"),
        "audioVisual": value.get("audioVisual"),
        "token": str(value.get("token") or value.get("paperToken") or ""),
        "ansVersion": value.get("ansVersion"),
        "questions": questions,
        "raw": paper_json,
    }


def save_raw(paper: dict[str, Any]) -> Path:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    path = PAPER_DIR / f"{paper['taskId']}.json"
    path.write_text(json.dumps(paper["raw"], ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def material(paper: dict[str, Any]) -> str:
    """给 LLM 的材料：每题题面 + 该题相关的原文/字幕。"""
    chunks: list[str] = []
    for q in paper["questions"]:
        chunks.append(f"\n# 第{q['startNo'] or '?'}题（instanceId={q['instanceId']}, {q['name'] or q['type']}）")
        if q["prompt"]:
            chunks.append("题面：" + q["prompt"])
        for opt in q["options"]:
            chunks.append(f"  {opt['value']}. {opt['label']}")
        for t in q["texts"]:
            if len(t) > 6 and t != q["prompt"]:
                chunks.append("原文/字幕：" + t)
    return "\n".join(chunks)


def describe(paper: dict[str, Any]) -> str:
    qs = paper["questions"]
    lines = [f"卷面：{paper['title']}（taskId={paper['taskId']}，ansVersion={paper['ansVersion']}，"
             f"token={'有' if paper['token'] else '无'}）"]
    if not qs:
        lines.append("  ⚠ 没解析出题目：请把 papers/<taskId>.json 发我。")
        return "\n".join(lines)
    probe = [q for q in qs if q["action"] == "probe"]
    oral = [q for q in qs if q["action"] == "oral"]
    learn = [q for q in qs if q["action"] == "learn"]
    subs = sum(len(q["subs"]) for q in probe)
    lines.append(f"  共 {len(qs)} 项：客观题 {len(probe)}（含小题 {subs}），跟读/口语 {len(oral)}，学习段 {len(learn)}")
    part = None
    for q in qs:
        if q["partName"] != part:
            part = q["partName"]
            lines.append(f"  [{part or '未分章'}]")
        mark = {"probe": "答", "oral": "读", "learn": "学"}[q["action"]]
        n_items = len(q["subs"]) or (1 if q["options"] else 0)
        lines.append(f"    {mark} #{q['startNo']:<2} {q['name'] or q['type']}"
                     + (f"（{n_items} 小题）" if q["subs"] else ""))
        if q["subs"]:
            for i, sub in enumerate(q["subs"], 1):
                lines.append(f"        {i}) {sub['prompt'][:70]}")
                lines.append("           " + " ｜ ".join(
                    f"{o['value']}.{o['label'][:26]}" for o in sub["options"][:4]))
        else:
            if q["prompt"]:
                lines.append(f"        题面：{q['prompt'][:70]}")
            if q["options"]:
                lines.append("        选项：" + " ｜ ".join(
                    f"{o['value']}.{o['label'][:22]}" for o in q["options"][:4]))
    return "\n".join(lines)
