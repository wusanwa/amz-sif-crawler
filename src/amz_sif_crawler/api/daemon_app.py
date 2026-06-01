from __future__ import annotations

import logging
import os
import time

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import JSONResponse

from amz_sif_crawler.runtime.config import ensure_runtime_dirs, load_app_config
from amz_sif_crawler.runtime.daemon_manager import PersistentBrowserDaemon


logger = logging.getLogger("amz-sif-daemon")
HOST = os.getenv("APP_HOST", "0.0.0.0").strip() or "0.0.0.0"


class FetchRequest(BaseModel):
    asin: str = ""
    url: str = ""


def _log_progress(asin: str, step: str) -> None:
    logger.info("[%s] %s", asin, step)


def build_daemon_app() -> FastAPI:
    app = FastAPI(title="amz-sif-daemon")
    daemon_mode = os.getenv("DAEMON_MODE", "amazon").strip().lower()
    app_config = load_app_config()
    ensure_runtime_dirs(app_config)
    daemon = PersistentBrowserDaemon(
        mode=daemon_mode,
        profile_dir=app_config.amazon_profile_dir if daemon_mode == "amazon" else app_config.sif_profile_dir,
        headless=app_config.amazon_headless if daemon_mode == "amazon" else app_config.sif_headless,
    )

    @app.on_event("startup")
    async def on_startup() -> None:
        await daemon.start()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await daemon.stop()

    @app.get("/")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "daemon", "mode": daemon_mode})

    @app.post("/fetch")
    async def fetch(request: FetchRequest) -> JSONResponse:
        asin = (request.asin or "").strip() or "UNKNOWN"
        started_at = time.perf_counter()

        if daemon_mode == "amazon":
            _log_progress(asin, "🛒 Amazon daemon 常驻浏览器抓取中...")
            result = await daemon.fetch_amazon(url=request.url)
        elif daemon_mode == "sif":
            _log_progress(asin, "🔍 SIF daemon 常驻浏览器抓取中...")
            result = await daemon.fetch_sif(asin=asin)
        else:
            return JSONResponse({"data": {}, "error": f"Unsupported daemon mode: {daemon_mode}"}, status_code=400)

        logger.info("[%s] daemon total: %.2fs", asin, time.perf_counter() - started_at)
        return JSONResponse(result)

    return app


def run_daemon_server() -> None:
    port = int(os.getenv("PORT", "8001"))
    logger.info("Starting daemon on %s:%s mode=%s", HOST, port, os.getenv("DAEMON_MODE", "amazon"))
    uvicorn.run(build_daemon_app(), host=HOST, port=port)
