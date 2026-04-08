import asyncio
import os
import json
from pydantic import BaseModel, Field
from typing import List
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

# 1. 定义数据模型
class SifRanking(BaseModel):
    keyword: str = Field(..., description="关键词名称")
    organic_rank: str = Field(..., description="自然排名，格式 PX-Y")
    ad_rank: str = Field(..., description="广告排名，格式 PX-Y/Z 或 -")

class SifData(BaseModel):
    asin: str = Field(..., description="主 ASIN")
    top_rankings: List[SifRanking] = Field(..., description="只提取流量词表格最上方的前 3 行关键词")

async def main():
    profile_path = os.path.abspath("./sif_profile")
    
    # 彻底解开 SingletonLock 占用
    os.system("pkill -f chromium")
    lock_file = os.path.join(profile_path, "SingletonLock")
    if os.path.exists(lock_file):
        try: os.remove(lock_file)
        except: pass

    # 2. 浏览器配置
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        use_persistent_context=True,
        user_data_dir=profile_path,
        extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    # 3. 提取策略：强化 AI 的语义识别能力
    extraction_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/gpt-5.4", 
            api_token="sk-1234",
            base_url="http://localhost:4000/v1"
        ),
        schema=SifData.model_json_schema(),
        instruction="""
            严格执行以下逻辑：
            1. 忽略页面顶部的'变体卡片'（带有流量占比数字的区域）。
            2. 找到下方的关键词/流量词表格。
            3. 仅提取表格中排在最前面的前 3 行关键词数据。
            4. 自然排名转为 PX-Y，广告排名转为 PX-Y/Z。
            5. 如果排名显示'暂无'或'申请更新'，请填 '-'。
        """
    )

    # 4. 运行配置：去掉截图和 CSS 过滤，防止底层库冲突
    run_config = CrawlerRunConfig(
        extraction_strategy=extraction_strategy,
        cache_mode=CacheMode.BYPASS,
        # 放弃 CSS 选择器，直接传全文 Markdown 给 AI，由 AI 进行物理去重
        wait_until="domcontentloaded",
        screenshot=False, 
        js_code=[
            # 滚到足够深的地方触发 Vue 数据加载
            "window.scrollTo(0, 1500);",
            "await new Promise(r => setTimeout(r, 6000));",
            "window.scrollTo(0, 0);"
        ]
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        url = "https://www.sif.com/reverse?country=US&asin=B0CDX5XGLK&isListingSearch=0"
        print(f"[INFO] 正在启动纯数据抓取模式 (已禁用截图)...")
        
        result = await crawler.arun(url=url, config=run_config)

        if result.success:
            try:
                # 结果清洗：处理 AI 可能返回的列表格式
                raw_output = json.loads(result.extracted_content)
                final_data = {}
                if isinstance(raw_output, list):
                    # 寻找包含 top_rankings 的那个有效块
                    for block in raw_output:
                        if block.get("top_rankings"):
                            final_data = block
                            break
                else:
                    final_data = raw_output

                print("\n" + "="*25 + " SIF 核心排名结果 " + "="*25)
                if final_data and final_data.get("top_rankings"):
                    # 再次确保只保留前 3 个
                    final_data["top_rankings"] = final_data["top_rankings"][:3]
                    print(json.dumps(final_data, indent=2, ensure_ascii=False))
                else:
                    print("[WARN] 未能提取到有效数据，可能页面加载不全。")
                print("="*68)

            except Exception as e:
                print(f"数据解析失败: {e}")
                print(f"原始内容片段: {result.extracted_content[:500]}")
        else:
            print(f"抓取失败: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(main())