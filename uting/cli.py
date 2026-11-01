"""U听说「每周听力作业」命令行工具。

python -m uting login     登录（密码 或 粘贴 JWT）
python -m uting status    看本周进度（几篇 / 目标几篇）
python -m uting probe     取一篇卷并打印卷面结构（不交卷，第一次先跑这个）
python -m uting run       自动做完本周剩余篇数（默认 dry-run，加 --submit 才真交卷）
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from pathlib import Path

from . import paper as paper_mod
from . import solve
from .client import Client, UtingError, load_answers, save_answers

try:  # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BAR = "─" * 62


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, text: str) -> None:
    say(f"\n[{n}] {text}")


def _client(args: argparse.Namespace) -> Client:
    return Client(getattr(args, "user", "") or "")


async def cmd_account(args: argparse.Namespace) -> int:
    """多账号管理：list / use / import。"""
    from .client import ACTIVE, PROFILES, load_state, resolve_profile, save_state

    action = args.action
    if action == "list":
        current = resolve_profile()
        names = sorted(p.name for p in PROFILES.iterdir() if (p / "state.json").exists()) if PROFILES.exists() else []
        if not names and (Path.home() / ".uting" / "state.json").exists():
            names = ["(legacy)"]
        say(f"当前账号：{current or '（无）'}")
        for name in names:
            st = load_state(name if name != "(legacy)" else "")
            mark = " ←" if name == current else ""
            say(f"  {name:<14} jwt={'有' if st.get('jwt') else '无'}  "
                f"exp={time.strftime('%m-%d %H:%M', time.localtime(float(st.get('jwt_exp') or 0)))}{mark}")
        if ACTIVE.exists():
            say(f"（active 文件指向：{ACTIVE.read_text(encoding='utf-8').strip()}）")
        return 0

    if action == "use":
        phone = (args.phone or "").strip()
        if not phone:
            say("用法：python -m uting account use <手机号>")
            return 1
        if not (PROFILES / phone / "state.json").exists():
            say(f"没有 {phone} 的档案：先 login --user {phone}，或 account import {phone}")
            return 1
        ACTIVE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE.write_text(phone, encoding="utf-8")
        st = load_state(phone)
        say(f"已切换：{phone}（jwt={'有' if st.get('jwt') else '无'}）")
        say("后面的命令都会用这个账号；临时用别的号可加 --user <手机号>")
        return 0

    if action == "import":
        phone = (args.phone or "").strip()
        if not phone:
            say("用法：python -m uting account import <手机号>  （从平台数据库读取令牌）")
            return 1
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from app import db as platform_db

        platform_db.conn()
        row = platform_db.one("SELECT username,uc_jwt,uc_rt,uc_jwt_exp,uc_rt_exp FROM accounts WHERE username=?", (phone,))
        if row is None or not row["uc_jwt"]:
            say(f"平台里没有 {phone} 的令牌：先在 http://127.0.0.1:8710 用这个号登录一次")
            return 1
        state = {
            "phone": phone, "jwt": row["uc_jwt"], "rt": row["uc_rt"] or "",
            "jwt_exp": float(row["uc_jwt_exp"] or time.time() + 48 * 3600),
            "rt_exp": float(row["uc_rt_exp"] or time.time() + 30 * 86400),
            "saved_at": time.time(),
        }
        save_state(state, phone)
        say(f"已导入 {phone} 的令牌到 {PROFILES / phone / 'state.json'}")
        say(f"下一步：python -m uting --user {phone} verify")
        return 0

    say("用法：python -m uting account {list|use|import} [手机号]")
    return 1


# --------------------------------------------------------------------------- login
async def cmd_login(args: argparse.Namespace) -> int:
    c = _client(args)
    try:
        token = args.jwt
        if getattr(args, "jwt_stdin", False):
            say("从标准输入读取令牌（粘贴后按 Ctrl+Z 回车 / Ctrl+D）…")
            token = sys.stdin.read()
        if token:
            c.import_token(args.phone or c.phone, token, args.rt or "")
            exp = c.state.get("jwt_exp", 0)
            left = (exp - time.time()) / 3600
            say(f"已保存 JWT（剩余有效期约 {left:.1f} 小时）。")
            say("下一步：python -m uting verify")
        else:
            phone = args.phone or input("U校园手机号（11 位）：").strip()
            pwd = args.password or getpass.getpass("U校园密码（输入不回显）：")
            say("正在向 sso.unipus.cn 登录（和那个网站用的是同一个接口）…")
            await c.login_password(phone, pwd)
            say("登录成功，已保存 jwt/rt（后续会自动续期）。")
            say("下一步：python -m uting verify")
    except UtingError as exc:
        say(f"登录失败：{exc}")
        if exc.kind == "captcha":
            say("\n该账号/网络触发了滑块（code 1506），密码登录走不通。改用这条路：")
            say("  1) 浏览器打开 https://sso.unipus.cn/sso/login 正常登录")
            say("     （登进去后如果跳到 app.unipus.cn/ucloud/undefined，说明进错壳了，回上一步重登）")
            say("  2) 再打开 https://uadaptive.unipus.cn/  （U听说 H5）")
            say("  3) F12 → Network → 过滤 `uls` → 点任意一条 api/uls 请求")
            say("  4) 在 Request Headers 里找 `authorization: eyJ...`，复制 eyJ 后面整串")
            say("  5) 回来执行：python -m uting login --jwt-stdin --phone 你的手机号")
            say("     （PowerShell 里可以：Get-Clipboard | .\\.venv\\Scripts\\python.exe -m uting login --jwt-stdin --phone 你的手机号）")
        return 2
    finally:
        await c.aclose()
    return await cmd_status(args)


# --------------------------------------------------------------------------- status
async def cmd_status(args: argparse.Namespace) -> int:
    c = _client(args)
    try:
        say(BAR)
        say(f"手机号：{c.phone or '（未登录）'}")
        st = await c.week_status("listen")
        if st["type"] in ("grade", "grade_profile", "train_plan"):
            say(f"⚠ 账号还在「听力定级」阶段（type={st['type']}）：先在 App 里做完定级，周任务才会出现。")
        else:
            say(f"本周听力：{st['done']}/{st['target']}　级别：{st['level'] or '—'}　type={st['type']}")
        task = await c.current_task("listen")
        if task:
            say(f"当前任务：taskId={task['taskId']}  ansVersion={task['ansVersion']}  type={task['type']}")
        else:
            say("当前没有下发中的听力任务（可能本周已达标，或服务端还没派卷）。")
        try:
            sp = await c.week_status("speak")
            say(f"（口语：{sp['done']}/{sp['target']}）")
        except UtingError:
            pass
        say(BAR)
    except UtingError as exc:
        say(f"读取失败：{exc}")
        if exc.kind == "auth":
            say("先执行：python -m uting login")
        return 2
    finally:
        await c.aclose()
    return 0


# --------------------------------------------------------------------------- probe
async def cmd_verify(args: argparse.Namespace) -> int:
    """对照工具：把服务端看到的「本周进度 + 历史记录」原样打出来，用来核对手机是否变化。"""
    c = _client(args)
    try:
        st = await c.week_status("listen")
        say(BAR)
        say(f"账号：{c.phone or '（未知）'}　上游：真实 U校园（sso/ucloud/uadaptive）")
        say(f"本周听力：{st['done']}/{st['target']}　type={st['type']}　level={st['level'] or '—'}")
        if st["type"] in ("grade", "grade_profile", "train_plan"):
            say("⚠ 还在定级阶段：周任务不会下发。")
        task = await c.current_task("listen")
        say(f"当前下发任务：{task['taskId'] if task else '无'}"
            + (f"（ansVersion={task['ansVersion']}, type={task['type']}）" if task else ""))
        try:
            hist = await c.history("listen")
        except UtingError as exc:
            say(f"历史列表读取失败：{exc}")
            hist = []
        say(f"\n最近的听力历史（共 {len(hist)} 条）：")
        if not hist:
            say("  （空）—— 说明服务端还没收到任何一次有效交卷")
        for it in hist[:8]:
            tid = it.get("taskId")
            score = it.get("score")
            out = it.get("scoreIsOut")
            when = it.get("submitTime") or it.get("createTime") or ""
            say(f"  · taskId={tid}  score={score}  scoreIsOut={out}  {when}")
        say(BAR)
        say("手机上怎么核对：")
        say("  U校园 App → 听说 → 听力 → 首页「本周总计 x/周目标」；")
        say("  以及「历史训练」里应能看到上面列出的 taskId 对应篇章。")
        say("如果 App 里数字没变，但这里 x 变了：下拉刷新/重进页面即可。")
        say("如果两边都没变：说明那一篇其实没交成功（看 run 的输出与 error 行）。")
        say(BAR)
        return 0
    except UtingError as exc:
        say(f"读取失败：{exc}")
        if exc.kind == "auth":
            say("先执行：python -m uting login")
        return 2
    finally:
        await c.aclose()


async def _confirm_submit(c: Client, task_id: str, before_done: int) -> bool:
    """交卷后核对：本周计数是否 +1、历史里是否有这篇。"""
    ok = False
    done = before_done
    for _ in range(10):
        await asyncio.sleep(3)
        try:
            st = await c.week_status("listen")
        except UtingError as exc:
            say(f"  核对进度失败：{exc}")
            break
        done = int(st["done"] or 0)
        if done > before_done:
            ok = True
            break
    say(f"  服务端本周计数：{before_done} → {done}"
        + ("（已记账 ✅）" if ok else "（**没变** ❌）"))
    try:
        hist = await c.history("listen")
        hit = next((h for h in hist if str(h.get("taskId")) == str(task_id)), None)
        if hit:
            say(f"  历史记录：已出现 taskId={task_id}  score={hit.get('score')}  scoreIsOut={hit.get('scoreIsOut')}")
            ok = ok or bool(hit.get("scoreIsOut")) or hit.get("score") is not None
        else:
            say(f"  历史记录：还没出现 taskId={task_id}（服务端可能仍在处理）")
    except UtingError as exc:
        say(f"  历史核对失败：{exc}")
    if ok:
        say("  手机验证：App → 听力 → 下拉刷新，本周总计应已 +1，历史里能看到这篇。")
    else:
        say("  ⚠ 服务端没有确认这笔交卷。可能原因：① 交到了别的 flow；② 试卷其实已提交过（4013）；")
        say("     ③ 账号在多端同时作答（4021）；④ 这篇不是当前下发任务。请把上面的输出发我。")
    return ok


async def cmd_probe(args: argparse.Namespace) -> int:
    c = _client(args)
    try:
        task = await c.current_task("listen")
        if not task:
            say("没有当前任务：先把本周该做的做完，或等系统派卷。")
            return 1
        say(f"当前任务 taskId={task['taskId']} ansVersion={task['ansVersion']}，正在取卷…")
        value = await c.load_paper(task["taskId"], task["ansVersion"])
        p = paper_mod.normalize(value, task["taskId"])
        say(paper_mod.describe(p))
        raw_path = paper_mod.save_raw(p)
        mat = paper_mod.material(p)
        say(f"\n卷面可读文本：{len(mat)} 字"
            + ("（含原文/字幕，LLM 能据此作答）" if len(mat) > 400
               else "（基本只有题面：这段没原文，只能靠听音频或人工填答案）"))
        if args.show_material:
            say("---- 材料前 800 字 ----")
            say(mat[:800])
            say("---- 结束 ----")
        say(f"原始卷面已保存：{raw_path}")
        if args.template:
            tpl = {
                "taskId": p["taskId"],
                "title": p["title"],
                "_说明": "在 answers 里给每道 probe 题填答案：选择题填字母(A/B/C/AB)，填空/听写填文字，多空用 | 分隔",
                "questions": [
                    {"instanceId": q["instanceId"], "partName": q["partName"],
                     "type": q["type"], "prompt": q["prompt"],
                     "options": [f"{o['value']}. {o['label']}" for o in q["options"]]}
                    for q in p["questions"] if q["action"] == "probe"
                ],
                "answers": {q["instanceId"]: "" for q in p["questions"] if q["action"] == "probe"},
            }
            tpl_path = raw_path.with_suffix(".answers.json")
            tpl_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
            say(f"答题模板已生成：{tpl_path}")
            say("填好后执行：")
            say(f"  python -m uting answers import \"{tpl_path}\" --task {p['taskId']}")
        return 0
    except UtingError as exc:
        say(f"取卷失败：{exc}")
        return 2
    finally:
        await c.aclose()


# --------------------------------------------------------------------------- run
async def _submit_with_fallback(c: Client, task: dict, p: dict, answers: dict,
                                base_user_data: list[dict]) -> tuple[bool, str]:
    """提交；遇到 4295（实例数不匹配）自动补非客观题占位再试一次。"""
    try:
        await c.submit(task["taskId"], task["ansVersion"], p["token"],
                       base_user_data, duration=max(5, len(base_user_data) * 4))
        return True, ""
    except UtingError as exc:
        if exc.code != 4295:
            return False, str(exc)
        say("  4295：服务端要求覆盖全部实例，补上跟读/学习段占位后重试…")
        full, _ = solve.build_user_data(p, answers, include_non_objective=True)
        say(f"  重发 userData {len(full)} 条（原 {len(base_user_data)} 条）")
        try:
            await c.submit(task["taskId"], task["ansVersion"], p["token"],
                           full, duration=max(5, len(full) * 4))
            return True, ""
        except UtingError as exc2:
            return False, str(exc2)


async def cmd_run(args: argparse.Namespace) -> int:
    c = _client(args)
    bank = load_answers()
    llm = solve.llm_config()
    submitted = 0
    try:
        st = await c.week_status("listen")
        done = int(st["done"] or 0)
        target = int(st["target"] or args.target or 3)
        if args.target:
            target = max(target, args.target)
        if st["type"] in ("grade", "grade_profile", "train_plan"):
            say("账号仍在听力定级阶段，先完成定级。")
            return 1
        say(BAR)
        say(f"本周进度 {done}/{target}；目标：再完成 {max(0, target - done)} 篇。"
            + ("（dry-run：不会真交卷）" if not args.submit else "（真实提交）"))
        say(f"题库：{len(bank)} 篇已存答案；LLM：{'已配置' if llm else '未配置'}")
        say(BAR)

        round_no = 0
        seen: set[str] = set()
        while done < target and submitted < args.max_papers:
            round_no += 1
            step(round_no, "读取当前任务")
            task = None
            for _ in range(args.wait_rounds):
                cand = await c.current_task("listen")
                if cand and cand["taskId"] not in seen:
                    task = cand
                    break
                if cand:
                    say(f"  服务端还是上一次那篇（{cand['taskId']}），等它派新卷…")
                else:
                    say("  服务端还没派下一篇，等 5 秒…")
                await asyncio.sleep(5)
            if not task:
                say("  没有新任务了（可能已达标或需要手动在 App 里刷新）。")
                break
            seen.add(task["taskId"])
            say(f"  taskId={task['taskId']} ansVersion={task['ansVersion']} type={task['type']}")
            if task["type"] == "grade":
                say("  这是定级卷，本工具不代做定级，跳过。")
                break

            step(round_no, "取卷")
            try:
                value = await c.load_paper(task["taskId"], task["ansVersion"])
            except UtingError as exc:
                if exc.code == 4021:
                    say("  多端冲突（4021）：先关掉 App/网页里的作答页，10 秒后重试")
                    await asyncio.sleep(10)
                    continue
                raise
            p = paper_mod.normalize(value, task["taskId"])
            say("  " + paper_mod.describe(p).replace("\n", "\n  "))
            raw_path = paper_mod.save_raw(p)
            say(f"  ▶ 这一篇就是你手机 App「听力 → 历史训练」里会新增的那一篇："
                f"{p['title']}（taskId={p['taskId']}）")

            step(round_no, "准备答案")
            answers = solve.bank_lookup(p, bank)
            say(f"  题库命中 {len(answers)} 题")
            probe_total = sum(1 for q in p["questions"] if q["action"] == "probe")
            if len(answers) < probe_total and llm:
                say("  题库不全，调用 LLM 作答…")
                try:
                    ai = await solve.llm_solve(p, llm, paper_mod.material(p))
                    for k, v in ai.items():
                        answers.setdefault(k, v)
                    say(f"  LLM 给出 {len(ai)} 题，合并后 {len(answers)}/{probe_total}")
                except Exception as exc:  # noqa: BLE001
                    say(f"  LLM 失败：{exc}")
            if probe_total == 0:
                say(f"  这张卷没有可提交的客观题（只有学习段/跟读），跳过。原始卷面：{raw_path}")
                break

            user_data, missing = solve.build_user_data(p, answers)
            if missing:
                say(f"  ⚠ 还缺 {len(missing)} 题答案：{missing[:6]}{'…' if len(missing) > 6 else ''}")
                say(f"  卷面已保存：{raw_path}")
                say("  两条路：① 配置 LLM（见 README_UTING.md 的环境变量）自动作答；")
                say("          ② 打开这个 json 自己填，再用 python -m uting answers import <文件>")
                break

            step(round_no, "交卷" if args.submit else "预演提交（未真正交卷）")
            say(f"  userData {len(user_data)} 条")
            if args.verbose or not args.submit:
                say("  预览：" + json.dumps(user_data[:2], ensure_ascii=False)[:600])
            if not args.submit:
                say("  加 --submit 就会真正提交；只有真的提交，手机 App 才会变化。")
                break
            before_done = done
            ok, err = await _submit_with_fallback(c, task, p, answers, user_data)
            if not ok:
                if "4021" in err:
                    say("  4021 多端冲突：关闭其它端作答页后重试")
                    continue
                if "4013" in err:
                    say("  该篇此前已提交过（4013），不重复提交。")
                    break
                say(f"  提交失败：{err}")
                break
            submitted += 1

            step(round_no, "读分")
            score = None
            # 优先用历史列表里的整卷分（scoreIsOut=true 才是最终分）；分项平均只作兜底
            for _ in range(8):
                await asyncio.sleep(3)
                try:
                    hist = await c.history("listen")
                    hit = next((x for x in hist if str(x.get("taskId")) == str(task["taskId"])), None)
                    if hit and hit.get("scoreIsOut") and isinstance(hit.get("score"), (int, float)):
                        score = float(hit["score"])
                        break
                except UtingError:
                    pass
                if score is not None:
                    break
            if score is None:
                try:
                    items = await c.graded(task["taskId"], task["ansVersion"])
                    scores = [float(i["score"]) for i in items
                              if isinstance(i, dict) and isinstance(i.get("score"), (int, float))]
                    if scores:
                        score = round(max(scores), 1)
                except UtingError:
                    pass
            say(f"  得分：{score if score is not None else '评分中（稍后 App 里看）'}")

            solve.bank_store(p, answers, bank)
            save_answers(bank)
            say("  本次答案已存入题库，下次同样的卷直接命中。")

            step(round_no, "核对服务端是否记账")
            await _confirm_submit(c, task["taskId"], before_done)

            st = await c.week_status("listen")
            done = int(st["done"] or 0)
            say(f"  最新进度：{done}/{target}")
            await asyncio.sleep(args.delay)

        say(BAR)
        say(f"结束：本周听力 {done}/{target}，本次成功提交 {submitted} 篇。")
        say(BAR)
        return 0
    except UtingError as exc:
        say(f"执行中断：{exc}")
        if exc.kind == "auth":
            say("先执行：python -m uting login")
        return 2
    finally:
        await c.aclose()


# --------------------------------------------------------------------------- answers
def cmd_answers(args: argparse.Namespace) -> int:
    bank = load_answers()
    if args.action == "list":
        for tid, node in bank.items():
            say(f"{tid}  {node.get('title', '')}  {len(node.get('answers') or {})} 题")
        if not bank:
            say("题库还是空的。")
        return 0
    path = Path(args.file)
    if not path.exists():
        say(f"文件不存在：{path}")
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        say("格式不对：应为 {任务ID: {'answers': {...}}} 或 {'answers': {...}}")
        return 1
    if "answers" in data and isinstance(data["answers"], dict) and args.task:
        bank[str(args.task)] = {"title": "", "answers": data["answers"], "updated": time.time()}
    else:
        for tid, node in data.items():
            if isinstance(node, dict) and isinstance(node.get("answers"), dict):
                bank[str(tid)] = node
            elif isinstance(node, dict):
                bank[str(tid)] = {"title": "", "answers": node, "updated": time.time()}
    save_answers(bank)
    say(f"已导入，题库现有 {len(bank)} 篇。")
    return 0


# --------------------------------------------------------------------------- doctor
async def cmd_do(args: argparse.Namespace) -> int:
    """强制做指定的某一篇（忽略"本周已达标"的判断），用于补做/预做下一篇。"""
    c = _client(args)
    bank = load_answers()
    llm = solve.llm_config()
    task_id = (args.task or "").strip()
    try:
        if not task_id:
            t = await c.current_task("listen")
            if not t:
                say("服务端没有下发任务。")
                return 1
            task_id = t["taskId"]
            ans_version = int(t["ansVersion"])
        else:
            ans_version = int(args.ansVersion or 1)

        say(BAR)
        say(f"强制处理 taskId={task_id} ansVersion={ans_version}")
        say(BAR)
        value = await c.load_paper(task_id, ans_version)
        p = paper_mod.normalize(value, task_id)
        say(paper_mod.describe(p))
        paper_mod.save_raw(p)

        answers = solve.bank_lookup(p, bank)
        probe_total = sum(1 for q in p["questions"] if q["action"] == "probe")
        say(f"\n题库命中 {len(answers)}/{probe_total}")
        if len(answers) < probe_total and llm:
            ai = await solve.llm_solve(p, llm, paper_mod.material(p))
            for k, v in ai.items():
                answers.setdefault(k, v)
            say(f"LLM 补充后 {len(answers)}/{probe_total}")

        user_data, missing = solve.build_user_data(p, answers)
        if missing:
            say(f"⚠ 缺 {len(missing)} 题答案：{missing[:6]}")
            say("先补题库：python -m uting answers import <模板> --task " + task_id)
            return 1
        say(f"userData {len(user_data)} 条")

        if not args.submit:
            say("预演模式：加 --submit 才真正提交。")
            return 0
        before_done = int((await c.week_status("listen"))["done"] or 0)
        ok, err = await _submit_with_fallback(c, {"taskId": task_id, "ansVersion": ans_version}, p, answers, user_data)
        if not ok:
            say(f"提交失败：{err}")
            return 2
        solve.bank_store(p, answers, bank)
        save_answers(bank)
        say("已提交，答案已入库。")
        await _confirm_submit(c, task_id, before_done)
        return 0
    except UtingError as exc:
        say(f"失败：{exc}")
        return 2
    finally:
        await c.aclose()


async def cmd_llm(args: argparse.Namespace) -> int:
    """DeepSeek 设置：show / set / test（与网页共用 ~/.uting/llm.json）。"""
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app import llm as llm_mod

    action = args.action
    if action == "show":
        cfg = llm_mod.load_config()
        say(f"存储位置：{llm_mod.CFG_PATH}")
        say(f"Key：{llm_mod.mask(cfg.get('api_key', '')) or '（未设置）'}")
        say(f"模型：{cfg.get('model')}　Base：{cfg.get('base_url')}")
        return 0
    if action == "set":
        cfg = llm_mod.save_config(args.key or None, args.model or None, args.base or None)
        say(f"已保存：Key={llm_mod.mask(cfg['api_key'])} 模型={cfg['model']} Base={cfg['base_url']}")
        say("网页（http://127.0.0.1:8710）和命令行都会用它")
        return 0
    if action == "test":
        if not llm_mod.configured():
            say("还没设置 Key：python -m uting llm set --key sk-...")
            return 1
        say("正在测试 DeepSeek 连接…")
        try:
            r = await llm_mod.test_key()
            say(f"连接正常（{r['ms']}ms）：{r.get('reply', '')[:60]}")
            return 0
        except llm_mod.LLMError as exc:
            say(f"失败：{exc}")
            return 2
    say("用法：python -m uting llm {show|set|test} [--key sk-...] [--model deepseek-chat]")
    return 1


async def cmd_token(args: argparse.Namespace) -> int:
    """打印当前 JWT（并尝试复制到剪贴板），方便粘到网页控制台。"""
    c = _client(args)
    await c.aclose()
    if not c.jwt:
        say("还没有登录：先 python -m uting login")
        return 2
    say(f"手机号：{c.phone}")
    say(f"有效期至：{time.strftime('%Y-%m-%d %H:%M', time.localtime(c.state.get('jwt_exp', 0)))}")
    if not args.show:
        say(f"JWT 长度 {len(c.jwt)}，已尝试复制到剪贴板（加 --show 可打印出来）")
        try:
            import subprocess

            subprocess.run("clip", input=c.jwt.encode(), check=False, shell=True)
            say("已复制。去网页控制台 → 登录框下面「或用 JWT 登录」→ 粘贴。")
        except OSError:
            say("复制失败，请加 --show 手动复制")
    else:
        say(c.jwt)
    return 0


async def cmd_doctor(args: argparse.Namespace) -> int:
    from .client import HOME, STATE_FILE

    say(BAR)
    say("环境检查")
    say(f"  状态文件：{STATE_FILE}（{'存在' if STATE_FILE.exists() else '还没有'}）")
    say(f"  卷面目录：{HOME / 'papers'}")
    llm = solve.llm_config()
    say(f"  LLM：{'已配置 ' + llm['model'] if llm else '未配置（只用题库作答）'}")
    c = _client(args)
    try:
        say(f"  JWT：{'有' if c.jwt else '无'}")
        st = await c.week_status("listen")
        say(f"  周进度接口：OK（{st['done']}/{st['target']}，type={st['type']}）")
        task = await c.current_task("listen")
        say(f"  当前任务：{task['taskId'] if task else '无'}")
    except UtingError as exc:
        say(f"  上游检查失败：{exc}")
        return 2
    finally:
        await c.aclose()
    say(BAR)
    return 0


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m uting", description="U听说听力作业自动完成")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user", default="", help="指定账号（手机号），默认用当前激活的账号")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", parents=[common], help="登录（密码或粘贴 JWT）")
    p.add_argument("--phone", default="")
    p.add_argument("--password", default="")
    p.add_argument("--jwt", default="", help="浏览器里复制的 authorization 值")
    p.add_argument("--jwt-stdin", action="store_true", help="从标准输入读令牌（配合 Get-Clipboard）")
    p.add_argument("--rt", default="", help="可选 refresh token")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", parents=[common], help="查看本周进度")
    p.add_argument("--target", type=int, default=0)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("probe", parents=[common], help="取一篇卷并打印结构（不交卷）")
    p.add_argument("--target", type=int, default=0)
    p.add_argument("--template", action="store_true", help="同时生成可填写的答题模板")
    p.add_argument("--show-material", action="store_true", help="打印抽出的原文/字幕片段")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("run", parents=[common], help="自动完成本周剩余篇数")
    p.add_argument("--submit", action="store_true", help="真正交卷（默认只预演）")
    p.add_argument("--target", type=int, default=0, help="本周目标篇数（默认用服务端给的 weekFrequency）")
    p.add_argument("--max-papers", type=int, default=6)
    p.add_argument("--wait-rounds", type=int, default=6)
    p.add_argument("--delay", type=float, default=5.0)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("answers", parents=[common], help="题库管理")
    p.add_argument("action", choices=["list", "import"])
    p.add_argument("file", nargs="?", default="")
    p.add_argument("--task", default="", help="导入单篇答案时指定 taskId")
    p.set_defaults(func=cmd_answers)

    p = sub.add_parser("account", parents=[common], help="多账号管理：list / use / import")
    p.add_argument("action", choices=["list", "use", "import"])
    p.add_argument("phone", nargs="?", default="")
    p.set_defaults(func=cmd_account)

    p = sub.add_parser("llm", parents=[common], help="DeepSeek 设置：show / set / test")
    p.add_argument("action", choices=["show", "set", "test"], nargs="?", default="show")
    p.add_argument("--key", default="")
    p.add_argument("--model", default="")
    p.add_argument("--base", default="")
    p.set_defaults(func=cmd_llm)

    p = sub.add_parser("doctor", parents=[common], help="环境与连通性检查")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("verify", parents=[common], help="对照服务端进度与历史，确认交卷是否真的生效")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("token", parents=[common], help="把 JWT 复制到剪贴板（用于网页控制台登录）")
    p.add_argument("--show", action="store_true", help="直接打印完整 JWT")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("do", parents=[common], help="强制做指定的一篇（忽略本周达标判断）")
    p.add_argument("--task", default="", help="taskId，留空则用当前下发的那篇")
    p.add_argument("--ansVersion", type=int, default=0)
    p.add_argument("--submit", action="store_true")
    p.set_defaults(func=cmd_do)

    args = ap.parse_args(argv)
    if args.func in (cmd_answers,):
        return args.func(args)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        say("\n已中断。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
