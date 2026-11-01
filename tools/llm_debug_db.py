"""调试：把平台已缓存的卷子喂给模型，打印原始返回（定位"没有返回 JSON"）。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, llm  # noqa: E402


async def main(task_id: str) -> None:
    db.conn()
    row = db.one("SELECT payload FROM papers WHERE task_id=? ORDER BY fetched_at DESC LIMIT 1", (task_id,))
    if not row:
        raise SystemExit("papers 表里没有这篇，先调一次取卷接口")
    paper = json.loads(row["payload"])
    questions = paper.get("questions") or []
    probe = [q for q in questions if q.get("action") == "probe"]
    material = "\n".join(t for q in questions for t in (q.get("texts") or []) if len(t) > 25)

    print(f"题目 {len(questions)} 项，客观 {len(probe)}，材料 {len(material)} 字")
    items = llm._brief(questions)
    user = ("卷面原文/字幕：\n" + (material or "（无原文）")[:24000] +
            "\n\n需要作答的题目：\n" + json.dumps(items, ensure_ascii=False, indent=1) +
            "\n\n只输出一个 json 对象：{instanceId: 答案}，不要任何解释或代码块。")
    print(f"prompt 长度：{len(user)} 字符，题目条目：{len(items)}")
    text = await llm._chat([{"role": "system", "content": llm.SYSTEM}, {"role": "user", "content": user}],
                           max_tokens=3000)
    print("\n===== 原始返回 =====")
    print(text[:2000] if text else "（空响应）")
    print("\n===== 解析 =====")
    try:
        print(json.dumps(await llm.solve(questions, material), ensure_ascii=False)[:600])
    except Exception as exc:  # noqa: BLE001
        print("solve 失败：", type(exc).__name__, exc)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
