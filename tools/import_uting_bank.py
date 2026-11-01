"""把命令行工具（uting）积累的题库导入平台数据库，让网页也能直接答题。

用法：python tools/import_uting_bank.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from uting.client import load_answers  # noqa: E402


def main() -> None:
    db.conn()
    bank = load_answers()
    n = 0
    for task_id, node in bank.items():
        answers = (node or {}).get("answers") or {}
        if not answers:
            continue
        db.ex("""INSERT INTO answer_bank(paper_hash,ans_version,payload,source,updated_at)
                 VALUES(?,?,?,?,?)
                 ON CONFLICT(paper_hash,ans_version) DO UPDATE SET
                   payload=excluded.payload, source=excluded.source, updated_at=excluded.updated_at""",
              (str(task_id), 1, json.dumps(answers, ensure_ascii=False), "uting-bank", time.time()))
        n += 1
    print(f"已导入 {n} 篇题库到平台（paper_hash = taskId, ansVersion = 1）")
    print("平台里已有的题库：" + ", ".join(
        r["paper_hash"] for r in db.q("SELECT paper_hash FROM answer_bank ORDER BY updated_at DESC LIMIT 10")))


if __name__ == "__main__":
    main()
