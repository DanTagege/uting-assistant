# U听说助手 · 源码交接说明

这是一个**本地运行的 U 听说（U听力）周训练托管工具**：登录 U校园 → 读取本周听力进度 → 取卷 → 用题库/大模型作答 → 按可调时长模拟真实做题节奏（中途快照、最后交卷）→ 回读成绩。附带一个网页控制台。

> 本包**不含任何账号数据、令牌或 API Key**。你自己的登录信息与密钥都在解压目录之外（`~/.uting/`、`data/`），首次运行会重新生成。

---

## 一、运行环境

- Windows 10 / 11
- Python **3.12**（3.11 也可以）
- 能访问 `sso.unipus.cn` / `ucloud.unipus.cn` / `uadaptive.unipus.cn`
- 可选：DeepSeek API Key（题库没有的卷靠它读原文作答）

---

## 二、三步跑起来

```powershell
# 1) 建虚拟环境并装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) 复制配置模板
Copy-Item .env.example .env

# 3) 启动
.\.venv\Scripts\python.exe run.py
```

浏览器打开 <http://127.0.0.1:8710/>。

想给手机用：把 `.env` 里的 `UC_HOST` 改成 `0.0.0.0`，手机访问 `http://<电脑局域网IP>:8710/`（Safari → 添加到主屏幕）。

---

## 三、首次使用要做两件事

### 1) 登录 U校园

- 网页上用手机号 + 密码登录（走 `sso.unipus.cn`，密码不落盘，只存令牌）
- 如果提示「SSO 要求滑块」（code 1506）：浏览器登录 <https://sso.unipus.cn/sso/login>，再打开 <https://uadaptive.unipus.cn/>，F12 → Network → 任意 `api/uls/*` 请求 → 复制 `authorization` 头，回到网页登录框下方「或用 JWT 登录」粘贴

### 2) 配置 DeepSeek（可选但强烈建议）

网页 → 训练面板上方「DeepSeek 自动答题设置」→ 粘贴 `sk-...` → 保存 → 测试连接。

- 模型建议 `deepseek-chat`（`deepseek-reasoner` 会消耗思考 token，容易返回空内容）
- Key 存 `~/.uting/llm.json`（0600），不写进源码、不进数据库

---

## 四、界面上几个关键开关

| 控件 | 作用 |
|---|---|
| **真实提交** | 勾上后点「自动答题」才真的交卷；不勾只预演（打印将要提交的内容） |
| **每篇用时 + 随机 %** | 模拟真实作答时长（默认 12 分钟 ±20%），过程中会多次 `saveSnapshot`，交卷时 `duration` 字段是真实耗时 |
| **听力定级** | 账号未定级时出现：选目标级别 → 预览 → 开始定级（只能做一次） |
| **取卷/补答案** | 题库没有这篇时，就地渲染题目让你填，填完存题库（永久复用） |
| **AI 作答** | 用 DeepSeek 读卷面原文自动作答，答案入题库 |

---

## 五、命令行（不开网页也能跑）

```powershell
.\.venv\Scripts\python.exe -m uting login                    # 登录
.\.venv\Scripts\python.exe -m uting verify                   # 看本周进度 + 历史
.\.venv\Scripts\python.exe -m uting probe --template         # 取卷并生成答题模板
.\.venv\Scripts\python.exe -m uting run --submit             # 自动做完本周剩余篇数
.\.venv\Scripts\python.exe -m uting account list             # 多账号
.\.venv\Scripts\python.exe -m uting llm set --key sk-...     # 配置模型
```

---

## 六、目录说明

```
app/            后端（FastAPI）：路由、任务队列、调度、风控、上游适配器
  upstream/       unipus.py = 真实 U校园；mock.py = 离线假上游
uting/          命令行工具（登录/取卷/答题/多账号/LLM）
tools/           维护脚本（打包 exe、生成图标、CLI 探针、题库合并…）
docs/            上游接口说明
ios-app/         Capacitor iOS 外壳（需要 macOS + Xcode 才能出 ipa）
protector/       exe 加固外壳源码（反调试/反虚拟机/加密载荷）
pack_entry.py    打包入口（PyInstaller / Nuitka）
launcher.cs      轻量启动器源码（csc 编译成 exe）
SEND_TO_OTHERS.md 本文件
FILE_LIST.txt    包内文件清单
```

---

## 七、打包成 exe（可选）

```powershell
# 方案 A：PyInstaller（快，仍含 pyc）
.\.venv\Scripts\python.exe -m PyInstaller --onefile --console --name UtingStandalone `
  --add-data "app/static;app/static" --collect-submodules app pack_entry.py

# 方案 B：Nuitka（原生编译，无 pyc，门槛更高；需 MSVC）
.\.venv\Scripts\python.exe -m nuitka --onefile --msvc=latest --windows-console-mode=force `
  --include-package=app --include-package=uvicorn --include-package=fastapi `
  --include-data-dir="app/static=app/static" --python-flag=-OO pack_entry.py

# 可选：再套一层加固壳（反调试/反虚拟机/加密载荷，机器绑定）
powershell -ExecutionPolicy Bypass -File tools\build_protected.ps1 `
  -Inner ".\dist\UtingNative.exe" -Out ".\Uting加固版.exe"
```

---

## 八、注意事项

1. **只用自己的账号**：这是个人自动化工具，不是代刷服务；原项目本身也声明禁止接单代刷。
2. **定级只能做一次**：定级页有独立确认勾选，先点「预览」再决定。
3. **并发**：同一账号不要在多端同时打开同一篇卷，会触发 `4021 请勿多设备同时作答`。
4. **令牌**：U校园 JWT 约 48 小时，`~/.uting/accounts/<手机号>/state.json`；过期后用 RT 自动续期，续不动就重新登录。
5. **别把 `data/` 和 `~/.uting/` 发给别人**——里面有你的账号令牌、成绩和 API Key。
