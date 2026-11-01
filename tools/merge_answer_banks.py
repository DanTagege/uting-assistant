"""合并所有 answers.json 到全局题库，并同步进平台数据库。

扫描：
  ~/.uting/answers.json
  ~/.uting/accounts/*/answers.json
  <UTING_LEGACY_HOMES 里的>/answers.json（默认带上 D:\\Ucampus\\data\\uting_b）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

HOME = Path(os.environ.get("UTING_HOME", Path.home() / ".uting"))
TARGET = HOME / "answers.json"


def candidates() -> list[Path]:
    out = [TARGET]
    if (HOME / "accounts").exists():
        out += sorted((HOME / "accounts").glob("*/answers.json"))
    legacy = Path(r"D:\Ucampus\data\uting_b\answers.json")
    if legacy.exists():
        out.append(legacy)
    return out


def main() -> None:
    merged: dict = {}
    if TARGET.exists():
        try:
            merged = json.loads(TARGET.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            merged = {}

    stats = []
    for path in candidates():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        n = 0
        for task_id, node in (data or {}).items():
            answers = (node or {}).get("answers") if isinstance(node, dict) else None
            if not answers:
                continue
            if task_id not in merged or len(answers) >= len((merged[task_id].get("answers") or {})):
                merged[task_id] = {"title": (node or {}).get("title", ""), "answers": answers,
                                   "updated": time.time()}
                n += 1
        stats.append((path, n))

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"合并完成：{len(merged)} 篇 → {TARGET}")
    for path, n in stats:
        print(f"  来源 {path}  采用 {n} 篇")

    db.conn()
    saved = 0
    for task_id, node in merged.items():
        db.ex("""INSERT INTO answer_bank(paper_hash,ans_version,payload,source,updated_at)
                 VALUES(?,?,?,?,?) ON CONFLICT(paper_hash,ans_version) DO UPDATE SET
                   payload=excluded.payload, source=excluded.source, updated_at=excluded.updated_at""",
              (str(task_id), 1, json.dumps(node["answers"], ensure_ascii=False), "merged", time.time()))
        saved += 1
    print(f"已同步 {saved} 篇到平台题库")


if __name__ == "__main__":
    main()
