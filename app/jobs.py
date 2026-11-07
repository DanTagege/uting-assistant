"""任务队列与执行器：queued -> running -> succeeded|failed。

一次 job 的动作序列：
  1. 取账号上游令牌（快过期则先用 RT 续期，失败 -> 账号置 relogin_required）
  2. 拉训练总览，定位 taskId 的元数据（名称 / 音视频类型 / paperHash）
  3. 拉试卷并缓存（供 /api/training/jobs/{id}/paper 回显）
  4. 用题库按 ansVersion 组装答案序列
  5. 逐题提交（按 questionDelaySeconds 停顿）
  6. 回读判分，写 score 与日志
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from . import db, runtime
from .config import settings
from .engine import AnswerRefused, build_answers
from .upstream import UpstreamCtx, UpstreamError, get_upstream


def _material(paper: dict[str, Any]) -> str:
    """把卷面里的原文/字幕拼成 LLM 输入。"""
    chunks: list[str] = []
    for q in paper.get("questions") or []:
        for t in q.get("texts") or []:
            if isinstance(t, str) and len(t) > 25 and t not in chunks:
                chunks.append(t)
    return "\n".join(chunks)

_queue: "asyncio.Queue[int] | None" = None
_workers: list[asyncio.Task] = []
_worker_count = 0
_loop: "asyncio.AbstractEventLoop | None" = None


# --------------------------------------------------------------------------- 基础
def _log(job_id: int, line: str) -> None:
    seq = (db.one("SELECT COALESCE(MAX(seq),0) AS s FROM job_logs WHERE job_id=?", (job_id,))["s"] + 1)
    db.ex("INSERT INTO job_logs(job_id,seq,line,ts) VALUES(?,?,?,?)", (job_id, seq, line[:2000], db.now()))


def _status(job_id: int, status: str, **fields: Any) -> None:
    sets = ["status=?"]
    args: list[Any] = [status]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        args.append(v)
    args.append(job_id)
    db.ex(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args)


def _set_account_status(username: str, status: str) -> None:
    db.ex("UPDATE accounts SET status=? WHERE username=?", (status, username))


def get_job(job_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    return dict(row) if row else None


def job_logs(job_id: int, limit: int = 500) -> list[str]:
    rows = db.q("SELECT line FROM job_logs WHERE job_id=? ORDER BY seq LIMIT ?", (job_id, limit))
    return [r["line"] for r in rows]


def job_answers(job_id: int) -> list[dict[str, Any]]:
    row = db.one("SELECT v FROM kv WHERE k=?", (f"job:answers:{job_id}",))
    if not row:
        return []
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return []


def cache_paper(paper_hash: str, task_id: str, paper: dict[str, Any]) -> None:
    if not paper_hash:
        return
    db.ex("INSERT INTO papers(paper_hash,task_id,payload,fetched_at) VALUES(?,?,?,?) "
          "ON CONFLICT(paper_hash) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
          (paper_hash, task_id, json.dumps(paper, ensure_ascii=False), db.now()))


def cached_paper(paper_hash: str) -> dict[str, Any] | None:
    row = db.one("SELECT payload FROM papers WHERE paper_hash=?", (paper_hash,))
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- 入队
def enqueue(
    account: str,
    task: dict[str, Any],
    *,
    trigger: str = "user",
    ans_version: int = 0,
    delay_seconds: int = 0,
    origin_ip: str = "",
    browser_id: str = "",
    source: str = "manual",
) -> int:
    job_id = db.ex(
        """INSERT INTO jobs(account,task_id,task_name,trigger,status,source,paper_hash,ans_version,
                            delay_s,audio_visual,origin_ip,browser_id,created_at)
           VALUES(?,?,?,?,'queued',?,?,?,?,?,?,?,?)""",
        (account, task["taskId"], task.get("name", ""), trigger, source,
         task.get("paperHash", ""), ans_version, delay_seconds, int(task.get("audioVisual") or 0),
         origin_ip, browser_id, db.now()),
    )
    _log(job_id, f"INFO queued task={task['taskId']} trigger={trigger} ansVersion={ans_version} delay={delay_seconds}s")
    if _queue is None:
        raise RuntimeError("worker 未启动：请先 await jobs.start_workers()")
    loop = _loop
    if loop is None or loop.is_closed():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(_queue.put_nowait, job_id)
    else:
        _queue.put_nowait(job_id)
    return job_id


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """记录主事件循环，允许从同步线程（FastAPI 线程池）安全投递任务。"""
    global _loop
    _loop = loop


async def start_workers(count: int | None = None) -> None:
    global _queue, _worker_count, _loop
    count = count or settings.default_parallelism
    if _queue is None:
        _queue = asyncio.Queue()
    _loop = asyncio.get_running_loop()
    while _worker_count < count:
        _workers.append(asyncio.create_task(_worker(), name=f"job-worker-{_worker_count}"))
        _worker_count += 1


async def stop_workers() -> None:
    global _worker_count
    for w in _workers:
        w.cancel()
    _workers.clear()
    _worker_count = 0


async def _worker() -> None:
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        try:
            await _run(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 兜底，避免单个任务拖死 worker
            _log(job_id, f"ERROR unhandled {type(exc).__name__}: {exc}")
            _status(job_id, "failed", error=f"内部错误：{exc}", finished_at=db.now())
        finally:
            _queue.task_done()


# --------------------------------------------------------------------------- 执行
def _find_task(overview: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    buckets = [
        (overview.get("adaptive") or {}).get("current"),
        *((overview.get("adaptive") or {}).get("tasks") or []),
        *((overview.get("assigned") or {}).get("tasks") or []),
        *((overview.get("history") or {}).get("tasks") or []),
    ]
    for t in buckets:
        if t and str(t.get("taskId")) == str(task_id):
            return t
    return None


def _pick_auto(overview: dict[str, Any]) -> dict[str, Any] | None:
    """托管模式下自动选题：优先自适应当前卷，其次老师布置的未完成未锁定任务。"""
    adaptive = overview.get("adaptive") or {}
    cur = adaptive.get("current")
    if cur and not cur.get("locked") and not cur.get("completed"):
        return cur
    now = db.now()
    for t in adaptive.get("tasks") or []:
        if t and not t.get("locked") and not t.get("completed"):
            return t
    for t in (overview.get("assigned") or {}).get("tasks") or []:
        if not t or t.get("locked") or t.get("completed"):
            continue
        end = t.get("endTime") or ""
        if end:
            try:
                from datetime import datetime

                if datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() < now:
                    continue
            except ValueError:
                pass
        return t
    return None


async def _ensure_ctx(acc: dict[str, Any]) -> UpstreamCtx:
    """确保上游访问令牌可用；jwt 过期且有 RT 时自动续期。

    注意：JWT 还在有效期内就**不需要** RT —— 用 import_jwt.py 只导入 jwt 也能跑。
    """
    upstream = get_upstream()
    ctx = UpstreamCtx(account=acc["username"], jwt=acc.get("uc_jwt") or "", rt=acc.get("uc_rt") or "")
    needs_refresh = not ctx.jwt or float(acc.get("uc_jwt_exp") or 0) < time.time() + 30
    if needs_refresh:
        if not ctx.rt:
            raise UpstreamError("credentials_missing", "账号凭证缺失，需要重新登录", fatal=True)
        sess = await upstream.refresh(ctx)
        ctx.jwt = sess.jwt
        db.ex("UPDATE accounts SET uc_jwt=?, uc_jwt_exp=?, uc_rt=?, uc_rt_exp=?, status='active' WHERE username=?",
              (sess.jwt, sess.jwt_exp, sess.rt or ctx.rt, sess.rt_exp, acc["username"]))
    return ctx


def _fail_message(reason: str) -> str:
    return {
        "auth_failed": "U校园账号或密码错误，需要重新登录",
        "rt_expired": "登录已过期，请重新登录后再托管",
        "credentials_missing": "账号凭证缺失，请重新登录",
        "captcha": "上游要求滑块验证，请用浏览器登录后导入 jwt",
        "network": "无法连接上游，请稍后重试",
        "rate_limited": "上游限流，请稍后重试",
        "not_configured": "上游未配置：请先完成 data/upstream.json 的端点配置",
        "unsupported": "该题型当前不支持（口语/跟读需要音频链路），已拒交",
        "already_submitted": "该训练已提交过，无需重复作答",
        "upstream": "上游返回异常，请稍后重试",
    }.get(reason, "自动答题失败")


def _set_progress(job_id: int, percent: float, phase: str, elapsed: float, total: float) -> None:
    """把进度写进 kv，供 /api/training/jobs/{id} 实时读。"""
    payload = {"percent": round(max(0.0, min(100.0, percent)), 1), "phase": phase,
               "elapsed": int(elapsed), "total": int(total),
               "eta": max(0, int(total - elapsed))}
    db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (f"job:progress:{job_id}", json.dumps(payload, ensure_ascii=False)))


def get_progress(job_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT v FROM kv WHERE k=?", (f"job:progress:{job_id}",))
    if not row:
        return None
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return None


def _answer_payload(item: dict[str, Any]) -> dict[str, Any]:
    """把一条答案转成上游需要的 answer JSON（与上游适配器同一套规则）。"""
    subs = int(item.get("sub_count") or 0)
    value = item.get("answer")
    qtype = str(item.get("type") or "").lower()

    if subs > 0:
        vals = value if isinstance(value, list) else [
            x for x in str(value or "").replace(",", " ").split() if x]
        children = []
        for i in range(subs):
            v = str(vals[i]).strip().upper() if i < len(vals) else ""
            children.append({"value": [v] if v else []})
        return {"value": [], "children": children, "record": None}

    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if "blank" in qtype or "fill" in qtype or "dictation" in qtype:
        return {"value": [], "children": [{"value": [text]}], "record": None}
    if "multi" in qtype:
        return {"value": [v.strip() for v in text.split(",") if v.strip()], "children": [], "record": None}
    return {"value": [text], "children": [], "record": None}


def _split_values(raw: Any, count: int, is_choice: bool) -> list[str]:
    """把题库里的答案拆成"每个空一个值"。

    题库约定：多空用逗号或竖线分隔（"B,C,C,A" / "ACCOUNTS|GLUED|TOUGH"），
    也可能是列表，或（选择题）连写的 "BCCA"。
    """
    if isinstance(raw, list):
        vals = [str(v).strip() for v in raw]
    else:
        s = str(raw or "").strip()
        if not s:
            return []
        sep = "|" if "|" in s else ("," if "," in s else (";" if ";" in s else None))
        vals = [p.strip() for p in s.split(sep)] if sep else [s]
        # 选择题连写："BCCA" / "A" 这类，按字符拆
        if sep is None and is_choice and count > 1 and len(vals[0]) == count \
                and vals[0].replace(" ", "").isalpha():
            vals = list(vals[0].replace(" ", ""))
    return [v for v in vals if v != ""] or []


def _build_user_data(upstream: Any, answers: list[dict[str, Any]], paper: dict[str, Any]) -> list[dict[str, Any]]:
    """按卷面结构组装 userData。

    关键：平台按"卷面叶子数"校验覆盖率（否则 4295），所以每条实例都必须出现，
    且 children 段数 = 该实例的卷面子题数；音频/学习/口语这类没有客观答案的，
    补空值即可（不会被判分，但缺了就算覆盖不全）。
    """
    by_instance = {str(a["instanceId"]): a for a in answers}
    questions = paper.get("questions") or []
    out: list[dict[str, Any]] = []

    for q in questions:
        iid = str(q.get("instanceId") or "")
        if not iid:
            continue
        item = by_instance.get(iid) or {}
        is_probe = (item.get("action") == "probe") and item.get("answer") not in (None, "", [])
        count = int(q.get("childCount") or len(q.get("subs") or []) or 0)
        qtype = str(q.get("type") or "").lower()
        is_choice = any(k in qtype for k in ("choice", "sequence", "select", "judge"))

        vals = _split_values(item.get("answer"), count, is_choice) if is_probe else []
        if not is_choice:
            vals = [v for v in vals]          # 填空/跟读保留原文大小写
        else:
            vals = [v.upper() for v in vals]

        if count <= 0:
            payload = {"value": vals, "children": [], "record": None}
        else:
            children = []
            for i in range(count):
                v = vals[i] if i < len(vals) else ""
                children.append({"value": [v] if v else []})
            payload = {"value": [], "children": children, "record": None}

        out.append({
            "instanceId": iid,
            "answer": json.dumps(payload, ensure_ascii=False),
            "answerVersion": int(paper.get("ansVersion") or 1),
            "context": json.dumps({"state": "done"}),
            "contextVersion": 1,
        })
    return out


async def _paced_submit(job_id: int, upstream: Any, ctx: Any, task_id: str,
                        user_data: list[dict[str, Any]], duration_s: int, logger: Any) -> Any:
    """模拟真人作答：分阶段 saveSnapshot，最后一次性提交。"""
    import asyncio

    total = max(1, len(user_data))
    chunks = max(1, min(total, 12))
    t0 = db.now()
    _set_progress(job_id, 0, "准备", 0, duration_s)

    # 训练时长由平台的独立计时服务统计：作答期间保持 socket 计时会话
    from .timing import TimingSession, open_id_from_jwt

    probe_modules = [f"uls_{u['instanceId']}" for u in user_data]

    session = TimingSession(ctx.jwt, task_id,
                            int(ctx.meta.get("ans", {}).get(task_id, 1) or 1),
                            modules=probe_modules)
    timing_ok = await session.start()
    if logger:
        if timing_ok:
            await logger(f"INFO 计时会话已建立（{session.module_group}，"
                         f"ack={session.kick_ack}），平台会记录真实时长")
        else:
            await logger(f"WARNING 计时会话建立失败：{session.error or '未知原因'}"
                         f"（训练时长可能仍显示 0）")

    for i in range(1, chunks + 1):
        target = duration_s * i / chunks
        while db.now() - t0 < target:
            elapsed = db.now() - t0
            pct = min(99.0, elapsed / duration_s * 100)
            _set_progress(job_id, pct, "作答中", elapsed, duration_s)
            await asyncio.sleep(1.0)
        upto = max(1, round(total * i / chunks))
        try:
            await upstream.save_snapshot(ctx, task_id, user_data[:upto],
                                         duration=runtime.to_payload_duration(db.now() - t0))
            if logger:
                await logger(f"INFO snapshot {upto}/{total} ({round(i / chunks * 100)}%)")
        except Exception as exc:  # noqa: BLE001 - 快照失败不影响最终提交
            if logger:
                await logger(f"WARNING snapshot 失败：{exc}")
        _set_progress(job_id, i / chunks * 100 * 0.99, "作答中", db.now() - t0, duration_s)

    elapsed = int(db.now() - t0)
    payload_duration = runtime.to_payload_duration(elapsed)
    if logger:
        await logger(f"INFO 作答完成，用时 {elapsed}s（上报 duration={payload_duration}"
                     f"{runtime.get_duration_unit()}），提交中…")
    _set_progress(job_id, 99.0, "提交中", elapsed, duration_s)
    try:
        if hasattr(upstream, "submit_userdata"):
            res = await upstream.submit_userdata(ctx, task_id, user_data,
                                                 duration=max(1, payload_duration))
            score, graded = await upstream._read_score(
                ctx, task_id, int(ctx.meta.get("ans", {}).get(task_id, 1) or 1), logger)
        else:
            # 本地 mock 上游：退回普通提交
            res = None
            simple = []
            for u in user_data:
                parsed = json.loads(u["answer"])
                if parsed.get("children"):
                    ans = (parsed["children"][0].get("value") or [""])[0]
                else:
                    ans = (parsed.get("value") or [""])[0]
                simple.append({"instanceId": u["instanceId"], "action": "probe", "answer": ans})
            r = await upstream.submit(ctx, task_id, simple, delay_seconds=0, log=logger)
            score, graded = r.score, r.graded
    finally:
        # 无论交卷成功与否都要关闭计时房间，否则服务端不结算时长
        await session.stop()
        if timing_ok:
            from .timing import query_duration

            recorded = await query_duration(ctx.jwt, session.module_group, session.open_id)
            if logger:
                await logger(f"INFO 平台记录的训练时长 = {recorded if recorded is not None else 0}s"
                             f"（本地计时 {session.elapsed()}s）")
    _set_progress(job_id, 100.0, "完成", elapsed, duration_s)
    from .upstream.base import SubmitResult

    return SubmitResult(score=score, graded=graded, total=total,
                        raw={"paced": True, "duration": elapsed, "resp": res})


async def _run(job_id: int) -> None:
    job = get_job(job_id)
    if not job:
        return
    _status(job_id, "running", started_at=db.now())
    _log(job_id, f"INFO job start account={job['account']} task={job['task_id']}")

    acc = db.one("SELECT * FROM accounts WHERE username=?", (job["account"],))
    if acc is None:
        _log(job_id, "ERROR account not found")
        _status(job_id, "failed", error="账号不存在", finished_at=db.now())
        return
    acc = dict(acc)

    upstream = get_upstream()
    try:
        ctx = await _ensure_ctx(acc)
        ctx.meta = {"ansVersion": job["ans_version"], "delay": job["delay_s"]}

        overview = await upstream.overview(ctx)
        if job["task_id"] == "__auto__":
            picked = _pick_auto(overview)
            if picked is None:
                _status(job_id, "succeeded", message="本周暂无待完成的练习", score=None,
                        finished_at=db.now())
                _log(job_id, "INFO no actionable task, skip")
                return
            db.ex("UPDATE jobs SET task_id=?, task_name=?, paper_hash=?, audio_visual=? WHERE id=?",
                  (picked["taskId"], picked.get("name", ""), picked.get("paperHash", ""),
                   int(picked.get("audioVisual") or 0), job_id))
            job = get_job(job_id) or job
            _log(job_id, f"INFO auto picked task={picked['taskId']} name={picked.get('name','')}")
        task = _find_task(overview, job["task_id"]) or {"taskId": job["task_id"], "name": job["task_name"]}
        _log(job_id, f"INFO task resolved name={task.get('name','')} audioVisual={task.get('audioVisual', 0)} locked={task.get('locked')}")

        paper = await upstream.paper(ctx, job["task_id"])
        paper_hash = str(paper.get("paperHash") or job["paper_hash"] or "")
        cache_paper(paper_hash, job["task_id"], paper)
        questions = paper.get("questions") or []
        _log(job_id, f"INFO paper {paper_hash} questions={len(questions)} title={paper.get('paperTitle','')}")

        try:
            answers, stats = build_answers(
                paper, int(job["ans_version"]),
                strict=bool(getattr(upstream, "strict", False)),
                supports_oral=bool(getattr(upstream, "supports_oral", True)),
                supports_skip=bool(getattr(upstream, "supports_skip", True)),
            )
        except AnswerRefused as first:
            from . import answer_bank, llm as llm_mod

            if first.reason != "answer_missing" or not llm_mod.configured():
                raise
            _log(job_id, f"WARNING 题库缺答案（{first.instance_id}），调用 {llm_mod.load_config().get('model')} 读原文作答…")
            try:
                ai = await llm_mod.solve(paper.get("questions") or [], _material(paper))
            except llm_mod.LLMError as exc:
                _log(job_id, f"WARNING LLM 失败：{exc}")
                ai = {}
            if not ai:
                raise
            merged = {k: v["answer"] for k, v in
                      answer_bank.load_bank(paper_hash, int(job["ans_version"])).items()}
            merged.update(ai)
            answer_bank.save_bank(paper_hash, int(job["ans_version"]), merged,
                                  source=f"llm:{llm_mod.load_config().get('model')}")
            _log(job_id, f"INFO LLM 给出 {len(ai)} 题，已写入题库，重新组包")
            answers, stats = build_answers(
                paper, int(job["ans_version"]),
                strict=bool(getattr(upstream, "strict", False)),
                supports_oral=bool(getattr(upstream, "supports_oral", True)),
                supports_skip=bool(getattr(upstream, "supports_skip", True)),
            )
        db.ex("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
              (f"job:answers:{job_id}", json.dumps(answers, ensure_ascii=False)))
        _log(job_id, "INFO answers built total={total} probe={probe} oral={oral} skip={skip} "
                     "bank={from_bank} guessed={guessed}".format(**stats))

        from . import runtime

        user_data = _build_user_data(upstream, answers, paper)

        # 提交前校验：客观题每空都要有值，否则中止（宁可失败也不要 0 分）
        malformed: list[str] = []
        for q in (paper.get("questions") or []):
            if q.get("action") != "probe":
                continue
            want = int(q.get("childCount") or len(q.get("subs") or []) or 0)
            if want <= 0:
                continue
            got = 0
            for u in user_data:
                if u["instanceId"] == str(q.get("instanceId")):
                    got = len([c for c in json.loads(u["answer"]).get("children") or [] if c.get("value")])
                    break
            if got != want:
                malformed.append(f"idx={q.get('startNo')} 需要 {want} 空、实到 {got} 空")
        if malformed:
            raise UpstreamError("answer_malformed",
                                "答案格式与卷面不符，已中止提交（避免判 0 分）：" + "；".join(malformed[:4]))

        duration_s = runtime.pick_duration_seconds() if runtime.get_real_submit() else 0
        if duration_s > 0 and user_data:
            _log(job_id, f"INFO 进入模拟作答：目标时长 {duration_s}s（可在页面调整）")
            res = await _paced_submit(job_id, upstream, ctx, job["task_id"],
                                      user_data, duration_s, _make_logger(job_id))
        else:
            res = await upstream.submit(ctx, job["task_id"], answers,
                                        delay_seconds=float(job["delay_s"]), log=_make_logger(job_id))
        score = res.score
        if res.raw.get("dryRun"):
            msg = "演练模式：已完成取卷与答案组装，未真正交卷（UC_REAL_SUBMIT=0）"
        elif score is None:
            msg = "自动答题已提交，报告待出分，稍后刷新可同步成绩"
        else:
            msg = f"自动答题已提交，得分 {score:g}"
        _status(job_id, "succeeded", score=score, message=msg, paper_hash=paper_hash,
                finished_at=db.now(), error="")
        _log(job_id, f"INFO done score={score} graded={res.graded}/{res.total} dryRun={bool(res.raw.get('dryRun'))}")
        db.ex("UPDATE accounts SET status='active', last_seen_at=? WHERE username=?", (db.now(), acc["username"]))

    except AnswerRefused as exc:
        msg = "已拒交：题库/题型不足以安全作答"
        _log(job_id, f"ERROR refused {exc.reason} {exc.instance_id}: {exc.detail}")
        _status(job_id, "failed", error=msg, message=msg, finished_at=db.now())

    except UpstreamError as exc:
        msg = _fail_message(exc.reason)
        _log(job_id, f"ERROR {exc.reason}: {exc}")
        _status(job_id, "failed", error=msg, message=msg, finished_at=db.now())
        if exc.fatal:
            _set_account_status(acc["username"], "relogin_required")
    except asyncio.CancelledError:
        _status(job_id, "failed", error="任务被取消", finished_at=db.now())
        raise
    except Exception as exc:
        _log(job_id, f"ERROR {type(exc).__name__}: {exc}")
        _status(job_id, "failed", error=f"自动答题失败：{exc}", finished_at=db.now())


def _make_logger(job_id: int):
    async def _l(line: str) -> None:
        _log(job_id, line)
    return _l
