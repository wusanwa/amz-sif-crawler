from __future__ import annotations

import json
import logging
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from amz_sif_crawler.service import crawl_and_wrap


logger = logging.getLogger("amz-sif-crawler")
HOST = os.getenv("APP_HOST", "0.0.0.0").strip() or "0.0.0.0"
mcp = FastMCP("amazon-sif-crawler-mcp", host=HOST)


@mcp.tool()
async def track_competitor_intelligence(urls: list[str]) -> str:
    payload = await crawl_and_wrap(urls)
    return json.dumps(payload, ensure_ascii=False)


def build_app():
    app = mcp.sse_app
    if callable(app):
        app = app()

    allow_origins = [x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if x.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    async def crawl_endpoint(request):
        data = await request.json()
        urls = data.get("urls", [])
        return JSONResponse(await crawl_and_wrap(urls))

    async def health_endpoint(_request):
        return JSONResponse({"status": "ok", "service": "amz-sif-crawler"})

    app.add_route("/crawl", crawl_endpoint, methods=["POST"])
    app.add_route("/", health_endpoint, methods=["GET"])
    return app


def run_server() -> None:
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting crawler server on %s:%s", HOST, port)
    uvicorn.run(build_app(), host=HOST, port=port)
