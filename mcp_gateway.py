import asyncio
import httpx
import json
import logging
import os
import uvicorn
from starlette.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-gateway")

mcp = FastMCP("amazon-crawler-gateway")

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
    
    # 建立 SIF 映射 (ASIN -> Rankings)
    sif_map = {item['asin']: item.get('full_sif', []) for item in sif_data if 'asin' in item}
    
    final_results = []
    # 如果 AMZ 有结果，以 AMZ 为主合并 SIF
    if amz_data:
        for amz_item in amz_data:
            asin = amz_item.get('asin', 'UNKNOWN')
            amz_item['full_sif'] = sif_map.get(asin, [])
            final_results.append(amz_item)
    # 如果 AMZ 没结果但 SIF 有结果，把 SIF 的结果也补充进去（可选，通常是以 AMZ 为主）
    elif sif_data:
        logger.warning("⚠️ AMZ 无数据，仅返回 SIF 数据（标记为 PARTIAL）")
        for item in sif_data:
            if not isinstance(item, dict):
                continue
            old_reason = item.get("failure_reason", "")
            merged_reason = "; ".join(filter(None, [old_reason, amz_error_reason or "AMZ Worker No Data"]))
            item["status"] = "PARTIAL"
            item["failure_reason"] = merged_reason
        final_results = sif_data

    return {"status": "success", "count": len(final_results), "results": final_results}

# 注册为 MCP 工具
@mcp.tool()
async def crawl_batch_unified(urls: list[str]) -> str:
    res = await perform_unified_crawl(urls)
    return json.dumps(res, ensure_ascii=False)

# --- 运行网关 ---
if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", 8888))
    app = mcp.sse_app
    if callable(app):
        app = app()

    # 添加直接的 HTTP 测试入口：POST /crawl
    async def http_test_endpoint(request):
        data = await request.json()
        urls = data.get("urls", [])
        res = await perform_unified_crawl(urls)
        return JSONResponse(res)

    app.add_route("/crawl", http_test_endpoint, methods=["POST"])

    logger.info(f"Starting Gateway on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
