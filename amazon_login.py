from __future__ import annotations

import asyncio
import logging
import sys
import shutil
from pathlib import Path

from playwright.async_api import Page, async_playwright

from amz_sif_crawler.runtime.browser import COMMON_BROWSER_ARGS
from amz_sif_crawler.runtime.config import ensure_runtime_dirs, load_app_config
from amz_sif_crawler.fetchers.amazon import AMAZON_USER_AGENT

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("amazon-login")


async def run_login_flow() -> int:
    config = load_app_config()
    
    # 强制重置已损坏的 Amazon profile 目录
    profile_dir = config.amazon_profile_dir
    logger.info("📂 Amazon Profile 路径: %s", profile_dir)
    if profile_dir.exists():
        logger.info("🗑️ 检测到可能已损坏的 Amazon Profile 目录，正在重置以确保 Chromium 顺利启动...")
        shutil.rmtree(profile_dir, ignore_errors=True)
    ensure_runtime_dirs(config)

    async with async_playwright() as playwright:
        logger.info("🚀 正在启动有头模式 Chromium 浏览器...")
        # 使用 Chromium 启动持久化上下文
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            user_agent=AMAZON_USER_AGENT,
            viewport={"width": 1440, "height": 1000},
            args=COMMON_BROWSER_ARGS,
        )
        
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            logger.info("🌐 打开 Amazon 首页...")
            await page.goto("https://www.amazon.com/", wait_until="domcontentloaded", timeout=60000)
            
            print("\n" + "=" * 60)
            print("【交互式 Amazon Profile 初始化提示】")
            print("1. 请在弹出的浏览器中进行操作（如：登录、解决验证码/CAPTCHA、设置邮编地址）。")
            print("2. 在浏览器中完成操作后，请回到此终端。")
            print("3. 按【回车键/ENTER】以保存 profile 并关闭浏览器。")
            print("=" * 60 + "\n")
            
            # 使用 asyncio.to_thread 异步等待用户在命令行输入回车，不阻塞事件循环
            await asyncio.to_thread(input, "按回车键/ENTER 退出: ")
            
            logger.info("💾 正在保存 session 并关闭浏览器...")
        finally:
            await context.close()
            
    logger.info("🎉 Amazon Profile 初始化完成！")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run_login_flow()))
    except KeyboardInterrupt:
        logger.info("👋 用户中断，退出程序")
        sys.exit(0)
