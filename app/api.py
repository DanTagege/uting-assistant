"""HTTP 接口层：路径、方法、入参、错误码（见 docs/上游接口说明.md）。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from . import answer_bank, db, jobs, scheduler, security
from .config import settings
from .models import CompleteRequest, LoginRequest, SchedulerUpdate
from .security import ApiError, Ctx
from .upstream import UpstreamError, get_upstream

router = APIRouter(prefix="/api")
BOOT_TS = time.time()


# --------------------------------------------------------------------------- 依赖
def _ctx(request: Request) -> Ctx:
    ctx = security.resolve_ctx(request)
    assert ctx is not None
    return ctx


def _admin(ctx: Ctx = Depends(_ctx)) -> Ctx:
    return security.require_admin(ctx)


def _dev(request: Request):
    return security.device_from_request(request)


# --------------------------------------------------------------------------- system
@router.get("/health")
def health() -> dict[str, str]:
    db.conn().execute("SELECT 1")
    return {"status": "ok", "redis": "ok"}          # 健康检查字段（本地用 SQLite 代替 Redis）


@router.get("/uptime")
def uptime() -> dict[str, float]:
    return {"uptime_days": round((time.time() - BOOT_TS) / 86400, 1)}


@router.get("/stats")
def stats() -> dict[str, int]:
    hosted = int(db.one("SELECT COUNT(*) AS c FROM hosting WHERE active=1")["c"])
    done = int(db.one("SELECT COUNT(*) AS c FROM jobs WHERE status='succeeded'")["c"])
    return {"hosted_accounts": hosted, "completed_jobs": done}


@router.get("/settings")
def get_settings(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import runtime

    return {"realSubmit": runtime.get_real_submit(), "upstream": settings.upstream,
            "durationMinutes": runtime.get_duration_minutes(),
            "jitterPercent": runtime.get_jitter_percent(),
            "note": "realSubmit=true 时，点「自动答题」会真的交卷；durationMinutes>0 时按该时长模拟作答"}


@router.put("/settings")
def put_settings(payload: dict[str, Any], ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import runtime

    if "realSubmit" in payload:
        runtime.set_real_submit(bool(payload["realSubmit"]))
    if "durationMinutes" in payload:
        runtime.set_duration_minutes(float(payload["durationMinutes"] or 0))
    if "jitterPercent" in payload:
        runtime.set_jitter_percent(int(payload["jitterPercent"] or 0))
    return {"ok": True, "realSubmit": runtime.get_real_submit(),
            "durationMinutes": runtime.get_duration_minutes(),
            "jitterPercent": runtime.get_jitter_percent()}


@router.get("/meta")
def meta() -> dict[str, Any]:
    """告诉前端：现在跑的是假数据还是真实 U校园、以及是否真交卷。"""
    from . import runtime

    upstream = settings.upstream
    real_submit = runtime.get_real_submit()
    return {
        "upstream": upstream,
        "real": upstream == "unipus",
        "real_submit": real_submit,
        "u_app_id": settings.u_app_id,
        "note": ("当前为 mock 演示数据：卷面与成绩都是本地生成的，手机上不会发生任何变化。"
                 if upstream != "unipus" else
                 ("已连真实 U校园；**未开启真实提交**，点答题只预演，不会交卷。"
                  if not real_submit else
                  "已连真实 U校园，真实提交已开启：点「自动答题」会直接交卷。")),
    }


# --------------------------------------------------------------------------- auth
@router.get("/captcha/register")
def captcha_register() -> dict[str, Any]:
    """本地默认关闭滑块（UC_CAPTCHA=off）。

    需要演示完整链路时设 UC_CAPTCHA=static：返回静态题目，前端交 UC_CAPTCHA_ANSWER 即通过。
    """
    if settings.captcha_mode == "off":
        return {"gt": "local", "challenge": "off", "new_captcha": True}
    return {"gt": "local", "challenge": "static", "new_captcha": True,
            "question": "请输入验证码（见 .env 的 UC_CAPTCHA_ANSWER）"}


@router.post("/login")
async def login(request: Request, response: Response, payload: LoginRequest) -> dict[str, Any]:
    dev = _dev(request)
    if settings.captcha_mode != "off":
        if payload.validate_.strip() != settings.captcha_answer:
            raise ApiError(422, "本次验证已失效，请重新完成滑块")

    upstream = get_upstream()
    if payload.jwt and not payload.password:
        # 令牌登录：跳过上游密码登录（滑块场景用这条）
        from .upstream.unipus import decode_jwt_payload

        info = decode_jwt_payload(payload.jwt) if payload.jwt.startswith("eyJ") else {}
        exp = float(info.get("exp") or 0)
        import time as _t

        if exp and exp < _t.time():
            raise ApiError(422, "这个 JWT 已经过期了，请重新复制")
        sess = type("S", (), {
            "jwt": payload.jwt,
            "rt": payload.rt,
            "jwt_exp": exp or _t.time() + 48 * 3600,
            "rt_exp": _t.time() + settings.rt_ttl_seconds,
            "profile": {"account": payload.username, "name": "", "schoolName": ""},
        })()
    else:
        try:
            sess = await upstream.login(payload.username, payload.password)
        except UpstreamError as exc:
            raise ApiError(422, str(exc) or "U校园账号或密码错误") from exc
    if sess is None:
        raise ApiError(502, "上游登录失败")

    ts = db.now()
    row = db.one("SELECT is_admin FROM accounts WHERE username=?", (payload.username,))
    is_admin = bool(row["is_admin"]) if row else payload.username in _admin_users()
    db.ex("""INSERT INTO accounts(username,password,name,school,is_admin,status,uc_jwt,uc_rt,uc_jwt_exp,uc_rt_exp,
                                  created_at,last_seen_at)
             VALUES(?,?,?,?,?,'active',?,?,?,?,?,?)
             ON CONFLICT(username) DO UPDATE SET
               password=excluded.password, name=excluded.name, school=excluded.school,
               status='active', uc_jwt=excluded.uc_jwt, uc_rt=excluded.uc_rt,
               uc_jwt_exp=excluded.uc_jwt_exp, uc_rt_exp=excluded.uc_rt_exp, last_seen_at=excluded.last_seen_at""",
          (payload.username, payload.password, sess.profile.get("name", ""), sess.profile.get("schoolName", ""),
           1 if is_admin else 0, sess.jwt, sess.rt, sess.jwt_exp, sess.rt_exp, ts, ts))

    sid = security.new_sid()
    security.set_auth_cookies(response, payload.username, sid)

    acc = db.one("SELECT * FROM accounts WHERE username=?", (payload.username,))
    assert acc is not None
    return {
        "authenticated": True,
        "sessionId": sid,
        "profile": {"account": payload.username, "name": acc["name"], "schoolName": acc["school"],
                    "is_admin": bool(acc["is_admin"])},
        "server_time": db.now(),
    }


def _admin_users() -> set[str]:
    import os

    return {x.strip() for x in os.environ.get("UC_ADMIN_USERS", "").split(",") if x.strip()}


def _in_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _sync_call(coro):
    """在同步路由里安全地跑协程（FastAPI 把它放在线程池里，没有运行中的循环）。"""
    import asyncio

    return asyncio.run(coro)


@router.get("/auth/session")
def session(request: Request) -> dict[str, Any]:
    ctx = security.resolve_ctx(request, allow_anonymous=True)
    if ctx is None:
        return {"authenticated": False}
    acc = ctx.account
    return {
        "authenticated": True,
        "sessionId": ctx.sid,
        "profile": {"account": acc["username"], "name": acc["name"], "schoolName": acc["school"],
                    "is_admin": bool(acc["is_admin"])},
        "server_time": db.now(),
    }


@router.get("/userinfo")
def userinfo(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    acc = ctx.account
    return {
        "account": acc["username"], "name": acc["name"], "schoolName": acc["school"],
        "is_admin": bool(acc["is_admin"]), "status": acc["status"],
        "uc_jwt_exp": acc["uc_jwt_exp"], "uc_rt_exp": acc["uc_rt_exp"],
        "hosted": bool(db.one("SELECT active FROM hosting WHERE username=? AND active=1", (ctx.username,))),
    }


@router.post("/auth/refresh")
def refresh(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get("ucampus_rt", "")
    data = security.decode_token(token, "refresh") if token else None
    if not data:
        security.clear_auth_cookies(response)
        raise ApiError(401, "请先登录")
    sid = security.new_sid()
    security.set_auth_cookies(response, str(data["sub"]), sid)
    return {"ok": True, "sessionId": sid}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    ctx = security.resolve_ctx(request, allow_anonymous=True)
    if ctx:
        pass
    security.clear_auth_cookies(response)
    return {"ok": True}


# --------------------------------------------------------------------------- hosting
def _hosting_payload(username: str) -> dict[str, Any]:
    row = db.one("SELECT active, slot, updated_at FROM hosting WHERE username=?", (username,))
    if not row:
        return {"active": False, "slot": None, "updated_at": 0}
    return {"active": bool(row["active"]), "slot": int(row["slot"]), "updated_at": row["updated_at"]}


@router.get("/hosting")
def hosting_get(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    return _hosting_payload(ctx.username)


@router.post("/hosting")
def hosting_on(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    slot = scheduler.assign_slot(ctx.username)
    ts = db.now()
    db.ex("""INSERT INTO hosting(username,active,slot,updated_at) VALUES(?,1,?,?)
             ON CONFLICT(username) DO UPDATE SET active=1, slot=excluded.slot, updated_at=excluded.updated_at""",
          (ctx.username, slot, ts))
    db.ex("UPDATE accounts SET hosted_at=? WHERE username=?", (ts, ctx.username))
    return _hosting_payload(ctx.username)


@router.delete("/hosting")
def hosting_off(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    db.ex("UPDATE hosting SET active=0, updated_at=? WHERE username=?", (db.now(), ctx.username))
    return _hosting_payload(ctx.username)


# --------------------------------------------------------------------------- training
async def _overview(ctx: Ctx) -> dict[str, Any]:
    upstream = get_upstream()
    from .jobs import _ensure_ctx

    try:
        uctx = await _ensure_ctx(dict(ctx.account))
        data = await upstream.overview(uctx)
    except UpstreamError as exc:
        if exc.reason in ("credentials_missing", "rt_expired"):
            raise ApiError(409, "账号登录已过期，请重新登录后再打开训练面板") from exc
        raise ApiError(502, f"训练数据获取失败：{exc}") from exc
    for bucket in ("adaptive", "assigned", "history"):
        node = data.get(bucket) or {}
        for task in node.get("tasks") or []:
            task.setdefault("ansVersion", 0)
            task["bank_ready"] = _bank_ready(task)
        if bucket == "adaptive" and node.get("current"):
            node["current"].setdefault("ansVersion", 0)
            node["current"]["bank_ready"] = _bank_ready(node["current"])
    return data


def _bank_ready(task: dict[str, Any]) -> bool:
    """题库里是否有这一篇的答案（真实上游 paper_hash = taskId）。"""
    task_id = str(task.get("taskId") or "")
    if not task_id:
        return False
    row = db.one("SELECT 1 AS ok FROM answer_bank WHERE paper_hash=? AND ans_version=? LIMIT 1",
                 (task_id, int(task.get("ansVersion") or 1)))
    return bool(row)


@router.get("/training/overview")
async def training_overview(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    return await _overview(ctx)


@router.post("/training/tasks/{task_id}/complete")
async def training_complete(task_id: str, payload: CompleteRequest,
                            ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    overview = await _overview(ctx)
    task = jobs._find_task(overview, task_id)
    if task is None:
        raise ApiError(404, "任务不存在")
    if task.get("locked"):
        raise ApiError(409, "任务尚未解锁")
    if task.get("completed"):
        raise ApiError(409, "任务已完成")
    job_id = jobs.enqueue(ctx.username, task, trigger="user", ans_version=payload.ansVersion,
                          delay_seconds=payload.questionDelaySeconds,
                          origin_ip=ctx.dev.ip, browser_id=ctx.dev.browser_id, source="manual")
    return {"jobId": job_id, "status": "queued", "taskId": task_id, "message": "已进入答题队列",
            "ansVersion": payload.ansVersion}


def _job_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobId": row["id"], "account": row["account"], "taskId": row["task_id"], "taskName": row["task_name"],
        "trigger": row["trigger"], "source": row["source"], "status": row["status"],
        "message": row["message"], "error": row["error"], "score": row["score"],
        "paperHash": row["paper_hash"], "paperTitle": row["task_name"], "ansVersion": row["ans_version"],
        "audioVisual": row["audio_visual"], "startTime": row["started_at"], "endTime": row["finished_at"],
        "origin": {"ip": row["origin_ip"], "browserId": row["browser_id"]},
        "progress": jobs.get_progress(row["id"]),
    }


class AnswersIn(__import__("pydantic").BaseModel):
    answers: dict[str, Any]


@router.get("/training/tasks/{task_id}/paper")
async def training_task_paper(task_id: str, ans_version: int = 0,
                              ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    """为当前登录账号取这一篇卷（不交卷），返回可编辑的题目结构。"""
    from .jobs import _ensure_ctx, cache_paper

    upstream = get_upstream()
    try:
        uctx = await _ensure_ctx(dict(ctx.account))
        ver = ans_version or int(uctx.meta.get("ans", {}).get(task_id, 1) or 1)
        paper = await upstream.paper(uctx, task_id)
    except UpstreamError as exc:
        if exc.reason in ("rt_expired", "credentials_missing"):
            raise ApiError(409, "账号登录已过期，请重新登录") from exc
        raise ApiError(502, f"取卷失败：{exc}") from exc

    paper_hash = str(paper.get("paperHash") or task_id)
    cache_paper(paper_hash, task_id, paper)
    questions = paper.get("questions") or []
    ready = bool(db.one("SELECT 1 AS ok FROM answer_bank WHERE paper_hash=? AND ans_version=? LIMIT 1",
                        (task_id, ver)))
    return {
        "taskId": task_id, "ansVersion": ver, "paperHash": paper_hash,
        "paperTitle": paper.get("paperTitle"), "audioVisual": paper.get("audioVisual", 0),
        "questionCount": len(questions),
        "probeCount": sum(1 for q in questions if q.get("action") == "probe"),
        "oralCount": sum(1 for q in questions if q.get("action") == "oral"),
        "bankReady": ready,
        "questions": [
            {"instanceId": q.get("instanceId"), "startNo": q.get("startNo"), "type": q.get("type"),
             "name": q.get("name"), "action": q.get("action"), "prompt": q.get("prompt"),
             "options": q.get("options") or [], "subs": q.get("subs") or []}
            for q in questions
        ],
    }


@router.post("/training/tasks/{task_id}/answers")
async def training_task_answers(task_id: str, payload: AnswersIn, ans_version: int = 0,
                                ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    """把这个账号这一篇的答案写进题库（paper_hash = taskId），之后自动答题即可命中。"""
    ver = ans_version or 1
    if not payload.answers:
        raise ApiError(422, "answers 不能为空")
    answer_bank.save_bank(task_id, ver, payload.answers, source=f"ui:{ctx.username}")
    return {"ok": True, "taskId": task_id, "ansVersion": ver, "saved": len(payload.answers)}


# --------------------------------------------------------------------------- LLM（DeepSeek）
class LlmIn(__import__("pydantic").BaseModel):
    apiKey: str = ""
    model: str = ""
    baseUrl: str = ""


@router.get("/llm")
def llm_status(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import llm as llm_mod

    cfg = llm_mod.load_config()
    return {
        "configured": bool(cfg.get("api_key")),
        "model": cfg.get("model"),
        "baseUrl": cfg.get("base_url"),
        "keyMasked": llm_mod.mask(cfg.get("api_key", "")),
    }


@router.put("/llm")
def llm_save(payload: LlmIn, ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import llm as llm_mod

    cfg = llm_mod.save_config(payload.apiKey or None, payload.model or None, payload.baseUrl or None)
    return {"ok": True, "configured": bool(cfg.get("api_key")), "model": cfg["model"],
            "baseUrl": cfg["base_url"], "keyMasked": llm_mod.mask(cfg["api_key"])}


@router.post("/llm/test")
async def llm_test(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import llm as llm_mod

    try:
        return await llm_mod.test_key()
    except llm_mod.LLMError as exc:
        raise ApiError(422, str(exc)) from exc


@router.post("/training/tasks/{task_id}/solve")
async def training_task_solve(task_id: str, ans_version: int = 0,
                              ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    """用 LLM 读原文给这一篇生成答案并存进题库（不交卷）。"""
    from . import llm as llm_mod
    from .jobs import _ensure_ctx, _material, cached_paper, cache_paper

    if not llm_mod.configured():
        raise ApiError(422, "还没有配置 DeepSeek API Key")
    upstream = get_upstream()
    try:
        uctx = await _ensure_ctx(dict(ctx.account))
        ver = ans_version or int(uctx.meta.get("ans", {}).get(task_id, 1) or 1)
        paper = await upstream.paper(uctx, task_id)
    except UpstreamError as exc:
        raise ApiError(502, f"取卷失败：{exc}") from exc

    cache_paper(str(paper.get("paperHash") or task_id), task_id, paper)
    questions = paper.get("questions") or []
    try:
        answers = await llm_mod.solve(questions, _material(paper))
    except llm_mod.LLMError as exc:
        raise ApiError(502, f"模型作答失败：{exc}") from exc
    if not answers:
        raise ApiError(502, "模型没有给出可用答案")

    existing = answer_bank.load_bank(task_id, ver)
    merged = {k: v["answer"] for k, v in existing.items()}
    merged.update(answers)
    answer_bank.save_bank(task_id, ver, merged, source=f"llm:{llm_mod.load_config().get('model')}")
    return {"ok": True, "taskId": task_id, "ansVersion": ver,
            "solved": len(answers), "total": sum(1 for q in questions if q.get("action") == "probe"),
            "answers": answers}


# --------------------------------------------------------------------------- 听力定级
class PlacementIn(__import__("pydantic").BaseModel):
    targetLevel: str = "S14"
    ratio: int = 0            # 0 = 用映射表；1..100 = 手动指定正确率
    confirm: bool = False     # 必须显式确认，定级只能做一次
    dry: bool = False         # true = 只取卷+模型作答，不提交（预览）


@router.get("/placement/info")
async def placement_info(ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    from . import llm as llm_mod, placement, runtime
    from .jobs import _ensure_ctx

    upstream = get_upstream()
    try:
        uctx = await _ensure_ctx(dict(ctx.account))
        st = await upstream.status(uctx, "listen")
    except UpstreamError as exc:
        raise ApiError(502, f"读取定级状态失败：{exc}") from exc

    placed_level = None
    level_name = None
    try:
        rep = await upstream.level_report(uctx)
        placed_level = rep.get("level")
        level_name = rep.get("levelName")
    except UpstreamError as exc:
        if exc.reason != "not_placed":
            raise ApiError(502, f"读取定级报告失败：{exc}") from exc

    return {
        "type": st.get("type"),
        "needsPlacement": st.get("type") == "grade" and level_name is None,
        "planPending": st.get("type") in ("train_plan", "grade_profile"),
        "taskId": st.get("taskId"), "ansVersion": st.get("ansVersion"),
        "placed": level_name is not None or st.get("type") == "train",
        "level": placed_level, "levelName": level_name,
        "currentLevel": st.get("currentLevel"), "targetLevel": st.get("targetLevel"),
        "levels": placement.levels(),
        "map": {k: v for k, v in placement.load_map().items() if k != "observed"},
        "observed": placement.load_map().get("observed", [])[-5:],
        "llm": llm_mod.configured(),
        "realSubmit": runtime.get_real_submit(),
    }


@router.post("/placement/start")
async def placement_start(payload: PlacementIn, ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    import asyncio
    import json as _json

    from . import llm as llm_mod, placement, runtime
    from .jobs import _ensure_ctx, cache_paper

    if not payload.dry:
        if not payload.confirm:
            raise ApiError(422, "定级只能做一次，请先确认（confirm=true）")
        if not runtime.get_real_submit():
            raise ApiError(422, "当前是预演模式：请先打开页面上的「真实提交」开关")
    if not llm_mod.configured():
        raise ApiError(422, "定级需要模型读原文作答：请先配置 DeepSeek API Key")

    upstream = get_upstream()
    try:
        uctx = await _ensure_ctx(dict(ctx.account))
        st = await upstream.status(uctx, "listen")
    except UpstreamError as exc:
        raise ApiError(502, f"读取定级状态失败：{exc}") from exc
    if st.get("type") not in ("grade",):
        raise ApiError(409, "这个账号已经定级过了，不需要再做")
    task_id = str(st.get("taskId") or "")
    if not task_id:
        raise ApiError(409, "服务端没有下发定级卷（可能稍后再试）")
    ans_version = int(st.get("ansVersion") or 1)

    try:
        paper = await upstream.paper(uctx, task_id)
    except UpstreamError as exc:
        raise ApiError(502, f"取定级卷失败：{exc}") from exc
    questions = paper.get("questions") or []
    probe = [q for q in questions if q.get("action") == "probe"]
    if not probe:
        raise ApiError(502, "定级卷里没有解析到客观题，无法作答")
    cache_paper(str(paper.get("paperHash") or task_id), task_id, paper)

    material = "\n".join(t for q in questions for t in (q.get("texts") or []) if len(t) > 25)
    try:
        answers = await llm_mod.solve(questions, material)
    except llm_mod.LLMError as exc:
        raise ApiError(502, f"模型作答失败：{exc}") from exc
    # 排序题（sequence）不交给模型：选项的 value 就是正确位置，直接算
    seq_filled = 0
    for q in probe:
        seq = placement.sequence_answer(q)
        if seq:
            answers[q["instanceId"]] = seq
            seq_filled += 1

    if len(answers) < len(probe):
        # 定级只能做一次，不能因为个别题被过滤就中止：缺的用首个选项兜底
        filled: list[str] = []
        for q in probe:
            iid = q["instanceId"]
            if iid in answers:
                continue
            subs = q.get("subs") or []
            if subs:
                answers[iid] = [((s.get("options") or [{"value": "A"}])[0]["value"]) for s in subs]
            else:
                answers[iid] = ((q.get("options") or [{"value": "A"}])[0]["value"])
            filled.append(iid)
        if filled:
            say_filled = len(filled)
        else:
            say_filled = 0
    else:
        say_filled = 0

    ratio = int(payload.ratio or placement.target_ratio(payload.targetLevel))
    tuned = placement.trim_answers(answers, questions, ratio)

    if payload.dry:
        coverage = sum((len(q.get("subs") or []) or 1) for q in questions)
        return {"ok": True, "dry": True, "taskId": task_id, "ratio": ratio,
                "questions": len(questions), "probe": len(probe), "solved": len(answers),
                "filled": say_filled, "coverage": coverage, "targetLevel": payload.targetLevel,
                "sample": [{"instanceId": q["instanceId"], "type": q.get("type"),
                            "subs": len(q.get("subs") or []),
                            "answer": tuned.get(q["instanceId"])} for q in probe]}

    # 定级卷要求全卷覆盖：客观题按调好的答案，口语/学习段用空答案占位
    user_data: list[dict[str, Any]] = []
    for q in questions:
        iid = q["instanceId"]
        if q.get("action") == "probe":
            body_json = upstream._answer_json({
                "type": q.get("type"), "answer": tuned.get(iid, ""),
                "sub_count": len(q.get("subs") or []), "action": "probe"})
        else:
            body_json = {"value": [], "children": [], "record": None}
        user_data.append({
            "instanceId": iid,
            "answer": _json.dumps(body_json, ensure_ascii=False),
            "answerVersion": ans_version,
            "context": _json.dumps({"state": "done"}),
            "contextVersion": 1,
        })

    try:
        await upstream.submit_userdata(uctx, task_id, user_data, duration=max(10, len(user_data) * 3))
    except UpstreamError as exc:
        if "4295" not in str(exc):
            raise ApiError(502, f"交卷失败：{exc}") from exc
        # 兜底：改成"每个子题一条"的提交格式再试一次（有的卷子按子题计覆盖）
        per_child: list[dict[str, Any]] = []
        for q in questions:
            subs = q.get("subs") or []
            val = tuned.get(q["instanceId"])
            if q.get("action") != "probe":
                per_child.append({"instanceId": q["instanceId"],
                                  "answer": _json.dumps({"value": [], "children": [], "record": None}),
                                  "answerVersion": ans_version,
                                  "context": _json.dumps({"state": "done"}), "contextVersion": 1})
                continue
            vals = val if isinstance(val, list) else [val]
            targets = subs or [{"childId": ""}]
            for i, sub in enumerate(targets):
                iid = (sub.get("childId") or "") if isinstance(sub, dict) else ""
                if not iid:
                    iid = q["instanceId"] if len(targets) == 1 else f"{q['instanceId']}-{i + 1}"
                letter = str(vals[i]) if i < len(vals) else ""
                per_child.append({
                    "instanceId": iid,
                    "answer": _json.dumps({"value": [letter], "children": [], "record": None}, ensure_ascii=False),
                    "answerVersion": ans_version,
                    "context": _json.dumps({"state": "done"}),
                    "contextVersion": 1,
                })
        try:
            await upstream.submit_userdata(uctx, task_id, per_child,
                                           duration=max(10, len(per_child) * 3))
        except UpstreamError as exc2:
            raise ApiError(502, f"交卷失败（两种格式都被拒）：{exc2}") from exc2

    achieved: dict[str, Any] = {}
    for _ in range(12):
        await asyncio.sleep(5)
        try:
            achieved = await upstream.level_report(uctx)
            if achieved.get("levelName"):
                break
        except UpstreamError:
            continue

    placement.record_observation(payload.targetLevel, ratio,
                                 str(achieved.get("level") or ""), achieved.get("levelName"))
    return {"ok": True, "taskId": task_id, "ratio": ratio, "questions": len(questions),
            "probe": len(probe), "answers": len(answers),
            "level": achieved.get("level"), "levelName": achieved.get("levelName"),
            "description": achieved.get("levelDescription")}


@router.get("/training/jobs")
def training_jobs(trigger: str = "all", limit: int = 20, offset: int = 0,
                  ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    sql = "SELECT * FROM jobs WHERE account=?"
    args: list[Any] = [ctx.username]
    if trigger and trigger != "all":
        sql += " AND trigger=?"
        args.append(trigger)
    rows = db.q(sql + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    total = db.one(sql.replace("*", "COUNT(*) AS c"), args)["c"]
    return {"jobs": [_job_public(dict(r)) for r in rows], "total": int(total),
            "limit": limit, "offset": offset}


@router.get("/training/jobs/{job_id}")
def training_job(job_id: int, ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    row = jobs.get_job(job_id)
    if not row or row["account"] != ctx.username:
        raise ApiError(404, "任务不存在")
    data = _job_public(row)
    data["lines"] = jobs.job_logs(job_id)
    return data


@router.get("/training/jobs/{job_id}/paper")
def training_paper(job_id: int, ctx: Ctx = Depends(_ctx)) -> dict[str, Any]:
    row = jobs.get_job(job_id)
    if not row or row["account"] != ctx.username:
        raise ApiError(404, "任务不存在")
    paper = jobs.cached_paper(row["paper_hash"]) or {}
    answers = jobs.job_answers(job_id)
    answered = {a["instanceId"]: a.get("answer") for a in answers}
    questions = []
    for q in paper.get("questions") or []:
        item = dict(q)
        item["answer"] = answered.get(q.get("instanceId"), "")
        questions.append(item)
    return {
        "jobId": job_id, "paperHash": row["paper_hash"], "paperTitle": paper.get("paperTitle") or row["task_name"],
        "audioVisual": paper.get("audioVisual", row["audio_visual"]),
        "questionCount": paper.get("questionCount", len(questions)),
        "gradedCount": sum(1 for a in answers if a.get("action") == "probe"),
        "score": row["score"],
        "questions": questions,
    }


# --------------------------------------------------------------------------- admin
@router.get("/admin/hosted-accounts")
def admin_hosted_accounts(limit: int = 20, offset: int = 0, soon: bool = False,
                          slot: int | None = None, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    sql = """SELECT a.username AS account, a.name AS user_name, a.school AS school_name,
                    h.slot, a.hosted_at, a.uc_jwt_exp, a.uc_rt_exp, a.status
               FROM hosting h JOIN accounts a ON a.username=h.username
              WHERE h.active=1"""
    args: list[Any] = []
    if slot is not None:
        sql += " AND h.slot=?"
        args.append(slot)
    if soon:
        sql += " AND a.uc_rt_exp>0 AND a.uc_rt_exp < ?"
        args.append(db.now() + 3 * 86400)
    rows = db.q(sql + " ORDER BY a.hosted_at DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    count_sql = ("SELECT COUNT(*) AS c FROM hosting h JOIN accounts a ON a.username=h.username "
                 "WHERE h.active=1")
    count_args: list[Any] = []
    if slot is not None:
        count_sql += " AND h.slot=?"
        count_args.append(slot)
    if soon:
        count_sql += " AND a.uc_rt_exp>0 AND a.uc_rt_exp<?"
        count_args.append(db.now() + 3 * 86400)
    total = int(db.one(count_sql, count_args)["c"])
    return {
        "accounts": [{"account": r["account"], "user_name": r["user_name"], "school_name": r["school_name"],
                      "slot": r["slot"], "hosted_at": r["hosted_at"], "status": r["status"],
                      "rt_exp": r["uc_rt_exp"], "jwt_exp": r["uc_jwt_exp"]} for r in rows],
        "total": int(total), "slot_counts": scheduler.slot_counts(),
        "rt_expiring_soon_count": int(db.one(
            "SELECT COUNT(*) AS c FROM hosting h JOIN accounts a ON a.username=h.username "
            "WHERE h.active=1 AND a.uc_rt_exp>0 AND a.uc_rt_exp<?", (db.now() + 3 * 86400,))["c"]),
        "server_time": db.now(),
    }


@router.delete("/admin/hosted-accounts/{account}")
def admin_kick(account: str, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    db.ex("UPDATE hosting SET active=0, updated_at=? WHERE username=?", (db.now(), account))
    return {"ok": True, "account": account}


@router.get("/admin/scheduler")
def admin_scheduler(ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return scheduler.public_config()


@router.put("/admin/scheduler")
def admin_scheduler_put(payload: SchedulerUpdate, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return scheduler.put_config(payload.model_dump())


@router.post("/admin/scheduler/run")
async def admin_scheduler_run(ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return await scheduler.run_now(trigger="cron")


@router.get("/admin/scheduler/accounts")
def admin_scheduler_accounts(date: str | None = None, limit: int = 20, offset: int = 0,
                             ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return scheduler.scheduler_accounts(date=date, limit=limit, offset=offset)


@router.get("/admin/scheduler/accounts/{account}/jobs")
def admin_scheduler_account_jobs(account: str, date: str | None = None, limit: int = 20, offset: int = 0,
                                 ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    day = date or scheduler.datetime.now(scheduler.TZ).strftime("%Y-%m-%d")
    rows = db.q("""SELECT * FROM jobs WHERE account=?
                    AND date(created_at,'unixepoch','+8 hours')=?
                    ORDER BY created_at DESC LIMIT ? OFFSET ?""", (account, day, limit, offset))
    total = db.one("""SELECT COUNT(*) AS c FROM jobs WHERE account=?
                      AND date(created_at,'unixepoch','+8 hours')=?""", (account, day))["c"]
    return {"date": day, "jobs": [_job_public(dict(r)) for r in rows], "total": int(total)}


@router.get("/admin/jobs")
def admin_jobs(status: str = "all", account: str = "", trigger: str = "all",
               limit: int = 20, offset: int = 0, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    sql = "SELECT * FROM jobs WHERE 1=1"
    args: list[Any] = []
    if status and status != "all":
        sql += " AND status=?"
        args.append(status)
    if account:
        sql += " AND account=?"
        args.append(account)
    if trigger and trigger != "all":
        sql += " AND trigger=?"
        args.append(trigger)
    rows = db.q(sql + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    total = db.one(sql.replace("SELECT *", "SELECT COUNT(*) AS c"), args)["c"]
    return {"jobs": [_job_public(dict(r)) for r in rows], "total": int(total)}


@router.get("/admin/jobs/{job_id}")
def admin_job(job_id: int, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    row = jobs.get_job(job_id)
    if not row:
        raise ApiError(404, "任务不存在")
    data = _job_public(row)
    data["lines"] = jobs.job_logs(job_id)
    return data


@router.get("/admin/jobs/{job_id}/paper")
def admin_job_paper(job_id: int, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    row = jobs.get_job(job_id)
    if not row:
        raise ApiError(404, "任务不存在")
    paper = jobs.cached_paper(row["paper_hash"]) or {}
    answers = {a["instanceId"]: a.get("answer") for a in jobs.job_answers(job_id)}
    questions = [{**q, "answer": answers.get(q.get("instanceId"), "")} for q in paper.get("questions") or []]
    return {"jobId": job_id, "account": row["account"], "paperHash": row["paper_hash"],
            "paperTitle": paper.get("paperTitle") or row["task_name"], "score": row["score"],
            "questions": questions}


# --------------------------------------------------------------------------- 题库
@router.post("/admin/answer-bank/import")
def answer_bank_import(ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return answer_bank.import_bank()


@router.get("/admin/answer-bank/{paper_hash}")
def answer_bank_get(paper_hash: str, ans_version: int = 0, ctx: Ctx = Depends(_admin)) -> dict[str, Any]:
    return {"paperHash": paper_hash, "ansVersion": ans_version,
            "answers": answer_bank.load_bank(paper_hash, ans_version)}
