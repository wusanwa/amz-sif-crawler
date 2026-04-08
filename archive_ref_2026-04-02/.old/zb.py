import os
import sys
import asyncio
import json
import re
import base64
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

# --- 关键：必须在导入 crawl4ai 之前禁用日志 ---
os.environ["CRAWL4AI_LOG_LEVEL"] = "CRITICAL"
os.environ["PYTHONIOENCODING"] = "utf-8"

try:
    from mcp.server.fastmcp import FastMCP
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
except ImportError:
    print("请先安装依赖: pip install mcp crawl4ai pydantic", file=sys.stderr)
    sys.exit(1)

# --- 1. 数据模型定义 ---

class ProductVariant(BaseModel):
    variant_name: str = Field(..., description="变体名称")
    price: Optional[str] = Field(None, description="价格")
    is_available: bool = Field(True, description="是否有货")

class AmazonTechnicalSpec(BaseModel):
    brand: str = Field(..., description="品牌")
    model_number: str = Field(..., description="型号")
    connectivity: Optional[str] = Field(None, description="连接技术")

class SifRanking(BaseModel):
    keyword: str = Field(..., description="关键词")
    organic_rank: str = Field(..., description="自然排名")
    ad_rank: str = Field(..., description="广告排名")

# --- 2. 初始化 MCP 服务 ---
mcp = FastMCP("Amazon-Sif-Integrator")

# 使用绝对路径避免 WSL/Windows 路径混淆
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AMAZON_PROFILE = os.path.join(BASE_DIR, "profiles", "amazon")
SIF_PROFILE = os.path.join(BASE_DIR, "profiles", "sif")

def clean_lock(profile_path: str):
    """清理 Chromium 锁文件"""
    lock_file = os.path.join(profile_path, "SingletonLock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print(f"已清理锁文件: {lock_file}", file=sys.stderr)
        except Exception as e:
            print(f"清理锁文件失败: {e}", file=sys.stderr)

@mcp.tool()
async def get_amazon_and_sif_data(amazon_url: str) -> str:
    """
    输入亚马逊商品 URL，自动提取商品详情（标题、价格、规格）及 SIF 关键词排名数据。
    """
    # 提取 ASIN
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', amazon_url)
    if not asin_match:
        return json.dumps({"error": "无法从 URL 解析 ASIN，请检查链接格式"})
    
    asin = asin_match.group(1)
    sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=0"
    
    print(f"开始处理 ASIN: {asin}", file=sys.stderr)

    # 统一 LLM 配置 (请确保本地 4000 端口服务已开启)
    llm_cfg = LLMConfig(
        provider="openai/gpt-4o", # 建议先用 4o 或同级别模型测试稳定性
        api_token="sk-1234",
        base_url="http://localhost:4000/v1",
        temperature=0
    )

    final_data = {
        "asin": asin,
        "amazon_details": {},
        "sif_rankings": [],
        "status": "success"
    }

    # --- Step 1: 抓取 Amazon ---
    print("正在执行 Amazon 抓取...", file=sys.stderr)
    clean_lock(AMAZON_PROFILE)
    
    browser_cfg_amz = BrowserConfig(
        browser_type="chromium",
        headless=True,
        use_persistent_context=True,
        user_data_dir=AMAZON_PROFILE,
        extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )

    amz_strategy = LLMExtractionStrategy(
        llm_config=llm_cfg,
        instruction="提取主商品的 title, main_price, brand, model_number 以及变体列表。只需关注 #centerCol。"
    )

    async with AsyncWebCrawler(config=browser_cfg_amz) as crawler:
        res = await crawler.arun(
            url=amazon_url,
            config=CrawlerRunConfig(
                extraction_strategy=amz_strategy,
                css_selector="#centerCol",
                wait_for="css:#productTitle"
            )
        )
        if res.success:
            try:
                final_data["amazon_details"] = json.loads(res.extracted_content)
            except:
                final_data["amazon_details"] = {"raw": res.extracted_content}
        else:
            print(f"Amazon 抓取失败: {res.error_message}", file=sys.stderr)

    # --- Step 2: 抓取 SIF ---
    print("正在执行 SIF 排名抓取...", file=sys.stderr)
    clean_lock(SIF_PROFILE)
    
    browser_cfg_sif = BrowserConfig(
        browser_type="chromium",
        headless=True,
        use_persistent_context=True,
        user_data_dir=SIF_PROFILE,
        extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )

    sif_strategy = LLMExtractionStrategy(
        llm_config=llm_cfg,
        instruction="从表格中提取前 3 行关键词数据。包含 keyword, organic_rank(PX-Y), ad_rank(PX-Y/Z)。"
    )

    async with AsyncWebCrawler(config=browser_cfg_sif) as crawler:
        res = await crawler.arun(
            url=sif_url,
            config=CrawlerRunConfig(
                extraction_strategy=sif_strategy,
                js_code=[
                    "window.scrollTo(0, 1200);",
                    "await new Promise(r => setTimeout(r, 6000));"
                ]
            )
        )
        if res.success:
            try:
                sif_json = json.loads(res.extracted_content)
                # 兼容处理 LLM 返回列表或对象的情况
                if isinstance(sif_json, list):
                    for item in sif_json:
                        if "top_rankings" in item:
                            final_data["sif_rankings"] = item["top_rankings"][:3]
                            break
                else:
                    final_data["sif_rankings"] = sif_json.get("top_rankings", [])[:3]
            except:
                print("SIF 数据解析失败", file=sys.stderr)

    return json.dumps(final_data, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    # 彻底关闭 stdout 打印，除了 mcp.run()
    mcp.run()