"""应用入口：FastAPI + 静态控制台 + 后台 worker/调度器。"""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, db, jobs, scheduler, security
from .config import ROOT, settings
from .security import ApiError

STATIC = ROOT / "app" / "static"


async def _boot() -> None:
    db.conn()
    await jobs.start_workers(settings.default_parallelism)
    asyncio.create_task(scheduler.loop(), name="scheduler-loop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _boot()
    try:
        yield
    finally:
        await jobs.stop_workers()
        from .upstream import get_upstream

        up = get_upstream()
        if hasattr(up, "aclose"):
            await up.aclose()


app = FastAPI(
    title="U听说助手",
    version="0.1.0",
    description="U听说助手：周训练托管 + 题库/大模型作答 + 可调作答时长。",
    lifespan=lifespan,
)

OPEN_PATHS = {"/api/health", "/api/uptime", "/api/stats", "/api/captcha/register"}


@app.middleware("http")
async def hardening(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api")
    new_device = False

    # 设备主键：服务端下发的浏览器实例 cookie（HttpOnly），清 cookie 即新实例
    device_id = request.cookies.get("ucampus_did") or ""
    if is_api and not device_id:
        device_id = secrets.token_hex(12)
        request.scope["ucampus_did"] = device_id
        new_device = True

    if is_api and path not in OPEN_PATHS:
        try:
            security.check_csrf(request)
        except ApiError as exc:
            return JSONResponse({"error": exc.message, **({"code": exc.code} if exc.code else {})},
                                status_code=exc.status, headers=exc.headers)

    response = await call_next(request)
    response.headers["X-UCampus-Request-Id"] = secrets.token_hex(16)
    response.headers["cache-control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if new_device:
        response.set_cookie("ucampus_did", device_id, max_age=180 * 86400,
                            httponly=True, samesite="lax", path="/")
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    body = {"error": exc.message}
    if exc.code:
        body["code"] = exc.code
    return JSONResponse(body, status_code=exc.status, headers=exc.headers)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse({"error": "找不到该接口"}, status_code=404)
    return FileResponse(STATIC / "index.html")


app.include_router(api.router)

# ---- PWA：manifest / service worker / 图标 ----
app.mount("/icons", StaticFiles(directory=str(STATIC / "icons")), name="icons")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/apple-touch-icon.png")
def apple_icon() -> FileResponse:
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
