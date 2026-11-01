"""定向分析：找出真实 paperJson 里"题目实例 id / 题干 / 选项 / 题型"分别挂在哪个字段。"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

HEX16 = re.compile(r"^[0-9a-f]{16}$")


def main() -> None:
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))

    print("== 第 1 层（chr 里的 4 个元素） ==")
    chr_list = data.get("chr") or []
    for i, item in enumerate(chr_list):
        if isinstance(item, dict):
            print(f"[{i}] keys={list(item)}  type={item.get('type')!r}  name={str(item.get('name'))[:40]!r}  "
                  f"children={len(item.get('children') or [])}  contents={len(item.get('contents') or [])}")

    print("\n== 每个带 options 的节点 ==")
    found = []

    def walk(node, path_str="$"):
        if isinstance(node, dict):
            if "options" in node and isinstance(node["options"], list) and node["options"]:
                found.append((path_str, node))
            for k, v in node.items():
                walk(v, f"{path_str}.{k}")
        elif isinstance(node, list):
            for i, it in enumerate(node):
                walk(it, f"{path_str}[{i}]")

    walk(data)
    print(f"共 {len(found)} 个带 options 的节点")
    for p, node in found[:3]:
        slim = {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>")
                for k, v in node.items()}
        print(f"\n路径 {p}")
        print("  字段:", json.dumps(slim, ensure_ascii=False))
        opts = node.get("options") or []
        print("  options[0]:", json.dumps(opts[0], ensure_ascii=False)[:600])
        if len(opts) > 1:
            print("  options[1]:", json.dumps(opts[1], ensure_ascii=False)[:400])
        print("  完整节点前 1200 字:", json.dumps(node, ensure_ascii=False)[:1200])

    print("\n== 16 位 hex（疑似题目实例 id）出现在哪些键下 ==")
    where = collections.Counter()

    def walk2(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and HEX16.match(v):
                    where[k] += 1
                walk2(v)
        elif isinstance(node, list):
            for it in node:
                walk2(it)

    walk2(data)
    print(dict(where.most_common(10)))

    print("\n== 带 replyType 的节点（作答单元） ==")
    units = []

    def walk3(node):
        if isinstance(node, dict):
            if "replyType" in node:
                units.append(node)
            for v in node.values():
                walk3(v)
        elif isinstance(node, list):
            for it in node:
                walk3(it)

    walk3(data)
    print(f"共 {len(units)} 个")
    for u in units[:2]:
        keys = {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>") for k, v in u.items()}
        print("\n  字段:", json.dumps(keys, ensure_ascii=False)[:800])
        ch = u.get("children") or []
        if ch:
            print("  children[0] 字段:", json.dumps(
                {k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} len={len(v)}>")
                 for k, v in ch[0].items()}, ensure_ascii=False)[:600] if isinstance(ch[0], dict) else str(ch[0])[:200])


if __name__ == "__main__":
    main()
