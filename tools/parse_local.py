"""离线解析本地保存的卷面：不联网，验证解析规则并打印题目全貌。

用法：python tools/parse_local.py "<taskId>" [--full]
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
    full = "--full" in sys.argv
    path = P.PAPER_DIR / f"{task_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "token": "(local)", "ansVersion": 1}, task_id)
    print(P.describe(p))
    print("\n" + "=" * 70)
    for q in p["questions"]:
        if q["action"] != "probe":
            continue
        print(f"\n#{q['startNo']}  instanceId={q['instanceId']}  {q['name']} / {q['type']}")
        print("  题面:", q["prompt"][:200])
        for o in q["options"]:
            print(f"    {o['value']}. {o['label'][:80]}")
        if full:
            print("  相关原文:")
            for t in q["texts"][:6]:
                if len(t) > 8:
                    print("    -", t[:220])
    print("\n材料长度:", len(P.material(p)))


if __name__ == "__main__":
    main()
