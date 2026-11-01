"""打印某一章（chr[x]）的完整骨架，用于写解析规则。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HTML = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return HTML.sub("", s or "").strip()


def skeleton(node, depth=0, max_depth=6, out=None):
    out = out if out is not None else []
    pad = "  " * depth
    if isinstance(node, dict):
        keys = list(node)
        brief = {k: node[k] for k in keys if isinstance(node[k], (str, int, float, bool)) and str(node[k])[:1] not in "{}[]"}
        text = ""
        for k in ("text", "nm", "quesText", "title", "name"):
            if isinstance(node.get(k), str) and node[k].strip():
                text = strip_html(node[k])[:70]
                break
        out.append(f"{pad}dict keys={keys}")
        if brief:
            out.append(f"{pad}  scalars: {json.dumps(brief, ensure_ascii=False)[:260]}")
        if text:
            out.append(f"{pad}  ~text: {text}")
        if depth >= max_depth:
            return out
        for k, v in node.items():
            if isinstance(v, (dict, list)) and k not in ("options",):
                out.append(f"{pad}  ↳ {k}:")
                skeleton(v, depth + 2, max_depth, out)
            elif k == "options" and isinstance(v, list):
                out.append(f"{pad}  ↳ options[{len(v)}]: " +
                           json.dumps([{"name": o.get("name"), "text": strip_html(o.get("text", ""))}
                                       for o in v if isinstance(o, dict)], ensure_ascii=False))
    elif isinstance(node, list):
        out.append(f"{pad}list len={len(node)}")
        for i, it in enumerate(node):
            out.append(f"{pad}[{i}]")
            skeleton(it, depth + 1, max_depth, out)
    return out


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    node = data["chr"][idx]
    lines = skeleton(node, 0, 8)
    print(f"== chr[{idx}] 骨架（{len(lines)} 行）==")
    for line in lines[:180]:
        print(line)


if __name__ == "__main__":
    main()
