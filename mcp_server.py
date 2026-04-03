import os
import json
import logging
import asyncio
import uvicorn
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from crawler_worker import main_worker

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("amazon-crawler-api")

# --- 核心逻辑封装 ---
async def perform_crawl(urls: list[str]):
    if not urls:
        return {"error": "No URLs provided"}
    
    logger.info(f"Processing crawl request for {len(urls)} URLs")
    try:
        node_type = os.getenv("NODE_TYPE", "both")
        results = await main_worker(manual_urls=urls, skip_lock=False, task_type=node_type)
        
        if not results:
            return {
                "status": "warning",
                "message": "Crawl completed but no results were returned.",
                "results": []
            }
        return {"status": "success", "count": len(results), "results": results}
    except Exception as e:
        logger.error(f"Error during crawl execution: {e}")
        return {"error": str(e)}

# --- 1. 初始化 FastMCP ---
mcp = FastMCP("amazon-store-competitor-worker-mcp")

# --- 1.2 处理 HTTP 请求的函数 (Starlette 格式) ---
async def crawl_api_endpoint(request):
    """供内部 Cluster 调用，接收 urls 列表并行抓取结果"""
    try:
        data = await request.json()
        urls = data.get("urls", [])
        res = await perform_crawl(urls)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# 注册常规 MCP 工具
@mcp.tool()
async def crawl_amazon(urls: list[str]) -> str:
    """亚马逊店铺竞品追踪与抓包分析：单节点执行抓取任务。"""
    res = await perform_crawl(urls)
    return json.dumps(res, ensure_ascii=False)

# --- 2. 运行引导程序 ---
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Amazon Crawler MCP Server")
    parser.add_argument("--mode", choices=["mcp", "sse"], default="sse")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.mode == "mcp":
        mcp.run()
    elif args.mode == "sse":
        # 获取底层应用 (兼容不同版本的 FastMCP)
        app = mcp.sse_app
        if callable(app) and not hasattr(app, "add_route"):
            app = app()

        allow_origins = [x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if x.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins or ["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )
            
        # 使用 Starlette 兼容的 add_route 语法添加私有端点
        app.add_route("/crawl", crawl_api_endpoint, methods=["POST"])

        async def health_endpoint(_request):
            return JSONResponse({"status": "ok", "service": "mcp-worker", "node_type": os.getenv("NODE_TYPE", "both")})

        app.add_route("/", health_endpoint, methods=["GET"])

        logger.info(f"Starting in SSE mode on port {args.port}...")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
