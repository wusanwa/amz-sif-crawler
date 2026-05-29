import asyncio, os, re
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from sif_runtime import build_sif_browser_config
from sif_query import _extract_sif_top3_from_html

PROFILE=os.path.join(os.getcwd(),'runtime_data/profiles/sif')
URL='https://www.sif.com/reverse?country=US&asin=B0FW44LMP4&isListingSearch=0'

JS = [
"const wait = (ms) => new Promise(r => setTimeout(r, ms));",
"for (let i = 0; i < 20; i++) { const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap'); const rows = wrap ? wrap.querySelectorAll('.el-table__body tbody tr') : []; if (rows.length >= 3) break; await wait(500); }",
"const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap'); if (wrap) { wrap.scrollIntoView({ block: 'start' }); window.scrollBy(0, -100); }",
"await wait(1000);",
"(function() { const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim(); const wrap = document.querySelector('.table_wrap.section_block.keyword_list_table_wrap'); if (!wrap) return; const bodyTable = wrap.querySelector('.reverse_table_container .el-table__body-wrapper table.el-table__body'); const rows = Array.from((bodyTable || wrap).querySelectorAll('tbody > tr.el-table__row')).slice(0, 3); const data = rows.map((row) => { const cells = row.querySelectorAll('td'); const keywordCell = cells[1] || null; const organicCell = cells[6] || null; const spCell = cells[8] || null; const keywordNode = keywordCell ? (keywordCell.querySelector('.correct_direction.inline_block') || keywordCell.querySelector('.icon_copy') || keywordCell.querySelector('.edit_word')) : null; const organicNode = organicCell ? (organicCell.querySelector('.rank_page_pos') || organicCell.querySelector('.rank_num') || organicCell) : null; const spNode = spCell ? (spCell.querySelector('.rank_page_pos') || spCell.querySelector('.rank_num') || spCell) : null; return { keyword: clean(keywordNode && keywordNode.textContent), organic_rank: clean(organicNode && organicNode.textContent) || '-', ad_rank: clean(spNode && spNode.textContent) || '-', }; }).filter(item => item.keyword); let holder = document.getElementById('__SIF_TOP3__'); if (!holder) { holder = document.createElement('div'); holder.id = '__SIF_TOP3__'; holder.style.display = 'none'; document.body.appendChild(holder); } holder.textContent = JSON.stringify(data); })();"
]

async def main():
    cfg = build_sif_browser_config(profile_dir=PROFILE, headless=True, extra_args=['--disable-blink-features=AutomationControlled','--no-sandbox','--disable-dev-shm-usage'])
    async with AsyncWebCrawler(config=cfg) as crawler:
        res = await crawler.arun(url=URL, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, css_selector='body', wait_for='css:body', wait_until='commit', process_iframes=False, remove_overlay_elements=True, page_timeout=40000, js_code=JS, excluded_tags=['script', 'style', 'path', 'svg', 'nav', 'footer', 'header', 'aside', 'iframe', 'canvas', 'noscript', 'form'], excluded_selector='#header, #footer, .navbar, .sidebar'))
        html = res.cleaned_html or ''
        print('HAS_INJECTED', '__SIF_TOP3__' in html)
        m = re.search(r'<div[^>]+id=["\']__SIF_TOP3__["\'][^>]*>(.*?)</div>', html, re.I | re.S)
        print('MATCH', bool(m))
        if m:
            print('PAYLOAD', m.group(1)[:500])
        print('PARSED', _extract_sif_top3_from_html(html))

asyncio.run(main())
