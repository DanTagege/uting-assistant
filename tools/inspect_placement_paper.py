"""检查定级卷的"叶子"结构：q_qinstid / data.children[].id 到底哪个才是提交粒度。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402


def walk_units(node, out, depth=0):
    if isinstance(node, list):
        for it in node:
            walk_units(it, out, depth)
        return
    if not isinstance(node, dict):
        return
    if "q_qinstid" in node:
        out.append(node)
        return
    for v in node.values():
        if isinstance(v, (dict, list)):
            walk_units(v, out, depth)


def main(task_id: str) -> None:
    db.conn()
    row = db.one("SELECT payload FROM papers WHERE task_id=? ORDER BY fetched_at DESC LIMIT 1", (task_id,))
    if not row:
        raise SystemExit("papers 表里没有这篇")
    paper = json.loads(row["payload"])
    value = paper.get("raw") or {}
    pj = value.get("paperJson")
    if isinstance(pj, str):
        pj = json.loads(pj)

    units = []
    walk_units(pj, units)
    print(f"单元数（q_qinstid）= {len(units)}")
    total_children = 0
    for u in units:
        data = u.get("data") or {}
        children = data.get("children") or []
        opts_here = len(data.get("options") or [])
        ids = [c.get("id") for c in children if isinstance(c, dict)]
        sub_opts = [len((c.get("options") or [])) for c in children if isinstance(c, dict)]
        total_children += len(children)
        print(f"  idx={u.get('q_idx')} tp={u.get('tp')} obj={u.get('is_objective')} "
              f"inst={u.get('q_qinstid')} children={len(children)} child_ids={ids[:4]} "
              f"child_opts={sub_opts[:4]} top_opts={opts_here}")
    print(f"\n子题总数 = {total_children}")
    print(f"按实例提交 = {len(units)} 条；按子题提交 = {total_children or len(units)} 条")


if __name__ == "__main__":
    main(sys.argv[1])
