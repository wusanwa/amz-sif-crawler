import asyncio, os
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from sif_runtime import build_sif_browser_config

PROFILE=os.path.join(os.getcwd(),'runtime_data/profiles/sif')
URL='https://www.sif.com/reverse?country=US&asin=B0FW44LMP4&isListingSearch=0'

async def main():
    cfg = build_sif_browser_config(profile_dir=PROFILE, headless=True, extra_args=['--disable-blink-features=AutomationControlled','--no-sandbox','--disable-dev-shm-usage'])
    async with AsyncWebCrawler(config=cfg) as crawler:
        res = await crawler.arun(url=URL, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, css_selector='body', wait_for='css:body', wait_until='commit', process_iframes=False, remove_overlay_elements=True, page_timeout=40000))
        print('SUCCESS', res.success)
        print('STATUS', res.status_code)
        print('FINAL_URL', res.url)
        html = res.cleaned_html or ''
        print('HTML_LEN', len(html))
        for key in ['keyword_list_table_wrap','reverse_table_container','asin-keyword','请先登录','手机号密码登录','流量词','el-table__body','travel hair dryer']:
            print('HAS', key, key in html)
        with open('tmp_sif_page.html','w',encoding='utf-8') as f:
            f.write(html)
        print('WROTE tmp_sif_page.html')

asyncio.run(main())
