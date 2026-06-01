from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from typing import Any, Callable

from playwright.async_api import Page

from amz_sif_crawler.runtime.browser import COMMON_BROWSER_ARGS, open_persistent_context, summarize_browser_error
from amz_sif_crawler.utils import (
    clean_text,
    derive_model_from_title,
    detect_amazon_parent_item_count,
    extract_amazon_variants,
    normalize_price_text,
)


AMAZON_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


async def _read_amazon_payload(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const textOf = (selector) => {
            const node = document.querySelector(selector);
            return clean(node ? (node.innerText || node.textContent || '') : '');
          };
          const visibleTextOf = (selector) => {
            const node = document.querySelector(selector);
            if (!node) return '';
            const clone = node.cloneNode(true);
            clone.querySelectorAll('style, script').forEach((el) => el.remove());
            return clean(clone.innerText || clone.textContent || '');
          };
          const bodyText = clean(document.body ? (document.body.innerText || '') : '').toLowerCase();
          const currentUrl = location.href || '';

          const productTitle = textOf('#productTitle')
            || textOf('#title')
            || clean(document.title || '');

          const priceToPayLabel = textOf('#apex-pricetopay-accessibility-label');
          const mainPrice = priceToPayLabel
            || textOf('#corePriceDisplay_desktop_feature_div #apex_desktop .a-offscreen')
            || textOf('#corePrice_feature_div .a-price .a-offscreen')
            || textOf('#priceblock_ourprice')
            || textOf('#priceblock_dealprice')
            || textOf('#priceblock_saleprice');
          const listPrice = textOf('.basisPrice .a-price .a-offscreen')
            || textOf('.basisPrice .apex-basisprice-value .a-offscreen')
            || textOf('.basisPrice .apex-basisprice-value')
            || textOf('#priceBlockStrikePriceString')
            || textOf('.a-price.a-text-price .a-offscreen');
          const savingsPercent = textOf('.apex-savings-percentage')
            || textOf('.savingsPercentage');
          const dealBadge = textOf('#dealBadgeSupportingText');
          const couponText = textOf('.promoPriceBlockMessage .couponLabelText')
            || textOf('.promoPriceBlockMessage [id^="couponText"]')
            || visibleTextOf('.promoPriceBlockMessage');
          const appliedCouponText = textOf('.promoPriceBlockMessage [id^="done"]')
            || textOf('.promoPriceBlockMessage .a-color-success')
            || '';

          return {
            current_url: currentUrl,
            product_title: productTitle,
            main_price: mainPrice || null,
            list_price: listPrice || null,
            savings_text: savingsPercent || null,
            deal_type: dealBadge || null,
            is_limited_time_deal: /limited time deal/i.test(dealBadge),
            coupon_text: couponText || null,
            applied_coupon_text: appliedCouponText || null,
            has_coupon: /coupon/i.test(`${couponText} ${appliedCouponText}`),
            has_price_discount: !!listPrice || !!savingsPercent,
            page_state: bodyText.includes('robot check') || bodyText.includes('enter the characters you see below')
              ? 'challenge'
              : bodyText.includes('dogs of amazon') || bodyText.includes('sorry! we couldn\\'t find that page')
                ? 'not_found'
                : currentUrl.includes('/s?') || bodyText.includes('results for')
                  ? 'search_results'
                  : 'ok',
          };
        }
        """
    )


async def extract_amazon_product_from_page(page: Page) -> dict[str, Any]:
    payload = await _read_amazon_payload(page)
    state = str(payload.get("page_state") or "unknown")
    if state == "challenge":
        return {"data": {}, "error": "Amazon Blocked/CAPTCHA"}
    if state == "not_found":
        return {"data": {}, "error": "Amazon 404: Page Not Found"}
    if state == "search_results":
        return {"data": {}, "error": "Wrong Page Type: Search Results"}

    product_title = str(payload.get("product_title") or "").strip()
    if not product_title:
        return {"data": {}, "error": "Invalid Product Page: No Title Found"}

    raw_html = await page.content()
    variants = extract_amazon_variants(raw_html=raw_html)
    parent_item_count = detect_amazon_parent_item_count(raw_html=raw_html)
    if variants:
        parent_item_count = max(parent_item_count, len(variants))

    return {
        "data": {
            "product_title": product_title,
            "main_price": normalize_price_text(payload.get("main_price")),
            "list_price": normalize_price_text(payload.get("list_price")),
            "savings_text": clean_text(payload.get("savings_text")),
            "has_price_discount": bool(payload.get("has_price_discount")),
            "deal_type": clean_text(payload.get("deal_type")),
            "is_limited_time_deal": bool(payload.get("is_limited_time_deal")),
            "coupon_text": clean_text(payload.get("coupon_text")),
            "applied_coupon_text": clean_text(payload.get("applied_coupon_text")),
            "has_coupon": bool(payload.get("has_coupon")),
            "model_number": derive_model_from_title(product_title),
            "variants": variants,
            "parent_item_count": max(parent_item_count, 0),
            "current_url": payload.get("current_url") or page.url,
        },
        "error": None,
    }


async def fetch_amazon_data(
    *,
    url: str,
    asin: str,
    profile_dir: str,
    headless: bool,
    log_progress: Callable[[str, str], None],
) -> dict[str, Any]:
    temp_profile = tempfile.mkdtemp(prefix="amz-crawl-", dir="/tmp")
    started_at = time.perf_counter()
    try:
        log_progress(asin, "🛒 使用 Playwright + 页面内 JS 抓取 Amazon 数据...")
        async with open_persistent_context(
            profile_dir=temp_profile,
            headless=headless,
            user_agent=AMAZON_USER_AGENT,
            viewport={"width": 1440, "height": 1400},
            extra_args=COMMON_BROWSER_ARGS,
        ) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            for _ in range(20):
                payload = await _read_amazon_payload(page)
                if payload.get("product_title") or payload.get("main_price") or payload.get("page_state") != "ok":
                    break
                await page.wait_for_timeout(500)

            return await extract_amazon_product_from_page(page)
    except asyncio.TimeoutError:
        return {"data": {}, "error": "Amazon Timeout"}
    except Exception as exc:
        return {"data": {}, "error": summarize_browser_error(exc)}
    finally:
        log_progress(asin, f"⏱️ Amazon 耗时: {time.perf_counter() - started_at:.2f}s")
        shutil.rmtree(temp_profile, ignore_errors=True)
