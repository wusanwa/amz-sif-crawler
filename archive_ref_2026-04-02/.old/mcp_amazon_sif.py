import os
import sys
import asyncio
import json
import re
import logging
import io
import subprocess
from typing import List, Optional
from datetime import datetime

# --- 1. 终极静默策略：劫持 Stdout 和禁用所有日志 ---
# 必须在所有 import 之前执行

# 备份原始 stdout
_original_stdout = sys.stdout

# 禁用所有日志记录
logging.disable(logging.CRITICAL)

# 设置环境变量禁用库日志
os.environ["CRAWL4AI_LOG_LEVEL"] = "CRITICAL"
os.environ["PYTHONWARNINGS"] = "ignore"

# 在导入库之前重定向 stdout 到 /dev/null
_devnull = open(os.devnull, 'w')
sys.stdout = _devnull

try:
    from mcp.server.fastmcp import FastMCP
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    from pydantic import BaseModel, Field
except ImportError as e:
    sys.stderr.write(f"缺少依赖库: {e}\n")
    sys.exit(1)
finally:
    # 恢复 stdout 以便后续使用
    sys.stdout = _original_stdout

# --- 2. 数据模型定义 ---

class ProductVariant(BaseModel):
    variant_name: str = Field(..., description="变体名称")
    price: Optional[str] = Field(None, description="变体价格")
    is_available: bool = Field(..., description="是否可用")

class AmazonData(BaseModel):
    product_title: str = Field(..., description="商品标题")
    main_price: Optional[str] = Field(None, description="主商品价格")
    model_number: Optional[str] = Field(None, description="型号")
    variants: Optional[List[ProductVariant]] = Field(None, description="变体列表")

class SifRanking(BaseModel):
    keyword: str = Field(..., description="关键词名称")
    organic_rank: str = Field(..., description="自然排名，格式 PX-Y")
    ad_rank: str = Field(..., description="广告排名，格式 PX-Y/Z 或 -")

class SifData(BaseModel):
    asin: str = Field(..., description="主 ASIN")
    top_rankings: List[SifRanking] = Field(..., description="前3行关键词排名")

# --- 3. 初始化 MCP 服务 ---
mcp = FastMCP("Amazon-Sif-Integrator")

# ===== 日志配置 =====
DEBUG_LOG_ENABLED = False  # 是否启用详细日志记录
SAVE_LOG_FILE = False      # 是否保存日志文件

# 建议使用 WSL 下的绝对路径，避免路径混淆
BASE_DIR = "/home/koku/crawl" 
AMAZON_PROFILE = os.path.join(BASE_DIR, "profiles", "amazon")
SIF_PROFILE = os.path.join(BASE_DIR, "profiles", "sif")

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def clean_lock(profile_path: str):
    """彻底清理 Chromium 锁文件和目录"""
    lock_patterns = ["SingletonLock", "*.lock", "LOCK", "lockfile"]
    for pattern in lock_patterns:
        if "*" in pattern:
            os.system(f"rm -f '{profile_path}/{pattern}' 2>/dev/null")
        else:
            lock_file = os.path.join(profile_path, pattern)
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                except:
                    pass

def force_kill_browsers():
    """强制杀死所有浏览器进程并等待完全退出"""
    for _ in range(3):
        subprocess.run(["pkill", "-9", "-f", "chrome"], capture_output=True, timeout=5)
        subprocess.run(["pkill", "-9", "-f", "chromium"], capture_output=True, timeout=5)
        time.sleep(0.5)
    time.sleep(2)  # 等待端口释放

@mcp.tool()
async def get_amazon_and_sif_data(amazon_url: str) -> str:
    """
    输入亚马逊商品链接，一键获取：
    1. 亚马逊主商品标题、价格、型号及变体信息。
    2. Sif.com 对应 ASIN 的前 3 名关键词排名数据。
    """
    # 提取 ASIN
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', amazon_url)
    if not asin_match:
        return json.dumps({"error": "无效的亚马逊链接，未找到 ASIN"})
    
    asin = asin_match.group(1)
    sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=0"
    
    # 将日志输出重定向到 devnull
    devnull = open(os.devnull, 'w')
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = devnull
    sys.stderr = devnull
    
    final_report = {"asin": asin, "amazon": {}, "sif_rankings": [], "debug_logs": []}
    debug_logs = []
    
    def log_debug(msg):
        """记录诊断信息（受 DEBUG_LOG_ENABLED 控制）"""
        if DEBUG_LOG_ENABLED:
            debug_logs.append(msg)
            sys.stderr.write(msg + "\n")
    
    try:
        # 公共 LLM 配置 (连接你的本地 LiteLLM/One-API)
        llm_cfg = LLMConfig(
            provider="openai/gpt-5.4", 
            api_token="sk-Gpru3ui5t3CoHQ94kZDKRlD9wynY9MzUSIQ9gDmx2kho7gbz",
            base_url="https://www.aillm.link/v1",
            temperature=0
        )

        # --- 准备阶段：清理锁文件和进程（仅一次） ---
        ensure_dir(AMAZON_PROFILE)
        ensure_dir(SIF_PROFILE)
        
        # 仅清理一次进程
        force_kill_browsers()
        clean_lock(AMAZON_PROFILE)
        clean_lock(SIF_PROFILE)
        
        # --- 定义爬取任务函数 ---
        async def fetch_amazon_data():
            try:
                amz_browser = BrowserConfig(
                    browser_type="chromium", headless=True,
                    use_persistent_context=True, user_data_dir=AMAZON_PROFILE,
                    extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )

                amz_extract = LLMExtractionStrategy(
                    llm_config=llm_cfg,
                    schema=AmazonData.model_json_schema(),
                    instruction="""
                    从亚马逊页面的中心列提取以下信息：
                    1. 提取主商品的标题（product_title）
                    2. 提取主商品的价格（main_price）
                    3. 提取型号号码（model_number）
                    4. 如果有多种变体，提取前3个变体的名称、价格和可用性
                    5. 必须返回有效的 JSON 格式，严格遵循 AmazonData 结构
                    """
                )

                async with AsyncWebCrawler(config=amz_browser) as crawler:
                    res = await crawler.arun(
                        url=amazon_url,
                        config=CrawlerRunConfig(
                            extraction_strategy=amz_extract,
                            cache_mode=CacheMode.BYPASS,
                            wait_until="domcontentloaded",
                            screenshot=False,
                            js_code=[
                                "window.scrollTo(0, 500);",
                                "await new Promise(r => setTimeout(r, 2000));"  # 从3秒减少到2秒
                            ]
                        )
                    )
                    
                    log_debug(f"[Amazon诊断] ===== Amazon 页面抓取诊断开始 =====")
                    log_debug(f"[Amazon诊断] URL: {amazon_url}")
                    log_debug(f"[Amazon诊断] 页面加载成功: {res.success}")
                    
                    if res.success:
                        try:
                            amz_data = json.loads(res.extracted_content)
                            log_debug(f"[Amazon诊断] JSON解析成功，类型: {type(amz_data)}")
                            if isinstance(amz_data, dict):
                                final_report["amazon"] = amz_data
                                log_debug(f"[Amazon成功] 提取标题: {amz_data.get('product_title', 'N/A')[:50]}")
                            elif isinstance(amz_data, list) and len(amz_data) > 0:
                                log_debug(f"[Amazon诊断] 收到列表格式，长度: {len(amz_data)}")
                                final_report["amazon"] = amz_data[0] if isinstance(amz_data[0], dict) else {}
                                log_debug(f"[Amazon成功] 以列表形式接收数据")
                        except json.JSONDecodeError as e:
                            log_debug(f"[Amazon提取失败] JSON解析错误: {str(e)}")
                            log_debug(f"[Amazon调试] 原始内容: {res.extracted_content[:200]}")
                    else:
                        log_debug(f"[Amazon抓取失败] 页面加载失败")
                        if hasattr(res, 'error_message'):
                            log_debug(f"[Amazon错误信息] {res.error_message}")
                    
                    log_debug(f"[Amazon诊断] ===== Amazon 页面抓取诊断结束 =====\n")
            except Exception as e:
                log_debug(f"[Amazon任务] 异常: {str(e)}")
        
        async def fetch_sif_data():
            try:
                sif_browser = BrowserConfig(
                    browser_type="chromium", headless=True,
                    use_persistent_context=True, user_data_dir=SIF_PROFILE,
                    extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )

                sif_extract = LLMExtractionStrategy(
                    llm_config=llm_cfg,
                    schema=SifData.model_json_schema(),
                    instruction="""
                    严格执行以下逻辑：
                    1. 忽略页面顶部的'变体卡片'（带有流量占比数字的区域）。
                    2. 找到下方的关键词/流量词表格。
                    3. 仅提取表格中排在最前面的前 3 行关键词数据。
                    4. 自然排名转为 PX-Y，广告排名转为 PX-Y/Z。
                    5. 如果排名显示'暂无'或'申请更新'，请填 '-'。
                    6. 必须返回有效的 JSON 格式，包含 asin 和 top_rankings 数组。
                    """
                )

                async with AsyncWebCrawler(config=sif_browser) as crawler:
                    res = await crawler.arun(
                        url=sif_url,
                        config=CrawlerRunConfig(
                            extraction_strategy=sif_extract,
                            cache_mode=CacheMode.BYPASS,
                            wait_until="domcontentloaded",
                            screenshot=False,
                            js_code=[
                                "window.scrollTo(0, 1500);",
                                "await new Promise(r => setTimeout(r, 4000));",  # 从6秒减少到4秒
                                "window.scrollTo(0, 0);"
                            ]
                        )
                    )
                    
                    # 详细的诊断日志
                    log_debug(f"\n[SIF诊断] ===== SIF 页面抓取诊断开始 =====")
                    log_debug(f"[SIF诊断] URL: {sif_url}")
                    log_debug(f"[SIF诊断] 页面加载成功: {res.success}")
                    
                    if hasattr(res, 'markdown'):
                        markdown_preview = res.markdown[:500] if len(res.markdown) > 500 else res.markdown
                        log_debug(f"[SIF诊断] Markdown长度: {len(res.markdown)}")
                        log_debug(f"[SIF诊断] Markdown预览: {markdown_preview}...")
                    
                    if hasattr(res, 'extracted_content'):
                        log_debug(f"[SIF诊断] 提取内容长度: {len(res.extracted_content) if res.extracted_content else 0}")
                        if res.extracted_content:
                            content_preview = res.extracted_content[:300] if len(res.extracted_content) > 300 else res.extracted_content
                            log_debug(f"[SIF诊断] 提取内容预览: {content_preview}")
                    
                    if res.success:
                        try:
                            raw_output = json.loads(res.extracted_content)
                            log_debug(f"[SIF诊断] JSON解析成功，类型: {type(raw_output)}")
                            
                            # 处理可能返回的列表格式
                            if isinstance(raw_output, list):
                                log_debug(f"[SIF诊断] 返回列表，长度: {len(raw_output)}")
                                for idx, block in enumerate(raw_output):
                                    log_debug(f"[SIF诊断] 列表项{idx}: 类型={type(block)}")
                                    if isinstance(block, dict) and block.get("top_rankings"):
                                        final_report["sif_rankings"] = block["top_rankings"][:3]
                                        log_debug(f"[SIF成功] 提取了 {len(block.get('top_rankings', []))} 条排名")
                                        break
                            elif isinstance(raw_output, dict):
                                log_debug(f"[SIF诊断] 返回字典，键: {list(raw_output.keys())}")
                                if raw_output.get("top_rankings"):
                                    final_report["sif_rankings"] = raw_output["top_rankings"][:3]
                                    log_debug(f"[SIF成功] 提取了 {len(raw_output.get('top_rankings', []))} 条排名")
                                else:
                                    log_debug(f"[SIF警告] 字典中没有 top_rankings 键")
                                    log_debug(f"[SIF内容] {json.dumps(raw_output, ensure_ascii=False)[:500]}")
                            else:
                                log_debug(f"[SIF警告] 返回格式异常: {type(raw_output)}")
                                log_debug(f"[SIF原始] {str(raw_output)[:200]}")
                        except json.JSONDecodeError as e:
                            log_debug(f"[SIF提取失败] JSON解析错误: {str(e)}")
                            log_debug(f"[SIF原始内容长度] {len(res.extracted_content)}")
                            log_debug(f"[SIF原始内容] {res.extracted_content[:500]}")
                    else:
                        log_debug(f"[SIF抓取失败] 页面加载失败，success=False")
                        if hasattr(res, 'error_message'):
                            log_debug(f"[SIF错误信息] {res.error_message}")
                        elif hasattr(res, 'error'):
                            log_debug(f"[SIF错误信息] {res.error}")
                        if hasattr(res, 'error_trace'):
                            log_debug(f"[SIF错误追踪] {res.error_trace[:200]}")
                    
                    log_debug(f"[SIF诊断] ===== SIF 页面抓取诊断结束 =====\n")
            except Exception as e:
                log_debug(f"[SIF任务] 异常: {str(e)}")
        
        # --- 并行执行两个爬取任务 ---
        await asyncio.gather(fetch_amazon_data(), fetch_sif_data())

        return json.dumps(final_report, indent=2, ensure_ascii=False)
    except Exception as e:
        import traceback
        error_msg = f"处理错误: {str(e)}\n{traceback.format_exc()}"
        debug_logs.append(error_msg)
        debug_logs_for_return = debug_logs if DEBUG_LOG_ENABLED else []
        return json.dumps({"error": error_msg, "asin": asin, "debug_logs": debug_logs_for_return}, ensure_ascii=False)
    finally:
        # 恢复 stdout 和 stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        
        # 添加调试日志到最终报告（仅在启用日志时）
        if DEBUG_LOG_ENABLED and isinstance(final_report, dict):
            final_report["debug_logs"] = debug_logs
        else:
            final_report["debug_logs"] = []
        
        # 保存日志到本地文件
        try:
            if SAVE_LOG_FILE and debug_logs:
                log_file = os.path.join(BASE_DIR, f"logs_{asin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write(f"ASIN: {asin}\n")
                    f.write(f"时间: {datetime.now()}\n")
                    f.write("="*60 + "\n")
                    for log in debug_logs:
                        f.write(log + "\n")
                sys.stderr.write(f"[LOG] 诊断日志已保存到: {log_file}\n")
        except Exception as log_e:
            sys.stderr.write(f"[LOG错误] 无法保存日志文件: {log_e}\n")
        
        # 输出调试摘要到 stderr（仅在启用日志时）
        if DEBUG_LOG_ENABLED:
            sys.stderr.write(f"\n{'='*60}\n")
            sys.stderr.write(f"[汇总] ASIN: {asin}\n")
            sys.stderr.write(f"[汇总] Amazon数据: {'✓ 成功' if final_report.get('amazon') else '✗ 失败'}\n")
            sys.stderr.write(f"[汇总] SIF排名: {'✓ 成功 (' + str(len(final_report.get('sif_rankings', []))) + '条)' if final_report.get('sif_rankings') else '✗ 失败'}\n")
            sys.stderr.write(f"[汇总] 诊断日志: {len(debug_logs)} 条\n")
            sys.stderr.write(f"{'='*60}\n\n")
        
        devnull.close()


if __name__ == "__main__":
    # 关键：启动 MCP 服务前恢复 stdout，以便 FastMCP 进行 JSON 通信
    sys.stdout = _original_stdout
    mcp.run()