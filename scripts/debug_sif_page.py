from __future__ import annotations

import asyncio
import os

from amz_sif_crawler.fetchers.sif import detect_sif_auth_state, extract_sif_top3_from_page
from amz_sif_crawler.runtime.daemon_manager import PersistentBrowserDaemon


async def main() -> None:
    asin = os.getenv("DEBUG_ASIN", "B0CDX5XGLK").strip() or "B0CDX5XGLK"
    profile_dir = os.getenv("DEBUG_SIF_PROFILE_DIR", "/app/runtime_data/profiles/sif")
    base_dir = os.getenv("DEBUG_BASE_DIR", "/app")
    daemon = PersistentBrowserDaemon(mode="sif", profile_dir=profile_dir, headless=True, base_dir=base_dir)
    await daemon.start()
    try:
        page = daemon.page
        assert page is not None
        url = f"https://www.sif.com/reverse?country=US&asin={asin}&isListingSearch=false&trafficType="
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        title = await page.title()
        body = await page.locator("body").inner_text()
        rows = await extract_sif_top3_from_page(page)
        print(f"URL: {page.url}")
        print(f"TITLE: {title}")
        print(f"STATE: {detect_sif_auth_state(page.url, body)}")
        print(f"ROWS: {len(rows)}")
        print(f"DATA: {rows}")
        print("BODY_SNIPPET_START")
        print(body[:3000].replace("\n", " | "))
        print("BODY_SNIPPET_END")
    finally:
        await daemon.stop()


if __name__ == "__main__":
    asyncio.run(main())
