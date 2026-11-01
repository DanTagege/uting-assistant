"""上游连通性探针：不发任何带凭证的请求，先确认端点活着、鉴权口径对。

用法：
    .venv\\Scripts\\python.exe tools\\probe_upstream.py
    .venv\\Scripts\\python.exe tools\\probe_upstream.py --jwt eyJ...        # 有 JWT 时顺便读周进度
    .venv\\Scripts\\python.exe tools\\probe_upstream.py --jwt ... --task 12345   # 顺便试 loadPaper

判读规则：
    401 + {"message":"Missing JWT token in request"}  -> 端点存在，口径正确
    404                                               -> 路径写错/已改版
    200 + code 字段                                    -> 业务层级返回（要么成功要么业务报错）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.upstream.unipus import (  # noqa: E402
    ADAPTIVE_ORIGIN, P_LOAD_PAPER, P_STATUS_FOR_APP, P_USER_STATUS,
    SSO_LOGIN_PATH, SSO_ORIGIN, UCLOUD_ORIGIN, decode_jwt_payload,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def show(label: str, resp: httpx.Response) -> None:
    body = resp.text.strip()
    if len(body) > 300:
        body = body[:300] + " …"
    print(f"  {label:<52} {resp.status_code}  {body}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jwt", default="", help="可选：裸 JWT（不带 Bearer）")
    ap.add_argument("--task", default="", help="可选：taskId，用于试 loadPaper")
    args = ap.parse_args()

    jwt = args.jwt.strip()
    headers = {"user-agent": UA, "accept": "application/json, text/plain, */*"}
    if jwt:
        headers["authorization"] = jwt
        headers["u-app-id"] = "116"

    print("== 无凭证探活（应全部 401，而不是 404） ==")
    with httpx.Client(timeout=20, follow_redirects=False) as c:
        show("GET  ucloud/api/uls/user/getUserStatusForApp",
             c.get(f"{UCLOUD_ORIGIN}{P_STATUS_FOR_APP}", params={"flowType": "listen"},
                   headers={k: v for k, v in headers.items() if k != "authorization"}))
        show("GET  adaptive/api/uls/user/getUserStatus",
             c.get(f"{ADAPTIVE_ORIGIN}{P_USER_STATUS}", params={"flowType": "listen"}, headers=headers))
        show("POST adaptive/api/uls/user/loadPaper",
             c.post(f"{ADAPTIVE_ORIGIN}{P_LOAD_PAPER}", json={"taskId": "1", "ansVersion": 1},
                    headers={**headers, "content-type": "application/json"}))
        show("POST sso/sso/0.1/sso/cip/login (空凭证)",
             c.post(f"{SSO_ORIGIN}{SSO_LOGIN_PATH}",
                    json={"service": "https://ucloud.unipus.cn/", "username": "", "password": ""},
                    headers={**headers, "content-type": "application/json"}))

    if not jwt:
        print("\n提示：传入 --jwt 可继续验证周进度与取卷（不会提交任何答案）")
        return

    payload = decode_jwt_payload(jwt)
    exp = payload.get("exp")
    print(f"\n== JWT 载荷（未验签） ==\n  exp={exp}  字段={sorted(payload)[:12]}")
    if isinstance(exp, (int, float)):
        import time
        left = exp - int(time.time())
        print(f"  剩余有效期：{left/3600:.1f} 小时" + ("（已过期）" if left <= 0 else ""))

    print("\n== 带 JWT 读周进度 ==")
    with httpx.Client(timeout=20, follow_redirects=False) as c:
        for flow in ("listen", "speak"):
            r = c.get(f"{UCLOUD_ORIGIN}{P_STATUS_FOR_APP}", params={"flowType": flow}, headers=headers)
            print(f"  [{flow}] {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                print("   ", r.text[:200])
                continue
            value = data.get("value") or data.get("data") or {}
            print("    type={type} weekDone={d} weekFrequency={f} level={lv}".format(
                type=value.get("type"), d=value.get("weekDoneTaskCount"),
                f=value.get("weekFrequency"), lv=value.get("currentLevel") or value.get("level")))
        r = c.get(f"{ADAPTIVE_ORIGIN}{P_USER_STATUS}", params={"flowType": "listen"}, headers=headers)
        print(f"  [getUserStatus listen] {r.status_code} {r.text[:200]}")

        if args.task:
            print("\n== 试 loadPaper（只取卷，不提交） ==")
            r = c.post(f"{ADAPTIVE_ORIGIN}{P_LOAD_PAPER}", json={"taskId": args.task, "ansVersion": 1},
                       headers={**headers, "content-type": "application/json"})
            print(f"  {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                print("  ", r.text[:300])
                return
            value = data.get("value") or data.get("data") or {}
            print(f"  code={data.get('code')} token={'有' if value.get('token') else '无'} "
                  f"audioVisual={value.get('audioVisual')}")
            pj = value.get("paperJson")
            text = json.dumps(pj, ensure_ascii=False) if not isinstance(pj, str) else pj
            print(f"  paperJson 长度={len(text)}  片段={text[:400]}")


if __name__ == "__main__":
    main()
