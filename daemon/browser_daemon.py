import asyncio
import json
import socket
import subprocess
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .config import ensure_runtime_dirs
from .providers import get_provider_settings, list_supported_providers, resolve_browser_executable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrowserProviderDaemon:
    def __init__(self, provider: str):
        self.provider = provider
        self.settings = get_provider_settings(provider)
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.started_at = ""
        self.last_error = ""
        self._lock = asyncio.Lock()
        self._page_marker = f"daemon:{provider}"
        self._launched_process: subprocess.Popen[str] | None = None
        self._max_tabs = 5 if provider == "sif" else 1
        self._tab_pool: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "running": self.context is not None,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "profile_dir": str(self.settings.profile_dir),
            "start_url": self.settings.start_url,
            "headless": self.settings.headless,
            "cdp_endpoint": self._cdp_http_url() if self.settings.cdp_port else "",
            "max_tabs": self._max_tabs,
            "tabs": [
                {
                    "slot_id": item["slot_id"],
                    "busy": item["busy"],
                    "label": item["label"],
                    "current_url": item["current_url"],
                    "last_used_at": item["last_used_at"],
                }
                for item in self._tab_pool
            ],
        }

    def _context_alive(self) -> bool:
        if self.context is None:
            return False
        try:
            return not self.context.is_closed()
        except Exception:
            return False

    def _page_alive(self) -> bool:
        if self.page is None:
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    async def _stop_browser(self) -> None:
        if self.page is not None:
            with suppress(Exception):
                await self.page.close()
        self.page = None
        if self.browser is not None:
            with suppress(Exception):
                await self.browser.close()
        self.browser = None
        if self.context is not None:
            with suppress(Exception):
                await self.context.close()
        self.context = None
        if self.playwright is not None:
            with suppress(Exception):
                await self.playwright.stop()
        self.playwright = None
        if self._launched_process is not None:
            with suppress(Exception):
                self._launched_process.terminate()
            self._launched_process = None
        self._tab_pool = []

    def _cdp_http_url(self) -> str:
        return f"http://{self.settings.cdp_host}:{self.settings.cdp_port}"

    def _cdp_debug_url(self) -> str:
        return f"{self._cdp_http_url()}/json/version"

    def _port_open(self) -> bool:
        try:
            with socket.create_connection((self.settings.cdp_host, self.settings.cdp_port), timeout=1.0):
                return True
        except OSError:
            return False

    def _cdp_browser_available(self) -> bool:
        if not self.settings.prefer_cdp_attach or self.settings.cdp_port <= 0:
            return False
        try:
            with urlopen(self._cdp_debug_url(), timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("webSocketDebuggerUrl"))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return False

    async def _connect_over_cdp(self) -> bool:
        if not self._cdp_browser_available():
            return False
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(self._cdp_http_url())
        contexts = list(self.browser.contexts)
        self.context = contexts[0] if contexts else None
        if self.context is None:
            raise RuntimeError(f"CDP browser at {self._cdp_http_url()} has no persistent context")
        self.page = None
        await self._ensure_daemon_page()
        self.started_at = _now_iso()
        self.last_error = ""
        return True

    def _launch_cdp_browser_process(self, browser_path: str) -> subprocess.Popen[str]:
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            browser_path,
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            f"--remote-debugging-port={self.settings.cdp_port}",
            f"--user-data-dir={self.settings.profile_dir}",
        ]
        if self.settings.headless:
            cmd.append("--headless=new")
        cmd.append("about:blank")
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )

    async def _mark_daemon_page(self, page: Page) -> None:
        with suppress(Exception):
            await page.evaluate(
                """
                marker => {
                  try {
                    window.__DAEMON_PAGE_MARKER__ = marker;
                    document.documentElement.setAttribute('data-daemon-page-marker', marker);
                  } catch (e) {}
                }
                """,
                self._page_marker,
            )

    async def _page_marker_value(self, page: Page) -> str:
        try:
            marker = await page.evaluate(
                """
                () => {
                  try {
                    return window.__DAEMON_PAGE_MARKER__
                      || document.documentElement.getAttribute('data-daemon-page-marker')
                      || '';
                  } catch (e) {
                    return '';
                  }
                }
                """
            )
        except Exception:
            return ""
        return str(marker or "")

    async def _is_daemon_page(self, page: Page) -> bool:
        return await self._page_marker_value(page) == self._page_marker

    async def _mark_slot_page(self, page: Page, slot_id: str) -> None:
        marker = f"{self._page_marker}:slot:{slot_id}"
        with suppress(Exception):
            await page.evaluate(
                """
                marker => {
                  try {
                    window.__DAEMON_PAGE_MARKER__ = marker;
                    document.documentElement.setAttribute('data-daemon-page-marker', marker);
                  } catch (e) {}
                }
                """,
                marker,
            )

    def _find_slot(self, slot_id: str) -> dict[str, Any] | None:
        for item in self._tab_pool:
            if item["slot_id"] == slot_id:
                return item
        return None

    async def _ensure_slot_pool(self) -> None:
        assert self.context is not None
        while len(self._tab_pool) < self._max_tabs:
            slot_id = f"{len(self._tab_pool) + 1}"
            page = await self.context.new_page()
            await self._mark_slot_page(page, slot_id)
            self._tab_pool.append(
                {
                    "slot_id": slot_id,
                    "label": f"{self.provider}-tab-{slot_id}",
                    "page": page,
                    "busy": False,
                    "current_url": "",
                    "last_used_at": "",
                }
            )

    async def _refresh_slot_pages(self) -> None:
        assert self.context is not None
        pages = [page for page in self.context.pages if not page.is_closed()]
        slot_map: dict[str, Page] = {}
        for candidate in pages:
            marker = await self._page_marker_value(candidate)
            prefix = f"{self._page_marker}:slot:"
            if marker.startswith(prefix):
                slot_map[marker.removeprefix(prefix)] = candidate

        for item in self._tab_pool:
            slot_page = slot_map.get(item["slot_id"])
            if slot_page is not None:
                item["page"] = slot_page
                continue
            existing_page = item.get("page")
            if existing_page is not None:
                try:
                    if not existing_page.is_closed():
                        await self._mark_slot_page(existing_page, item["slot_id"])
                        item["page"] = existing_page
                        continue
                except Exception:
                    pass
            page = await self.context.new_page()
            await self._mark_slot_page(page, item["slot_id"])
            item["page"] = page

    async def _acquire_slot(self) -> dict[str, Any]:
        while True:
            await self.ensure_started()
            async with self._lock:
                await self._ensure_slot_pool()
                await self._refresh_slot_pages()
                for item in self._tab_pool:
                    page = item.get("page")
                    if page is None or page.is_closed():
                        continue
                    if item["busy"]:
                        continue
                    item["busy"] = True
                    item["last_used_at"] = _now_iso()
                    return item
            await asyncio.sleep(0.1)

    async def _release_slot(self, slot: dict[str, Any], *, current_url: str = "") -> None:
        async with self._lock:
            target = self._find_slot(str(slot.get("slot_id", ""))) or slot
            target["busy"] = False
            if current_url:
                target["current_url"] = current_url
            target["last_used_at"] = _now_iso()

    async def _ensure_daemon_page(self) -> Page:
        assert self.context is not None
        if self._page_alive():
            assert self.page is not None
            return self.page

        blank_candidates: list[Page] = []
        for candidate in list(self.context.pages):
            try:
                if candidate.is_closed():
                    continue
            except Exception:
                continue
            if await self._is_daemon_page(candidate):
                self.page = candidate
                return candidate
            try:
                candidate_url = candidate.url or ""
            except Exception:
                candidate_url = ""
            if candidate_url in {"", "about:blank", "chrome://newtab/"}:
                blank_candidates.append(candidate)

        if blank_candidates:
            page = blank_candidates[0]
            self.page = page
            await self._mark_daemon_page(page)
            for extra_page in blank_candidates[1:]:
                with suppress(Exception):
                    await extra_page.close()
            return page

        page = await self.context.new_page()
        self.page = page
        await self._mark_daemon_page(page)
        return page

    async def _ensure_started_locked(self) -> dict[str, Any]:
        if self._context_alive():
            await self._ensure_daemon_page()
            await self._ensure_slot_pool()
            await self._refresh_slot_pages()
            return self.status()

        ensure_runtime_dirs()
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        browser_path = resolve_browser_executable(self.provider)
        if not browser_path:
            raise RuntimeError(f"No browser executable found for provider={self.provider}")

        if self.settings.prefer_cdp_attach:
            if await self._connect_over_cdp():
                await self._ensure_slot_pool()
                await self._refresh_slot_pages()
                return self.status()
            self._launched_process = self._launch_cdp_browser_process(browser_path)
            for _ in range(30):
                if self._cdp_browser_available():
                    break
                await asyncio.sleep(0.5)
            if not self._cdp_browser_available():
                raise RuntimeError(f"CDP browser did not become ready at {self._cdp_http_url()}")
            await self._connect_over_cdp()
            await self._ensure_slot_pool()
            await self._refresh_slot_pages()
            return self.status()

        self.playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.profile_dir),
            headless=self.settings.headless,
            executable_path=browser_path,
            user_agent=self.settings.user_agent,
            viewport={"width": self.settings.viewport_width, "height": self.settings.viewport_height},
            args=launch_args,
        )
        self.page = None
        await self._ensure_daemon_page()
        await self._ensure_slot_pool()
        await self._refresh_slot_pages()
        self.started_at = _now_iso()
        self.last_error = ""
        return self.status()

    async def ensure_started(self) -> dict[str, Any]:
        async with self._lock:
            return await self._ensure_started_locked()

    async def warmup(self) -> dict[str, Any]:
        await self.ensure_started()
        assert self.page is not None
        try:
            await self.page.goto(self.settings.start_url, wait_until="domcontentloaded", timeout=45000)
            await self._mark_daemon_page(self.page)
            return self.status()
        except Exception as exc:
            self.last_error = str(exc)
            raise
        return self.status()

    async def open_and_capture(
        self,
        *,
        url: str,
        wait_until: str = "load",
        capture_network: bool = False,
        idle_ms: int = 5000,
    ) -> dict[str, Any]:
        await self.ensure_started()
        assert self.page is not None
        page = self.page

        try:
            response = await page.goto(url, wait_until=wait_until, timeout=60000)
            await page.wait_for_timeout(max(idle_ms, 0))
            await self._mark_daemon_page(page)

            result = {
                "provider": self.provider,
                "url": page.url,
                "title": await page.title(),
                "status_code": response.status if response else None,
                "captured_at": _now_iso(),
            }
            return result
        except Exception as exc:
            self.last_error = str(exc)
            raise

    async def fetch_sif_keywords(
        self,
        *,
        asin: str,
        capture_network: bool = False,
        idle_ms: int = 2500,
    ) -> dict[str, Any]:
        if self.provider != "sif":
            raise RuntimeError("fetch_sif_keywords is only supported for provider=sif")

        sif_url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=false&trafficType="
        slot = await self._acquire_slot()
        page = slot["page"]

        try:
            response = await page.goto(sif_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(max(idle_ms, 0))
            await self._mark_slot_page(page, str(slot["slot_id"]))
            rankings = await page.evaluate(
                """
                async () => {
                  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                  const pageLooksLoggedOut = () => {
                    const text = clean(document.body && document.body.innerText).toLowerCase();
                    const url = (location.href || '').toLowerCase();
                    const hardSignals = [
                      'session expired',
                      'unauthorized',
                      '请先登录',
                      '账号异常登录提醒',
                      '为了保证您的账号安全',
                      '手机号码',
                      '手机号',
                      '密码'
                    ];
                    if (url.includes('/login') || url.includes('/signin')) return true;
                    return hardSignals.some(item => text.includes(item.toLowerCase()));
                  };
                  const pageLooksBlocked = () => {
                    const text = clean(document.body && document.body.innerText).toLowerCase();
                    return ['captcha', 'robot', 'verify', 'verification', '验证码', '人机验证', '安全验证']
                      .some(item => text.includes(item.toLowerCase()));
                  };
                  const readRows = () => {
                    const rows = Array.from(
                      document.querySelectorAll('.table_wrap.section_block.keyword_list_table_wrap .reverse_table_container tbody tr.el-table__row')
                    ).slice(0, 3);
                    return rows.map((row) => {
                      const cells = row.querySelectorAll('td');
                      const keywordCell = cells[1] || null;
                      const organicCell = cells[6] || null;
                      const spCell = cells[8] || null;
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
                      const spNode = spCell
                        ? (spCell.querySelector('.rank_page_pos')
                          || spCell.querySelector('.rank_num')
                          || spCell)
                        : null;
                      return {
                        keyword: clean(keywordNode && keywordNode.textContent),
                        organic_rank: clean(organicNode && organicNode.textContent) || '-',
                        ad_rank: clean(spNode && spNode.textContent) || '-',
                      };
                    }).filter(item => item.keyword);
                  };

                  let rankings = [];
                  for (let i = 0; i < 40; i++) {
                    if (pageLooksLoggedOut()) {
                      return { state: 'login_required', rankings: [], current_url: location.href };
                    }
                    if (pageLooksBlocked()) {
                      return { state: 'challenge', rankings: [], current_url: location.href };
                    }
                    const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap');
                    if (wrap) {
                      wrap.scrollIntoView({ block: 'start' });
                      window.scrollBy(0, -100);
                    }
                    rankings = readRows();
                    const ready = rankings.length >= 3 && rankings.every(item => item.keyword);
                    if (ready) {
                      return { state: 'ok', rankings, current_url: location.href };
                    }
                    await wait(500);
                  }

                  rankings = readRows();
                  return {
                    state: rankings.length ? 'ok' : 'unknown',
                    rankings,
                    current_url: location.href,
                  };
                }
                """
            )
            result = {
                "provider": self.provider,
                "asin": asin,
                "url": page.url,
                "current_url": str((rankings or {}).get("current_url", "") or page.url),
                "title": await page.title(),
                "status_code": response.status if response else None,
                "state": str((rankings or {}).get("state", "unknown") or "unknown"),
                "rankings": (rankings or {}).get("rankings", []) if isinstance(rankings, dict) else [],
                "captured_at": _now_iso(),
            }
            return result
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            await self._release_slot(slot, current_url=page.url if not page.is_closed() else "")

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            await self._stop_browser()
            snapshot = self.status()
            return snapshot


class GenericBrowserDaemonManager:
    def __init__(self):
        self._services = {
            provider: BrowserProviderDaemon(provider) for provider in list_supported_providers()
        }

    def list_status(self) -> dict[str, Any]:
        return {provider: service.status() for provider, service in self._services.items()}

    def get_service(self, provider: str) -> BrowserProviderDaemon:
        normalized = str(provider or "").strip().lower()
        if normalized not in self._services:
            raise ValueError(f"Unsupported provider: {provider}")
        return self._services[normalized]
