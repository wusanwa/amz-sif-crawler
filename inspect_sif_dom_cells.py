import asyncio, os, json
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from sif_runtime import build_sif_browser_config

PROFILE=os.path.join(os.getcwd(),'runtime_data/profiles/sif')
URL='https://www.sif.com/reverse?country=US&asin=B0FW44LMP4&isListingSearch=0'

JS = [
"const wait = (ms) => new Promise(r => setTimeout(r, ms));",
"for (let i = 0; i < 20; i++) { const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap'); const rows = wrap ? wrap.querySelectorAll('.reverse_table_container tbody tr.el-table__row') : []; if (rows.length >= 3) break; await wait(500); }",
"await wait(1000);",
"(function() { const clean = (v) => (v || '').replace(/\\s+/g, ' ').trim(); const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap'); const rows = Array.from(wrap.querySelectorAll('.reverse_table_container tbody tr.el-table__row')).slice(0,3); const data = rows.map((row, rix) => ({ row: rix+1, cells: Array.from(row.querySelectorAll('td')).map((td, cix) => ({ idx: cix+1, text: clean(td.textContent).slice(0,200) })) })); let holder = document.getElementById('__SIF_DEBUG__'); if (!holder) { holder = document.createElement('div'); holder.id='__SIF_DEBUG__'; holder.style.display='none'; document.body.appendChild(holder);} holder.textContent = JSON.stringify(data); })();"
]

async def main():
    cfg = build_sif_browser_config(profile_dir=PROFILE, headless=True, extra_args=['--disable-blink-features=AutomationControlled','--no-sandbox','--disable-dev-shm-usage'])
    async with AsyncWebCrawler(config=cfg) as crawler:
        res = await crawler.arun(url=URL, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, css_selector='body', wait_for='css:body', wait_until='commit', process_iframes=False, remove_overlay_elements=True, page_timeout=40000, js_code=JS, excluded_tags=['script', 'style', 'path', 'svg', 'nav', 'footer', 'header', 'aside', 'iframe', 'canvas', 'noscript', 'form'], excluded_selector='#header, #footer, .navbar, .sidebar'))
        html = res.cleaned_html or ''
        import re
        m = re.search(r'<div[^>]+id=["\']__SIF_DEBUG__["\'][^>]*>(.*?)</div>', html, re.I | re.S)
        print(m.group(1) if m else 'NO_DEBUG')

asyncio.run(main())
