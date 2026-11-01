"""离线自检：验证"卷面 → 题目 → 提交体"的形状（用真实卷面，没有就用内置样例）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uting import paper as P  # noqa: E402
from uting import solve as S  # noqa: E402

# 内置样例：结构与真实卷面一致（q_qinstid / nm / is_objective / data.children+options）
SAMPLE = {
    "chr": [
        {"nm": "lead-in", "chr": [
            {"q_qinstid": 1000000000000000001, "q_idx": 1, "nm": "卷子导读",
             "q_template_name": "outline", "is_objective": False,
             "data": {"contents": [{"text": "<p>Lead in text</p>"}]}},
        ]},
        {"nm": "pre-listening", "chr": [
            {"q_qinstid": 1000000000000000002, "q_idx": 1, "nm": "听力_单选_客观题",
             "q_template_name": "single-choice_objective", "is_objective": True,
             "data": {"contents": [{"text": "<p>She studies <strong>biology</strong>.</p>"}],
                      "children": [{"options": [{"name": "A", "text": "<p>biology</p>", "value": "A"},
                                                {"name": "B", "text": "<p>geology</p>", "value": "B"}]}]}},
            {"q_qinstid": 1000000000000000003, "q_idx": 2, "nm": "单词跟读",
             "q_template_name": "vocabulary", "is_objective": False,
             "data": {"contents": [{"name": "<p>biology</p>"}]}},
        ]},
        {"nm": "while", "chr": [
            {"q_qinstid": 1000000000000000004, "q_idx": 3, "nm": "听力_单选_客观题",
             "q_template_name": "single-choice_objective", "is_objective": True,
             "data": {"contents": [{"text": "<p>Passage about music.</p>"}],
                      "children": [
                          {"contents": [{"text": "Q1?"}],
                           "options": [{"name": "A", "text": "a", "value": "A"}, {"name": "B", "text": "b", "value": "B"}]},
                          {"contents": [{"text": "Q2?"}],
                           "options": [{"name": "A", "text": "a", "value": "A"}, {"name": "B", "text": "b", "value": "B"}]},
                      ]}},
        ]},
    ]
}

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS " if cond else "  FAIL ") + name + (f"  <- {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def run_case(label: str, raw: dict) -> None:
    print(f"\n=== {label} ===")
    p = P.normalize({"taskId": "t1", "paperJson": raw, "token": "tok", "ansVersion": 1}, "t1")
    qs = p["questions"]
    probe = [q for q in qs if q["action"] == "probe"]
    check("解析出题目", len(qs) > 0, str(len(qs)))
    check("实例 id 是 19 位字符串", all(q["instanceId"].isdigit() and len(q["instanceId"]) >= 15 for q in qs),
          str([q["instanceId"] for q in qs[:3]]))
    check("客观题有选项或小题", all(q["options"] or q["subs"] for q in probe), "存在无选项的客观题")
    multi = [q for q in probe if len(q["subs"]) > 1]
    if multi:
        q = multi[0]
        check(f"多小题实例识别（{len(q['subs'])} 小题）", True)
        payload = S.payload_for(q, ["A"] * len(q["subs"]))
        check("多小题 -> children 段数对齐", len(payload["children"]) == len(q["subs"]),
              json.dumps(payload))

    answers = {}
    for q in probe:
        n = len(q["subs"]) or 1
        answers[q["instanceId"]] = [q["subs"][0]["options"][0]["value"]] * n if n > 1 \
            else (q["options"][0]["value"] if q["options"] else "A")
    ud, missing = S.build_user_data(p, answers)
    check("userData 覆盖所有客观题", len(ud) == len(probe) and not missing, f"{len(ud)}/{len(probe)} {missing}")
    check("answer 是 JSON 字符串", all(isinstance(i["answer"], str) for i in ud))
    if ud:
        first = json.loads(ud[0]["answer"])
        check("answer 含 children/value", "children" in first and "value" in first, json.dumps(first))
    full, _ = S.build_user_data(p, answers, include_non_objective=True)
    check("4295 兜底覆盖全部实例", len(full) >= len(qs), f"{len(full)} vs {len(qs)}")


def main() -> None:
    run_case("内置样例", SAMPLE)

    real = sorted(P.PAPER_DIR.glob("*.json")) if P.PAPER_DIR.exists() else []
    real = [r for r in real if not r.name.endswith(".answers.json")]
    if real:
        raw = json.loads(real[0].read_text(encoding="utf-8"))
        run_case(f"真实卷面 {real[0].stem}", raw)
    else:
        print("\n（没有真实卷面，跳过；跑一次 probe 后会生成）")

    print(f"\n结果：{len(fails)} 项失败" if fails else "\n结果：全部通过")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
