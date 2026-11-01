"""听力定级：控制答对比例，把定级卷做到目标级别区间。

已知（2026-10-28 实测）：
  · 未定级账号 getUserStatus(flowType=listen) → type=grade，带 taskId/ansVersion
  · 报告 POST /api/uls/report/levelReport → {level:14, levelName:"S14"}（不含分数）
  · 定级只能做一次

未知：服务端"分数 → 级别"的曲线，所以这里用可编辑的映射表（data/placement_map.json），
默认给出经验区间；第一次跑完会写回实测结果，后续越用越准。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import DATA

MAP_PATH = DATA / "placement_map.json"

# 默认经验映射：目标级别 -> 目标正确率(%)。跑完会写回 observed 记录。
DEFAULT_MAP: dict[str, Any] = {
    "S12": 40, "S13": 55, "S14": 70, "S15": 85, "S16": 95,
    "notes": "值是目标正确率百分比；服务端按正确率曲线给级别，实测后会自动校准",
    "observed": [],
}


def load_map() -> dict[str, Any]:
    if MAP_PATH.exists():
        try:
            data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in DEFAULT_MAP.items():
                    data.setdefault(k, v)
                return data
        except json.JSONDecodeError:
            pass
    save_map(DEFAULT_MAP)
    return dict(DEFAULT_MAP)


def save_map(data: dict[str, Any]) -> None:
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def levels() -> list[str]:
    m = load_map()
    return [k for k in m if re.fullmatch(r"S\d+", k)]


def target_ratio(level: str) -> int:
    m = load_map()
    try:
        return int(m.get(level, 70))
    except (TypeError, ValueError):
        return 70


def sequence_answer(question: dict[str, Any]) -> list[str] | None:
    """排序题：选项的 value 就是正确位置（1..N），据此排出每个位置该放哪个选项。

    卷面样例：{name:"A", text:"Cool the fish…", value:"4"} → A 是第 4 步。
    返回形如 ["C","B","D","A"]（第 1..N 个位置对应的选项名）。
    """
    qtype = (question.get("type") or "").lower()
    if "sequence" not in qtype:
        return None
    subs = question.get("subs") or []
    opts = (subs[0].get("options") if subs else question.get("options")) or []
    if len(opts) < 2:
        return None
    names = [str(o.get("name") or o.get("value") or "").strip().upper() for o in opts]
    vals = [str(o.get("value") or "").strip() for o in opts]
    if not all(v.isdigit() for v in vals):
        return None
    order = sorted(range(len(opts)), key=lambda i: int(vals[i]))
    if len(order) != len(subs) and subs:
        return None
    return [names[i] for i in order]


def record_observation(target_level: str, ratio: int, achieved_level: str | None,
                       achieved_name: str | None) -> None:
    m = load_map()
    obs = m.setdefault("observed", [])
    obs.append({"at": time.time(), "target": target_level, "ratio": ratio,
                "achieved": achieved_level, "achievedName": achieved_name})
    m["observed"] = obs[-20:]
    save_map(m)


def trim_answers(answers: dict[str, Any], questions: list[dict[str, Any]], ratio: int) -> dict[str, Any]:
    """按比例保留正确答案，其余故意改成错项（保持 coverage，不能留空）。

    策略：把客观题按题号顺序，前 ratio% 保留模型答案，后面的换成"另一个选项"。
    多小题实例按小题逐个处理。
    """
    ratio = max(0, min(100, ratio))
    probes = [q for q in questions if q.get("action") == "probe"]
    if not probes:
        return answers
    keep = round(len(probes) * ratio / 100)
    out: dict[str, Any] = {}

    for idx, q in enumerate(probes):
        iid = q["instanceId"]
        if iid not in answers:
            continue
        correct = answers[iid]
        if idx < keep:
            out[iid] = correct
            continue

        subs = q.get("subs") or []
        if subs:
            vals = correct if isinstance(correct, list) else [str(correct)]
            if "sequence" in (q.get("type") or "").lower():
                # 排序题：故意打乱顺序（反转），不要拿选项 value 去替换字母
                out[iid] = list(reversed(vals))
                continue
            wrong: list[str] = []
            for i, sub in enumerate(subs):
                opts = [o["value"] for o in sub.get("options", [])]
                pick = str(vals[i]) if i < len(vals) else ""
                other = next((o for o in opts if o.upper() != pick.upper()), pick)
                wrong.append(other)
            out[iid] = wrong
        else:
            opts = [o["value"] for o in q.get("options", [])]
            pick = str(correct)
            other = next((o for o in opts if o.upper() != pick.upper()), pick)
            out[iid] = other
    return out
