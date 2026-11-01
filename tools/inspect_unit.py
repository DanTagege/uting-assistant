"""看一个题目单元的子题结构与 q_qinst_struct（作答模板），定位多选题/子题怎么提交。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HTML = re.compile(r"<[^>]+>")


def clean(s):
    return re.sub(r"\s+", " ", HTML.sub(" ", s or "")).strip()


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    want = sys.argv[2] if len(sys.argv) > 2 else "1989611653936472074"
    found = []

    def walk(n):
        if isinstance(n, dict):
            if str(n.get("q_qinstid")) == str(want):
                found.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for it in n:
                walk(it)

    walk(data)
    if not found:
        raise SystemExit("没找到该 q_qinstid")
    node = found[0]
    print("== 单元字段 ==")
    print({k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>")
           for k, v in node.items()})

    struct = node.get("q_qinst_struct")
    print("\n== q_qinst_struct（作答模板） ==")
    if isinstance(struct, str):
        try:
            print(json.dumps(json.loads(struct), ensure_ascii=False, indent=1)[:2000])
        except json.JSONDecodeError:
            print(struct[:1500])
    else:
        print(json.dumps(struct, ensure_ascii=False, indent=1)[:2000])

    d = node.get("data") or {}
    print("\n== data.children 数量:", len(d.get("children") or []))
    for i, ch in enumerate(d.get("children") or []):
        print(f"\n--- child[{i}] ---")
        print("字段:", {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>")
                        for k, v in ch.items()})
        for c in ch.get("contents") or []:
            if isinstance(c, dict) and c.get("text"):
                print("  题面:", clean(c["text"])[:160])
        for o in ch.get("options") or []:
            print(f"   {o.get('name') or o.get('value')}. {clean(str(o.get('text')))[:70]}")


if __name__ == "__main__":
    main()
