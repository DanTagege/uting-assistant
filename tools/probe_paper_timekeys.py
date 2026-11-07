"""看取卷响应里有哪些"时间/计数"字段，判断提交时该回传什么。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

TIME_HINT = ("time", "duration", "cost", "count", "start", "end", "spend")


def walk(node, prefix="", depth=0):
    if depth > 3:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            low = k.lower()
            hit = any(h in low for h in TIME_HINT) and not isinstance(v, (dict, list))
            if hit:
                print(f"  {prefix}{k} = {v!r}")
            if isinstance(v, (dict, list)):
                walk(v, prefix + k + ".", depth + 1)
    elif isinstance(node, list) and node:
        walk(node[0], prefix + "[0].", depth + 1)


def main() -> None:
    db.conn()
    row = db.one("SELECT task_id, payload FROM papers ORDER BY fetched_at DESC LIMIT 1")
    if not row:
        raise SystemExit("没有缓存试卷")
    paper = json.loads(row["payload"])
    print(f"task_id={row['task_id']}")
    print("payload 顶层键:", sorted(paper.keys()))
    raw = paper.get("raw") or {}
    print("raw 顶层键:", sorted(raw.keys()))
    print("\n=== 时间/计数类字段（响应侧）===")
    walk(raw)
    print("\n=== 平台记录历史用的字段现状 ===")
    print("  startTime/endTime 由服务端记录，我们提交时未回传 → 历史里为空")


if __name__ == "__main__":
    main()
