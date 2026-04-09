import os
import sys
import asyncio
import json
import re
import html
import logging
import subprocess
import time
import faulthandler
import diskcache
import tempfile
import shutil
from typing import List, Optional, Dict, Any
from datetime import datetime
from urllib.parse import urlparse

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
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
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
DISABLE_CACHE = os.getenv("DISABLE_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}
DEBUG_MODE = False # Default, will be updated in main_worker

# 初始化高性能缓存
os.makedirs(CACHE_DIR, exist_ok=True)
db_cache = diskcache.Cache(CACHE_DIR)


def cache_get(key: str):
    if DISABLE_CACHE:
        return None
    return db_cache.get(key)


def cache_set(key: str, value, expire: int):
    if DISABLE_CACHE:
        return
    db_cache.set(key, value, expire=expire)

# 数据模型
class ProductVariant(BaseModel):
    variant_name: str = Field(..., description="变体名称")
    price: Optional[str] = Field(None, description="变体价格")
    is_available: bool = Field(..., description="是否可用")

class AmazonData(BaseModel):
    product_title: str = Field(..., description="商品标题")
    main_price: Optional[str] = Field(None, description="主商品价格")
    model_number: Optional[str] = Field(None, description="型号，优先从商品标题的第一段提取")
    variants: Optional[List[ProductVariant]] = Field(None, description="前3个核心变体列表")
    parent_item_count: int = Field(0, description="变体总数量")

class SifRanking(BaseModel):
    keyword: str = Field(..., description="关键词名称")
    organic_rank: str = Field(..., description="自然排名，格式如 P1-1")
    ad_rank: str = Field(..., description="广告排名，格式如 P1-1/SP，无广告填 -")

class SifData(BaseModel):
    asin: str = Field(..., description="ASIN")
    top_rankings: List[SifRanking] = Field(..., description="排名列表")

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


def _extract_asin(candidate: str) -> Optional[str]:
    if not candidate:
        return None
    match = re.search(r"\b(B[A-Z0-9]{9})\b", candidate.upper())
    return match.group(1) if match else None


def _build_canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


def normalize_amazon_input(input_value: Any) -> Dict[str, Any]:
    """将用户输入统一成可用于 crawl4ai 的 Amazon URL，并尽量提取 ASIN。"""
    raw = str(input_value or "").strip()
    if not raw:
        return {"ok": False, "error": "Empty URL/ASIN"}

    asin_direct = _extract_asin(raw)
    if asin_direct and re.fullmatch(r"B[A-Z0-9]{9}", raw.upper()):
        return {"ok": True, "url": _build_canonical_amazon_url(asin_direct), "asin": asin_direct}

    # 兼容缺少 scheme 的常见写法（如 www.amazon.com/...）
    normalized = raw
    if re.match(r"^(www\.)", normalized, flags=re.IGNORECASE):
        normalized = f"https://{normalized}"
    elif re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", normalized, flags=re.IGNORECASE):
        normalized = f"https://{normalized}"

    allowed_prefixes = ("http://", "https://", "file://", "raw:")
    if not normalized.lower().startswith(allowed_prefixes):
        if asin_direct:
            return {"ok": True, "url": _build_canonical_amazon_url(asin_direct), "asin": asin_direct}
        return {"ok": False, "error": "Invalid URL scheme"}

    asin = asin_direct
    if normalized.lower().startswith(("http://", "https://")):
        parsed = urlparse(normalized)
        host = (parsed.netloc or "").lower()
        is_amazon = "amazon." in host or host.endswith("amzn.to")
        if is_amazon and asin:
            normalized = _build_canonical_amazon_url(asin)
        elif is_amazon and not asin:
            return {"ok": False, "error": "Amazon URL missing ASIN"}

    return {"ok": True, "url": normalized, "asin": asin or "UNKNOWN"}


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
    candidates: List[int] = []
    html_text = raw_html or ""
    md_text = markdown_text or ""
    merged_text = f"{html_text}\n{md_text}"

    # 1) 直接读取常见字段
    for pattern in [
        r'"parent_item_count"\s*:\s*(\d+)',
        r'"parentItemCount"\s*:\s*(\d+)',
        r'"totalVariationCount"\s*:\s*(\d+)',
        r'"totalVariations"\s*:\s*(\d+)',
        r'"variationCount"\s*:\s*(\d+)',
    ]:
        for m in re.finditer(pattern, html_text, flags=re.IGNORECASE):
            candidates.append(int(m.group(1)))

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
                candidates.append(len(dim_map))
        except Exception:
            pass

    # 3) 页面上显式文案，如 "9 options" / "9 variants"
    for m in re.finditer(r"\b(\d{1,3})\s+(?:options?|variants?)\b", merged_text, flags=re.IGNORECASE):
        candidates.append(int(m.group(1)))

    # 4) 仅在 variation 区块中统计可选项数量，避免把推荐位误计入
    variation_blocks = re.findall(
        r'<[^>]+id=["\']variation_[^"\']+["\'][^>]*>.*?</(?:ul|div)>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in variation_blocks:
        option_count = len(
            re.findall(
                r'<li[^>]+(?:data-defaultasin|data-csa-c-item-id|data-value)=',
                block,
                flags=re.IGNORECASE,
            )
        )
        if option_count > 0:
            candidates.append(option_count)

    valid = [x for x in candidates if x > 0]
    return max(valid) if valid else 0


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

    count = 0
    for key in aliases:
        if key in data:
            count = max(count, _coerce_int(data.get(key)))

    if count <= 0:
        count = _extract_amazon_parent_item_count(raw_html=raw_html, markdown_text=markdown_text)

    variants = data.get("variants")
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
        "variants": [],
        "parent_item_count": _extract_amazon_parent_item_count(raw_html=raw_html),
    }
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


def detect_sif_auth_state(url: str = "", text: str = "", status_code: Optional[int] = None) -> str:
    """
    识别 SIF 页面状态：
    - login_required: 登录失效/需要重新登录
    - challenge: 验证码/风控拦截
    - ok: 未发现明显异常
    """
    u = (url or "").lower()
    t = (text or "").lower()

    # 明确的未登录/被踢线信号（避免用“登录”这种弱词造成误判）
    hard_login_signals = [
        "session expired",
        "unauthorized",
        "请先登录",
        "在别的浏览器登录",
        "账号异常登录提醒",
        "为了保证您的账号安全，我们已将您的账号从本浏览器退出",
        "我知道了重新登录",
        "手机号",
        "手机号码",
        "密码",
    ]
    # 常见已登录页面特征（出现时优先判定为 ok）
    logged_in_markers = [
        "查销量",
        "反查流量",
        "广告透视仪",
        "流量时光机",
        "会员购买",
        "到期",
    ]
    challenge_signals = [
        "captcha", "robot", "verify", "verification",
        "验证码", "人机验证", "安全验证",
    ]

    if status_code in (401, 403):
        return "login_required"
    if "/login" in u or "/signin" in u:
        return "login_required"
    if any(k in t for k in challenge_signals):
        return "challenge"
    # 页面出现明显业务菜单时，优先视为已登录，避免因页面中包含“登录”字样误判
    if any(k in t for k in logged_in_markers):
        return "ok"
    if any(k in t for k in hard_login_signals):
        return "login_required"
    return "ok"


def try_refresh_sif_profile(asin: str) -> dict:
    """触发外部登录脚本修补 SIF Profile。"""
    log_progress(asin, "❌ SIF 登录已失效或需要登录，准备进入自动登录流程...")
    try:
        login_script = os.path.join(BASE_DIR, "sif_login.py")
        log_progress(asin, "🔑 运行 sif_login.py 修补 Profile...")
        login_proc = subprocess.run([sys.executable, login_script], capture_output=True, text=True)
        if login_proc.returncode == 0:
            log_progress(asin, "✅ SIF 自动登录修补成功！")
            return {"data": [], "error": "SIF Session Refreshed, please retry"}
        log_progress(asin, f"❌ SIF 自动登录失败: {login_proc.stderr}")
        return {"data": [], "error": "SIF Login Failed"}
    except Exception as e:
        log_progress(asin, f"❌ 自动登录尝试异常: {str(e)}")
        return {"data": [], "error": f"SIF Login Exception: {str(e)}"}


async def probe_sif_session_after_timeout(sif_url: str, asin: str, browser_cfg: BrowserConfig) -> str:
    """
    当主抓取超时时，做一次轻量探测来判断是否为登录失效或风控拦截。
    返回: login_required | challenge | unknown
    """
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            probe = await asyncio.wait_for(
                crawler.arun(
                    url=sif_url,
                    config=CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        css_selector="body",
                        wait_for="css:body",
                        wait_until="domcontentloaded",
                        process_iframes=False,
                        remove_overlay_elements=True,
                        page_timeout=15000,
                    ),
                ),
                timeout=25,
            )

            merged_text = " ".join(
                filter(
                    None,
                    [
                        getattr(probe, "error_message", ""),
                        probe.markdown or "",
                        probe.cleaned_html or "",
                    ],
                )
            )
            state = detect_sif_auth_state(
                url=probe.url or sif_url,
                text=merged_text,
                status_code=probe.status_code,
            )
            log_progress(asin, f"🧪 超时后登录态探测结果: {state}")
            if state in ("login_required", "challenge"):
                return state
    except Exception as e:
        log_progress(asin, f"⚠️ 超时后探测失败: {str(e)}")
    return "unknown"

# ===== 抓取核心逻辑 (完全剥离，单实例进入) =====

async def fetch_amazon_data_multilayer(
    url: str, asin: str, llm_cfg, browser_cfg: BrowserConfig, allow_profile_fallback: bool = True
) -> dict:
    if not DEBUG_MODE:
        cached_data = cache_get(f"amz_{asin}")
        if cached_data: 
            log_progress(asin, "🛒 Amazon 数据已存在缓存中，跳过抓取")
            return {"data": cached_data, "error": None}

    log_progress(asin, "🛒 准备获取 Amazon 数据...")
    
    amz_extract = LLMExtractionStrategy(
        llm_config=llm_cfg, schema=AmazonData.model_json_schema(),
        instruction="提取商品名称 (product_title), 主价格 (main_price), 型号 (model_number), 变体列表 (variants: 只需前3个), 变体总数量 (parent_item_count)。注意：型号优先从标题第一个逗号前提取。范围在 #dp-container。"
    )
    
    try:
        log_progress(asin, "🛒 正在启动 Amazon 浏览器实例...")
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            log_progress(asin, "🛒 Amazon 页面加载中 (arun)...")
            res = await asyncio.wait_for(
                crawler.arun(
                    url=url,
                    config=CrawlerRunConfig(
                        extraction_strategy=amz_extract, 
                        markdown_generator=DefaultMarkdownGenerator(content_filter=None), 
                        cache_mode=CacheMode.BYPASS, 
                        css_selector="#dp-container, #main, #container, .a-section", 
                        wait_for="css:#productTitle, #dp-container, .a-error-code, #g, .s-result-list",
                        wait_until="commit", # 最新推荐策略：配合 wait_for 使用，极致性能
                        # 极其激进的标签排除
                        excluded_tags=['script', 'style', 'path', 'svg', 'nav', 'footer', 'header', 'aside', 'iframe', 'canvas', 'noscript', 'form'],
                        excluded_selector="#nav-belt, #nav-main, #nav-footer, #navbar, #apb-desktop-browse-navigation-left-column",
                        process_iframes=False,
                        remove_overlay_elements=True,
                        page_timeout=35000 
                    )
                ),
                timeout=50 # 额外给 LLM 留些余地
            )
            log_progress(asin, f"✨ Amazon arun 返回成功: {res.success} | Status: {res.status_code}")
            
            # --- 快速失败判定 ---
            if not res.success:
                err = getattr(res, 'error_message', '提取失败或内容为空')
                log_progress(asin, f"❌ Amazon 加载失败: {err}")
                return {"data": {}, "error": err}

            # 1. 状态码判定
            if res.status_code == 404:
                log_progress(asin, "❌ 页面不存在 (404)")
                return {"data": {}, "error": "Amazon 404: Page Not Found"}
            if res.status_code == 503 or "robot check" in (res.markdown or "").lower():
                log_progress(asin, "❌ 触发机器人检查或被拦截 (503/CAPTCHA)")
                return {"data": {}, "error": "Amazon Blocked/CAPTCHA"}

            # 2. 页面类型判定 (利用 wait_for 捕获的特征)
            cleaned_content = (res.cleaned_html or "").lower()
            if "s-result-list" in cleaned_content or "search-results" in cleaned_content:
                log_progress(asin, "⚠️ 检测到搜索结果页，非商品详情页")
                return {"data": {}, "error": "Wrong Page Type: Search Results"}

            if res.extracted_content:
                log_progress(asin, "✨ AI 正在解析 Amazon JSON 内容...")
                data = json.loads(res.extracted_content)
                if isinstance(data, list) and len(data) > 0: data = data[0]
                data = _normalize_amazon_data(
                    data,
                    raw_html=res.cleaned_html or "",
                    markdown_text=res.markdown or "",
                )
                
                # 3. 最终标题校验
                if not data.get("product_title"):
                    log_progress(asin, "⚠️ 页面结构不匹配，无法提取标题")
                    return {"data": {}, "error": "Invalid Product Page: No Title Found"}

                # 额外补充逻辑：模型通常是标题的第一段（以逗号、中划线、竖线分隔）
                current_model = str(data.get("model_number") or "").strip()
                is_asin = bool(re.match(r'^B[A-Z0-9]{9}$', current_model))
                
                if not current_model or current_model in ["N/A", "None", ""] or is_asin:
                    title = data.get("product_title", "")
                    # 匹配逗号、竖线、冒号、或带有空格的中划线
                    segments = re.split(r'[,|\|:]|\s-\s', title)
                    if segments:
                        candidate = segments[0].strip()
                        # 再次检查候选词是否为 ASIN
                        if not re.match(r'^B[A-Z0-9]{9}$', candidate):
                            data["model_number"] = candidate
                        elif len(segments) > 1:
                            data["model_number"] = segments[1].strip()
                
                if DISABLE_CACHE:
                    log_progress(asin, "✅ Amazon 数据提取成功，已跳过缓存写入")
                else:
                    log_progress(asin, "✅ Amazon 数据提取成功，进入缓存")
                cache_set(f"amz_{asin}", data, expire=CACHE_EXPIRY_SEC)
                return {"data": data, "error": None}
            
            err = getattr(res, 'error_message', '提取失败或内容为空')
            log_progress(asin, f"❌ Amazon 抓取失败: {err}")
            return {"data": {}, "error": err}
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

async def fetch_sif_data_multilayer(asin: str, llm_cfg, browser_cfg: BrowserConfig) -> dict:
    if not DEBUG_MODE:
        cached_rankings = cache_get(f"sif_{asin}")
        if cached_rankings: 
            log_progress(asin, "🔍 SIF 数据已存在缓存中，跳过抓取")
            return {"data": cached_rankings, "error": None}

    sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=0"
    log_progress(asin, "🔍 准备获取 SIF 数据...")
    
    sif_extract = LLMExtractionStrategy(
        llm_config=llm_cfg, schema=SifData.model_json_schema(),
        instruction="只在 .asin-keyword 标识的表格区域内，提取排在最前面的前 3 行关键词排名。Px-y 格式，广告位加 /SP。无排名填 -。"
    )
    
    try:
        log_progress(asin, "🔍 正在启动 SIF 浏览器实例...")
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            js_scroll = [
                "const el = document.querySelector('.asin-keyword'); if(el) { el.scrollIntoView(); window.scrollBy(0, -100); }",
                "(function() {"
                "  const pruner = () => {"
                "    const rows = document.querySelectorAll('.asin-keyword tr, .asin-keyword .keyword-row');"
                "    if (rows.length > 10) {"
                "      for (let i = 10; i < rows.length; i++) rows[i].remove();"
                "    }"
                "  };"
                "  pruner();"
                "})();",
                "await new Promise(r => setTimeout(r, 3000));"
            ]
            log_progress(asin, "🔍 SIF 页面加载中 (arun)...")
            res = await asyncio.wait_for(
                crawler.arun(
                    url=sif_url,
                    config=CrawlerRunConfig(
                        extraction_strategy=sif_extract, 
                        markdown_generator=DefaultMarkdownGenerator(content_filter=None), 
                        cache_mode=CacheMode.BYPASS, 
                        # 先保证页面可用，避免站点改版/登录重定向导致严格选择器直接超时
                        css_selector="body", 
                        wait_for="css:body",
                        wait_until="commit",
                        excluded_tags=['script', 'style', 'path', 'svg', 'nav', 'footer', 'header', 'aside', 'iframe', 'canvas', 'noscript', 'form'],
                        excluded_selector="#header, #footer, .navbar, .sidebar",
                        js_code=js_scroll,
                        process_iframes=False,
                        remove_overlay_elements=True,
                        page_timeout=40000
                    )
                ),
                timeout=55
            )
            log_progress(asin, f"✨ SIF arun 返回成功: {res.success} | Status: {res.status_code}")

            # 常见拦截判定：重定向到了登录页 或 出现了账号冲突/强制退出提示
            merged_probe_text = " ".join(
                filter(
                    None,
                    [
                        getattr(res, "error_message", ""),
                        res.markdown or "",
                        res.cleaned_html or "",
                    ],
                )
            )
            auth_state = detect_sif_auth_state(
                url=res.url or "",
                text=merged_probe_text,
                status_code=res.status_code,
            )
            redirect_to_login = auth_state == "login_required"
            blocked_by_challenge = auth_state == "challenge"

            final_url = (res.url or "").lower()
            requested_reverse = "/reverse" in sif_url.lower()
            landed_on_home = final_url.endswith("sif.com/") or final_url.endswith("sif.com")
            left_reverse_page = requested_reverse and final_url and "/reverse" not in final_url
            if auth_state == "ok" and (landed_on_home or left_reverse_page):
                redirect_to_login = True

            if redirect_to_login:
                return try_refresh_sif_profile(asin)
            if blocked_by_challenge:
                log_progress(asin, "❌ SIF 页面触发验证码/风控拦截")
                return {"data": [], "error": "SIF Challenge/CAPTCHA"}
            
            # --- SIF 快速失败判定 ---
            if not res.success:
                err = getattr(res, 'error_message', 'SIF 页面加载失败')
                log_progress(asin, f"❌ SIF 加载失败: {err}")
                return {"data": [], "error": err}
                
            if res.status_code and res.status_code >= 400:
                log_progress(asin, f"❌ SIF HTTP 错误: {res.status_code}")
                return {"data": [], "error": f"SIF HTTP {res.status_code}"}

            if res.extracted_content:
                log_progress(asin, "✨ AI 正在解析 SIF JSON 内容...")
                data = json.loads(res.extracted_content)
                if isinstance(data, list) and len(data) > 0: data = data[0]
                if isinstance(data, dict) and data.get("top_rankings"):
                    rankings = data["top_rankings"][:3]
                    if DISABLE_CACHE:
                        log_progress(asin, "✅ SIF 数据提取成功，已跳过缓存写入")
                    else:
                        log_progress(asin, "✅ SIF 数据提取成功，进入缓存")
                    cache_set(f"sif_{asin}", rankings, expire=CACHE_EXPIRY_SEC)
                    return {"data": rankings, "error": None}
            
            # 页面已加载但没有排名，优先提示登录失效，避免误导为普通空数据
            if "asin-keyword" not in (res.cleaned_html or "").lower():
                return {"data": [], "error": "SIF Login Required"}

            err = getattr(res, 'error_message', '') or '未找到 SIF 排名或内容为空'
            log_progress(asin, f"❌ SIF 抓取失败: {err}")
            return {"data": [], "error": err}
    except asyncio.TimeoutError:
        log_progress(asin, "⏰ SIF 抓取超时，开始登录态复核...")
        timeout_state = await probe_sif_session_after_timeout(sif_url, asin, browser_cfg)
        if timeout_state == "login_required":
            return try_refresh_sif_profile(asin)
        if timeout_state == "challenge":
            return {"data": [], "error": "SIF Challenge/CAPTCHA (after timeout)"}
        log_progress(asin, "⏰ SIF 抓取超时 (55s)")
        return {"data": [], "error": "SIF Timeout"}
    except Exception as e:
        log_progress(asin, f"💥 SIF 抓取异常: {str(e)}")
        return {"data": [], "error": str(e)}

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
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

        # 容器内通常没有 X server，必须无头；本地开发默认保留可视化。
        in_docker = os.getenv("DOCKER_ENV") == "1"
        amz_headless = True if in_docker else False

        amz_cfg = BrowserConfig(browser_type="chromium", headless=amz_headless, use_persistent_context=True, user_data_dir=AMAZON_PROFILE, extra_args=common_args, user_agent=user_agent)
        sif_cfg = BrowserConfig(browser_type="chromium", headless=True, use_persistent_context=True, user_data_dir=SIF_PROFILE, extra_args=common_args, user_agent=user_agent)
    
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

            asin = _extract_asin(str(raw_input or "")) or "UNKNOWN"
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
                        force_kill_browsers()
                        clean_lock(SIF_PROFILE)
                        sif_res = await fetch_sif_data_multilayer(asin, llm_cfg, sif_cfg)
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
        os.system("pkill -9 -f chrome 2>/dev/null")
