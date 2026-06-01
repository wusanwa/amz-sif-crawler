import os
import sys
import asyncio
import json
import re
import html
import logging
import time
import faulthandler
import diskcache
import tempfile
import shutil
from collections import Counter
from typing import List, Optional, Dict, Any
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# 增加递归深度限制，防止 html2text 在处理复杂页面时崩溃
sys.setrecursionlimit(20000)

# 允许底层崩溃诊断
faulthandler.enable()

# 保持 CLI 相对干净，但保留关键信息
os.environ["CRAWL4AI_LOG_LEVEL"] = "INFO"
os.environ["PYTHONWARNINGS"] = "ignore"

def log_progress(asin: str, step: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    sys.stderr.write(f"[{timestamp}] [{asin}] {step}\n")
    sys.stderr.flush()

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from pydantic import BaseModel, Field
    # 提前加载 litellm 以避免在其在异步任务内 lazy-import 时发生 Pydantic 段错误
    import litellm
    # 强制构建 litellm 内部复杂模型，提前发现潜在崩溃
    try:
        from litellm.proxy._types import UserAPIKeyData
    except ImportError:
        pass
except ImportError as e:
    sys.stderr.write(f"缺少依赖库: {e}\n")
    sys.exit(1)

from sif_query import fetch_sif_data_multilayer
from sif_runtime import build_sif_browser_config
from amazon_js_fetcher import fetch_amazon_data_js, normalize_amazon_input, extract_asin

# ===== 环境探测与基础路径 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")

def load_settings():
    if not os.path.exists(CONFIG_PATH):
        sys.stderr.write(f"❌ 错误: 配置文件不存在 {CONFIG_PATH}\n")
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

SETTINGS = load_settings()

# 基础路径配置（统一使用 runtime_data，允许环境变量覆盖）
RUNTIME_ROOT = os.getenv("APP_RUNTIME_ROOT", os.path.join(BASE_DIR, "runtime_data"))
PROFILE_ROOT = os.getenv("PROFILE_ROOT_DIR", os.path.join(RUNTIME_ROOT, "profiles"))
AMAZON_PROFILE = os.getenv("AMAZON_PROFILE_DIR", os.path.join(PROFILE_ROOT, "amazon"))
SIF_PROFILE = os.getenv("SIF_PROFILE_DIR", os.path.join(PROFILE_ROOT, "sif"))
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(RUNTIME_ROOT, "cache_db"))
CACHE_EXPIRY_SEC = SETTINGS.get("CACHE_EXPIRY_SEC", 80000)
DEBUG_MODE = False # Default, will be updated in main_worker

# 初始化高性能缓存
os.makedirs(CACHE_DIR, exist_ok=True)
db_cache = diskcache.Cache(CACHE_DIR)

# 数据模型
class ProductVariant(BaseModel):
    variant_name: str = Field(..., description="变体名称")
    price: Optional[str] = Field(None, description="变体价格")
    is_available: bool = Field(..., description="是否可用")

class AmazonData(BaseModel):
    product_title: str = Field(..., description="商品标题")
    main_price: Optional[str] = Field(None, description="主商品价格")
    list_price: Optional[str] = Field(None, description="原价/划线价/Typical price")
    savings_text: Optional[str] = Field(None, description="优惠文案，例如 -10%")
    has_price_discount: bool = Field(False, description="是否存在价格折扣（如 List/Typical price 或百分比折扣）")
    deal_type: Optional[str] = Field(None, description="促销类型，例如 Limited time deal")
    is_limited_time_deal: bool = Field(False, description="是否为限时折扣")
    coupon_text: Optional[str] = Field(None, description="优惠券文案，例如 Apply 15% coupon")
    applied_coupon_text: Optional[str] = Field(None, description="已应用优惠券文案，例如 15% off coupon applied")
    has_coupon: bool = Field(False, description="是否存在优惠券")
    model_number: Optional[str] = Field(None, description="型号，优先从商品标题的第一段提取")
    variants: Optional[List[ProductVariant]] = Field(None, description="页面展示的变体列表")
    parent_item_count: int = Field(0, description="变体总数量")

# ===== 系统锁 (防止多进程竞争浏览器 Profile) =====
class CrawlerLock:
    def __init__(self, lock_file="/tmp/crawler_worker.lock"):
        self.lock_file = lock_file
        self.fd = None

    def acquire(self):
        try:
            import fcntl
            self.fd = open(self.lock_file, "w")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (IOError, ImportError):
            return False

    def release(self):
        if self.fd:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                self.fd.close()
            except: pass

# ===== 缓存管理系统已迁移至 diskcache =====

# ===== 工具函数 =====
def ensure_dir(path):
    if not os.path.exists(path): os.makedirs(path, exist_ok=True)

def force_kill_browsers():
    """彻底清理所有浏览器残留进程"""
    log_progress("SYSTEM", "🔫 正在强制清理浏览器进程...")
    for _ in range(2):
        os.system("pkill -9 -f chrome 2>/dev/null")
        os.system("pkill -9 -f chromium 2>/dev/null")
        os.system("pkill -9 -f playwright 2>/dev/null")
        time.sleep(0.5)
    time.sleep(2) # 给端口和锁一些释放时间
    log_progress("SYSTEM", "🔫 清理完成")

def clean_lock(profile_path: str):
    for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie", "LOCK", "lockfile"]:
        lock_file = os.path.join(profile_path, lock_name)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass
    os.system(f"rm -f '{profile_path}'/*.lock 2>/dev/null")
    os.system(f"rm -f '{profile_path}'/Singleton* 2>/dev/null")

def is_profile_lock_error(err: Exception) -> bool:
    msg = str(err).lower()
    return (
        "processsingleton" in msg
        or "singletonlock" in msg
        or "profile appears to be in use" in msg
        or "profile is already in use" in msg
        or "failed to create a processsingleton" in msg
    )

def safe_get(data, key, default=""):
    if not data or not isinstance(data, dict): return default
    val = data.get(key, default)
    return val if val is not None else default


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


KEEP_SIF_BROWSER_OPEN = env_flag("KEEP_SIF_BROWSER_OPEN", False)
USE_SIF_DAEMON = bool(str(os.getenv("SIF_DAEMON_URL", "") or "").strip())


def should_cleanup_sif_browser() -> bool:
    return not KEEP_SIF_BROWSER_OPEN and not USE_SIF_DAEMON


def _build_canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


def _extract_text_by_id(html_text: str, element_id: str) -> str:
    pattern = rf'id=["\']{re.escape(element_id)}["\'][^>]*>(.*?)</'
    match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_amazon_price(html_text: str) -> str:
    price_ids = ["priceblock_ourprice", "priceblock_dealprice", "priceblock_saleprice"]
    for pid in price_ids:
        val = _extract_text_by_id(html_text, pid)
        if val:
            return val
    whole = _extract_text_by_id(html_text, "priceblock_ourprice")
    frac = _extract_text_by_id(html_text, "priceblock_ourprice_frac")
    if whole:
        return f"{whole}{frac}" if frac else whole
    return ""


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value.replace(",", ""))
        if match:
            try:
                return int(match.group(0))
            except ValueError:
                return 0
    return 0


def _extract_amazon_parent_item_count(raw_html: str = "", markdown_text: str = "") -> int:
    structural_candidates: List[int] = []
    html_text = raw_html or ""

    def _extract_candidate_li_tags(block: str) -> List[str]:
        return re.findall(
            r'<li\b[^>]*data-asin=["\'][^"\']+["\'][^>]*>',
            block,
            flags=re.IGNORECASE,
        )

    def _count_option_lis(block: str) -> int:
        li_tags = _extract_candidate_li_tags(block)
        direct_option_count = 0
        seen_keys = set()

        for li_tag in li_tags:
            if re.search(r'(?i)\baok-hidden\b', li_tag):
                continue

            attrs = []
            for attr in [
                "data-asin",
                "data-defaultasin",
                "data-csa-c-item-id",
                "data-value",
                "data-dp-url",
                "title",
                "aria-label",
            ]:
                m = re.search(rf'{attr}=["\']([^"\']+)["\']', li_tag, flags=re.IGNORECASE)
                if m and m.group(1).strip():
                    attrs.append(f"{attr}:{m.group(1).strip()}")

            if not attrs:
                continue

            option_key = "|".join(attrs)
            if option_key in seen_keys:
                continue
            seen_keys.add(option_key)
            direct_option_count += 1

        return direct_option_count

    # 1) 直接读取常见字段
    for pattern in [
        r'"parent_item_count"\s*:\s*(\d+)',
        r'"parentItemCount"\s*:\s*(\d+)',
        r'"totalVariationCount"\s*:\s*(\d+)',
        r'"totalVariations"\s*:\s*(\d+)',
        r'"variationCount"\s*:\s*(\d+)',
    ]:
        for m in re.finditer(pattern, html_text, flags=re.IGNORECASE):
            structural_candidates.append(int(m.group(1)))

    # 2) Amazon 常见脚本字段：dimensionValuesDisplayData 的 key 数量
    m_dim = re.search(
        r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,\s*"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_dim:
        try:
            dim_map = json.loads(m_dim.group(1))
            if isinstance(dim_map, dict):
                structural_candidates.append(len(dim_map))
        except Exception:
            pass

    # 3) 优先按完整的 twister/variation 列表容器统计，避免被内部嵌套 div 提前截断。
    option_list_blocks = re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*(?:dimension-values-list|a-button-toggle-group)[^"\']*["\'][^>]*>.*?</ul>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in option_list_blocks:
        direct_option_count = _count_option_lis(block)
        if direct_option_count > 0:
            structural_candidates.append(direct_option_count)

    # 4) 再兼容旧版 variation/twister 容器。
    variation_blocks = re.findall(
        r'<(?:div|ul)\b[^>]+id=["\'](?:variation_[^"\']+|tp-inline-twister-[^"\']+|inline-twister-[^"\']+)["\'][^>]*>.*?</(?:div|ul)>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in variation_blocks:
        direct_option_count = _count_option_lis(block)
        if direct_option_count > 0:
            structural_candidates.append(direct_option_count)

    # 5) 最后兜底：直接统计页面中所有 inline twister 的真实选项 li。
    global_twister_lis = _extract_candidate_li_tags(html_text)
    if global_twister_lis:
        structural_candidates.append(_count_option_lis("".join(global_twister_lis)))

    valid = [x for x in structural_candidates if x > 0]
    if not valid:
        return 0

    counter = Counter(valid)
    most_common_count, most_common_freq = counter.most_common(1)[0]
    if most_common_freq >= 2:
        return most_common_count

    valid.sort()
    return valid[len(valid) // 2]


def _extract_amazon_variants(raw_html: str = "") -> List[Dict[str, Any]]:
    html_text = raw_html or ""
    if not html_text:
        return []

    def _find_li_blocks(block: str) -> List[str]:
        return re.findall(
            r'<li\b[^>]*data-asin=["\'][^"\']+["\'][^>]*>.*?</li>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

    list_blocks = re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*(?:dimension-values-list|a-button-toggle-group)[^"\']*["\'][^>]*>.*?</ul>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    li_blocks: List[str] = []
    for block in list_blocks:
        li_blocks.extend(_find_li_blocks(block))

    if not li_blocks:
        li_blocks = _find_li_blocks(html_text)

    variants: List[Dict[str, Any]] = []
    seen_asins = set()

    for li_block in li_blocks:
        asin_match = re.search(r'data-asin=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
        asin = (asin_match.group(1).strip() if asin_match else "")
        if not asin or asin in seen_asins:
            continue
        seen_asins.add(asin)

        name = ""
        img_match = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
        if img_match:
            name = html.unescape(img_match.group(1)).strip()
        if not name:
            label_match = re.search(r'aria-label=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
            if label_match:
                name = html.unescape(label_match.group(1)).strip()
        if not name:
            title_match = re.search(r'title=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
            if title_match:
                name = html.unescape(title_match.group(1)).strip()
        if not name:
            name = asin

        price = None
        price_patterns = [
            r'class=["\'][^"\']*apex-pricetopay-value[^"\']*["\'][^>]*>.*?<span aria-hidden=["\']true["\']>(.*?)</span>',
            r'class=["\'][^"\']*twister_swatch_price[^"\']*["\'][^>]*>.*?<span class=["\'][^"\']*olpWrapper[^"\']*["\']>(.*?)</span>',
            r'<span aria-hidden=["\']true["\']>\s*([^<]*?(?:[$€£]|JPY|USD)[^<]*)</span>',
        ]
        for pattern in price_patterns:
            m = re.search(pattern, li_block, flags=re.IGNORECASE | re.DOTALL)
            if not m:
                continue
            candidate = re.sub(r'<[^>]+>', ' ', m.group(1))
            candidate = re.sub(r'\s+', ' ', html.unescape(candidate)).strip()
            if candidate:
                price = candidate
                break

        unavailable = bool(
            re.search(r'data-initiallyunavailable=["\']true["\']', li_block, flags=re.IGNORECASE)
            or re.search(r'\ba-button-unavailable\b', li_block, flags=re.IGNORECASE)
            or re.search(r'\bdefault-slot-unavailable\b', li_block, flags=re.IGNORECASE)
        )

        variants.append(
            {
                "variant_name": name,
                "price": price,
                "is_available": not unavailable,
            }
        )

    return variants


def _normalize_amazon_data(data: dict, raw_html: str = "", markdown_text: str = "") -> dict:
    if not isinstance(data, dict):
        return {}

    aliases = [
        "parent_item_count",
        "parentItemCount",
        "total_variants",
        "total_variant_count",
        "variant_count",
        "amazon_total_variants",
    ]

    alias_counts: List[int] = []
    for key in aliases:
        if key in data:
            coerced = _coerce_int(data.get(key))
            if coerced > 0:
                alias_counts.append(coerced)

    structural_count = _extract_amazon_parent_item_count(raw_html=raw_html, markdown_text=markdown_text)
    count = structural_count if structural_count > 0 else 0

    if count <= 0 and alias_counts:
        alias_counter = Counter(alias_counts)
        count = alias_counter.most_common(1)[0][0]

    parsed_variants = _extract_amazon_variants(raw_html=raw_html)
    variants = data.get("variants")
    if not isinstance(variants, list):
        variants = []

    if parsed_variants and len(parsed_variants) >= len(variants):
        data["variants"] = parsed_variants
        variants = parsed_variants

    if isinstance(variants, list) and variants:
        count = max(count, len(variants))

    data["parent_item_count"] = max(count, 0)
    return data


def build_amazon_fallback_data(raw_html: str) -> dict:
    title = _extract_text_by_id(raw_html, "productTitle")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()

    if not title:
        return {}

    segments = re.split(r"[,|\|:]|\s-\s", title)
    model = segments[0].strip() if segments else ""
    if re.match(r"^B[A-Z0-9]{9}$", model):
        model = segments[1].strip() if len(segments) > 1 else ""

    data = {
        "product_title": title,
        "main_price": _extract_amazon_price(raw_html) or None,
        "model_number": model or None,
        "variants": _extract_amazon_variants(raw_html),
        "parent_item_count": _extract_amazon_parent_item_count(raw_html=raw_html),
    }
    if data["variants"]:
        data["parent_item_count"] = max(data["parent_item_count"], len(data["variants"]))
    return data


async def fetch_amazon_data_dom_fallback(url: str, asin: str, browser_cfg: BrowserConfig) -> dict:
    try:
        log_progress(asin, "🛟 触发 DOM 回退提取（跳过 LLM）...")
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            res = await asyncio.wait_for(
                crawler.arun(
                    url=url,
                    config=CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        css_selector="#productTitle, #dp-container, #main",
                        wait_for="css:#productTitle, #dp-container, #main",
                        wait_until="commit",
                        excluded_tags=['script', 'style', 'path', 'svg', 'nav', 'footer', 'header', 'aside', 'iframe', 'canvas', 'noscript', 'form'],
                        excluded_selector="#nav-belt, #nav-main, #nav-footer, #navbar, #apb-desktop-browse-navigation-left-column",
                        process_iframes=False,
                        remove_overlay_elements=True,
                        page_timeout=25000
                    )
                ),
                timeout=35
            )
    except Exception as e:
        log_progress(asin, f"❌ DOM 回退提取失败: {str(e)}")
        return {"data": {}, "error": "Amazon Timeout"}

    if not res.success:
        err = getattr(res, "error_message", "DOM fallback crawl failed")
        log_progress(asin, f"❌ DOM 回退抓取失败: {err}")
        return {"data": {}, "error": "Amazon Timeout"}

    raw_html = res.cleaned_html or ""
    data = build_amazon_fallback_data(raw_html)
    if not data.get("product_title"):
        log_progress(asin, "❌ DOM 回退未提取到标题")
        return {"data": {}, "error": "Amazon Timeout"}

    log_progress(asin, "✅ DOM 回退提取成功，避免空返回")
    db_cache.set(f"amz_{asin}", data, expire=CACHE_EXPIRY_SEC)
    return {"data": data, "error": None}


# ===== 抓取核心逻辑 (完全剥离，单实例进入) =====

async def fetch_amazon_data_multilayer(
    url: str, asin: str, llm_cfg, browser_cfg: BrowserConfig, allow_profile_fallback: bool = True
) -> dict:
    if not DEBUG_MODE:
        cached_data = db_cache.get(f"amz_{asin}")
        if cached_data: 
            log_progress(asin, "🛒 Amazon 数据已存在缓存中，跳过抓取")
            return {"data": cached_data, "error": None}

    log_progress(asin, "🛒 准备获取 Amazon 数据...")

    try:
        result = await fetch_amazon_data_js(
            url=url,
            asin=asin,
            profile_dir=AMAZON_PROFILE,
            headless=browser_cfg.headless,
            user_agent=getattr(browser_cfg, "user_agent", None) or "",
            extra_args=getattr(browser_cfg, "extra_args", None) or [],
            log_progress=log_progress,
        )
        if not result.get("error"):
            log_progress(asin, "✅ Amazon JS 提取成功，进入缓存")
            db_cache.set(f"amz_{asin}", result["data"], expire=CACHE_EXPIRY_SEC)
        else:
            log_progress(asin, f"❌ Amazon JS 抓取失败: {result.get('error')}")
        return result
    except asyncio.TimeoutError:
        log_progress(asin, "⏰ Amazon 抓取超时，尝试 DOM 回退")
        return await fetch_amazon_data_dom_fallback(url, asin, browser_cfg)
    except Exception as e:
        if allow_profile_fallback and is_profile_lock_error(e):
            fallback_profile = tempfile.mkdtemp(prefix="amz-fallback-", dir="/tmp")
            log_progress(asin, f"⚠️ Amazon Profile 被占用，切换临时 Profile 重试: {fallback_profile}")
            try:
                fallback_cfg = BrowserConfig(
                    browser_type="chromium",
                    headless=browser_cfg.headless,
                    use_persistent_context=True,
                    user_data_dir=fallback_profile,
                    extra_args=getattr(browser_cfg, "extra_args", None),
                    user_agent=getattr(browser_cfg, "user_agent", None),
                )
                return await fetch_amazon_data_multilayer(
                    url=url,
                    asin=asin,
                    llm_cfg=llm_cfg,
                    browser_cfg=fallback_cfg,
                    allow_profile_fallback=False,
                )
            finally:
                shutil.rmtree(fallback_profile, ignore_errors=True)

        log_progress(asin, f"💥 Amazon 抓取异常: {str(e)}")
        return {"data": {}, "error": str(e)}

# ===== 主控制流 =====

async def main_worker(manual_urls: Optional[List[str]] = None, debug_mode: Optional[bool] = None, skip_lock: bool = False, outfile: Optional[str] = None, task_type: str = "both"):
    # 强制单实例运行，保护浏览器 Profile
    lock = CrawlerLock(lock_file=f"/tmp/crawler_{task_type}.lock")
    if not skip_lock and not lock.acquire():
        sys.stderr.write(f"⚠️ 另一个 {task_type} 爬虫实例正在运行中，已退出。\n")
        return []

    results = []
    try:
        global SETTINGS, DEBUG_MODE
        # 重新加载 settings.json 基础配置
        SETTINGS = load_settings() 
        
        # 优先级：函数参数 > 配置文件
        if debug_mode is not None:
            DEBUG_MODE = debug_mode
        else:
            DEBUG_MODE = SETTINGS.get("DEBUG_MODE", False)

        ensure_dir(AMAZON_PROFILE)
        ensure_dir(SIF_PROFILE)
        
        llm_s = SETTINGS.get("LLM", {})
        provider = llm_s.get("provider", "gpt-5.4-nano")
        # 自动补充 openai/ 前缀（针对某些 LiteLLM 配置）
        if "/" not in provider: provider = f"openai/{provider}"
    
        llm_cfg = LLMConfig(
            provider=provider,
            api_token=llm_s.get("api_token", ""),
            base_url=llm_s.get("base_url", ""), 
            temperature=llm_s.get("temperature", 0)
        )
    
        common_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox"]
        sif_args = common_args + ["--window-size=1600,1200"]
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        sif_user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

        # 容器内通常没有 X server，必须无头；本地开发默认保留可视化。
        in_docker = os.getenv("DOCKER_ENV") == "1"
        amz_headless = True if in_docker else False

        amz_cfg = BrowserConfig(browser_type="chromium", headless=amz_headless, use_persistent_context=True, user_data_dir=AMAZON_PROFILE, extra_args=common_args, user_agent=user_agent)
        sif_headless = True if in_docker else env_flag("SIF_HEADLESS", False)

        sif_cfg = build_sif_browser_config(
            profile_dir=SIF_PROFILE,
            headless=sif_headless,
            extra_args=sif_args,
            user_agent=sif_user_agent,
            viewport={"width": 1600, "height": 1200},
        )
    
        # 确定待爬取列表 (仅支持从外部手动传入)
        urls = manual_urls or []
        if not urls:
            sys.stderr.write("⚠️ 未提供待处理的 URL，程序退出。\n")
            return []

        normalized_inputs: List[Dict[str, str]] = []
        for raw_input in urls:
            normalized = normalize_amazon_input(raw_input)
            if normalized.get("ok"):
                normalized_inputs.append(
                    {
                        "url": normalized.get("url", ""),
                        "asin": normalized.get("asin", "UNKNOWN"),
                        "raw": str(raw_input),
                    }
                )
                continue

            asin = extract_asin(str(raw_input or "")) or "UNKNOWN"
            reason = normalized.get("error", "Invalid input")
            log_progress(asin, f"❌ 输入无效，已跳过: {raw_input} | 原因: {reason}")
            results.append(
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "asin": asin,
                    "status": "PARTIAL",
                    "failure_reason": f"Invalid Input: {reason}",
                    "amazon_title": "",
                    "amazon_price": "",
                    "amazon_list_price": "",
                    "amazon_savings_text": "",
                    "amazon_has_price_discount": False,
                    "amazon_deal_type": "",
                    "amazon_is_limited_time_deal": False,
                    "amazon_coupon_text": "",
                    "amazon_applied_coupon_text": "",
                    "amazon_has_coupon": False,
                    "amazon_model": "",
                    "amazon_total_variants": 0,
                    "amazon_variants": [],
                    "sif_1_kw": "",
                    "full_sif": [],
                }
            )

        if not normalized_inputs:
            sys.stderr.write("⚠️ 输入均无效，程序退出。\n")
            return results
        
        batch_s = SETTINGS.get("BATCH", {})
        # 处理输出文件路径
        current_outfile = outfile
        if current_outfile:
            if not os.path.isabs(current_outfile):
                current_outfile = os.path.join(BASE_DIR, current_outfile)

        for i, item in enumerate(normalized_inputs, 1):
            t0 = time.time()
            url = item.get("url", "")
            asin = item.get("asin", "UNKNOWN")
            log_progress(asin, f"🚀 开始处理项目 [{i}/{len(normalized_inputs)}] | URL: {url}")
            
            try:
                # --- 步骤 1: Amazon (仅当任务类型为 both 或 amazon) ---
                amz_res = {"data": {}, "error": None}
                if task_type in ["both", "amazon"]:
                    log_progress(asin, "➡️ 进入 Amazon 阶段")
                    for attempt in range(3):
                        force_kill_browsers()
                        clean_lock(AMAZON_PROFILE)
                        amz_res = await fetch_amazon_data_multilayer(url, asin, llm_cfg, amz_cfg)
                        if not amz_res.get("error"): break
                        log_progress(asin, f"🔄 Amazon 阶段失败，正在进行重试 [{attempt+1}/3]...")
                
                # --- 步骤 2: SIF (仅当任务类型为 both 或 sif) ---
                sif_res = {"data": [], "error": None}
                if task_type in ["both", "sif"]:
                    log_progress(asin, "➡️ 进入 SIF 阶段")
                    for attempt in range(3):
                        if should_cleanup_sif_browser():
                            force_kill_browsers()
                            clean_lock(SIF_PROFILE)
                        sif_res = await fetch_sif_data_multilayer(
                            asin,
                            llm_cfg,
                            sif_cfg,
                            db_cache=db_cache,
                            cache_expiry_sec=CACHE_EXPIRY_SEC,
                            debug_mode=DEBUG_MODE,
                            log_progress=log_progress,
                            base_dir=BASE_DIR,
                        )
                        if not sif_res.get("error"): break
                        log_progress(asin, f"🔄 SIF 阶段失败，正在进行重试 [{attempt+1}/3]...")
                
                # --- 数据处理 ---
                log_progress(asin, "💾 正在整合并保存数据...")
                amazon_data = amz_res.get("data", {})
                sif_rankings = sif_res.get("data", [])
                extra_errors = []
                if task_type in ["both", "amazon"] and not safe_get(amazon_data, "product_title"):
                    extra_errors.append("Amazon Empty Data")
                if task_type in ["both", "sif"] and not sif_rankings and not sif_res.get("error"):
                    extra_errors.append("SIF Empty Data")
                combined_error = "; ".join(
                    filter(None, [amz_res.get("error"), sif_res.get("error"), *extra_errors])
                )

                save_rec = {
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'asin': asin,
                    'status': "SUCCESS" if not combined_error else "PARTIAL",
                    'failure_reason': combined_error,
                    'amazon_title': safe_get(amazon_data, 'product_title'),
                    'amazon_price': safe_get(amazon_data, 'main_price'),
                    'amazon_list_price': safe_get(amazon_data, 'list_price'),
                    'amazon_savings_text': safe_get(amazon_data, 'savings_text'),
                    'amazon_has_price_discount': bool(amazon_data.get('has_price_discount', False)) if isinstance(amazon_data, dict) else False,
                    'amazon_deal_type': safe_get(amazon_data, 'deal_type'),
                    'amazon_is_limited_time_deal': bool(amazon_data.get('is_limited_time_deal', False)) if isinstance(amazon_data, dict) else False,
                    'amazon_coupon_text': safe_get(amazon_data, 'coupon_text'),
                    'amazon_applied_coupon_text': safe_get(amazon_data, 'applied_coupon_text'),
                    'amazon_has_coupon': bool(amazon_data.get('has_coupon', False)) if isinstance(amazon_data, dict) else False,
                    'amazon_model': safe_get(amazon_data, 'model_number'),
                    'amazon_total_variants': safe_get(amazon_data, 'parent_item_count', 0),
                    'amazon_variants': safe_get(amazon_data, 'variants', []),
                    'sif_1_kw': safe_get(sif_rankings[0], 'keyword') if sif_rankings else '',
                    'full_sif': sif_rankings
                }
                
                results.append(save_rec)
                
                # 只有在提供了 outfile 时才写入文件
                if current_outfile:
                    with open(current_outfile, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(save_rec, ensure_ascii=False) + '\n')
                        f.flush()
                
                elapsed = round(time.time() - t0, 1)
                print(f"✓ {i}/{len(normalized_inputs)} [{asin}] {elapsed}s | Err: {combined_error or 'None'}")

            except Exception as e:
                sys.stderr.write(f"💥 循环内异常: {str(e)}\n")
            finally:
                if task_type in ["both", "sif"] and should_cleanup_sif_browser():
                    force_kill_browsers() # 每轮结束清理
    except Exception as e:
        sys.stderr.write(f"💥 main_worker 异常: {str(e)}\n")
    finally:
        if not skip_lock:
            lock.release()
    return results

if __name__ == "__main__":
    try:
        asyncio.run(main_worker())
    finally:
        db_cache.close()
        if not USE_SIF_DAEMON:
            os.system("pkill -9 -f chrome 2>/dev/null")
