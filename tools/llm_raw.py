"""裸调 DeepSeek，把完整响应（含 finish_reason / usage）打出来。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import llm  # noqa: E402


async def main() -> None:
    cfg = llm.load_config()
    print("model =", cfg["model"], " base =", cfg["base_url"], " key =", llm.mask(cfg["api_key"]))
    for jm in (True, False):
        payload = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": "只输出 json"},
                {"role": "user", "content": '回答一个 json：{"ok":true}'},
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        if jm:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{cfg['base_url']}/chat/completions",
                             headers={"Authorization": f"Bearer {cfg['api_key']}",
                                      "Content-Type": "application/json"}, json=payload)
        print(f"\n--- json_mode={jm}  HTTP {r.status_code} ---")
        try:
            data = r.json()
        except ValueError:
            print(r.text[:600])
            continue
        ch = (data.get("choices") or [{}])[0]
        print("finish_reason =", ch.get("finish_reason"))
        print("content =", repr((ch.get("message") or {}).get("content"))[:400])
        print("usage =", data.get("usage"))
        if data.get("error"):
            print("error =", data["error"])


if __name__ == "__main__":
    asyncio.run(main())
