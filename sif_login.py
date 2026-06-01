from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import Page

from amz_sif_crawler.runtime.browser import COMMON_BROWSER_ARGS, open_persistent_context
from amz_sif_crawler.runtime.config import ensure_runtime_dirs, load_app_config


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("sif-login")


async def _js_click_if(page: Page, script: str, step_name: str) -> bool:
    try:
        clicked = await page.evaluate(script)
        if clicked:
            logger.info("✅ %s 成功", step_name)
            return True
    except Exception as exc:
        logger.warning("⚠️ %s 失败: %s", step_name, exc)
    return False


async def _detect_home_login_block(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
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
            )
        )
    except Exception:
        return False


async def _is_logged_in(page: Page) -> bool:
    return not await _detect_home_login_block(page)


async def _open_login_dialog(page: Page) -> bool:
    return await _js_click_if(
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


async def _switch_to_password_login(page: Page) -> bool:
    return await _js_click_if(
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


async def _fill_password_login_form(page: Page, phone: str, password: str) -> bool:
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
            logger.info("✅ 已填写手机号与密码")
        return bool(filled)
    except Exception as exc:
        logger.warning("⚠️ 填写登录表单失败: %s", exc)
        return False


async def _submit_password_login(page: Page) -> bool:
    return await _js_click_if(
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


async def run_login_flow() -> int:
    config = load_app_config()
    ensure_runtime_dirs(config)
    settings = {}
    settings_path = Path(config.base_dir) / "config" / "settings.json"
    if settings_path.exists():
        import json

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    sif_settings = settings.get("SIF", {})
    phone = str(sif_settings.get("phone", "")).strip()
    password = str(sif_settings.get("password", "")).strip()
    if not phone or not password:
        logger.error("❌ 缺少 SIF 登录账号配置，请检查 config/settings.json 的 SIF.phone 和 SIF.password")
        return 1

    async with open_persistent_context(
        profile_dir=config.sif_profile_dir,
        headless=False,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1600, "height": 1200},
        extra_args=COMMON_BROWSER_ARGS + ["--window-size=1600,1200"],
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        logger.info("🌐 打开 SIF 首页")
        await page.goto("https://www.sif.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)

        if await _is_logged_in(page):
            logger.info("🎉 当前 profile 已登录")
            return 0

        if not await _open_login_dialog(page):
            logger.error("❌ 未找到首页登录按钮")
            return 1
        await page.wait_for_timeout(1000)

        if not await _switch_to_password_login(page):
            logger.error("❌ 未找到手机号密码登录按钮")
            return 1
        await page.wait_for_timeout(800)

        if not await _fill_password_login_form(page, phone, password):
            logger.error("❌ 未能填写登录表单")
            return 1
        await page.wait_for_timeout(500)

        if not await _submit_password_login(page):
            logger.error("❌ 未找到提交登录按钮")
            return 1

        await page.wait_for_timeout(4000)
        if await _is_logged_in(page):
            logger.info("🎉 登录成功")
            return 0

        logger.error("❌ 登录流程已执行，但页面仍未登录")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_login_flow()))
