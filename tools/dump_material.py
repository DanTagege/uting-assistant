"""打印某篇卷的全部可读材料（题面 + 原文/字幕），给人工/模型答题用。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uting import paper as P  # noqa: E402


def main() -> None:
    task_id = sys.argv[1]
    raw = json.loads((P.PAPER_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "ansVersion": 1}, task_id)
    for q in p["questions"]:
        print(f"\n#### #{q['startNo']} {q['name']} [{q['action']}] id={q['instanceId']}")
        for t in q["texts"]:
            if len(t) > 25:
                print("  " + t[:1200].replace("\n", " "))


if __name__ == "__main__":
    main()
