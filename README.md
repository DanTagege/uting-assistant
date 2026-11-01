# U听说助手 · U听力周训练托管

<p align="center">
  <img src="app/static/icons/icon-192.png" width="96" alt="U听说助手">
</p>

<p align="center">
  登录 U校园 → 读本周进度 → 取卷 → 题库/大模型作答 → <b>按可调时长模拟真人节奏</b> → 回读成绩
  <br/>
  <sub>FastAPI + 原生网页控制台 + 命令行 + PWA/iOS 壳 + exe 打包与加固</sub>
</p>

---

## 目录

- [这是什么](#这是什么)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置项](#配置项)
- [使用方式](#使用方式)
- [工作原理](#工作原理)
- [目录结构](#目录结构)
- [打包分发](#打包分发)
- [数据与隐私](#数据与隐私)
- [常见问题](#常见问题)
- [开发](#开发)
- [免责声明](#免责声明)
- [License](#license)

---

## 这是什么

U校园「U听说 / U听力」每周要完成若干篇自适应训练（听力卷）。本项目把这套流程做成本地工具：

- 用**自己的账号**登录（`sso.unipus.cn`），登录态只存本机
- 读本周进度（`1/3`）与当前下发的那一篇
- 取卷（`loadPaper`）→ 解析题目（单选 / 一题多问 / 排序 / 跟读）
- 答案三层来源：**本地题库** → **DeepSeek 读原文作答** → 网页手填
- 按你设置的**每篇用时**分阶段作答（多次 `saveSnapshot`），最后一次性交卷，`duration` 是真实耗时
- 回读成绩与历史，网页实时显示进度条

> 全部数据在本机：SQLite（`data/ucampus.db`）+ `~/.uting/`。没有中间服务器，没有远程上报。

## 功能特性

| 模块 | 能力 |
|---|---|
| 登录 | SSO 手机号 + 密码（AES-128-CBC 加密体）；或粘贴浏览器 JWT 绕过滑块；RT 自动续期 |
| 周训练 | 进度 / 取卷 / 作答 / 交卷 / 读分，全链路真实接口 |
| 答案 | 题库（`paperHash` × `ansVersion` 版本化）、DeepSeek 读卷面原文作答、网页「取卷/补答案」 |
| 多小题 | 一题多问时 `children` 段数严格对齐；**排序题按选项 `value` 直接排出正确顺序** |
| 作答时长 | 每篇 0–60 分钟（默认 12）+ 随机抖动（默认 ±20%），分 7–12 段快照，交卷时长真实 |
| 进度条 | `GET /api/training/jobs/{id}` 返回 `progress{percent,phase,elapsed,total,eta}`，前端 700ms 刷新 |
| 听力定级 | 未定级账号可选目标级别：预览（不提交）→ 执行；按答对率逼近级别区间，映射表可调并自动回写实测 |
| 多账号 | `~/.uting/accounts/<手机号>/`，`--user` 切换；**题库全局共享**（同卷复用） |
| 控制台 | 单文件原生 JS：任务、日志、答题记录、LLM 设置、真实提交开关、PWA 可安装 |
| 命令行 | `login / verify / probe / run / do / account / llm / answers / token / doctor` |
| 分发 | PyInstaller 单文件、Nuitka 原生编译（无 pyc）、可选 C# 加固壳 |
| iOS | PWA（Safari 添加到主屏幕）+ Capacitor 工程 + GitHub Actions 出未签名 ipa |

---

## 快速开始

```powershell
git clone <你的仓库地址> uting-assistant
cd uting-assistant

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env      # 按需修改
.\.venv\Scripts\python.exe run.py
```

打开 <http://127.0.0.1:8710/>，用 U校园 手机号 + 密码登录。

首推流程：

1. **登录** → 看「本周已完成 x/y」
2. 顶部「**真实提交**」勾上（不勾是预演，只打印不交卷）
3. 「**每篇用时**」填 10–15 分钟（模拟真人节奏，建议开）
4. 「**DeepSeek 自动答题设置**」填 `sk-...`（题库没有的卷靠它读原文）
5. 任务卡点「**自动答题**」→ 看进度条 → 完成后回读成绩

> 手机使用：`.env` 里 `UC_HOST=0.0.0.0`，手机访问 `http://<电脑局域网IP>:8710/`，Safari → 添加到主屏幕。

---

## 配置项

`.env`（都可选，有默认值）：

| 键 | 默认 | 说明 |
|---|---|---|
| `UC_HOST` | `127.0.0.1` | 改 `0.0.0.0` 供手机/局域网访问 |
| `UC_PORT` | `8710` | 端口 |
| `UC_UPSTREAM` | `mock` | `unipus` = 真实 U校园；`mock` = 离线假上游（演示/自检） |
| `UC_REAL_SUBMIT` | `0` | 真实交卷开关的**初值**（网页开关优先，存库） |
| `UC_SECRET_KEY` | 自动生成 | 本平台 JWT 签名密钥 → `data/secret.key` |
| `UC_JWT_TTL` / `UC_RT_TTL` | 2h / 30d | 本平台访问 / 刷新令牌 |
| `UC_CSRF` | `1` | 非 GET 必须带 `X-UCampus-Request: 1` |
| `UC_ADMIN_USERS` | — | 逗号分隔，首次登录即授权管理员 |
| `UC_PARALLELISM` | `8` | 任务 worker 并发 |
| `UC_WEEKDAY/HOUR/MINUTE` | 0/12/0 | 托管排班默认时间 |
| `UC_WEEKLY_QUOTA` | `5` | mock 上游的周配额（真实上游以服务端为准） |

敏感数据不在 `.env`：

- DeepSeek Key → `~/.uting/llm.json`（0600，网页与命令行共用）
- U校园 令牌 → `~/.uting/accounts/<手机号>/state.json`（0600）

---

## 使用方式

### 网页控制台

| 区块 | 说明 |
|---|---|
| 顶部横幅 | 绿=真实提交已开启 / 黄=预演 / 红=mock 假数据 |
| 开关行 | 真实提交、每篇用时（分钟）、随机抖动（%） |
| 统计卡 | 本周听力 `x/y`、历史最高分 |
| 自适应训练 | 当前下发卷：有答案→「自动答题」；无答案→「AI 作答」/「取卷/补答案」 |
| 老师布置 | U口语周训练（口语链路未实现，仅展示） |
| 训练历史 | `taskId / 得分 / 是否出分` |
| 答题记录 | 本平台任务队列与日志 |
| 执行日志 | 实时日志 + **作答进度条**（百分比 / 已用 / 预计剩余） |
| 听力定级 | 未定级时出现：目标级别、答对率、预览、执行（需勾确认） |
| DeepSeek | Key（掩码）、模型、保存、测试连接 |

### 命令行

```powershell
.\.venv\Scripts\python.exe -m uting login                 # 手机号+密码，或 --jwt 粘贴
.\.venv\Scripts\python.exe -m uting verify                # 本周进度 + 历史（唯一可信口径）
.\.venv\Scripts\python.exe -m uting probe --template      # 取卷 + 生成答题模板
.\.venv\Scripts\python.exe -m uting run --submit          # 做到本周达标
.\.venv\Scripts\python.exe -m uting do --task <id> --submit  # 强制做指定一篇
.\.venv\Scripts\python.exe -m uting answers list
.\.venv\Scripts\python.exe -m uting llm test
.\.venv\Scripts\python.exe -m uting doctor
.\.venv\Scripts\python.exe -m uting token --show          # 打印 JWT（粘到网页）
```

### 多账号

```powershell
.\.venv\Scripts\python.exe -m uting account list
.\.venv\Scripts\python.exe -m uting account use 138xxxxxxxx
.\.venv\Scripts\python.exe -m uting account import 138xxxxxxxx   # 从网页登录态导入令牌
.\.venv\Scripts\python.exe -m uting verify --user 139xxxxxxxx
.\.venv\Scripts\python.exe -m uting run --submit --user 139xxxxxxxx
```

- 档案：`~/.uting/accounts/<手机号>/state.json`
- 题库全局共享：`~/.uting/answers.json`（A 号做过的卷，B 号遇到同篇直接命中）
- 合并历史题库：`python tools/merge_answer_banks.py`

### 听力定级

未定级账号（`getUserStatus.type == "grade"`）会显示定级卡：

1. 选目标级别（S12–S16）→ 自动填映射表里的答对率（可手改）
2. 「**预览（不提交）**」：真取卷 + 模型作答，返回题数 / 覆盖面 / 样例
3. 勾「我确认定级只能做一次」→「**开始定级**」

- 服务端只回级别、不回分数，所以用"控制答对率"逼近目标级别
- 映射表在 `data/placement_map.json`，每次执行会把实测写入 `observed`，越用越准

### 作答时长模拟

```
取卷 → 出答案 → 按目标时长切片 → 每片 saveSnapshot → 最后 submitAnswer(duration=真实耗时)
```

日志示例：

```
INFO 进入模拟作答：目标时长 723s（可在页面调整）
INFO snapshot 1/7 (14%) … 7/7 (100%)
INFO 作答完成，用时 723s，提交中…
```

填 0 即关闭（立即交卷）。定级卷不受此项影响。

---

## 工作原理

### 接口链路

| 用途 | 方法 & 路径 | 主机 |
|---|---|---|
| 登录 | `POST /sso/0.1/sso/cip/login`（账号密码 AES-128-CBC） | `sso.unipus.cn` |
| 续期 | `POST /sso/4.0/sso/refresh_jwt` | `sso.unipus.cn` |
| 周进度 | `GET /api/uls/user/getUserStatusForApp?flowType=listen` | `ucloud.unipus.cn` |
| 当前任务 | `GET /api/uls/user/getUserStatus?flowType=listen` | `uadaptive.unipus.cn` |
| 取卷 | `POST /api/uls/user/loadPaper {taskId, ansVersion}` | `uadaptive` |
| 作答快照 | `POST /api/uls/user/saveSnapshot` | `uadaptive` |
| 交卷 | `POST /api/uls/user/submitAnswer` | `uadaptive` |
| 读分 | `POST /api/uls/user/loadGradedQuestions` | `uadaptive` |
| 历史 | `POST /api/uls/user/getHistoryTrainingTasks` | `ucloud` |
| 定级报告 | `POST /api/uls/report/levelReport` | `uadaptive` |

约定：`Authorization` 是**裸 JWT**（不加 `Bearer`）；`getUserStatusForApp` 需 `u-app-id: 116`；`loadPaper` 的 `token` 必须带回 `submitAnswer`，否则 `4021`。

### 试卷结构

```
paperJson.chr[]                      ← 章：lead-in / pre-listening / while / Post-listen
  └ chr[]                            ← 题目单元
      ├ q_qinstid    : 19 位雪花 id（JSON 数字，必须当字符串）
      ├ q_idx        : 题号
      ├ nm           : 听力_单选_客观题 / 基础_单选_客观题 / 单词跟读 / 音频学习
      ├ q_template_name : single-choice_objective / vocabulary / sequence_objective / outline
      ├ is_objective : 是否客观题
      └ data
          ├ children[] : 子题（一题多问 / 排序题的每个位置）
          ├ options[]  : 常在最深 children 里，text 带 HTML
          ├ contents[] : 原文/字幕（约 1 万字/篇）+ 音频路径
          └ replyType  : singlechoice / outline / sequence
```

排序题（`sequence_objective`）特殊点：**选项的 `value` 就是正确位置**（`{name:"A", value:"4"}` = A 排第 4 步），由代码直接排出 `["C","B","D","A"]`，不交给模型。

### 提交格式

```jsonc
{
  "taskId": "...", "ansVersion": 1, "token": "<loadPaper token>", "duration": 723,
  "userData": [
    {"instanceId": "1989...",
     "answer": "{\"value\":[],\"children\":[{\"value\":[\"C\"]},{\"value\":[\"B\"]}]}",
     "answerVersion": 1, "context": "{\"state\":\"done\"}", "contextVersion": 1}
  ]
}
```

- `answer` / `context` 都是 **JSON 字符串**
- 覆盖不全 → `4295`：`children` 总数必须等于卷面叶子数
- `4021` = token 冲突，需重新 `loadPaper`

### 错误码

| code | 含义 | 处理 |
|---|---|---|
| `0` / `1` / `200` | 成功（不同接口口径不同） | — |
| `1506` | SSO 要滑块 | 浏览器登录后导入 JWT |
| `4012` | 尚未定级 | 先做定级 |
| `4013` | 训练已提交 | 跳过，不重复交 |
| `4021` | 多端同时作答 | 关掉那篇，重新取卷 |
| `4295` | 小题数与卷面不符 | 检查 `children` 覆盖 |

---

## 目录结构

```
.
├── app/                      后端
│   ├── main.py               应用、中间件、静态与 PWA 路由
│   ├── api.py                全部 HTTP 接口
│   ├── jobs.py               任务队列、状态机、节奏/快照、进度
│   ├── engine.py             答案组装（skip/oral/probe，严格模式拒交）
│   ├── answer_bank.py        题库（paperHash × ansVersion）
│   ├── llm.py                DeepSeek 客户端（容错 JSON 解析、reasoner 预算）
│   ├── placement.py          定级：级别映射、排序题、答对率裁剪
│   ├── runtime.py            运行时开关（真实提交、作答时长、抖动）
│   ├── scheduler.py          托管排班（周槽位、并发、报表）
│   ├── security.py           JWT/RT、会话、CSRF
│   ├── db.py                 SQLite
│   ├── upstream/             上游适配器
│   │   ├── unipus.py         真实 U校园
│   │   ├── mock.py           离线假上游
│   │   ├── paper_parse.py    卷面解析（按真实结构）
│   │   └── unipus_ai.py      模板型适配器（自定义端点）
│   └── static/               控制台（单文件）、PWA manifest、图标、sw.js
├── uting/                    命令行工具（client / paper / solve / cli）
├── tools/                    维护脚本（打包、图标、探针、题库合并…）
├── docs/                     上游接口说明
├── ios-app/                  Capacitor iOS 工程 + CI 工作流
├── protector/                exe 加固外壳（C#）
├── pack_entry.py             打包入口（PyInstaller / Nuitka）
├── launcher.cs               轻量启动器（csc 编译）
├── run.py
├── requirements.txt
└── .env.example
```

---

## 打包分发

### PyInstaller

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --onefile --console --name UtingStandalone ^
  --icon app.ico --add-data "app/static;app/static" ^
  --collect-submodules app --collect-submodules uvicorn ^
  pack_entry.py
```

产物 `dist\UtingStandalone.exe`（约 19 MB，内置 Python 与依赖，双击启动并开浏览器）。

### Nuitka 原生编译

门槛最高的方案：Python 编成 C，产物中**没有 pyc**。

```powershell
.\.venv\Scripts\python.exe -m pip install nuitka
.\.venv\Scripts\python.exe -m nuitka --onefile --msvc=latest --assume-yes-for-downloads ^
  --windows-console-mode=force --windows-icon-from-ico=app.ico ^
  --include-package=app --include-package=uvicorn --include-package=fastapi ^
  --include-package=starlette --include-package=pydantic --include-package=anyio --include-package=httpx ^
  --include-data-dir="app/static=app/static" --python-flag=-OO ^
  --nofollow-import-to=tkinter,unittest,tests,pydoc ^
  pack_entry.py
```

> 别把 `email` 放进 `--nofollow-import-to`，uvicorn 依赖它。

### 加固外壳

`protector/Program.cs`：反调试（`IsDebuggerPresent` / `CheckRemoteDebuggerPresent` / `NtQueryInformationProcess` + 定时复检）、防分析工具、反虚拟机、载荷 AES-256-CBC 加密（密钥由本机 MachineGuid 派生）、运行期 SHA-256 校验、随机名释放并清理。

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_protected.ps1 `
  -Inner ".\dist\UtingNative.exe" -Out ".\Uting加固版.exe"
```

> Python 应用无法做到"无法彻底防住分析"，这套只是把成本从分钟级抬到小时级；加固版**只在本机可运行**。

### iPhone / PWA

- **PWA**：Safari 打开 `http://<局域网IP>:8710/` → 分享 → 添加到主屏幕
- **原生壳**：`ios-app/`（Capacitor）：`npm install && npx cap add ios && npx cap open ios`
- CI：`.github/workflows/ios-build.yml`（macOS runner 出未签名 ipa，SideStore/AltStore 侧载）
- 细节见 [README_IOS.md](README_IOS.md)

---

## 数据与隐私

| 数据 | 位置 | 说明 |
|---|---|---|
| 账号令牌 | `data/ucampus.db` + `~/.uting/accounts/*/state.json` | 本地明文，**不要提交、不要外发** |
| DeepSeek Key | `~/.uting/llm.json`（0600） | 页面只回显掩码 |
| 题库 | `~/.uting/answers.json` + `data/ucampus.db` | 可安全分享（不含身份信息） |
| 日志 | `data/logs/` | 已 gitignore |

`.gitignore` 已覆盖 `data/`、`*.db`、`.env`、`secret.key`、`llm.json`、`*.exe`、`dist/`、`build*/`、`__pycache__`、`*.har` 等。

提交前自查：

```powershell
git status --short
git grep -nE "sk-[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]{20,}" -- . ':!docs'   # 应为空
```

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 登录提示「SSO 要求滑块验证」 | 风控触发 `1506` | 浏览器登录 → 复制 `authorization` → 网页「或用 JWT 登录」 |
| 点答题报「已拒交」 | 题库缺答案且未配模型 | 配 DeepSeek，或「取卷/补答案」手填 |
| `4295 作答小题数与试卷不匹配` | `children` 覆盖数不对 | 更新到最新代码（解析器已按真实结构对齐） |
| `4021 请勿多设备同时作答` | 手机/网页同时开着同一篇 | 关掉一端，重新取卷 |
| 页面一直 0/3 不变 | 服务端停在 `train_plan` 未派卷 | 手机 App 打开一次定级报告后再刷新 |
| `deepseek-reasoner` 返回空 | 思考 token 吃掉 `max_tokens` | 换 `deepseek-chat`（已自动加大预算并重试） |
| 手机打不开 `192.168.x.x:8710` | 只听 127.0.0.1 或被防火墙拦 | `UC_HOST=0.0.0.0` + 放行专用网络 |
| exe 启动闪退 | 缺 `.venv` 或路径不对 | 用独立版 exe，或把启动器放项目根目录 |

---

## 开发

```powershell
# 离线自检（不联网）
.\.venv\Scripts\python.exe tools\selftest.py           # 解析/组包/严格拒交/AES
.\.venv\Scripts\python.exe tools\selftest_uting.py     # 卷面 → 题目 → userData

# 只读探针
.\.venv\Scripts\python.exe tools\probe_upstream.py
.\.venv\Scripts\python.exe tools\check_account.py 138xxxxxxxx
.\.venv\Scripts\python.exe tools\token_status.py

# 端到端（mock 上游）
$env:UC_UPSTREAM='mock'; .\.venv\Scripts\python.exe tools\check_pipeline.py --username <账号>
```

设计约定：业务层只依赖 `Upstream` 协议；新增上游能力优先写进 `app/upstream/`；所有"可调行为"进 `app/runtime.py`（落 kv，改完即生效）。

---

## 免责声明

- 本工具仅用于**本人账号**的学习流程自动化，请勿用于代刷、接单或违反平台与学校规定的用途。
- 上游接口可能随时变更，本项目不保证持续可用；使用风险自负。
- 仓库不含任何账号数据或密钥；请勿把 `data/`、`~/.uting/` 提交或外发。

---

## License

[MIT](LICENSE)
