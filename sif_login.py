import os
import sys
import asyncio
import json
import logging
import inspect
import argparse
import subprocess
import tempfile
import shutil
from typing import Optional, List, Any
from pydantic import BaseModel, Field

# 尝试导入 crawl4ai
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    HAS_CRAWL4AI = True
except ImportError:
    logger = logging.getLogger("sif-login-ai")
    logger.error("❌ 未检测到 crawl4ai，这是核心依赖。")
    sys.exit(1)

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
MAX_LOGIN_ROUNDS = 8

# LLM 配置
LLM_S = SETTINGS.get("LLM", {})
LLM_PROVIDER = LLM_S.get("provider", "openai/gpt-4o")
if "/" not in LLM_PROVIDER: LLM_PROVIDER = f"openai/{LLM_PROVIDER}"
LLM_CONFIG = LLMConfig(
    provider=LLM_PROVIDER,
    api_token=LLM_S.get("api_token"),
    base_url=LLM_S.get("base_url")
)

# 定义状态模型用于 AI 分析
class PageState(BaseModel):
    is_logged_in: bool = Field(..., description="是否已登录（看到个人中心、退出按钮、我的项目等）")
    has_conflict: bool = Field(..., description="是否显示账号冲突提示（如“在别的浏览器登录”）")
    current_view: str = Field(..., description="当前视图：login_form, dashboard, home, other")
    phone_input_selector: Optional[str] = Field(None, description="手机号输入框的选择器")
    password_input_selector: Optional[str] = Field(None, description="密码输入框的选择器")
    submit_button_selector: Optional[str] = Field(None, description="登录提交按钮的选择器")
    login_button_selector: Optional[str] = Field(None, description="唤起登录弹窗/页面的按钮选择器")
    conflict_button_selector: Optional[str] = Field(None, description="处理冲突的“重新登录”按钮选择器")
    agree_checkbox_selector: Optional[str] = Field(None, description="同意协议复选框选择器")
    login_tab_selector: Optional[str] = Field(None, description="手机号密码登录 Tab 选择器")
    needs_human_verify: Optional[bool] = Field(False, description="是否出现验证码/滑块/人机校验，需人工处理")
    blocking_reason: Optional[str] = Field(None, description="阻塞原因简述")

FORCED_LOGOUT_TEXT = "为了保证您的账号安全，我们已将您的账号从本浏览器退出。".lower()

async def safe_wait_dom(page, timeout_ms: int = 6000):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

async def safe_click(page, selectors: List[str], step_name: str = "点击") -> bool:
    """按顺序尝试多个 selector，任意成功返回 True。"""
    for sel in selectors:
        if not sel:
            continue
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                logger.info(f"✅ {step_name}成功: {sel}")
                return True
        except Exception:
            continue
    logger.info(f"ℹ️ {step_name}未命中可点击元素。")
    return False

async def fill_first_available(page, selectors: List[str], value: str, step_name: str = "填充") -> Optional[str]:
    """按顺序尝试输入，成功返回命中的 selector。"""
    for sel in selectors:
        if not sel:
            continue
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(value, timeout=2500)
                logger.info(f"✅ {step_name}成功: {sel}")
                return sel
        except Exception:
            continue
    logger.warning(f"⚠️ {step_name}失败，未命中可输入元素。")
    return None

async def submit_login(page, state: "PageState", password_selector: str) -> bool:
    """
    多策略触发登录提交，避免“输入完成但按钮未触发”。
    顺序：按钮点击 -> 密码框回车 -> 页面回车 -> JS 提交。
    """
    submit_selectors = [
        state.submit_button_selector,
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('登录'):not(.nav-item)",
        ".el-button:has-text('登录')",
        ".login-btn, .btn-login, .submit-btn",
    ]
    if await safe_click(page, submit_selectors, step_name="提交登录"):
        return True

    # 常见表单行为：在密码框回车触发提交
    try:
        await page.locator(password_selector).first.press("Enter", timeout=2000)
        logger.info("✅ 通过密码框 Enter 触发提交。")
        return True
    except Exception:
        pass

    try:
        await page.keyboard.press("Enter")
        logger.info("✅ 通过页面 Enter 触发提交。")
        return True
    except Exception:
        pass

    # JS 保底：点击最可能按钮或提交最近 form
    try:
        triggered = await page.evaluate(
            """
            () => {
                const cands = Array.from(document.querySelectorAll(
                    "button[type='submit'],input[type='submit'],button,.el-button"
                ));
                const byText = cands.find(el => (el.innerText || el.value || '').includes('登录'));
                if (byText) {
                    byText.click();
                    return true;
                }
                const pwd = document.querySelector("input[type='password']");
                const form = pwd ? pwd.closest('form') : document.querySelector('form');
                if (form) {
                    if (typeof form.requestSubmit === 'function') form.requestSubmit();
                    else form.submit();
                    return true;
                }
                return false;
            }
            """
        )
        if triggered:
            logger.info("✅ 通过 JS 保底触发提交。")
            return True
    except Exception:
        pass

    logger.warning("⚠️ 所有提交策略都未触发。")
    return False

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

async def analyze_current_page(page) -> PageState:
    """基于当前 Playwright Page 做原地 AI 分析（不跳转）。"""
    try:
        if not page:
            logger.error("❌ 未能获取到 Playwright Page 对象")
            return PageState(is_logged_in=False, has_conflict=False, current_view="error")
        
        await safe_wait_dom(page)
        html_content = await page.content()
        
        # 准备 AI 提取策略
        extraction_strategy = LLMExtractionStrategy(
            llm_config=LLM_CONFIG,
            schema=PageState.model_json_schema(),
            instruction=(
                "分析 SIF 页面当前状态并输出 JSON。"
                "请判断是否已登录、是否有账号冲突、当前是否在登录表单。"
                "若出现“为了保证您的账号安全，我们已将您的账号从本浏览器退出。”，"
                "应判定 has_conflict=true 且 is_logged_in=false。"
                "若有登录相关元素，请尽量给出可执行的 CSS 选择器："
                "login_button_selector, conflict_button_selector, phone_input_selector, "
                "password_input_selector, submit_button_selector, agree_checkbox_selector, login_tab_selector。"
                "若出现验证码/滑块/人机验证或其它导致自动化无法继续的情况，"
                "needs_human_verify=true，并用 blocking_reason 简述。"
            ),
        )
        
        # 原地提取内容（不通过 arun 导航）
        # 兼容不同 crawl4ai 版本：extract(html) 或 extract(ix, html)
        extracted = None
        extract_fn = extraction_strategy.extract
        call_variants = [
            (html_content,),
            (0, html_content),
        ]
        for args in call_variants:
            try:
                result = extract_fn(*args)
                extracted = await result if inspect.isawaitable(result) else result
                break
            except TypeError as e:
                if "required positional arguments" in str(e) or "positional argument" in str(e):
                    continue
                raise
        
        if extracted:
            data = extracted if isinstance(extracted, (dict, list)) else json.loads(extracted)
            if isinstance(data, list) and len(data) > 0:
                return PageState(**data[0])
            return PageState(**data)
            
    except Exception as e:
        logger.error(f"❌ AI 原地分析失败: {e}")
    
    # 手工规则回退
    try:
        body_text = (await page.inner_text("body")).lower()
        phone_like = await page.locator(
            "input[type='tel'], input[name*='phone' i], input[placeholder*='手机'], input[placeholder*='电话']"
        ).count()
        password_like = await page.locator(
            "input[type='password'], input[placeholder*='密码'], input[name*='password' i]"
        ).count()
        submit_like = await page.locator(
            "button[type='submit'], input[type='submit'], button:has-text('登录'), .el-button:has-text('登录')"
        ).count()

        is_logged_in = (
            ("退出登录" in body_text)
            or ("个人中心" in body_text)
            or ("我的课程" in body_text)
            or ("我的项目" in body_text)
        )
        has_conflict = (
            "在别的浏览器登录" in body_text
            or "重新登录" in body_text
            or FORCED_LOGOUT_TEXT in body_text
        )
        in_login_form = (password_like > 0 and (phone_like > 0 or submit_like > 0))
        current_view = "login_form" if in_login_form else "other"
        needs_human_verify = (
            "验证码" in body_text or "滑块" in body_text or "人机验证" in body_text or "请完成验证" in body_text
        )
        blocking_reason = "检测到验证码/人机验证" if needs_human_verify else None
        return PageState(
            is_logged_in=is_logged_in, has_conflict=has_conflict, current_view=current_view,
            phone_input_selector='input[type="tel"], input[name*="phone" i], input[placeholder*="手机"]',
            password_input_selector='input[type="password"], input[placeholder*="密码"], input[name*="password" i]',
            submit_button_selector="button:has-text('登录'):not(.nav-item)",
            login_button_selector=".nav-item:has-text('登录'), button:has-text('登录'), .el-link:has-text('登录'), a:has-text('登录')",
            conflict_button_selector="button:has-text('重新登录'), .el-button:has-text('重新登录')",
            agree_checkbox_selector=None,
            login_tab_selector="text=手机号密码登录",
            needs_human_verify=needs_human_verify,
            blocking_reason=blocking_reason
        )
    except:
        return PageState(is_logged_in=False, has_conflict=False, current_view="error")

def parse_page_state_payload(payload: Any) -> Optional[PageState]:
    """把不同来源的 payload 尽量解析成 PageState。"""
    try:
        data = payload
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, list):
            if not data:
                return None
            data = data[0]
        if isinstance(data, dict):
            return PageState(**data)
    except Exception:
        return None
    return None

async def verify_logged_in_by_dom(page) -> bool:
    """当 AI 判定不稳定时，用 DOM/文本做兜底确认。"""
    try:
        body_text = (await page.inner_text("body")).lower()
    except Exception:
        body_text = ""

    # 安全策略强制下线时，必须重新登录
    if FORCED_LOGOUT_TEXT in body_text:
        return False

    logged_text_hits = [
        "退出登录", "安全退出", "个人中心", "用户中心", "我的课程", "我的项目", "我的订单", "账户设置",
    ]
    if any(t in body_text for t in logged_text_hits):
        return True

    try:
        # 常见已登录态 UI：头像/用户菜单/个人中心入口
        user_ui_cnt = await page.locator(
            ".avatar, .user-avatar, .user-menu, .profile, [class*='user' i][class*='menu' i], a:has-text('个人中心'), a:has-text('退出登录')"
        ).count()
        if user_ui_cnt > 0:
            return True
    except Exception:
        pass

    try:
        # 若页面没有可见登录入口，且不在登录页，通常已登录或无需登录
        login_entry_cnt = await page.locator(
            ".nav-item:has-text('登录'), button:has-text('登录'), .el-link:has-text('登录'), a:has-text('登录')"
        ).count()
        current_url = (page.url or "").lower()
        if login_entry_cnt == 0 and "login" not in current_url:
            return True
    except Exception:
        pass

    return False

async def verify_logged_in_by_crawl4ai(crawler: AsyncWebCrawler, page, session_id: str) -> bool:
    """
    使用 crawl4ai 会话快照复核登录态。
    适用于“页面 DOM 看起来不稳定、AI 原地分析误判”的场景。
    """
    if not crawler or not page:
        return False

    current_url = page.url or "https://www.sif.com/"
    try:
        logger.info(f"🛰️ 使用 crawl4ai 会话快照复核登录态: {current_url}")
        extraction_strategy = LLMExtractionStrategy(
            llm_config=LLM_CONFIG,
            schema=PageState.model_json_schema(),
            instruction=(
                "你在复核 SIF 页面是否已登录。"
                "若出现“为了保证您的账号安全，我们已将您的账号从本浏览器退出。”，"
                "必须判定为未登录且需要重新登录。"
                "若看到个人中心/退出登录/我的项目/用户菜单等已登录信号，请 is_logged_in=true。"
                "若仍在账号密码输入页且可提交登录，请 current_view=login_form 且 is_logged_in=false。"
            ),
        )
        result = await crawler.arun(
            url=current_url,
            config=CrawlerRunConfig(
                session_id=session_id,
                cache_mode=CacheMode.BYPASS,
                extraction_strategy=extraction_strategy,
                markdown_generator=DefaultMarkdownGenerator(),
            ),
        )

        # 优先读取结构化提取结果
        parsed = None
        for src in [result, getattr(result, "extracted_content", None)]:
            parsed = parse_page_state_payload(src)
            if parsed:
                break
        if parsed and parsed.is_logged_in and not parsed.has_conflict:
            logger.info("✅ crawl4ai 结构化结果判定为已登录。")
            return True

        # 结构化未命中时，扫 crawl4ai 产出的文本/HTML
        snapshot_parts = []
        for attr in ["markdown", "fit_markdown", "cleaned_html", "html", "extracted_content"]:
            v = getattr(result, attr, None)
            if v:
                snapshot_parts.append(str(v))
        merged = "\n".join(snapshot_parts).lower()
        if FORCED_LOGOUT_TEXT in merged:
            logger.warning("⚠️ crawl4ai 快照检测到安全策略强制下线提示，需要重新登录。")
            return False
        if any(k in merged for k in ["退出登录", "安全退出", "个人中心", "用户中心", "我的项目", "我的课程"]):
            logger.info("✅ crawl4ai 文本快照命中已登录关键词。")
            return True
    except Exception as e:
        logger.warning(f"⚠️ crawl4ai 登录态复核失败: {e}")

    return False

async def run_login_flow(crawler: AsyncWebCrawler) -> bool:
    """已启动 crawler 后执行登录流程主体。"""
    # 步骤 1: 仅加载一次主页
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
    
    submitted = False
    used_fallback_goto = False

    for round_idx in range(1, MAX_LOGIN_ROUNDS + 1):
        logger.info(f"🔎 第 {round_idx}/{MAX_LOGIN_ROUNDS} 轮 AI 页面分析...")
        state = await analyze_current_page(page)

        # 强制下线提示优先级最高：即使页面局部看起来“像已登录”，也必须重新登录
        try:
            latest_body = (await page.inner_text("body")).lower()
            if FORCED_LOGOUT_TEXT in latest_body:
                state.has_conflict = True
                state.is_logged_in = False
                logger.warning("🚨 检测到安全策略强制下线提示，转入重新登录流程。")
        except Exception:
            pass

        if not state.is_logged_in:
            dom_verified = await verify_logged_in_by_dom(page)
            if dom_verified:
                logger.info("✅ DOM 兜底判定已登录（覆盖 AI 未登录结果）。")
                state.is_logged_in = True
            else:
                crawl_verified = await verify_logged_in_by_crawl4ai(crawler, page, SESSION_ID)
                if crawl_verified:
                    logger.info("✅ crawl4ai 会话复核判定已登录（覆盖 AI 未登录结果）。")
                    state.is_logged_in = True

        logger.info(
            f"🧭 状态: view={state.current_view}, logged_in={state.is_logged_in}, "
            f"conflict={state.has_conflict}, human_verify={state.needs_human_verify}"
        )

        if state.is_logged_in and not state.has_conflict:
            logger.info("🎉 登录成功！")
            return True

        if state.needs_human_verify:
            logger.error(f"❌ 遇到人机验证，自动流程暂停: {state.blocking_reason or 'unknown'}")
            return False

        # 优先处理冲突提示
        if state.has_conflict:
            logger.warning("🚨 检测到冲突，尝试点击‘重新登录’...")
            conflict_clicked = await safe_click(
                page,
                [
                    state.conflict_button_selector,
                    "button:has-text('重新登录')",
                    ".el-button:has-text('重新登录')",
                ],
                step_name="冲突处理",
            )
            if conflict_clicked:
                await asyncio.sleep(1.5)
                continue

        # 若不在登录表单，先想办法唤起登录弹窗
        if state.current_view != "login_form":
            # 二次兜底：避免“已登录但 AI 误判”为未登录时误触发登录入口
            if await verify_logged_in_by_dom(page):
                logger.info("✅ 唤起前复核：页面已登录，跳过唤起流程。")
                logger.info("🎉 登录成功！")
                return True
            if await verify_logged_in_by_crawl4ai(crawler, page, SESSION_ID):
                logger.info("✅ 唤起前 crawl4ai 复核：页面已登录，跳过唤起流程。")
                logger.info("🎉 登录成功！")
                return True

            logger.info("🔑 当前不在登录表单，尝试唤起登录入口...")
            login_opened = await safe_click(
                page,
                [
                    state.login_button_selector,
                    ".nav-item:has-text('登录')",
                    "button:has-text('登录')",
                    ".el-link:has-text('登录')",
                    "a:has-text('登录')",
                ],
                step_name="唤起登录",
            )
            if login_opened:
                await asyncio.sleep(1.5)
                continue

            if not used_fallback_goto:
                logger.warning("⚠️ 未找到有效登录入口，执行一次保底跳转...")
                try:
                    await page.goto("https://new.sif.com/login", wait_until="domcontentloaded", timeout=12000)
                except Exception as e:
                    logger.warning(f"⚠️ 保底跳转异常: {e}")
                used_fallback_goto = True
                await asyncio.sleep(1.5)
                continue

        # 在登录表单内执行填充与提交
        if state.current_view == "login_form":
            logger.info("⌨️ 进入登录表单，执行填写与提交...")

            await safe_click(
                page,
                [
                    state.login_tab_selector,
                    "text=手机号密码登录",
                ],
                step_name="切换登录方式",
            )

            phone_selectors = [
                state.phone_input_selector,
                'input[placeholder*="手机号码"]',
                'input[placeholder*="手机"]',
                "input[type='tel']",
                "input[name*='phone' i]",
            ]
            pass_selectors = [
                state.password_input_selector,
                'input[placeholder*="密码"]',
                "input[type='password']",
                "input[name*='password' i]",
            ]
            phone_used = await fill_first_available(page, phone_selectors, PHONE, step_name="填写手机号")
            pass_used = await fill_first_available(page, pass_selectors, PASS, step_name="填写密码")
            if not phone_used or not pass_used:
                logger.warning("⚠️ 未能完整填写登录表单，等待下一轮重新分析。")
                await asyncio.sleep(1.2)
                continue

            # 仅在 AI 明确给出协议框时才尝试，避免“页面无协议”时误点击。
            if state.agree_checkbox_selector:
                await safe_click(
                    page,
                    [state.agree_checkbox_selector],
                    step_name="勾选协议",
                )

            submit_ok = await submit_login(page, state, pass_used)
            submitted = submitted or submit_ok
            await asyncio.sleep(4)
            continue

        await asyncio.sleep(1.2)

    logger.error("❌ 达到最大尝试轮次，仍未完成登录。")
    if submitted:
        logger.error("ℹ️ 已提交过登录请求，但未确认登录成功，可能被验证码/风控拦截。")
    return False

def is_profile_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "processsingleton" in msg
        or "singletonlock" in msg
        or "profile is already in use" in msg
        or "failed to create a processsingleton" in msg
    )

async def run_sif_login(headless=False):
    logger.info(f"🚀 开始 SIF 自动登录流程 (原地交互版)...")

    if not os.path.exists(SIF_PROFILE):
        os.makedirs(SIF_PROFILE, exist_ok=True)

    temp_profile_dir = None
    profile_candidates = [SIF_PROFILE]

    for idx, profile_dir in enumerate(profile_candidates):
        browser_cfg = BrowserConfig(
            browser_type="chromium",
            headless=headless,
            use_persistent_context=True,
            user_data_dir=profile_dir,
            extra_args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1440, "height": 900}
        )
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                return await run_login_flow(crawler)
        except Exception as e:
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

def pack_sif_profile() -> None:
    bundle_script = os.path.join(BASE_DIR, "scripts", "profile_bundle.sh")
    if not os.path.exists(bundle_script):
        logger.warning(f"⚠️ 未找到打包脚本，跳过自动打包: {bundle_script}")
        return
    logger.info("📦 正在打包 SIF profile 压缩包...")
    subprocess.run(["bash", bundle_script, "pack", "sif"], check=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIF 自动登录脚本")
    parser.add_argument("--headless", action="store_true", help="使用 headless 模式运行")
    parser.add_argument("--no-pack", action="store_true", help="登录完成后不自动打包 profile")
    args = parser.parse_args()

    success = asyncio.run(run_sif_login(headless=args.headless))
    if success and not args.no_pack:
        pack_sif_profile()
    sys.exit(0 if success else 1)
