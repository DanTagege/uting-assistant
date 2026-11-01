"""打包入口：单文件 exe 启动 U听说助手（内置 uvicorn，自动开浏览器）。

默认行为（可用同目录 .env 覆盖）：
    UC_HOST=0.0.0.0       手机/局域网可访问
    UC_UPSTREAM=unipus    连真实 U校园
    UC_REAL_SUBMIT=1      点「自动答题」就真交卷
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

DEFAULTS = {
    "UC_HOST": "0.0.0.0",
    "UC_UPSTREAM": "unipus",
    "UC_REAL_SUBMIT": "1",
}


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent


def _dotenv_keys() -> set[str]:
    keys: set[str] = set()
    env_file = _exe_dir() / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.add(line.split("=", 1)[0].strip())
    return keys


def _apply_defaults() -> None:
    declared = _dotenv_keys()
    for k, v in DEFAULTS.items():
        if k not in os.environ and k not in declared:
            os.environ[k] = v


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""


def _open_later(url: str, delay: float = 2.0) -> None:
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def main() -> None:
    _apply_defaults()

    import uvicorn  # 延迟导入，确保环境变量先生效

    from app.config import settings

    port = settings.port
    local = f"http://127.0.0.1:{port}/"
    ip = _lan_ip()

    if _port_open(port):
        print(f"服务已在运行（{local}），直接打开页面。")
        try:
            webbrowser.open(local)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
        return

    print("=" * 62)
    print("  U听说助手 · 本地服务")
    print("=" * 62)
    print(f"  本机访问 : {local}")
    if ip:
        print(f"  手机访问 : http://{ip}:{port}/   （同一 Wi-Fi，Safari 添加到主屏幕）")
    print(f"  上游模式 : {settings.upstream}")
    print(f"  数据目录 : {settings.db_path.parent}")
    print("  关闭本窗口即停止服务")
    print("=" * 62)

    threading.Thread(target=_open_later, args=(local,), daemon=True).start()
    try:
        from app.main import app as fastapi_app     # 静态导入，保证 PyInstaller 打包进去

        uvicorn.run(fastapi_app, host=settings.host, port=port, log_level="info", access_log=False)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
