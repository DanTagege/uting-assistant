"""启动脚本：python run.py  （或 uvicorn app.main:app --reload）"""
from __future__ import annotations

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")
