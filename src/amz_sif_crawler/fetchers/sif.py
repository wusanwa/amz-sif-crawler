from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page

from amz_sif_crawler.runtime.browser import (
    COMMON_BROWSER_ARGS,
    clone_profile_dir,
    open_persistent_context,
    summarize_browser_error,
)


SIF_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def detect_sif_auth_state(url: str = "", text: str = "") -> str:
    lower_url = (url or "").lower()
    lower_text = (text or "").lower()
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
    logged_in_markers = ["查销量", "反查流量", "广告透视仪", "流量时光机", "会员购买", "到期"]
    challenge_signals = ["captcha", "robot", "verify", "verification", "验证码", "人机验证", "安全验证"]

    if "/login" in lower_url or "/signin" in lower_url:
        return "login_required"
    if any(marker in lower_text for marker in challenge_signals):
        return "challenge"
    if any(marker in lower_text for marker in logged_in_markers):
        return "ok"
    if any(marker in lower_text for marker in hard_login_signals):
        return "login_required"
    return "ok"


async def extract_sif_top3_from_page(page: Page) -> list[dict[str, str]]:
    return await page.evaluate(
        """
        async () => {
          const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const readRows = () => {
            const rows = Array.from(
              document.querySelectorAll('.table_wrap.section_block.keyword_list_table_wrap .reverse_table_container tbody tr.el-table__row')
            ).slice(0, 3);

            return rows.map((row) => {
              const cells = row.querySelectorAll('td');
              const keywordCell = cells[1] || null;
              const organicCell = cells[6] || null;
              const adCell = cells[8] || null;

              const keywordNode = keywordCell
                ? (keywordCell.querySelector('.correct_direction.inline_block')
                  || keywordCell.querySelector('.icon_copy')
                  || keywordCell.querySelector('.edit_word'))
                : null;
              const organicNode = organicCell
                ? (organicCell.querySelector('.rank_page_pos')
                  || organicCell.querySelector('.rank_num')
                  || organicCell)
                : null;
              const adNode = adCell
                ? (adCell.querySelector('.rank_page_pos')
                  || adCell.querySelector('.rank_num')
                  || adCell)
                : null;

              return {
                keyword: clean(keywordNode && keywordNode.textContent),
                organic_rank: clean(organicNode && organicNode.textContent),
                ad_rank: clean(adNode && adNode.textContent),
              };
            });
          };

          let stableRows = [];
          for (let i = 0; i < 40; i++) {
            const rows = readRows();
            const ready = rows.length >= 3 && rows.every(item =>
              item.keyword &&
              item.organic_rank &&
              item.organic_rank !== '-' &&
              item.ad_rank
            );
            if (ready) {
              stableRows = rows;
              break;
            }
            await wait(500);
          }

          const rows = stableRows.length ? stableRows : readRows();
          return rows
            .map((row) => ({
              keyword: row.keyword || '',
              organic_rank: row.organic_rank || '-',
              ad_rank: row.ad_rank || '-',
            }))
            .filter((item) => item.keyword);
        }
        """
    )


def try_refresh_sif_profile(asin: str, log_progress: Callable[[str, str], None], project_root: Path) -> dict[str, Any]:
    login_script = project_root / "sif_login.py"
    if not login_script.exists():
        return {"data": [], "error": "SIF Login Required"}
    log_progress(asin, "🔑 运行 sif_login.py 修补 SIF profile...")
    proc = subprocess.run([sys.executable, str(login_script)], capture_output=True, text=True)
    if proc.returncode == 0:
        return {"data": [], "error": "SIF Session Refreshed, please retry"}
    return {"data": [], "error": f"SIF Login Failed: {proc.stderr.strip()}"}


async def fetch_sif_data(
    *,
    asin: str,
    profile_dir: str,
    headless: bool,
    log_progress: Callable[[str, str], None],
    project_root: Path,
) -> dict[str, Any]:
    sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=false&trafficType="
    profile_candidates = [str(profile_dir)]
    cloned_profile = clone_profile_dir(profile_dir, prefix="sif-crawl-")
    profile_candidates.append(cloned_profile)

    try:
        last_error = ""
        for index, candidate in enumerate(profile_candidates):
            try:
                if index == 1:
                    log_progress(asin, "🔁 SIF 原始 profile 启动失败，改用临时复制 profile 重试...")
                log_progress(asin, "🔍 使用 Playwright + 页面内 JS 抓取 SIF 数据...")
                async with open_persistent_context(
                    profile_dir=candidate,
                    headless=headless,
                    user_agent=SIF_USER_AGENT,
                    viewport={"width": 1600, "height": 1200},
                    extra_args=COMMON_BROWSER_ARGS + ["--window-size=1600,1200"],
                ) as context:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(sif_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(1500)

                    for _ in range(20):
                        rankings = await extract_sif_top3_from_page(page)
                        if rankings:
                            return {"data": rankings, "error": None}
                        body_text = await page.locator("body").inner_text()
                        state = detect_sif_auth_state(page.url, body_text)
                        if state == "login_required":
                            return try_refresh_sif_profile(asin, log_progress, project_root)
                        if state == "challenge":
                            return {"data": [], "error": "SIF Challenge/CAPTCHA"}
                        await page.wait_for_timeout(500)

                    body_text = await page.locator("body").inner_text()
                    state = detect_sif_auth_state(page.url, body_text)
                    if state == "login_required":
                        return try_refresh_sif_profile(asin, log_progress, project_root)
                    if state == "challenge":
                        return {"data": [], "error": "SIF Challenge/CAPTCHA"}
                    return {"data": [], "error": "SIF Empty Data"}
            except asyncio.TimeoutError:
                last_error = "SIF Timeout"
            except Exception as exc:
                last_error = summarize_browser_error(exc)
        return {"data": [], "error": last_error or "SIF Browser Launch Failed"}
    finally:
        shutil.rmtree(cloned_profile, ignore_errors=True)
