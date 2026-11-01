"""打印某一篇卷的完整题面/原文（给人工答题用）。

用法：python tools/show_paper_text.py <taskId> [--idx 13] [--max 4000]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uting import paper as P  # noqa: E402


def main() -> None:
    task_id = sys.argv[1]
    only = None
    maxlen = 3000
    if "--idx" in sys.argv:
        only = int(sys.argv[sys.argv.index("--idx") + 1])
    if "--max" in sys.argv:
        maxlen = int(sys.argv[sys.argv.index("--max") + 1])

    raw = json.loads((P.PAPER_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "ansVersion": 1}, task_id)

    for q in p["questions"]:
        if only is not None and q["startNo"] != only:
            continue
        print("=" * 78)
        print(f"#{q['startNo']} {q['name'] or q['type']}  instanceId={q['instanceId']}  action={q['action']}")
        if q["subs"]:
            for i, sub in enumerate(q["subs"], 1):
                print(f"\n  小题 {i}：{sub['prompt']}")
                for o in sub["options"]:
                    print(f"     {o['value']}. {o['label']}")
        elif q["options"]:
            for o in q["options"]:
                print(f"   {o['value']}. {o['label']}")
        texts = [t for t in q["texts"] if len(t) > 12]
        if texts:
            print("\n  --- 原文/字幕 ---")
            for t in texts[:6]:
                print("  " + t[:maxlen].replace("\n", " "))


if __name__ == "__main__":
    main()
