"""题库：`paperHash` + `ansVersion` -> 每道题的答案。

设计要点：用 `paperHash` 定位试卷、`ansVersion` 区分同一篇的第几套答案，
目的是让不同账号/不同批次提交的序列不同。
本模块额外提供：
  - `import_bank()`：从 data/answers/*.json 批量导入（一次答对后沉淀）
  - 未命中时的启发式兜底（低置信度，会被记录到日志，便于后续人工修正）
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from . import db
from .config import DATA

OPTION_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def _norm(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value or "")


def load_bank(paper_hash: str, ans_version: int) -> dict[str, dict[str, Any]]:
    """取指定版本的答案；版本不存在时回退到最近的较低版本（并标记 source）。"""
    row = db.one("SELECT payload, source FROM answer_bank WHERE paper_hash=? AND ans_version=?",
                 (paper_hash, ans_version))
    if row:
        data = json.loads(row["payload"])
        return {k: {"answer": v, "confidence": 1.0, "source": row["source"]} for k, v in data.items()}
    row = db.one("SELECT payload, ans_version FROM answer_bank WHERE paper_hash=? AND ans_version<? "
                 "ORDER BY ans_version DESC LIMIT 1", (paper_hash, ans_version))
    if row:
        data = json.loads(row["payload"])
        return {k: {"answer": v, "confidence": 0.9, "source": f"fallback:v{row['ans_version']}"}
                for k, v in data.items()}
    return {}


def save_bank(paper_hash: str, ans_version: int, mapping: dict[str, Any], source: str = "seed") -> None:
    db.ex("INSERT INTO answer_bank(paper_hash,ans_version,payload,source,updated_at) VALUES(?,?,?,?,?) "
          "ON CONFLICT(paper_hash,ans_version) DO UPDATE SET payload=excluded.payload, "
          "source=excluded.source, updated_at=excluded.updated_at",
          (paper_hash, ans_version, json.dumps({k: _norm(v) for k, v in mapping.items()}, ensure_ascii=False),
           source, db.now()))


def heuristic(question: dict[str, Any]) -> tuple[str, float]:
    """兜底猜测：优先"最长选项"启发式（听力题里常见正确答案信息量最大）。

    完全是占位策略，置信度写 0.35，日志里会标注 guessed，
    方便你在 `/api/training/jobs/{id}/paper` 里回看并修正题库。
    """
    options = question.get("options") or []
    if not options:
        return "", 0.1
    if len(options) == 1:
        return _norm(options[0].get("value")), 0.5
    scored = sorted(options, key=lambda o: (-len(str(o.get("label", ""))), str(o.get("value"))))
    return _norm(scored[0].get("value")), 0.35


def import_bank(directory: Path | None = None) -> dict[str, int]:
    """从 data/answers/<paperHash>.<ansVersion>.json 或 <paperHash>.json 导入。

    文件格式（两种都支持）：
      {"paperHash": "…", "ansVersion": 0, "answers": {"<instanceId>": "A"}}
      或直接 {"<instanceId>": "A"}
    """
    directory = directory or (DATA / "answers")
    directory.mkdir(parents=True, exist_ok=True)
    stats = {"files": 0, "entries": 0}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "answers" in payload:
            ph = str(payload.get("paperHash") or path.stem.split(".")[0])
            ver = int(payload.get("ansVersion", 0))
            answers = payload["answers"]
        else:
            ph = path.stem.split(".")[0]
            ver = 0
            answers = payload
        if not isinstance(answers, dict):
            continue
        save_bank(ph, ver, answers, source=f"file:{path.name}")
        stats["files"] += 1
        stats["entries"] += len(answers)
    return stats


def seed_demo_versions(paper_hash: str, questions: list[dict[str, Any]], key: dict[str, str]) -> None:
    """演示用：把 mock 上游的标准答案写成多个版本，体现 ansVersion 的差异。"""
    save_bank(paper_hash, 0, key, source="mock-key")
    for ver in (1, 2):
        mutated = dict(key)
        ids = sorted(mutated)[:ver]
        for iid in ids:
            mutated[iid] = random.Random(iid + str(ver)).choice(["A", "B", "C", "D"])
        save_bank(paper_hash, ver, mutated, source=f"mock-key-v{ver}")
