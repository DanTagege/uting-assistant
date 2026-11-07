"""全局配置：全部来自环境变量 / .env，默认值即可直接本地跑起来。

同时支持打包后的运行方式（PyInstaller / Nuitka）：
  · 资源（app/static、manifest、icons）跟随模块所在目录（Nuitka 解包目录 / _MEIPASS）
  · 数据（data/、.env）固定在 exe 同目录，便于随身携带
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _is_packed() -> bool:
    """PyInstaller 用 sys.frozen；Nuitka 只给每个模块注入 __compiled__。"""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _exe_dir() -> Path:
    try:
        return Path(sys.executable).resolve().parent
    except OSError:
        return Path.cwd()


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _exe_dir()))
    return Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    if _is_packed():
        return _exe_dir() / "data"
    return _resource_root() / "data"


ROOT = _resource_root()          # 资源根（打包后是解包目录）
DATA = _data_root()              # 可写数据目录（打包后在 exe 同级）
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "logs").mkdir(exist_ok=True)


def _env_file() -> Path:
    if _is_packed():
        return _exe_dir() / ".env"
    return ROOT / ".env"


def _load_dotenv(path: Path | None = None) -> None:
    """极简 .env 解析，避免额外依赖。"""
    path = path or _env_file()
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _secret_key() -> str:
    """JWT 密钥：优先环境变量，否则落地到 data/secret.key（首次生成）。"""
    env = _env("UC_SECRET_KEY")
    if env:
        return env
    f = DATA / "secret.key"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    f.write_text(key, encoding="utf-8")
    return key


@dataclass
class Settings:
    # 服务
    host: str = field(default_factory=lambda: _env("UC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("UC_PORT", 8710))
    timezone: str = field(default_factory=lambda: _env("UC_TZ", "Asia/Shanghai"))

    # 安全
    secret_key: str = field(default_factory=_secret_key)
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = field(default_factory=lambda: _env_int("UC_JWT_TTL", 2 * 3600))          # 访问令牌
    rt_ttl_seconds: int = field(default_factory=lambda: _env_int("UC_RT_TTL", 30 * 24 * 3600))     # 刷新令牌 = 站点口径 30 天
    captcha_mode: str = field(default_factory=lambda: _env("UC_CAPTCHA", "off"))                   # off | static
    captcha_answer: str = field(default_factory=lambda: _env("UC_CAPTCHA_ANSWER", "1234"))
    require_csrf_header: bool = field(default_factory=lambda: _env("UC_CSRF", "1") == "1")

    # 上游
    upstream: str = field(default_factory=lambda: _env("UC_UPSTREAM", "mock"))                     # mock | unipus | unipus_ai
    upstream_config: Path = field(default_factory=lambda: DATA / "upstream.json")
    upstream_timeout: int = field(default_factory=lambda: _env_int("UC_UPSTREAM_TIMEOUT", 25))
    upstream_user_agent: str = field(default_factory=lambda: _env(
        "UC_UPSTREAM_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"))
    u_app_id: str = field(default_factory=lambda: _env("UC_U_APP_ID", "116"))
    ucloud_origin: str = field(default_factory=lambda: _env("UC_UCLOUD_ORIGIN", "https://ucloud.unipus.cn"))
    adaptive_origin: str = field(default_factory=lambda: _env("UC_ADAPTIVE_ORIGIN", "https://uadaptive.unipus.cn"))
    real_submit: bool = field(default_factory=lambda: _env("UC_REAL_SUBMIT", "0") == "1")

    # 调度默认值
    default_parallelism: int = field(default_factory=lambda: _env_int("UC_PARALLELISM", 8))
    scheduler_enabled: bool = field(default_factory=lambda: _env("UC_SCHEDULER", "1") == "1")
    scheduler_weekday: int = field(default_factory=lambda: _env_int("UC_WEEKDAY", 0))
    scheduler_hour: int = field(default_factory=lambda: _env_int("UC_HOUR", 12))
    scheduler_minute: int = field(default_factory=lambda: _env_int("UC_MINUTE", 0))
    scheduler_tick_seconds: int = field(default_factory=lambda: _env_int("UC_TICK", 20))


    # 数据库
    db_path: Path = field(default_factory=lambda: DATA / "ucampus.db")

    @property
    def weekly_quota(self) -> int:
        return _env_int("UC_WEEKLY_QUOTA", 5)


settings = Settings()
