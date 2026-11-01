"""按账号打印某一篇卷的题目/选项/原文；也能删题库条目。

用法：
    python tools/show_task.py 104413919195597518 --user 18321531958
    python tools/show_task.py --rm-bank 104413919195597518
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from uting import paper as P  # noqa: E402
from uting.client import profile_dir  # noqa: E402


def show(task_id: str, user: str) -> None:
    candidates = [profile_dir(user) / "papers" / f"{task_id}.json", P.PAPER_DIR / f"{task_id}.json"]
    path = next((c for c in candidates if c.exists()), None)
    if path is None:
        raise SystemExit(f"没有这个卷面文件：{candidates[0]}（先跑 uting probe --user {user}）")
    raw = json.loads(path.read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "ansVersion": 1}, task_id)
    print(f"taskId={task_id}  共 {len(p['questions'])} 项")
    for q in p["questions"]:
        if q["action"] != "probe":
            print(f"\n# {q['startNo']} [{q['action']}] {q['name']} —— 不提交")
            continue
        print(f"\n# {q['startNo']} {q['name']}  id={q['instanceId']}  subs={len(q['subs'])}")
        for i, sub in enumerate(q["subs"], 1):
            if sub["prompt"]:
                print(f"   {i}) {sub['prompt']}")
            for o in sub["options"]:
                print(f"      {o['value']}. {o['label']}")
    print("\n===== 原文/字幕 =====")
    for q in p["questions"]:
        for t in q["texts"]:
            if len(t) > 30:
                print("-", t[:900])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--rm-bank", default="")
    args = ap.parse_args()

    if args.rm_bank:
        db.conn()
        db.ex("DELETE FROM answer_bank WHERE paper_hash=?", (args.rm_bank,))
        print(f"已从平台题库删除 {args.rm_bank}")
        return

    if not args.task:
        raise SystemExit("用法：python tools/show_task.py <taskId> --user <手机号>")
    show(args.task, args.user)


if __name__ == "__main__":
    main()
