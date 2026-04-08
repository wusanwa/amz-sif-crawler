import asyncio
import os
from crawl4ai import AsyncWebCrawler, BrowserConfig

async def inspect_crawler():
    browser_cfg = BrowserConfig(headless=True)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        print(f"Crawler strategy type: {type(crawler.crawler_strategy)}")
        print(f"Crawler strategy attributes: {dir(crawler.crawler_strategy)}")
        if hasattr(crawler, 'page'):
            print("Crawler has 'page' attribute")
        else:
            print("Crawler does NOT have 'page' attribute")

if __name__ == "__main__":
    asyncio.run(inspect_crawler())
