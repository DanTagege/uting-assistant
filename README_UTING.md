# U听说 每周听力作业 —— 3 条命令搞完

这个工具只做一件事：**把 U听说「听力」本周该做的 N 篇（你的账号是 3 篇）做完并交卷**。

`app/` 是可选的网页控制台（用于看进度与手动触发），不用它也能用命令行。
真正干活的是 `uting/` 这个命令行工具，不启动服务、不开网页。

---

## 零、先搞清楚一件事：你之前为什么"手机上没变化"
网页那套默认 `UC_UPSTREAM=mock`，**题目和成绩都是本地生成的假数据**（`视听说练习 1F6A` 这种名字就是造出来的）。
你在网页点"自动答题"→ 只是往我本地的 SQLite 写了条记录，**从来没碰过 U校园服务器**。

真实模式必须同时满足两条：

```powershell
$env:UC_UPSTREAM='unipus'    # 打真实网关
$env:UC_REAL_SUBMIT='1'      # 真交卷（否则只预演）
```

而且真实模式需要你的 JWT（浏览器登录后粘进来），见下面第一节。

网页控制台现在也会在顶部用红字提示当前是不是 mock——看到红字就说明"手机上不会有任何变化"。

### "自适应训练"到底对应哪个接口

U听说 App 里「听力 → 自适应训练」= 真实接口的这条链：

| App 里的东西 | 接口 | 说明 |
|---|---|---|
| 首页「本周总计 x/3」 | `GET ucloud/api/uls/user/getUserStatusForApp?flowType=listen` | `weekDoneTaskCount` / `weekFrequency` |
| 「继续训练 / 开始训练」下发的那一篇 | `GET uadaptive/api/uls/user/getUserStatus?flowType=listen` | 返回 `taskId` / `ansVersion`，`type=train` 就是自适应周训练 |
| 打开这篇卷子 | `POST loadPaper {taskId, ansVersion}` | 拿 `token` + `paperJson` |
| 交卷 | `POST submitAnswer {taskId, ansVersion, token, userData[]}` | 服务端按这个记账，手机上的 x 才会 +1 |
| 历史训练 / 是否出分 | `POST getHistoryTrainingTasks {flowType:"listen"}` | `scoreIsOut=true` 才算出分 |

`type=grade` 是**定级**（不是周训练），本工具不代做。

---

## 一、三步

```powershell
cd D:\Ucampus
.\.venv\Scripts\python.exe -m uting login          # 1) 登录
.\.venv\Scripts\python.exe -m uting verify         # 2) 看服务端真实进度（几篇 / 哪一篇 / 历史）
.\.venv\Scripts\python.exe -m uting probe --template   # 3) 取一篇卷，看它长什么样
```

确认没问题后：

```powershell
.\.venv\Scripts\python.exe -m uting run            # 预演：只组答案，不交卷
.\.venv\Scripts\python.exe -m uting run --submit   # 真交卷，一直做到本周达标
.\.venv\Scripts\python.exe -m uting verify         # 复查：计数有没有 +1、历史里有没有这篇
```

---

## 二、每一步在干什么

### 多账号（2026-10-26 新增）

一个手机号一个档案，命令都支持 `--user` 临时指定：

```powershell
.\.venv\Scripts\python.exe -m uting account list                 # 看有哪些号、令牌还有多久
.\.venv\Scripts\python.exe -m uting account use 18321531958      # 切成默认账号
.\.venv\Scripts\python.exe -m uting account import 18321531958   # 从网页控制台的登录态里取令牌
.\.venv\Scripts\python.exe -m uting verify --user 16639373818    # 临时用另一个号
.\.venv\Scripts\python.exe -m uting run --submit --user 18321531958
```

- 档案：`~/.uting/accounts/<手机号>/state.json`
- 当前账号：`~/.uting/active`
- **题库全局共享**：`~/.uting/answers.json`，A 号答过的卷，B 号遇到同一篇直接命中
- 合并历史题库：`python tools/merge_answer_banks.py`

### 网页端（换号后不用再找命令行）

1. 右上角 **切换账号** → 退出并回到登录页，换号登录；
2. 页面自动拉这个号的真实进度与当前任务（标题旁显示 `账号 183****1958`，避免看串）；
3. 有答案的卷 → 直接「自动答题」；没答案的卷 → **取卷/补答案**：
   - 弹窗里直接渲染这道卷的所有客观题（含一个实例多道小题的情况），
   - 选完点「保存到题库」→ 按钮立刻变成「自动答题」。


### `login`

两条路，走哪条取决于会不会遇到滑块：

**A. 密码登录（和那个网站后端用的同一个接口）**

```powershell
.\.venv\Scripts\python.exe -m uting login --phone 138xxxxxxxx
# 会提示输入密码（不回显）
```

它直接调 `POST https://sso.unipus.cn/sso/0.1/sso/cip/login`，请求体里手机号/密码是 AES 加密的，
拿回 `jwt`（48 小时）+ `rt`（自动续期），存在 `C:\Users\<你>\.uting\state.json`。

如果返回 `code 1506 需要滑块`，说明这条路被风控挡了，走 B。

**B. 粘贴浏览器里的令牌（绕开滑块）**

1. 浏览器打开 <https://sso.unipus.cn/sso/login> —— 标题「Unipus-欢迎登录」才是登录页；
2. 登录成功后打开 <https://uadaptive.unipus.cn/>（U听说 H5）；
3. F12 → Network → 过滤框输入 `uls` → 点任意一条 `api/uls/...` 请求；
4. 在 **Request Headers** 里找 `authorization: eyJ...`，复制 `eyJ` 开头的整串（很长，三段用点分隔）；
5. 回来粘进去：

```powershell
Get-Clipboard | .\.venv\Scripts\python.exe -m uting login --jwt-stdin --phone 138xxxxxxxx
# 或者：-m uting login --phone 138xxxxxxxx --jwt "eyJ..."
```

> ⚠️ 千万别从 `app.unipus.cn/ucloud/...` 进。那是老版网页 U校园的壳，要 CAS 票据；
> 票据拿不到时前端会把 `undefined` 拼进 URL，于是就出现
> `404 Not Found — http://app.unipus.cn/ucloud/undefined`。这**不是登录失败**，是进错了门。
> U听说（U听力/U口语）走的是 `uadaptive.unipus.cn` + `ucloud.unipus.cn` 两个域名。

### `status`
输出类似：

```
手机号：138xxxxxxxx
本周听力：1/3　级别：B1　type=train
当前任务：taskId=1900000000000000001  ansVersion=1  type=train
```

- `1/3` = 本周已完成 1 篇，目标 3 篇 → 还要做 2 篇。
- `type=grade` = 你还在**定级**阶段，周任务还没出来；先在 App 里把听力定级做完。

### `probe --template`
把服务端下发的**这一篇卷子**取回来（不交卷），翻译成人话：

```
卷面：Unit 3 校园生活（taskId=…，token=有）
  共 14 项：客观题 10，跟读/口语 2，学习段 2
  [Part 1 Listening]
    · #1 [singlechoice|probe] What is the speaker's major? ｜ A.History B.Biology
    · #2 [audio-learn|learn] 听音频学习
```

同时生成两个文件：
- `~/.uting/papers/<taskId>.json` —— 原始卷面（结构看不懂就把它发给我）
- `~/.uting/papers/<taskId>.answers.json` —— **可填写的答题模板**（只有客观题）

### `run --submit`
循环执行，直到本周达标：

```
[1] 读取当前任务 → taskId=…
[1] 取卷 → 14 项（客观题 10）
      ▶ 这一篇就是你手机 App「听力 → 历史训练」里会新增的那一篇：Unit 3 …（taskId=…）
[1] 准备答案 → 题库命中 8 题，LLM 补 2 题
[1] 交卷 → userData 10 条
[1] 读分 → 得分 90
[1] 核对服务端是否记账
      服务端本周计数：1 → 2（已记账 ✅）
      历史记录：已出现 taskId=…  score=90  scoreIsOut=true
      手机验证：App → 听力 → 下拉刷新，本周总计应已 +1
    最新进度：2/3
```

如果这里出现 **「服务端本周计数：1 → 1（没变 ❌）」**，就说明这次交卷没被记账，
输出里会列出可能原因（交到别的 flow / 已提交过 4013 / 多端冲突 4021 / 不是当前任务）。把那段发我。

### `verify`
任何时候都可以跑，用来对照"服务端认为你做了多少"：

```powershell
.\.venv\Scripts\python.exe -m uting verify
```

```
本周听力：1/3　type=train　level=B1
当前下发任务：taskId=1900000000000000001（ansVersion=1, type=train）
最近的听力历史（共 3 条）：
  · taskId=1900000000000000001  score=90  scoreIsOut=true  2026-10-20 21:13
  · taskId=1900000000000000000  score=85  scoreIsOut=true  2026-10-18 09:02
```

手机上「历史训练」应该能看到同样的 taskId。**这里是唯一可信的"到底做没做"的地方**——
网页控制台里的记录只代表本地数据库。

---

## 三、答案从哪来（关键，别在这卡住）

听力题要"听见内容"才能答。好消息：**U听说的卷面里自带原文/字幕**
（`data.contents[].text`，约 1 万字/篇），所以答案是可以从文本推出来的。

真实卷面结构（2026-10 实测，S13）：

```
paperJson.chr[]                     ← 章：lead-in / pre-listening / while / Post-listen
  └ chr[]                           ← 题目单元
      ├ q_qinstid   : 19 位雪花 id（JSON 数字！必须当字符串）
      ├ q_idx       : 题号
      ├ nm          : 听力_单选_客观题 / 基础_单选_客观题 / 单词跟读 / 音频学习 …
      ├ q_template_name : single-choice_objective / vocabulary / outline
      ├ is_objective: true=要作答
      └ data
          ├ children[] : 一个单元下可能挂多道小题（如 1 个实例 4 问）
          ├ options[]  : 通常在最深的 children 里，text 带 HTML
          └ contents[] : 原文/字幕 + .wav/.mp3 路径
```

工具按这个顺序找答案：

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | **本地题库** `~/.uting/answers.json` | 做过一次的卷子，答案就记住了；同班同卷、下周重复卷直接命中 |
| 2 | **LLM**（可选） | 把卷面原文 + 题目喂给模型；配好环境变量即自动启用 |
| 3 | **人工/我** | 工具会打印题面和原文，答案可以一次填进题库永久复用 |

> 单词跟读（`is_objective=false`）需要上传录音，本工具**不提交**，会以空答案占位；
> 这部分通常不影响整卷通过（实测三篇都判 100）。

### 实测一轮（2026-10-26，S13 账号）

```
[1] 取卷 → 12 项（客观题 7，跟读 3，学习 2）
[1] 交卷 → userData 7 条（第 10 题含 3 道小题，children 三段）
[1] 核对 → 服务端本周计数 0 → 1（已记账 ✅）  历史 score=100
… 连续三篇 …
[3] 核对 → 2 → 3（已记账 ✅）  最新进度 3/3
```

三篇主题：音乐品味 / 火星探索 / 顾拜旦与现代奥运，全部 100 分。

### 配 LLM（DeepSeek，key 由你自己填）

网页里填一次即可，**命令行和网页共用**（存 `~/.uting/llm.json`，0600 权限，页面只回显 `sk-…cdef` 这种掩码）：

1. 打开 <http://127.0.0.1:8710> 登录；
2. 训练面板上方 → **「DeepSeek 自动答题设置」** → 粘贴 `sk-...` → 选 `deepseek-chat` → **保存** → **测试连接**；
3. 刷新后，题库里没有的卷会多出一个 **「AI 作答」** 按钮：点一下就让模型读卷面原文作答，答案直接写进题库，然后就能「自动答题」。

命令行等价操作：

```powershell
.\.venv\Scripts\python.exe -m uting llm set --key sk-...
.\.venv\Scripts\python.exe -m uting llm test
.\.venv\Scripts\python.exe -m uting llm show
```

- 模型走 `https://api.deepseek.com/v1/chat/completions`，默认 `deepseek-chat`（可选 `deepseek-reasoner`）
- 也可用环境变量覆盖：`UTING_LLM_API_KEY` / `UTING_LLM_MODEL` / `UTING_LLM_BASE_URL`
- 未配置时：缺答案的卷只能手动补（「取卷/补答案」）
- 已配置时：`run` / `do` 遇到缺答案会自动调用模型，答完存题库（同一篇以后不再花钱）

### 手填一次，以后复用

```powershell
# 1) 生成模板
.\.venv\Scripts\python.exe -m uting probe --template
# 2) 打开 ~/.uting/papers/<taskId>.answers.json，在 "answers" 里给每题填字母/文字
# 3) 导入题库
.\.venv\Scripts\python.exe -m uting answers import "C:\Users\你\.uting\papers\<taskId>.answers.json" --task <taskId>
# 4) 交卷
.\.venv\Scripts\python.exe -m uting run --submit
```

---

## 四、报错说明

| 输出 | 原因 | 怎么办 |
|---|---|---|
| `SSO 要求滑块验证` / `code 1506` | 风控要滑块 | 用浏览器登录后 `login --jwt` 粘贴令牌 |
| `JWT 无效或已过期（401）` | 令牌过期 | 重新 `login`（有 rt 会自动续期） |
| `4021 请勿多设备同时作答` | 手机上正开着这篇卷 | 关掉 App/网页里的作答页，等 10 秒重试 |
| `4295 作答小题数与试卷不匹配` | 有题目没解析出来 | 把 `~/.uting/papers/<taskId>.json` 发我，我按真实结构改解析 |
| `本周听力：x/3` 一直不变 | 提交没成功/还在评分 | 看 `run` 输出的"读分"和 error 行 |
| `type=grade` | 还在定级 | 先在 App 里完成听力定级 |
| `没有新任务了` | 本周已达标，或服务端没派卷 | 手动在 App 里刷一下首页 |

---

## 五、它**不**做什么

- ❌ 不做**口语**（跟读、AI 对话、自由表达）——那需要录音 + Clio 打分服务，本工具会明确拒交，不会乱提交。
- ❌ 不做**定级卷**——定级影响整个学期的推送难度，代做风险高。
- ❌ 不猜答案：题库和 LLM 都没有答案时，它会**停下并告诉你缺哪几题**，而不是提交空答案（空交会被记 0 分）。

---

## 六、文件都在哪

```
C:\Users\<你>\.uting\
├── state.json                 登录令牌（600 权限）
├── answers.json               题库（taskId -> 每题答案）
└── papers\
    ├── <taskId>.json          原始卷面（排错用）
    └── <taskId>.answers.json  可填写的答题模板
```

---

## 七、第一次跑完，请把这些发我

1. `python -m uting status` 的完整输出
2. `python -m uting probe` 的完整输出
3. 如果报了 4295 或"没解析出题目"：`~/.uting/papers/<taskId>.json`

有这三样，我就能把解析规则和答案形状对准你的真实卷子，而不是靠猜。
