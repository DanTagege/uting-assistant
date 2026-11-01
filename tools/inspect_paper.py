"""结构分析器：把真实 paperJson 的字段分布打出来，用来校准解析规则。

用法：
    python tools/inspect_paper.py "C:\\Users\\你\\.uting\\papers\\<taskId>.json"
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def walk(node, keys, vals, depth=0, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            keys[k] += 1
            if isinstance(v, (str, int, float)) and len(str(v)) < 40:
                vals[k][str(v)] += 1
            walk(v, keys, vals, depth + 1, f"{path}.{k}")
    elif isinstance(node, list):
        for i, it in enumerate(node):
            walk(it, keys, vals, depth + 1, f"{path}[{i}]")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python tools/inspect_paper.py <paper.json>")
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))

    print("== 顶层 ==")
    print("type:", type(data).__name__)
    if isinstance(data, dict):
        for k, v in data.items():
            desc = f"{type(v).__name__}"
            if isinstance(v, (list, dict)):
                desc += f" len={len(v)}"
            elif isinstance(v, str):
                desc += f" len={len(v)} preview={v[:60]!r}"
            else:
                desc += f" {v}"
            print(f"  {k}: {desc}")

    keys = collections.Counter()
    vals: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    walk(data, keys, vals)

    print("\n== 高频键 ==")
    for k, c in keys.most_common(30):
        print(f"  {k:<22} {c}")

    print("\n== 疑似题型字段的取值分布 ==")
    for k in ("tp", "type", "q_tp", "qType", "resType", "templateType", "questionType", "action"):
        if k in vals:
            print(f"  {k}: {dict(vals[k].most_common(20))}")

    print("\n== 带实例 id 的节点样本 ==")
    samples = []

    def find(node, depth=0):
        if len(samples) >= 3:
            return
        if isinstance(node, dict):
            if any(k in node for k in ("q_qinstid", "qinstid", "instanceId", "q_instance_id")):
                samples.append(node)
            for v in node.values():
                find(v, depth + 1)
        elif isinstance(node, list):
            for it in node:
                find(it, depth + 1)

    find(data)
    for i, s in enumerate(samples, 1):
        print(f"\n--- 样本 {i} ---")
        print(json.dumps(s, ensure_ascii=False, indent=1)[:1800])

    print("\n== 顶层 children 的两层形状 ==")

    def shape(node, depth=0, limit=3):
        if depth > 3:
            return
        if isinstance(node, dict):
            print("  " * depth + f"dict keys={list(node)[:10]}")
            for k in list(node)[:4]:
                v = node[k]
                if isinstance(v, (list, dict)):
                    print("  " * (depth + 1) + f"↳ {k}:")
                    shape(v, depth + 2, limit)
        elif isinstance(node, list):
            print("  " * depth + f"list len={len(node)}")
            if node:
                shape(node[0], depth + 1, limit)

    if isinstance(data, dict):
        for k in list(data)[:4]:
            v = data[k]
            if isinstance(v, (list, dict)):
                print(f"\n[{k}]")
                shape(v, 1)


if __name__ == "__main__":
    main()
