"""离线自检：不联网，验证解析器/引擎/加密/拒交逻辑。

用法：.venv\\Scripts\\python.exe tools\\selftest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import answer_bank, db  # noqa: E402
from app.engine import AnswerRefused, build_answers  # noqa: E402
from app.upstream.paper_parse import normalize  # noqa: E402
from app.upstream.unipus import UnipusUpstream, encrypt_sso_field_fallback  # noqa: E402

FAIL = []
OK = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


SAMPLE_PAPER = {
    "taskId": "1900000000000000001",
    "paperName": "Unit 3 校园生活",
    "audioVisual": 1,
    "paperJson": {
        "chr": "unit3",
        "id": "p1",
        "tp": "chapter",
        "children": [
            {"tp": "part", "partName": "Part 1 Listening", "children": [
                {"tp": "singlechoice", "q_qinstid": 1984905701219868673, "stem": "What is the topic?",
                 "options": [{"key": "A", "text": "Library"}, {"key": "B", "text": "Gym"}]},
                {"tp": "audio-learn", "q_qinstid": 1984905701219868674, "text": "听音频学习"},
            ]},
            {"tp": "part", "partName": "Part 2 Speaking", "children": [
                {"tp": "sentence-scoop-record", "q_qinstid": "1984905701219868675",
                 "text": "I like the campus library."},
                {"tp": "fillblank-scoop", "q_qinstid": "1984905701219868676",
                 "stem": "The library opens at ____.", "options": []},
            ]},
        ],
    },
}


def main() -> None:
    print("== 1. paperJson 归一化 ==")
    paper = normalize(SAMPLE_PAPER, "1900000000000000001")
    qs = paper["questions"]
    check("识别出 4 道题", len(qs) == 4, f"actual={len(qs)}")
    check("snowflake id 保持字符串", all(isinstance(q["instanceId"], str) for q in qs))
    check("含 1984905701219868673", any(q["instanceId"] == "1984905701219868673" for q in qs))
    by_id = {q["instanceId"]: q for q in qs}
    check("单选判为 probe", by_id["1984905701219868673"]["action"] == "probe")
    check("学习段判为 skip", by_id["1984905701219868674"]["action"] == "skip")
    check("跟读判为 oral", by_id["1984905701219868675"]["action"] == "oral")
    check("填空判为 probe", by_id["1984905701219868676"]["action"] == "probe")
    check("选项解析成 value/label", by_id["1984905701219868673"]["options"][0]["value"] == "A")
    check("partName 继承", by_id["1984905701219868675"]["partName"] == "Part 2 Speaking")

    print("\n== 2. 提交体构造 ==")
    up = UnipusUpstream.__new__(UnipusUpstream)
    choice = UnipusUpstream._answer_json({"type": "singlechoice", "answer": "A", "action": "probe"})
    check("单选 -> value:[A]", choice["value"] == ["A"])
    blank = UnipusUpstream._answer_json({"type": "fillblank-scoop", "answer": "9", "action": "probe"})
    check("填空 -> children[0].value:[9]", blank["children"][0]["value"] == ["9"])
    multi = UnipusUpstream._answer_json({"type": "multi-select", "answer": "A,C", "action": "probe"})
    check("多选 -> value:[A,C]", multi["value"] == ["A", "C"])

    print("\n== 3. 严格模式拒交 ==")
    db.conn()
    paper_hash = "selftesthash0001"
    paper2 = {"paperHash": paper_hash, "questions": qs}
    try:
        build_answers(paper2, 0, strict=True, supports_oral=False)
        check("缺答案应拒交", False, "没有抛 AnswerRefused")
    except AnswerRefused as exc:
        check("缺答案 -> AnswerRefused", True)
        print(f"        reason={exc.reason} instance={exc.instance_id}")
    # 灌入答案后应可组包（口语仍拒）
    answer_bank.save_bank(paper_hash, 0, {"1984905701219868673": "A", "1984905701219868676": "9"}, "selftest")
    try:
        build_answers(paper2, 0, strict=True, supports_oral=False)
        check("口语不支持也应拒交", False)
    except AnswerRefused as exc:
        check("口语 -> AnswerRefused(oral_unsupported)", exc.reason == "oral_unsupported", exc.reason)
    ans, stats = build_answers(paper2, 0, strict=True, supports_oral=True)
    check("口语放开后组包成功", len(ans) == 4, f"{stats}")
    check("题库命中 2 题", stats["from_bank"] == 2, str(stats))

    print("\n== 4. SSO AES ==")
    try:
        from app.upstream.unipus import encrypt_sso_field
        a = encrypt_sso_field("13800000000")
        b = encrypt_sso_field_fallback("13800000000")
        check("cryptography 与纯 Python 结果一致", a == b, f"{a} vs {b}")
        check("密文长度 32 hex（16 字节）", len(b) == 32, b)
    except ImportError:
        b = encrypt_sso_field_fallback("13800000000")
        check("纯 Python AES 产出 32 hex", len(b) == 32, b)

    print(f"\n结果：{len(OK)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
