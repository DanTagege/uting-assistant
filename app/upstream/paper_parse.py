"""把 U 听说的 paperJson 归一化成平台的 questions 结构（真实结构版）。

真实结构（2026-10 实测）：
  chr[] → chr[] → { q_qinstid, q_idx, nm, q_template_name, is_objective, data }
  data.children[] 里可能挂多道小题，每道小题自带 options[]
  data.contents[].text 是原文/字幕（HTML）
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

ORAL_HINTS = ("跟读", "口语", "朗读", "record", "speak", "oral", "pronounce")
LEARN_HINTS = ("学习", "导读", "讲解", "词汇", "vocabulary", "outline", "learn", "guide")
PART_KEYS = ("partName", "partTitle", "chapter", "sectionName", "unitName")


def clean(s: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", s or "")).strip()


def _iter_units(node: Any, part: str = "") -> Iterator[dict[str, Any]]:
    if isinstance(node, list):
        for it in node:
            yield from _iter_units(it, part)
        return
    if not isinstance(node, dict):
        return
    if "q_qinstid" in node:
        yield {"node": node, "part": part}
        return
    here = part
    nm = node.get("nm")
    if isinstance(nm, str) and nm.strip() and "chr" in node:
        here = nm.strip()
    for v in node.values():
        if isinstance(v, (dict, list)):
            yield from _iter_units(v, here)


def _options(node: Any) -> list[dict[str, str]]:
    if isinstance(node, dict):
        opts = node.get("options")
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
                out.append({"value": value, "label": label,
                            "name": str(o.get("name") or "").strip()})
            if out:
                return out
        for v in node.values():
            found = _options(v)
            if found:
                return found
    elif isinstance(node, list):
        for it in node:
            found = _options(it)
            if found:
                return found
    return []


def _texts(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for k in ("quesText", "text", "nm", "name"):
            v = node.get(k)
            if isinstance(v, str) and v.strip():
                out.append(clean(v))
        if isinstance(node.get("direction"), dict):
            for k in ("text", "mText", "pcText"):
                v = node["direction"].get(k)
                if isinstance(v, str) and v.strip():
                    out.append(clean(v))
        for v in node.values():
            if isinstance(v, (dict, list)):
                out.extend(_texts(v))
    elif isinstance(node, list):
        for it in node:
            out.extend(_texts(it))
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


def _classify(node: dict[str, Any]) -> str:
    if bool(node.get("is_objective")):
        return "probe"
    label = f"{node.get('nm') or ''} {node.get('q_template_name') or ''}".lower()
    if any(h in label for h in ORAL_HINTS):
        return "oral"
    if any(h in label for h in LEARN_HINTS):
        return "skip"
    return "probe" if _options(node.get("data")) else "skip"


def _int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def normalize(paper_payload: dict[str, Any], task_id: str) -> dict[str, Any]:
    paper_json = paper_payload.get("paperJson") or paper_payload.get("paper") or paper_payload
    if isinstance(paper_json, str):
        try:
            paper_json = json.loads(paper_json)
        except json.JSONDecodeError:
            paper_json = {}

    questions: list[dict[str, Any]] = []
    for unit in _iter_units(paper_json):
        node = unit["node"]
        data = node.get("data") or {}
        instance = str(node.get("q_qinstid") or "").strip()
        if instance.endswith(".0"):
            instance = instance[:-2]
        if not instance:
            continue

        top_options = _options(data)
        subs: list[dict[str, Any]] = []
        for i, ch in enumerate(data.get("children") or []):
            if not isinstance(ch, dict):
                continue
            # 子题没自带选项时（排序/多问共用选项），继承顶层选项
            sub_opts = _options(ch) or top_options
            sub_text = ""
            for c in ch.get("contents") or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip():
                    sub_text = clean(c["text"])
                    break
            if not sub_text:
                sub_text = clean(ch.get("quesText") or (ch.get("direction") or {}).get("text") or "")
            if not sub_opts and not sub_text:
                continue          # 真正空的占位子项才跳过
            subs.append({"prompt": sub_text or f"第{i + 1}小题", "options": sub_opts,
                         "childId": str(ch.get("id") or "").strip()})

        texts = _texts(data)
        headline = ""
        if isinstance(data.get("quesText"), str) and clean(data["quesText"]):
            headline = clean(data["quesText"])
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
            "action": _classify(node),
            "prompt": headline,
            "texts": texts,
            "options": top_options if (top_options and len(subs) > 1) else (subs[0]["options"] if len(subs) == 1 else top_options),
            "subs": subs,
        })
    questions.sort(key=lambda q: (q.get("startNo") or 0))

    return {
        "paperHash": str(paper_payload.get("paperHash") or paper_payload.get("taskId") or task_id),
        "paperTitle": str(paper_payload.get("paperName") or paper_payload.get("title") or f"U听说 {task_id}"),
        "audioVisual": int(paper_payload.get("audioVisual") or 0),
        "questionCount": len(questions),
        "gradedCount": 0,
        "questions": questions,
        "raw": paper_payload,
    }
