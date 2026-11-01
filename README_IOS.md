# 打包成 iPhone 上能用的「App」

Windows 上**不能**直接产出可安装的 `.ipa`（那必须 macOS + Xcode 签名）。所以我给你三条路，
从"今晚就能用"到"真正的原生 App"：

---

## 方案 A（推荐，今天就能用）：添加到主屏幕（PWA）

已经内置好了：manifest、180×180 图标、全屏 standalone、离线壳。

1. 电脑上启动服务（**必须监听局域网**，否则手机连不上）：

   ```powershell
   cd D:\Ucampus
   $env:UC_HOST='0.0.0.0'; $env:UC_UPSTREAM='unipus'; $env:UC_REAL_SUBMIT='1'
   .\.venv\Scripts\python.exe run.py
   ```

   记下窗口里打印的局域网地址（例如 `http://192.168.1.23:8710`）。
   Windows 防火墙弹窗要**允许专用网络**。

2. iPhone 用 **Safari** 打开 `http://192.168.1.23:8710`（必须同一个 Wi-Fi）。

3. 点底部 **分享 ⤴ → 添加到主屏幕 → 添加**。

效果：桌面上出现「U听说」图标，点开是全屏（没有 Safari 地址栏），状态栏适配刘海，
和原生 App 的观感一致。缺点：它本质还是网页，必须电脑上的服务开着。

---

## 方案 B：真原生 .ipa（需要一台 Mac 或云 Mac）

工程已经在 `ios-app/`（Capacitor 6 外壳，加载你电脑上的服务）。

```bash
# 在 macOS 上
cd ios-app
node -v            # 需要 18+
npm install
npx cap add ios    # 首次
npx cap sync ios
npx cap open ios   # 打开 Xcode
```

Xcode 里：
1. 选 `App` target → Signing & Capabilities → Team 选你的 **Apple ID（免费即可）**
2. Bundle Identifier 改成唯一的，例如 `com.dantagege.uting2`
3. 选中你的 iPhone → Run

免费 Apple ID 的限制：证书 **7 天过期**，到期重新 Run 一次即可（数据不丢）。

> 服务地址在 `ios-app/capacitor.config.json` 的 `server.url`。
> 换电脑/换 Wi-Fi 后改这里，或用 `tools\set_ios_server.ps1` 自动写入当前局域网 IP。

---

## 方案 C：不想买 Mac，用 GitHub Actions 出 .ipa

仓库里已放好 `.github/workflows/ios-build.yml`（macOS runner，无需签名）：

1. 把 `ios-app/` 推到 GitHub 仓库（私有也行）
2. Actions → **Build unsigned iOS IPA** → Run workflow
3. 跑完在 Artifacts 下载 `UtingApp-unsigned.ipa`

拿到的是**未签名**包，用侧载工具装：
- [SideStore](https://sidestore.io/) / [AltStore](https://altstore.io/)（用免费 Apple ID 签，7 天续一次）
- 或 Sideloadly（Windows 也能用，需 iTunes 驱动）

---

## 方案 D：TestFlight / App Store（最正规，但要钱）

需要 **Apple Developer Program（$99/年）**，然后 Xcode → Archive → Distribute → App Store Connect
→ TestFlight。适合要给很多人用的情况。

---

## 工程结构

```
D:\Ucampus\
├── app\static\                 ← 就是 App 的界面（PWA 资源都在这里）
│   ├── manifest.webmanifest
│   ├── sw.js
│   ├── apple-touch-icon.png
│   └── icons\icon-{192,512,1024}.png
├── ios-app\                    ← Capacitor 原生壳
│   ├── capacitor.config.json   ← server.url 指向你的电脑
│   ├── package.json
│   ├── www\index.html          ← 兜底页（连不上服务时显示）
│   └── .github\workflows\ios-build.yml
└── tools\
    ├── set_ios_server.ps1      ← 一键写入当前局域网 IP
    └── package_ios_kit.ps1     ← 打成一个 zip 交付包
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 手机打不开 `192.168.x.x:8710` | 服务只监听了 127.0.0.1 | 用 `UC_HOST=0.0.0.0` 启动 |
| 打开是白屏/无法连接 | 防火墙拦了 8710 | Windows 防火墙允许"专用网络" |
| 添加到主屏幕后没图标 | Safari 缓存 | 删掉图标重新添加；确认 `/apple-touch-icon.png` 可访问 |
| 原生壳一直停在"正在连接…" | `server.url` 还是占位 IP | 跑 `tools\set_ios_server.ps1` |
| 换 Wi-Fi 后 App 打不开 | 电脑 IP 变了 | 重跑 `set_ios_server.ps1` 并 `npx cap sync ios` |
