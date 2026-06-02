from __future__ import annotations

import asyncio
import logging
import os

from playwright.async_api import Page

from amz_sif_crawler.runtime.browser import COMMON_BROWSER_ARGS, open_persistent_context
from amz_sif_crawler.runtime.config import ensure_runtime_dirs, load_app_config
from amz_sif_crawler.sif_auth import ensure_sif_logged_in, load_sif_credentials


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("sif-login")


def _login_headless_default(config_headless: bool) -> bool:
    raw = os.getenv("SIF_LOGIN_HEADLESS")
    if raw is None:
        return config_headless
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def run_login_flow() -> int:
    config = load_app_config()
    ensure_runtime_dirs(config)
    phone, password = load_sif_credentials(config.base_dir)
    if not phone or not password:
        logger.error("❌ 缺少 SIF 登录账号配置，请检查 config/settings.json 的 SIF.phone 和 SIF.password")
        return 1
    headless = _login_headless_default(config.sif_headless)

    async with open_persistent_context(
        profile_dir=config.sif_profile_dir,
        headless=headless,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1600, "height": 1200},
        extra_args=COMMON_BROWSER_ARGS + ["--window-size=1600,1200"],
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        logger.info("🌐 打开 SIF 首页并检查登录态(headless=%s)", headless)
        if await ensure_sif_logged_in(page, phone=phone, password=password):
            logger.info("🎉 登录成功")
            return 0

        logger.error("❌ 登录流程已执行，但页面仍未登录")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_login_flow()))
