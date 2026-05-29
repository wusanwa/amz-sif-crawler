import asyncio
import os

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

from sif_runtime import build_sif_browser_config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_ROOT = os.getenv("APP_RUNTIME_ROOT", os.path.join(BASE_DIR, "runtime_data"))
PROFILE_ROOT = os.getenv("PROFILE_ROOT_DIR", os.path.join(RUNTIME_ROOT, "profiles"))
SIF_PROFILE = os.getenv("SIF_PROFILE_DIR", os.path.join(PROFILE_ROOT, "sif"))
SIF_URL = os.getenv("SIF_DEBUG_URL", "https://www.sif.com/reverse?country=US&asin=B0FW44LMP4&isListingSearch=0")
SIF_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"


async def main():
    cfg = build_sif_browser_config(
        profile_dir=SIF_PROFILE,
        headless=False,
        extra_args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1600,1200",
        ],
        user_agent=SIF_UA,
        viewport={"width": 1600, "height": 1200},
    )

    async with AsyncWebCrawler(config=cfg) as crawler:
        print(f"Opening: {SIF_URL}", flush=True)
        await crawler.arun(
            url=SIF_URL,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                session_id="sif-debug-open",
                css_selector="body",
                wait_for="css:body",
                wait_until="commit",
                process_iframes=False,
                remove_overlay_elements=True,
                page_timeout=40000,
            ),
        )
        print("Browser is open. Close the window manually or press Ctrl+C here.", flush=True)
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
