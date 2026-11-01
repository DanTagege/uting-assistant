"""调试 LLM 作答：打印模型原始返回，定位为什么答案被过滤掉。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm  # noqa: E402
from uting import paper as P  # noqa: E402


async def main(task_id: str) -> None:
    raw = json.loads((P.PAPER_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    p = P.normalize({"taskId": task_id, "paperJson": raw, "ansVersion": 1}, task_id)
    material = P.material(p)
    items = llm._brief(p["questions"])
    print(f"客观题 {len(items)} 道，材料 {len(material)} 字")
    user = ("卷面原文/字幕：\n" + material[:24000] +
            "\n\n需要作答的题目：\n" + json.dumps(items, ensure_ascii=False, indent=1) +
            "\n\n只输出一个 json 对象：{instanceId: 答案}，不要任何解释或代码块。")
    text = await llm._chat([{"role": "system", "content": llm.SYSTEM}, {"role": "user", "content": user}])
    print("\n===== 模型原始返回（前 1500 字） =====")
    print(text[:1500])
    print("\n===== solve() 过滤后 =====")
    print(json.dumps(await llm.solve(p["questions"], material), ensure_ascii=False, indent=1))
    print("\n===== 期望的 instanceId =====")
    print([q["instanceId"] for q in p["questions"] if q["action"] == "probe"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
