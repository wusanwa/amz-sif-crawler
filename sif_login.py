import os
import sys
import asyncio
import json
import logging
import argparse
import subprocess
import tempfile
import shutil

# 尝试导入 crawl4ai
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    HAS_CRAWL4AI = True
except ImportError:
    logger = logging.getLogger("sif-login-ai")
    logger.error("❌ 未检测到 crawl4ai，这是核心依赖。")
    sys.exit(1)

from sif_runtime import build_sif_browser_config, resolve_sif_browser_path

# 配置基础路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")
RUNTIME_ROOT = os.getenv("APP_RUNTIME_ROOT", os.path.join(BASE_DIR, "runtime_data"))
PROFILE_ROOT = os.getenv("PROFILE_ROOT_DIR", os.path.join(RUNTIME_ROOT, "profiles"))
SIF_PROFILE = os.getenv("SIF_PROFILE_DIR", os.path.join(PROFILE_ROOT, "sif"))

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sif-login-ai")

def load_settings():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

SETTINGS = load_settings()
SIF_CONF = SETTINGS.get("SIF", {})
PHONE = SIF_CONF.get("phone", "13714929577")
PASS = SIF_CONF.get("password", "JiaOyu21122")
SESSION_ID = "sif-login-session"

async def safe_wait_dom(page, timeout_ms: int = 6000):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

async def js_click_if(page, script: str, step_name: str) -> bool:
    """优先使用页面内 JS 触发点击，避免模拟鼠标事件路径。"""
    try:
        clicked = await page.evaluate(script)
        if clicked:
            logger.info(f"✅ {step_name}成功(JS)。")
            return True
    except Exception as e:
        logger.warning(f"⚠️ {step_name}执行 JS 异常: {e}")
    return False

async def detect_home_login_block(page) -> bool:
    """检测首页是否存在“注册免费领会员 / 登录”区域；存在则视为未登录。"""
    try:
        return bool(await page.evaluate(
            """
            () => {
                const blocks = Array.from(document.querySelectorAll('div.login'));
                return blocks.some(block => {
                    const buttons = Array.from(block.querySelectorAll('button'));
                    const texts = buttons.map(btn => (btn.textContent || '').replace(/\\s+/g, '').trim());
                    return texts.includes('注册免费领会员') && texts.includes('登录');
                });
            }
            """
        ))
    except Exception:
        return False

async def open_login_dialog_via_js(page) -> bool:
    return await js_click_if(
        page,
        """
        () => {
            const blocks = Array.from(document.querySelectorAll('div.login'));
            for (const block of blocks) {
                const buttons = Array.from(block.querySelectorAll('button'));
                const target = buttons.find(btn => (btn.textContent || '').replace(/\\s+/g, '').trim() === '登录');
                const hasRegister = buttons.some(btn => (btn.textContent || '').replace(/\\s+/g, '').trim() === '注册免费领会员');
                if (target && hasRegister) {
                    target.click();
                    return true;
                }
            }
            return false;
        }
        """,
        "点击首页登录按钮",
    )

async def switch_to_password_login_via_js(page) -> bool:
    return await js_click_if(
        page,
        """
        () => {
            const section = document.querySelector('section.login_type_two');
            if (!section) return false;
            const buttons = Array.from(section.querySelectorAll('button'));
            const target = buttons.find(btn => (btn.textContent || '').replace(/\\s+/g, '').trim() === '手机号密码登录');
            if (!target) return false;
            target.click();
            return true;
        }
        """,
        "切换到手机号密码登录",
    )

async def fill_password_login_form_via_js(page, phone: str, password: str) -> bool:
    try:
        filled = await page.evaluate(
            """
            ({ phone, password }) => {
                const root = document.querySelector('div.limit-content div.login_phone');
                if (!root) return false;
                const inputs = Array.from(root.querySelectorAll('input.el-input__inner, input'));
                const phoneInput = inputs.find(input => {
                    const placeholder = input.getAttribute('placeholder') || '';
                    return input.type === 'number' || placeholder.includes('手机号码') || placeholder.includes('手机');
                });
                const passwordInput = inputs.find(input => {
                    const placeholder = input.getAttribute('placeholder') || '';
                    return input.type === 'password' || placeholder.includes('登录密码') || placeholder.includes('密码');
                });
                if (!phoneInput || !passwordInput) return false;

                const setNativeValue = (el, value) => {
                    const proto = Object.getPrototypeOf(el);
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) desc.set.call(el, value);
                    else el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                };

                phoneInput.focus();
                setNativeValue(phoneInput, phone);
                passwordInput.focus();
                setNativeValue(passwordInput, password);
                return true;
            }
            """,
            {"phone": phone, "password": password},
        )
        if filled:
            logger.info("✅ 已通过 JS 填写手机号密码。")
            return True
    except Exception as e:
        logger.warning(f"⚠️ JS 填写手机号密码异常: {e}")
    return False

async def submit_password_login_via_js(page) -> bool:
    return await js_click_if(
        page,
        """
        () => {
            const root = document.querySelector('div.limit-content div.login_phone');
            if (!root) return false;
            const buttons = Array.from(root.querySelectorAll('button'));
            const target = buttons.find(btn => (btn.textContent || '').replace(/\\s+/g, '').trim() === '登录');
            if (!target) return false;
            target.click();
            return true;
        }
        """,
        "点击弹窗登录按钮",
    )

def get_session_page(crawler: AsyncWebCrawler, session_id: str):
    """从 crawl4ai 0.8.6 的 browser_manager 会话池中拿到 Playwright Page。"""
    try:
        strategy = getattr(crawler, "crawler_strategy", None)
        manager = getattr(strategy, "browser_manager", None)
        sessions = getattr(manager, "sessions", {}) if manager else {}
        session = sessions.get(session_id)
        if not session:
            return None
        # sessions[session_id] = (context, page, last_used)
        return session[1]
    except Exception:
        return None

async def is_logged_in(page) -> bool:
    try:
        return not await detect_home_login_block(page)
    except Exception:
        return False

async def run_login_flow(crawler: AsyncWebCrawler) -> bool:
    """按固定普通登录流程执行。"""
    logger.info("🌐 加载 SIF 首页...")
    await crawler.arun(
        url="https://www.sif.com/",
        config=CrawlerRunConfig(
            cache_mode=CacheMode.DISABLED,
            session_id=SESSION_ID,
        ),
    )
    
    # 步骤 2: 获取原生 page 对象进行后续所有操作
    page = get_session_page(crawler, SESSION_ID)
    if not page:
        logger.error("❌ 无法从会话中获取 Page，请检查 crawl4ai 版本或会话配置。")
        return False

    await safe_wait_dom(page)

    if await is_logged_in(page):
        logger.info("🎉 当前已登录，跳过登录流程。")
        return True

    logger.info("🔐 检测到首页登录区，开始执行普通登录流程。")

    if not await open_login_dialog_via_js(page):
        logger.error("❌ 未找到首页登录按钮。")
        return False

    await asyncio.sleep(1.2)

    if not await switch_to_password_login_via_js(page):
        logger.error("❌ 未找到“手机号密码登录”切换按钮。")
        return False

    await asyncio.sleep(0.8)

    if not await fill_password_login_form_via_js(page, PHONE, PASS):
        logger.error("❌ 未找到手机号/密码输入框，无法填写登录表单。")
        return False

    await asyncio.sleep(0.5)

    if not await submit_password_login_via_js(page):
        logger.error("❌ 未找到弹窗里的“登录”按钮。")
        return False

    await asyncio.sleep(4)

    if await is_logged_in(page):
        logger.info("🎉 登录成功！")
        return True

    logger.error("❌ 已执行普通登录流程，但页面仍显示未登录状态。")
    return False

def is_profile_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "processsingleton" in msg
        or "singletonlock" in msg
        or "profile is already in use" in msg
        or "failed to create a processsingleton" in msg
    )

def is_missing_playwright_browser_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "executable doesn't exist" in msg
        or "please run the following command to download new browsers" in msg
        or "playwright install" in msg
    )

def install_playwright_browser(browser_name: str = "chromium") -> bool:
    logger.info(f"🧩 检测到 Playwright 浏览器缺失，尝试自动安装: {browser_name}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser_name],
            check=False,
        )
        if result.returncode == 0:
            logger.info(f"✅ Playwright 浏览器安装完成: {browser_name}")
            return True
    except Exception as e:
        logger.error(f"❌ 自动安装 Playwright 浏览器失败: {e}")

    logger.error(
        "❌ 仍未完成浏览器安装，请手动执行: "
        f"{sys.executable} -m playwright install {browser_name}"
    )
    return False

async def run_sif_login(headless: bool = False):
    logger.info(f"🚀 开始 SIF 自动登录流程 (原地交互版)...")

    if not os.path.exists(SIF_PROFILE):
        os.makedirs(SIF_PROFILE, exist_ok=True)

    temp_profile_dir = None
    profile_candidates = [SIF_PROFILE]

    for idx, profile_dir in enumerate(profile_candidates):
        browser_cfg = build_sif_browser_config(
            profile_dir=profile_dir,
            headless=headless,
            extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"],
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            viewport={"width": 1600, "height": 1200},
        )
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                return await run_login_flow(crawler)
        except Exception as e:
            if is_missing_playwright_browser_error(e):
                installed = install_playwright_browser("chromium")
                if installed:
                    logger.info("🔁 浏览器安装成功，重新启动登录流程...")
                    async with AsyncWebCrawler(config=browser_cfg) as crawler:
                        return await run_login_flow(crawler)
                raise RuntimeError(
                    "Playwright Chromium 未安装，且自动安装失败。"
                ) from e
            # 持久化 profile 被占用时，自动切换临时 profile 重试一次
            if idx == 0 and is_profile_lock_error(e):
                temp_profile_dir = tempfile.mkdtemp(prefix="sif-fallback-", dir="/tmp")
                logger.warning(
                    "⚠️ 检测到浏览器 profile 被占用，自动切换临时 profile 重试一次。"
                )
                logger.warning(f"ℹ️ 临时 profile: {temp_profile_dir}")
                profile_candidates.append(temp_profile_dir)
                continue
            raise
        finally:
            if temp_profile_dir and profile_dir == temp_profile_dir:
                shutil.rmtree(temp_profile_dir, ignore_errors=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIF 自动登录脚本")
    parser.add_argument("--headless", action="store_true", help="使用 headless 模式运行")
    parser.add_argument(
        "--install-browser-only",
        action="store_true",
        help="仅安装 Playwright Chromium 浏览器，不执行登录流程",
    )
    args = parser.parse_args()

    if args.install_browser_only:
        ok = install_playwright_browser("chromium")
        sys.exit(0 if ok else 1)

    browser_path = resolve_sif_browser_path()
    if browser_path:
        logger.info(f"🌐 SIF 固定浏览器路径: {browser_path}")
    else:
        logger.warning("⚠️ 未找到固定浏览器路径，将回退到 Playwright 默认 Chromium。")

    # 容器环境无 X server，默认强制 headless，避免自动重登直接崩溃
    effective_headless = args.headless or os.getenv("DOCKER_ENV") == "1"
    success = asyncio.run(run_sif_login(headless=effective_headless))
    sys.exit(0 if success else 1)
