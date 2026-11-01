"""HAR 提取器：把一次抓包变成 data/upstream.json 的端点草稿。

用法：
    python tools/har_extract.py capture.har --host unipus
    python tools/har_extract.py capture.har --host unipus --out data/upstream.json

它会：
  1) 列出所有命中的 host 与 (method, path)
  2) 识别登录 / 刷新 / 总览 / 取卷 / 作答 / 成绩 六类请求，生成带 {{占位}} 的端点定义
  3) 自动脱敏：token、cookie、手机号、学号一律替换为 REPLACE_ME
  4) 输出 data/upstream.json（已存在则备份为 .bak）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PHONE = re.compile(r"1[3-9]\d{9}")
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-\.]{24,}")

# 关键词 -> 逻辑端点名（命中即归类）
ROUTES: list[tuple[str, tuple[str, ...]]] = [
    ("login", ("login", "signin", "auth/token", "passport", "sso")),
    ("refresh", ("refresh", "renew", "token/refresh")),
    ("overview", ("overview", "task/list", "course", "training", "home", "index")),
    ("paper", ("paper", "detail", "question", "exam", "content")),
    ("answer", ("answer", "submit", "save", "record", "progress")),
    ("result", ("result", "score", "report", "grade")),
]


def _text(content: dict[str, Any] | None) -> str:
    if not content:
        return ""
    return content.get("text", "") or ""


def _redact(value: str) -> str:
    value = PHONE.sub("REPLACE_PHONE", value)
    if LONG_TOKEN.fullmatch(value) and len(value) >= 24:
        return "REPLACE_ME"
    return value


def _json_or_none(text: str) -> Any:
    text = (text or "").strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def extract(har_path: Path, host_filter: str) -> dict[str, Any]:
    data = json.loads(har_path.read_text(encoding="utf-8", errors="replace"))
    entries = (data.get("log") or {}).get("entries") or []
    hits: list[dict[str, Any]] = []
    hosts: dict[str, int] = {}

    for e in entries:
        req = e.get("request") or {}
        url = req.get("url", "")
        host = urlparse(url).netloc
        if host_filter and host_filter.lower() not in host.lower():
            continue
        hosts[host] = hosts.get(host, 0) + 1
        hits.append({
            "method": req.get("method", "GET").upper(),
            "host": host,
            "path": urlparse(url).path or "/",
            "query": urlparse(url).query,
            "req_headers": {h.get("name", ""): _redact(str(h.get("value", ""))) for h in req.get("headers") or []},
            "req_body": _json_or_none(_text(req.get("postData"))),
            "status": (e.get("response") or {}).get("status"),
            "resp": _json_or_none(_text(e.get("response") or {}).get("content")),
        })

    print("== 命中的 host ==")
    for h, n in sorted(hosts.items(), key=lambda kv: -kv[1]):
        print(f"  {h}  ({n} 条)")

    print("\n== 候选端点 ==")
    for h in sorted({x['host'] for x in hits}):
        print(f"[{h}]")
        for x in hits:
            if x["host"] == h:
                print(f"  {x['method']:6} {x['path']}  -> {x['status']}")

    def classify(path: str) -> str | None:
        p = path.lower()
        for name, keys in ROUTES:
            if any(k in p for k in keys):
                return name
        return None

    base_host = max(hosts, key=hosts.get) if hosts else ""
    endpoints: dict[str, Any] = {}
    for x in hits:
        name = classify(x["path"])
        if not name or name in endpoints:
            continue
        spec: dict[str, Any] = {"method": x["method"], "path": x["path"]}
        if x["req_body"]:
            spec["json"] = _redact_tree(x["req_body"])
        endpoints[name] = spec

    # 登录/取卷/作答端点必然需要 task_id / 手机号等变量
    for name in ("paper", "answer", "result"):
        if name in endpoints:
            endpoints[name]["path"] = re.sub(r"/\d{3,}", "/{{task_id}}", endpoints[name]["path"])

    cfg = {
        "base_url": f"https://{base_host}" if base_host else "REPLACE_ME",
        "headers": {
            "Accept": "application/json",
            "User-Agent": "REPLACE_ME",
        },
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "prefix": "Bearer ",
            "token_path": "data.accessToken",
            "refresh_path": "data.refreshToken",
        },
        "profile_path": "data.profile",
        "token_ttl": 7200,
        "retry": {"times": 2, "backoff": 0.8},
        "mapping": {
            "adaptive": "data.adaptive",
            "assigned": "data.assigned",
            "history": "data.history",
            "paper": "data",
            "score": "data.score",
            "graded": "data.gradedCount",
            "total": "data.questionCount",
        },
        "endpoints": endpoints,
        "_notes": [
            "1. base_url / headers.User-Agent 从抓包里的真实值填写",
            "2. 若上游用 query 或 form 而不是 JSON，请把 json 改成 params/body",
            "3. token_path / mapping 需要对照真实响应结构微调",
            "4. 路径里的 {{task_id}} / {{instanceId}} / {{answer}} 由程序自动填充",
            "5. 配好后设置 UC_UPSTREAM=unipus_ai 重启即可",
        ],
    }

    print("\n== 生成的端点草稿 ==")
    print(json.dumps(endpoints, ensure_ascii=False, indent=2))
    return cfg


def _redact_tree(node: Any) -> Any:
    if isinstance(node, str):
        return _redact(node)
    if isinstance(node, list):
        return [_redact_tree(x) for x in node]
    if isinstance(node, dict):
        return {k: ("REPLACE_ME" if k.lower() in ("password", "pwd", "token", "authorization") else _redact_tree(v))
                for k, v in node.items()}
    return node


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("har")
    ap.add_argument("--host", default="unipus", help="只保留 host 含该子串的请求（空字符串=全部）")
    ap.add_argument("--out", default="data/upstream.json")
    args = ap.parse_args()

    out = Path(args.out)
    cfg = extract(Path(args.har), args.host)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        shutil.copy2(out, out.with_suffix(out.suffix + ".bak"))
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}（原文件已备份为 {out.name}.bak）")


if __name__ == "__main__":
    main()
