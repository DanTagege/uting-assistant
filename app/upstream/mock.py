"""本地模拟上游：按视听说作业结构（学习段 / 跟读段 / 客观题）生成样例与判分。

用途：离线验证整条链路（登录 -> 总览 -> 取卷 -> 逐题作答 -> 判分 -> 成绩）。
真上游对接见 unipus_ai.py。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import secrets
import time
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import settings
from .base import LogFn, SubmitResult, Upstream, UpstreamCtx, UpstreamError, UpstreamSession

TZ = timezone(timedelta(hours=8))
TOPICS = ["校园生活", "科技与环境", "职场沟通", "文化交流", "健康生活"]
GENRES = ["对话", "新闻", "讲座", "故事"]
LEVELS = ["A2", "B1", "B2"]
PARTS = ["Part 1 Listening", "Part 2 Viewing", "Part 3 Speaking"]
OPTIONS = ["A", "B", "C", "D"]
PROMPTS = [
    "What is the main topic of the conversation?",
    "Why does the student go to the library?",
    "What does the professor suggest?",
    "How does the woman feel about the plan?",
    "Which statement is true according to the passage?",
    "What can we infer from the speaker's tone?",
]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def week_start(now: float | None = None) -> float:
    now = now or time.time()
    dt = datetime.fromtimestamp(now, TZ)
    monday = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def paper_hash_of(task_id: str) -> str:
    return hashlib.sha1(task_id.encode("utf-8")).hexdigest()[:16]


def task_id_for(account: str, idx: int, kind: str) -> str:
    """稳定的任务 id（不要用内置 hash()，它受 PYTHONHASHSEED 影响）。"""
    digest = hashlib.sha1(f"{account}:{idx}:{kind}".encode("utf-8")).hexdigest()[:8].upper()
    return ("T" if kind == "teacher" else "A") + digest


def answer_key(paper_hash: str) -> dict[str, str]:
    return {f"{paper_hash}-q{i}": random.Random(paper_hash + str(i)).choice(OPTIONS) for i in range(1, 7)}


class MockUpstream(Upstream):
    name = "mock"

    # ------------------------------------------------------------------ state
    def _state(self, account: str) -> dict:
        row = db.one("SELECT payload FROM mock_state WHERE account=?", (account,))
        state = None
        if row:
            try:
                state = json.loads(row["payload"])
            except json.JSONDecodeError:
                state = None
        if not state or state.get("week") != week_start():
            state = {"week": week_start(), "keys": {}, "submitted": {}}
            self._save(account, state)
        return state

    def _save(self, account: str, state: dict) -> None:
        db.ex("INSERT INTO mock_state(account,payload) VALUES(?,?) "
              "ON CONFLICT(account) DO UPDATE SET payload=excluded.payload",
              (account, json.dumps(state, ensure_ascii=False)))

    # ------------------------------------------------------------------ 生成
    def _task(self, account: str, idx: int, kind: str, state: dict) -> dict:
        rnd = random.Random(f"{account}:{idx}:{kind}")
        task_id = task_id_for(account, idx, kind)
        ph = paper_hash_of(task_id)
        state["keys"].setdefault(ph, answer_key(ph))
        now = time.time()
        end = now + 86400 * rnd.randint(1, 12) if kind == "teacher" else 0.0
        if kind == "teacher" and idx == 2:
            end = now - 86400 * 2                                     # 一条"已截止"，用于验证前端态
        sub = state["submitted"].get(task_id)
        return {
            "taskId": task_id,
            "taskStudentId": f"S{idx:04d}",
            "name": f"{rnd.choice(TOPICS)} · {rnd.choice(GENRES)}",
            "kind": kind,
            "audioVisual": 1 if rnd.random() < 0.35 else 0,
            "level": rnd.choice(LEVELS),
            "genre": rnd.choice(GENRES),
            "topicCategory": rnd.choice(TOPICS),
            "paperHash": ph,
            "ansVersion": int((sub or {}).get("ansVersion", 0)),
            "completed": bool(sub and sub.get("score") is not None),
            "locked": False,
            "startTime": _iso(now - 86400 * 3),
            "endTime": _iso(end) if end else "",
            "score": (sub or {}).get("score"),
            "questionCount": 9,
            "gradedCount": 9 if (sub and sub.get("score") is not None) else 0,
        }

    def _build_overview(self, account: str) -> dict:
        state = self._state(account)
        quota = settings.weekly_quota

        adaptive = [self._task(account, i, "adaptive", state) for i in range(1, quota + 1)]
        done = sum(1 for t in adaptive if t["completed"])
        # 顺序解锁：第一项未完成的可答，其余锁定
        pending_list = [t for t in adaptive if not t["completed"]]
        for i, t in enumerate(pending_list):
            t["locked"] = i > 0
        current = pending_list[0] if pending_list else None

        assigned = [self._task(account, i, "teacher", state) for i in range(1, 4)]

        history = []
        for tid, sub in state["submitted"].items():
            if tid.startswith("_"):
                continue
            history.append({
                "taskId": tid, "taskStudentId": "", "name": sub.get("name", ""), "kind": sub.get("kind", "adaptive"),
                "audioVisual": sub.get("audioVisual", 0), "level": "", "genre": "", "topicCategory": "",
                "paperHash": sub.get("paperHash", ""), "ansVersion": sub.get("ansVersion", 0),
                "completed": True, "locked": False, "startTime": sub.get("at", ""), "endTime": "",
                "score": sub.get("score"), "questionCount": sub.get("total", 9), "gradedCount": sub.get("graded", 9),
            })
        history.sort(key=lambda x: x["startTime"], reverse=True)

        self._save(account, state)
        return {
            "adaptive": {"week": {"done": done, "quota": quota, "pending": max(0, quota - done)},
                         "current": current, "tasks": adaptive},
            "assigned": {"tasks": assigned},
            "history": {"tasks": history[:20]},
        }

    def _build_paper(self, account: str, task_id: str) -> dict:
        state = self._state(account)
        ph = paper_hash_of(task_id)
        state["keys"].setdefault(ph, answer_key(ph))
        self._save(account, state)

        questions = [
            {"instanceId": f"{ph}-s1", "partName": PARTS[0], "startNo": 0, "span": 0,
             "name": "导读：本节学习目标", "action": "skip",
             "prompt": "本节练习校园场景听力理解，请先浏览学习目标。", "prompts": [], "options": []},
            {"instanceId": f"{ph}-s2", "partName": PARTS[1], "startNo": 0, "span": 0,
             "name": "观看视频并跟读", "action": "oral",
             "prompt": "Please read the sentence after the speaker.",
             "prompts": ["I think the campus library is a great place to study."], "options": []},
        ]
        for i, text in enumerate(PROMPTS, start=1):
            questions.append({
                "instanceId": f"{ph}-q{i}",
                "partName": PARTS[0] if i <= 3 else PARTS[2],
                "startNo": i, "span": 1, "name": "", "action": "probe",
                "prompt": text, "prompts": [text],
                "options": [{"value": v, "label": f"选项 {v}"} for v in OPTIONS],
            })
        return {
            "paperHash": ph,
            "paperTitle": f"视听说练习 {task_id[-4:]}",
            "audioVisual": 0,
            "questionCount": len(questions),
            "gradedCount": 0,
            "questions": questions,
        }

    # ------------------------------------------------------------------ 协议实现
    async def login(self, username: str, password: str) -> UpstreamSession:
        await asyncio.sleep(0.12)
        if len(password) < 4:
            raise UpstreamError("auth_failed", "U校园账号或密码错误")
        rnd = random.Random(username)
        now = time.time()
        return UpstreamSession(
            jwt="mock-jwt-" + secrets.token_hex(12),
            rt="mock-rt-" + secrets.token_hex(12),
            jwt_exp=now + 7200,
            rt_exp=now + settings.rt_ttl_seconds,
            profile={"account": username, "name": f"同学{rnd.randint(1000, 9999)}",
                     "schoolName": rnd.choice(["示例大学", "上海立信会计金融学院", "某某学院"])},
        )

    async def refresh(self, ctx: UpstreamCtx) -> UpstreamSession:
        await asyncio.sleep(0.03)
        if not ctx.rt.startswith("mock-rt-"):
            raise UpstreamError("rt_expired", "刷新令牌已失效，需要重新登录", fatal=True)
        now = time.time()
        return UpstreamSession(jwt="mock-jwt-" + secrets.token_hex(12), rt=ctx.rt,
                               jwt_exp=now + 7200, rt_exp=now + settings.rt_ttl_seconds)

    async def overview(self, ctx: UpstreamCtx) -> dict:
        await asyncio.sleep(0.03)
        return self._build_overview(ctx.account)

    async def save_snapshot(self, ctx: UpstreamCtx, task_id: str, user_data: list[dict], *,
                            duration: int = 0) -> dict:
        """本地 mock 只记录，不发请求。"""
        await asyncio.sleep(0.01)
        return {"ok": True, "saved": len(user_data)}

    async def paper(self, ctx: UpstreamCtx, task_id: str) -> dict:
        await asyncio.sleep(0.03)
        return self._build_paper(ctx.account, task_id)

    async def submit(self, ctx: UpstreamCtx, task_id: str, answers: list[dict], *,
                     delay_seconds: float = 0.0, log: LogFn | None = None) -> SubmitResult:
        state = self._state(ctx.account)
        ph = paper_hash_of(task_id)
        key = state["keys"].setdefault(ph, answer_key(ph))
        correct = graded = 0
        for item in answers:
            if log:
                await log(f"INFO answer {item['instanceId']} action={item['action']} "
                          f"-> {item.get('answer')!r} (conf={item.get('confidence', 0):.2f})")
            if delay_seconds:
                await asyncio.sleep(min(delay_seconds, 60))
            if item["action"] == "probe":
                graded += 1
                if str(item.get("answer", "")).upper() == key.get(item["instanceId"]):
                    correct += 1
        total = len(answers) or 1
        score = round(100 * (correct + (total - graded)) / total, 1)
        ans_version = int(ctx.meta.get("ansVersion", 0))
        state["submitted"][task_id] = {
            "score": score, "graded": graded, "total": total, "paperHash": ph,
            "ansVersion": ans_version, "at": _iso(time.time()), "name": f"视听说练习 {task_id[-4:]}",
            "answers": {a["instanceId"]: a.get("answer") for a in answers},
        }
        self._save(ctx.account, state)
        return SubmitResult(score=score, graded=graded, total=total, raw={"paperHash": ph})
