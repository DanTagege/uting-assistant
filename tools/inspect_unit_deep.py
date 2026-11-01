"""打印某个 q_idx 单元的完整字段，确认子题（children）题干/选项来源。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

TAG = re.compile(r"<[^>]+>")


def clean(s):
    return re.sub(r"\s+", " ", TAG.sub(" ", str(s or ""))).strip()


def walk_units(node, out):
    if isinstance(node, list):
        for it in node:
            walk_units(it, out)
        return
    if not isinstance(node, dict):
        return
    if "q_qinstid" in node:
        out.append(node)
        return
    for v in node.values():
        if isinstance(v, (dict, list)):
            walk_units(v, out)


def main(task_id: str, idx: int) -> None:
    db.conn()
    row = db.one("SELECT payload FROM papers WHERE task_id=? ORDER BY fetched_at DESC LIMIT 1", (task_id,))
    paper = json.loads(row["payload"])
    pj = (paper.get("raw") or {}).get("paperJson")
    if isinstance(pj, str):
        pj = json.loads(pj)
    units = []
    walk_units(pj, units)
    unit = next(u for u in units if int(u.get("q_idx")) == idx)
    data = unit.get("data") or {}
    print(f"unit idx={idx} inst={unit.get('q_qinstid')} template={unit.get('q_template_name')}")
    print("顶层 keys:", list(data)[:12])
    print("顶层 options:", [clean(o.get('text')) for o in (data.get('options') or [])])
    print("顶层 options 原始:", json.dumps(data.get("options"), ensure_ascii=False)[:900])
    print("顶层 contents:", [clean(c.get('text'))[:120] for c in (data.get('contents') or [])][:3])
    for i, ch in enumerate(data.get("children") or []):
        print(f"\n  -- child[{i}] keys={list(ch)[:10]}")
        print(f"     id={ch.get('id')} type={ch.get('type')} replyType={ch.get('replyType')}")
        print(f"     options={[clean(o.get('text')) for o in (ch.get('options') or [])]}")
        print(f"     contents={[clean(c.get('text'))[:130] for c in (ch.get('contents') or [])][:2]}")
        print(f"     quesText={clean(ch.get('quesText'))[:130]!r} direction={clean((ch.get('direction') or {}).get('text'))[:100]!r}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
