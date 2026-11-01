"""答题引擎：把一张卷子变成可提交的答案序列。

规则（按问卷结构与 U听说 实际题型）：
  action == "skip"  -> 学习段，不提交
  action == "oral"  -> 跟读/口语段（需要音频 + 打分服务）
  action == "probe" -> 客观题，从题库按 ansVersion 取答案

strict 模式（真实上游默认开启）：题库缺答案 / 口语段不支持时**直接拒交**，
不猜题、不空交——空交会被上游按 0 分记账，比失败更糟。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import answer_bank

LogFn = Callable[[str], Awaitable[None]]


class AnswerRefused(Exception):
    """缺答案或题型不支持：整卷不提交。"""

    def __init__(self, reason: str, instance_id: str = "", detail: str = ""):
        self.reason = reason
        self.instance_id = instance_id
        self.detail = detail
        super().__init__(detail or reason)


def _text_of(question: dict[str, Any]) -> str:
    prompts = question.get("prompts") or []
    if prompts:
        return str(prompts[0])
    return str(question.get("prompt") or "")


def build_answers(
    paper: dict[str, Any],
    ans_version: int,
    *,
    strict: bool = False,
    supports_oral: bool = True,
    supports_skip: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    paper_hash = str(paper.get("paperHash") or "")
    questions: list[dict[str, Any]] = paper.get("questions") or []
    bank = answer_bank.load_bank(paper_hash, ans_version) if paper_hash else {}

    answers: list[dict[str, Any]] = []
    stats = {"total": 0, "probe": 0, "oral": 0, "skip": 0, "from_bank": 0, "guessed": 0}

    for q in questions:
        action = str(q.get("action") or "probe")
        iid = str(q.get("instanceId") or "")
        qtype = str(q.get("type") or "")
        stats["total"] += 1

        if action == "skip":
            stats["skip"] += 1
            answers.append({"instanceId": iid, "action": "skip", "type": qtype,
                            "answer": "", "confidence": 1.0})
            continue

        if action == "oral":
            stats["oral"] += 1
            if supports_oral:
                answers.append({"instanceId": iid, "action": "oral", "type": qtype,
                                "answer": _text_of(q), "confidence": 1.0})
            else:
                # 跟读/口语要上传录音，不提交；不影响客观题判分（实测整卷仍可 100）
                stats["oral_skipped"] = stats.get("oral_skipped", 0) + 1
            continue

        stats["probe"] += 1
        hit = bank.get(iid)
        n_subs = len(q.get("subs") or [])
        if hit:
            got = hit["answer"]
            if n_subs > 1:
                vals = got if isinstance(got, list) else [x for x in str(got).replace(",", " ").split() if x]
                if len(vals) < n_subs:
                    if strict:
                        raise AnswerRefused("answer_missing", iid,
                                            f"题库里 {iid} 只有 {len(vals)} 个答案，但该实例有 {n_subs} 道小题")
                    hit = None
            if hit:
                stats["from_bank"] += 1
                answers.append({"instanceId": iid, "action": "probe", "type": qtype,
                                "answer": hit["answer"], "confidence": hit["confidence"],
                                "source": hit["source"], "sub_count": n_subs})
                continue

        if strict:
            raise AnswerRefused("answer_missing", iid,
                                f"题库缺少 {paper_hash} v{ans_version} 中题 {iid} 的答案（type={qtype or '?'}）")

        guess, conf = answer_bank.heuristic(q)
        stats["guessed"] += 1
        answers.append({"instanceId": iid, "action": "probe", "type": qtype,
                        "answer": [guess] * n_subs if n_subs > 1 else guess,
                        "confidence": conf, "source": "heuristic", "sub_count": n_subs})

    return answers, stats
