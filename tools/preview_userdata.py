"""离线预览：用给定答案组装 userData，检查形状（不联网、不交卷）。

用法：python tools/preview_userdata.py <taskId>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uting import paper as P  # noqa: E402
from uting import solve as S  # noqa: E402

ANSWERS = {
    # 词汇/听辨（题面里就是原文那句话，答案是句中出现的目标词或其释义）
    "1989611653936472065": "C",      # biological
    "1989611653936472066": "A",      # 生理的
    "1989611653936472068": "A",      # social psychology
    "1989611653936472069": "B",      # 社会心理学
    "1989611653936472071": "C",      # exposure
    "1989611653936472072": "B",      # 接触
    # while-listening：一个实例 3 道小题（children 三段）
    "1989611653936472074": ["D", "C", "A"],
}


def main() -> None:
    task_id = sys.argv[1]
    raw = json.loads((P.PAPER_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "token": "tok_local", "ansVersion": 1}, task_id)

    print(P.describe(p))
    print("\n" + "=" * 70)
    multi = [q for q in p["questions"] if q.get("subs")]
    for q in multi:
        print(f"#{q['startNo']} 有 {len(q['subs'])} 道小题：")
        for i, sub in enumerate(q["subs"], 1):
            print(f"  {i}) {sub['prompt']}")
            print("     " + " | ".join(f"{o['value']}.{o['label']}" for o in sub["options"]))

    ud, missing = S.build_user_data(p, ANSWERS)
    print(f"\nuserData {len(ud)} 条，缺失 {len(missing)} 条 {missing}")
    for item in ud:
        ans = json.loads(item["answer"])
        print(f"  {item['instanceId']}  ->  {json.dumps(ans, ensure_ascii=False)}")

    full, _ = S.build_user_data(p, ANSWERS, include_non_objective=True)
    print(f"\n（4295 兜底）覆盖全部实例：{len(full)} 条")


if __name__ == "__main__":
    main()
