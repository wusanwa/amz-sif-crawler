from __future__ import annotations

import json
from pathlib import Path

from playwright.async_api import Page


def load_sif_credentials(base_dir: str | Path) -> tuple[str, str]:
    settings_path = Path(base_dir).resolve() / "config" / "settings.json"
    if not settings_path.exists():
        return "", ""
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    sif_settings = settings.get("SIF", {})
    return str(sif_settings.get("phone", "")).strip(), str(sif_settings.get("password", "")).strip()


async def js_click_if(page: Page, script: str) -> bool:
    try:
        return bool(await page.evaluate(script))
    except Exception:
        return False


async def detect_home_login_block(page: Page) -> bool:
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


async def is_sif_logged_in(page: Page) -> bool:
    return not await detect_home_login_block(page)


async def open_login_dialog(page: Page) -> bool:
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
    )


async def switch_to_password_login(page: Page) -> bool:
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
    )


async def fill_password_login_form(page: Page, phone: str, password: str) -> bool:
    try:
        return bool(
            await page.evaluate(
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
        )
    except Exception:
        return False


async def submit_password_login(page: Page) -> bool:
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
    )


async def ensure_sif_logged_in(page: Page, *, phone: str, password: str) -> bool:
    if not phone or not password:
        return False
    await page.goto("https://www.sif.com/", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)
    if await is_sif_logged_in(page):
        return True
    if not await open_login_dialog(page):
        return False
    await page.wait_for_timeout(1000)
    if not await switch_to_password_login(page):
        return False
    await page.wait_for_timeout(800)
    if not await fill_password_login_form(page, phone, password):
        return False
    await page.wait_for_timeout(500)
    if not await submit_password_login(page):
        return False
    await page.wait_for_timeout(4000)
    return await is_sif_logged_in(page)
