"""把某一篇卷的答案写入本地题库（~/.uting/answers.json），供 uting run 命中。

用法：python tools/seed_answer.py <taskId> key=value ...（value 可以是 A 或 "D,C,A"）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uting.client import ANSWER_FILE, load_answers, save_answers  # noqa: E402
from uting import paper as P  # noqa: E402


def main() -> None:
    task_id = sys.argv[1]
    pairs = sys.argv[2:]
    answers: dict[str, object] = {}
    for p in pairs:
        k, _, v = p.partition("=")
        answers[k.strip()] = [x.strip().upper() for x in v.split(",")] if "," in v else v.strip().upper()

    bank = load_answers()
    title = ""
    raw_path = P.PAPER_DIR / f"{task_id}.json"
    if raw_path.exists():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            title = str(raw.get("paperName") or "")
        except json.JSONDecodeError:
            pass
    bank[task_id] = {"title": title, "answers": answers, "updated": time.time()}
    save_answers(bank)
    print(f"已写入题库：{task_id}（{len(answers)} 题）→ {ANSWER_FILE}")
    for k, v in answers.items():
        print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
