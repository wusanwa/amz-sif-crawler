import asyncio
import httpx
import json
import logging
import os
import uvicorn
from starlette.responses import JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-gateway")

mcp = FastMCP("amazon-store-competitor-intelligence-mcp")

# --- 配置区 ---
AMZ_WORKER_BASE = os.getenv("AMZ_WORKER_URL", "http://amazon-worker:8000" if os.getenv("DOCKER_ENV") else "http://localhost:8001")
SIF_WORKER_BASE = os.getenv("SIF_WORKER_URL", "http://sif-worker:8000" if os.getenv("DOCKER_ENV") else "http://localhost:8002")
GATEWAY_MODE = os.getenv("GATEWAY_MODE", "parallel").strip().lower()
IN_DOCKER = os.getenv("DOCKER_ENV") == "1"

# Docker 运行形态下强制并行，避免配置漂移导致 Amazon/SIF 退化为串行。
if IN_DOCKER and GATEWAY_MODE != "parallel":
    logger.warning(f"⚠️ Docker 环境检测到 GATEWAY_MODE={GATEWAY_MODE}，已强制切换为 parallel")
EFFECTIVE_GATEWAY_MODE = "parallel" if IN_DOCKER else GATEWAY_MODE

logger.info(
    f"⚙️ Config: AMZ_WORKER={AMZ_WORKER_BASE} | SIF_WORKER={SIF_WORKER_BASE} | MODE={EFFECTIVE_GATEWAY_MODE}"
)

def _normalize_asin_key(value: str) -> str:
    return str(value or "UNKNOWN").strip().upper() or "UNKNOWN"


def _merge_failure_reason(*parts: str) -> str:
    merged = []
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        # 兼容上游已拼接的 "; " 字符串，按片段去重，避免重复 "SIF Empty Data"
        for seg in [x.strip() for x in text.split(";") if x.strip()]:
            if seg not in merged:
                merged.append(seg)
    return "; ".join(merged)


class LenientOptionsMiddleware:
    """放宽 OPTIONS 处理，兼容部分 MCP 客户端的非标准预检请求。

    使用原生 ASGI 中间件，避免 BaseHTTPMiddleware 对 SSE 流式响应的兼容问题。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin", "*")
            req_headers = headers.get("access-control-request-headers", "*")
            response = Response(
                status_code=204,
                headers={
                    "access-control-allow-origin": origin,
                    "access-control-allow-methods": "GET,POST,OPTIONS,PUT,PATCH,DELETE,HEAD",
                    "access-control-allow-headers": req_headers,
                    "access-control-max-age": "600",
                    "vary": "Origin",
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

async def perform_unified_crawl(urls: list[str]):
    """核心逻辑封装，供 MCP Tool 和 HTTP API 共享"""
    if not urls: return {"results": []}

    logger.info(f"🚀 触发调度 [模式: {EFFECTIVE_GATEWAY_MODE}] | 目标: {len(urls)} 条")

    async with httpx.AsyncClient(timeout=600.0) as client:
        amz_url = f"{AMZ_WORKER_BASE}/crawl"
        sif_url = f"{SIF_WORKER_BASE}/crawl"
        
        try:
            if EFFECTIVE_GATEWAY_MODE == "parallel":
                # 内部并行
                logger.info(f"📡 并发请求: {amz_url} & {sif_url}")
                amz_task = client.post(amz_url, json={"urls": urls})
                sif_task = client.post(sif_url, json={"urls": urls})
                amz_resp, sif_resp = await asyncio.gather(amz_task, sif_task, return_exceptions=True)
            else:
                # 内部串行
                logger.info(f"📡 串行请求: {amz_url}")
                amz_resp = await client.post(amz_url, json={"urls": urls})
                logger.info(f"📡 串行请求: {sif_url}")
                sif_resp = await client.post(sif_url, json={"urls": urls})
        except Exception as e:
            logger.error(f"❌ 调度过程发生网络异常: {str(e)}")
            return {"status": "error", "message": f"Network Error: {str(e)}"}
        
    amz_error_reason = ""
    sif_error_reason = ""

    # 处理 AMZ 结果
    if isinstance(amz_resp, Exception):
        logger.error(f"❌ AMZ Worker ({amz_url}) 网络失败: {amz_resp}")
        amz_error_reason = f"AMZ Worker Network Error: {amz_resp}"
        amz_data = []
    elif amz_resp.status_code != 200:
        logger.error(f"❌ AMZ Worker ({amz_url}) 响应异常 [{amz_resp.status_code}]: {amz_resp.text}")
        amz_error_reason = f"AMZ Worker HTTP {amz_resp.status_code}"
        amz_data = []
    else:
        amz_data = amz_resp.json().get("results", [])
        logger.info(f"✅ AMZ Worker 返回 {len(amz_data)} 条结果")

    # 处理 SIF 结果
    if isinstance(sif_resp, Exception):
        logger.error(f"❌ SIF Worker ({sif_url}) 网络失败: {sif_resp}")
        sif_error_reason = f"SIF Worker Network Error: {sif_resp}"
        sif_data = []
    elif sif_resp.status_code != 200:
        logger.error(f"❌ SIF Worker ({sif_url}) 响应异常 [{sif_resp.status_code}]: {sif_resp.text}")
        sif_error_reason = f"SIF Worker HTTP {sif_resp.status_code}"
        sif_data = []
    else:
        sif_data = sif_resp.json().get("results", [])
        logger.info(f"✅ SIF Worker 返回 {len(sif_data)} 条结果")
    
    # 建立 SIF 映射 (ASIN -> 完整记录)
    sif_item_map = {}
    for item in sif_data:
        if not isinstance(item, dict):
            continue
        asin_key = _normalize_asin_key(item.get("asin", "UNKNOWN"))
        sif_item_map[asin_key] = item
    
    final_results = []
    # 如果 AMZ 有结果，以 AMZ 为主合并 SIF
    if amz_data:
        for amz_item in amz_data:
            if not isinstance(amz_item, dict):
                continue

            asin_key = _normalize_asin_key(amz_item.get("asin", "UNKNOWN"))
            sif_item = sif_item_map.get(asin_key, {})
            sif_rankings = sif_item.get("full_sif", []) if isinstance(sif_item, dict) else []
            if not isinstance(sif_rankings, list):
                sif_rankings = []

            amz_item["full_sif"] = sif_rankings
            sif_1_kw = ""
            if isinstance(sif_item, dict):
                sif_1_kw = str(sif_item.get("sif_1_kw", "") or "")
            if not sif_1_kw and sif_rankings and isinstance(sif_rankings[0], dict):
                sif_1_kw = str(sif_rankings[0].get("keyword", "") or "")
            amz_item["sif_1_kw"] = sif_1_kw

            amz_status = str(amz_item.get("status", "SUCCESS")).upper()
            merged_status = amz_status if amz_status in ("SUCCESS", "PARTIAL", "FAILED") else "SUCCESS"
            merged_reason = str(amz_item.get("failure_reason", "") or "")

            if sif_error_reason:
                merged_status = "PARTIAL"
                merged_reason = _merge_failure_reason(merged_reason, sif_error_reason)
            else:
                if not sif_item:
                    merged_status = "PARTIAL"
                    merged_reason = _merge_failure_reason(merged_reason, "SIF Missing Result")
                else:
                    sif_status = str(sif_item.get("status", "")).upper()
                    sif_reason = str(sif_item.get("failure_reason", "") or "")
                    if sif_status and sif_status != "SUCCESS":
                        merged_status = "PARTIAL"
                    if sif_reason:
                        merged_status = "PARTIAL"
                        merged_reason = _merge_failure_reason(merged_reason, sif_reason)
                    if not sif_rankings and not sif_reason:
                        merged_status = "PARTIAL"
                        merged_reason = _merge_failure_reason(merged_reason, "SIF Empty Data")

            amz_item["status"] = merged_status
            amz_item["failure_reason"] = merged_reason
            final_results.append(amz_item)
    # 如果 AMZ 没结果但 SIF 有结果，把 SIF 的结果也补充进去（可选，通常是以 AMZ 为主）
    elif sif_data:
        logger.warning("⚠️ AMZ 无数据，仅返回 SIF 数据（标记为 PARTIAL）")
        for item in sif_data:
            if not isinstance(item, dict):
                continue
            old_reason = item.get("failure_reason", "")
            if not item.get("sif_1_kw") and isinstance(item.get("full_sif"), list) and item["full_sif"]:
                first = item["full_sif"][0]
                if isinstance(first, dict):
                    item["sif_1_kw"] = first.get("keyword", "") or ""
            merged_reason = _merge_failure_reason(old_reason, amz_error_reason or "AMZ Worker No Data")
            item["status"] = "PARTIAL"
            item["failure_reason"] = merged_reason
        final_results = sif_data

    return {"status": "success", "count": len(final_results), "results": final_results}

# 注册为 MCP 工具
@mcp.tool()
async def track_competitor_intelligence(urls: list[str]) -> str:
    """亚马逊店铺竞品追踪与抓包分析：并行调度 Amazon/SIF 数据并按 ASIN 聚合返回。"""
    res = await perform_unified_crawl(urls)
    return json.dumps(res, ensure_ascii=False)

# --- 运行网关 ---
if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", 8888))
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
    # 置于最外层，确保所有 OPTIONS（含非标准预检）都返回 204 而非 400。
    app.add_middleware(LenientOptionsMiddleware)

    # 添加直接的 HTTP 测试入口：POST /crawl
    async def http_test_endpoint(request):
        data = await request.json()
        urls = data.get("urls", [])
        res = await perform_unified_crawl(urls)
        return JSONResponse(res)

    async def health_endpoint(_request):
        return JSONResponse({"status": "ok", "service": "mcp-gateway"})

    app.add_route("/crawl", http_test_endpoint, methods=["POST"])
    app.add_route("/", health_endpoint, methods=["GET"])

    logger.info(f"Starting Gateway on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
