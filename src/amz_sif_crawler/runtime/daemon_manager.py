from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from amz_sif_crawler.fetchers.amazon import AMAZON_USER_AGENT, _read_amazon_payload, extract_amazon_product_from_page
from amz_sif_crawler.fetchers.sif import (
    SIF_USER_AGENT,
    detect_sif_auth_state,
    extract_sif_top3_from_page,
)
from amz_sif_crawler.runtime.browser import COMMON_BROWSER_ARGS


class PersistentBrowserDaemon:
    def __init__(
        self,
        *,
        mode: str,
        profile_dir: str | Path,
        headless: bool,
    ) -> None:
        self.mode = mode
        self.profile_dir = str(profile_dir)
        self.headless = headless
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.lock = asyncio.Lock()
        self.max_tabs = max(1, int(os.getenv("SIF_DAEMON_MAX_TABS", "3")))
        self.sif_pages: list[Page] = []
        self.sif_page_queue: asyncio.Queue[Page] | None = None

    async def start(self) -> None:
        self.playwright = await async_playwright().start()
        if self.mode == "amazon":
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=self.headless,
                user_agent=AMAZON_USER_AGENT,
                viewport={"width": 1440, "height": 1400},
                args=COMMON_BROWSER_ARGS,
            )
        elif self.mode == "sif":
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=self.headless,
                user_agent=SIF_USER_AGENT,
                viewport={"width": 1600, "height": 1200},
                args=COMMON_BROWSER_ARGS + ["--window-size=1600,1200"],
            )
        else:
            raise ValueError(f"Unsupported daemon mode: {self.mode}")
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if self.mode == "sif":
            self.sif_pages = [self.page]
            while len(self.sif_pages) < self.max_tabs:
                self.sif_pages.append(await self.context.new_page())
            self.sif_page_queue = asyncio.Queue()
            for sif_page in self.sif_pages:
                await self.sif_page_queue.put(sif_page)

    async def stop(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
        self.page = None
        self.sif_pages = []
        self.sif_page_queue = None

    async def fetch_amazon(self, *, url: str) -> dict[str, Any]:
        async with self.lock:
            if self.page is None:
                raise RuntimeError("Amazon daemon page is not ready")
            page = self.page
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(20):
                payload = await _read_amazon_payload(page)
                if payload.get("product_title") or payload.get("main_price") or payload.get("page_state") != "ok":
                    break
                await page.wait_for_timeout(500)
            return await extract_amazon_product_from_page(page)

    async def fetch_sif(self, *, asin: str) -> dict[str, Any]:
        if self.sif_page_queue is None:
            raise RuntimeError("SIF daemon page pool is not ready")
        page = await self.sif_page_queue.get()
        try:
            sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=false&trafficType="
            await page.goto(sif_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(300)
            rankings = await extract_sif_top3_from_page(page)
            if rankings:
                return {"data": rankings, "error": None}

            body_text = await page.locator("body").inner_text()
            state = detect_sif_auth_state(page.url, body_text)
            if state == "login_required":
                return {"data": [], "error": "SIF Login Required"}
            if state == "challenge":
                return {"data": [], "error": "SIF Challenge/CAPTCHA"}
            return {"data": [], "error": "SIF Empty Data"}
        finally:
            await self.sif_page_queue.put(page)
