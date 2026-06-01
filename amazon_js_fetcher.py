import asyncio
import html
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, async_playwright


def extract_asin(candidate: str) -> Optional[str]:
    if not candidate:
        return None
    match = re.search(r"\b(B[A-Z0-9]{9})\b", candidate.upper())
    return match.group(1) if match else None


def build_canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


def normalize_amazon_input(input_value: Any) -> Dict[str, Any]:
    raw = str(input_value or "").strip()
    if not raw:
        return {"ok": False, "error": "Empty URL/ASIN"}

    asin_direct = extract_asin(raw)
    if asin_direct and re.fullmatch(r"B[A-Z0-9]{9}", raw.upper()):
        return {"ok": True, "url": build_canonical_amazon_url(asin_direct), "asin": asin_direct}

    normalized = raw
    if re.match(r"^(www\.)", normalized, flags=re.IGNORECASE):
        normalized = f"https://{normalized}"
    elif re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", normalized, flags=re.IGNORECASE):
        normalized = f"https://{normalized}"

    allowed_prefixes = ("http://", "https://")
    if not normalized.lower().startswith(allowed_prefixes):
        if asin_direct:
            return {"ok": True, "url": build_canonical_amazon_url(asin_direct), "asin": asin_direct}
        return {"ok": False, "error": "Invalid URL scheme"}

    asin = asin_direct
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    is_amazon = "amazon." in host or host.endswith("amzn.to")
    if is_amazon and asin:
        normalized = build_canonical_amazon_url(asin)
    elif is_amazon and not asin:
        return {"ok": False, "error": "Amazon URL missing ASIN"}

    return {"ok": True, "url": normalized, "asin": asin or "UNKNOWN"}


def _derive_model_from_title(title: str) -> Optional[str]:
    normalized_title = str(title or "").strip()
    if not normalized_title:
        return None
    segments = re.split(r"[,|\|:]|\s-\s", normalized_title)
    if not segments:
        return None
    candidate = segments[0].strip()
    if re.fullmatch(r"B[A-Z0-9]{9}", candidate):
        candidate = segments[1].strip() if len(segments) > 1 else ""
    return candidate or None


def _normalize_price_text(raw_value: Any) -> Optional[str]:
    value = str(raw_value or "").strip()
    if not value:
        return None
    match = re.search(r"([$€£]\s*\d[\d,]*(?:\.\d{2})?)", value)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    return value or None


def _clean_text(raw_value: Any) -> Optional[str]:
    value = re.sub(r"\s+", " ", str(raw_value or "")).strip()
    if not value:
        return None
    value = re.sub(r"\[[^\]]+\]\s*\{[^}]*\}", "", value).strip()
    value = re.sub(r"\s*Terms\s*$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value.replace(",", ""))
        if match:
            try:
                return int(match.group(0))
            except ValueError:
                return 0
    return 0


def _extract_amazon_parent_item_count(raw_html: str = "") -> int:
    structural_candidates: List[int] = []
    html_text = raw_html or ""

    def _extract_candidate_li_tags(block: str) -> List[str]:
        return re.findall(
            r'<li\b[^>]*data-asin=["\'][^"\']+["\'][^>]*>',
            block,
            flags=re.IGNORECASE,
        )

    def _count_option_lis(block: str) -> int:
        li_tags = _extract_candidate_li_tags(block)
        direct_option_count = 0
        seen_keys = set()

        for li_tag in li_tags:
            if re.search(r'(?i)\baok-hidden\b', li_tag):
                continue

            attrs = []
            for attr in [
                "data-asin",
                "data-defaultasin",
                "data-csa-c-item-id",
                "data-value",
                "data-dp-url",
                "title",
                "aria-label",
            ]:
                match = re.search(rf'{attr}=["\']([^"\']+)["\']', li_tag, flags=re.IGNORECASE)
                if match and match.group(1).strip():
                    attrs.append(f"{attr}:{match.group(1).strip()}")

            if not attrs:
                continue

            option_key = "|".join(attrs)
            if option_key in seen_keys:
                continue
            seen_keys.add(option_key)
            direct_option_count += 1

        return direct_option_count

    for pattern in [
        r'"parent_item_count"\s*:\s*(\d+)',
        r'"parentItemCount"\s*:\s*(\d+)',
        r'"totalVariationCount"\s*:\s*(\d+)',
        r'"totalVariations"\s*:\s*(\d+)',
        r'"variationCount"\s*:\s*(\d+)',
    ]:
        for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
            structural_candidates.append(int(match.group(1)))

    match_dim = re.search(
        r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,\s*"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match_dim:
        try:
            dim_map = json.loads(match_dim.group(1))
            if isinstance(dim_map, dict):
                structural_candidates.append(len(dim_map))
        except Exception:
            pass

    option_list_blocks = re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*(?:dimension-values-list|a-button-toggle-group)[^"\']*["\'][^>]*>.*?</ul>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in option_list_blocks:
        direct_option_count = _count_option_lis(block)
        if direct_option_count > 0:
            structural_candidates.append(direct_option_count)

    variation_blocks = re.findall(
        r'<(?:div|ul)\b[^>]+id=["\'](?:variation_[^"\']+|tp-inline-twister-[^"\']+|inline-twister-[^"\']+)["\'][^>]*>.*?</(?:div|ul)>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in variation_blocks:
        direct_option_count = _count_option_lis(block)
        if direct_option_count > 0:
            structural_candidates.append(direct_option_count)

    global_twister_lis = _extract_candidate_li_tags(html_text)
    if global_twister_lis:
        structural_candidates.append(_count_option_lis("".join(global_twister_lis)))

    valid_counts = [count for count in structural_candidates if count > 0]
    if not valid_counts:
        return 0

    counter = Counter(valid_counts)
    most_common_count, most_common_freq = counter.most_common(1)[0]
    if most_common_freq >= 2:
        return most_common_count

    valid_counts.sort()
    return valid_counts[len(valid_counts) // 2]


def _extract_amazon_variants(raw_html: str = "") -> List[Dict[str, Any]]:
    html_text = raw_html or ""
    if not html_text:
        return []

    def _find_li_blocks(block: str) -> List[str]:
        return re.findall(
            r'<li\b[^>]*data-asin=["\'][^"\']+["\'][^>]*>.*?</li>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

    list_blocks = re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*(?:dimension-values-list|a-button-toggle-group)[^"\']*["\'][^>]*>.*?</ul>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    li_blocks: List[str] = []
    for block in list_blocks:
        li_blocks.extend(_find_li_blocks(block))

    if not li_blocks:
        li_blocks = _find_li_blocks(html_text)

    variants: List[Dict[str, Any]] = []
    seen_asins = set()

    for li_block in li_blocks:
        asin_match = re.search(r'data-asin=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
        asin = asin_match.group(1).strip() if asin_match else ""
        if not asin or asin in seen_asins:
            continue
        seen_asins.add(asin)

        name = ""
        img_match = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
        if img_match:
            name = html.unescape(img_match.group(1)).strip()
        if not name:
            label_match = re.search(r'aria-label=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
            if label_match:
                name = html.unescape(label_match.group(1)).strip()
        if not name:
            title_match = re.search(r'title=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
            if title_match:
                name = html.unescape(title_match.group(1)).strip()
        if not name:
            name = asin

        price = None
        price_patterns = [
            r'class=["\'][^"\']*apex-pricetopay-value[^"\']*["\'][^>]*>.*?<span aria-hidden=["\']true["\']>(.*?)</span>',
            r'class=["\'][^"\']*twister_swatch_price[^"\']*["\'][^>]*>.*?<span class=["\'][^"\']*olpWrapper[^"\']*["\']>(.*?)</span>',
            r'<span aria-hidden=["\']true["\']>\s*([^<]*?(?:[$€£]|JPY|USD)[^<]*)</span>',
        ]
        for pattern in price_patterns:
            match = re.search(pattern, li_block, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            candidate = re.sub(r"<[^>]+>", " ", match.group(1))
            candidate = re.sub(r"\s+", " ", html.unescape(candidate)).strip()
            if candidate:
                price = candidate
                break

        unavailable = bool(
            re.search(r'data-initiallyunavailable=["\']true["\']', li_block, flags=re.IGNORECASE)
            or re.search(r'\ba-button-unavailable\b', li_block, flags=re.IGNORECASE)
            or re.search(r'\bdefault-slot-unavailable\b', li_block, flags=re.IGNORECASE)
        )

        variants.append(
            {
                "variant_name": name,
                "price": _normalize_price_text(price) if price else None,
                "is_available": not unavailable,
            }
        )

    return variants


def _normalize_amazon_data(data: Dict[str, Any], raw_html: str = "") -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    aliases = [
        "parent_item_count",
        "parentItemCount",
        "total_variants",
        "total_variant_count",
        "variant_count",
        "amazon_total_variants",
    ]

    alias_counts: List[int] = []
    for key in aliases:
        if key in data:
            coerced = _coerce_int(data.get(key))
            if coerced > 0:
                alias_counts.append(coerced)

    structural_count = _extract_amazon_parent_item_count(raw_html=raw_html)
    count = structural_count if structural_count > 0 else 0

    if count <= 0 and alias_counts:
        alias_counter = Counter(alias_counts)
        count = alias_counter.most_common(1)[0][0]

    parsed_variants = _extract_amazon_variants(raw_html=raw_html)
    variants = data.get("variants")
    if not isinstance(variants, list):
        variants = []

    if parsed_variants and len(parsed_variants) >= len(variants):
        data["variants"] = parsed_variants
        variants = parsed_variants

    if variants:
        count = max(count, len(variants))

    data["parent_item_count"] = max(count, 0)
    return data


async def _open_amazon_context(
    *,
    profile_dir: str,
    headless: bool,
    user_agent: str,
    extra_args: list[str],
) -> tuple[Any, BrowserContext]:
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=headless,
        user_agent=user_agent,
        viewport={"width": 1440, "height": 1400},
        args=extra_args,
    )
    return playwright, context


async def _extract_amazon_product(page: Page) -> Dict[str, Any]:
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
          const hasStrikethroughPrice = !!listPrice;
          const hasSavingsPercent = !!savingsPercent;

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
            has_price_discount: hasStrikethroughPrice || hasSavingsPercent,
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


async def extract_amazon_product_from_page(page: Page) -> Dict[str, Any]:
    payload = await _extract_amazon_product(page)
    page_state = str(payload.get("page_state") or "unknown")
    if page_state == "challenge":
        return {"data": {}, "error": "Amazon Blocked/CAPTCHA"}
    if page_state == "not_found":
        return {"data": {}, "error": "Amazon 404: Page Not Found"}
    if page_state == "search_results":
        return {"data": {}, "error": "Wrong Page Type: Search Results"}

    product_title = str(payload.get("product_title") or "").strip()
    if not product_title:
        return {"data": {}, "error": "Invalid Product Page: No Title Found"}

    data = {
        "product_title": product_title,
        "main_price": _normalize_price_text(payload.get("main_price")),
        "list_price": _normalize_price_text(payload.get("list_price")),
        "savings_text": _clean_text(payload.get("savings_text")),
        "has_price_discount": bool(payload.get("has_price_discount")),
        "deal_type": _clean_text(payload.get("deal_type")),
        "is_limited_time_deal": bool(payload.get("is_limited_time_deal")),
        "coupon_text": _clean_text(payload.get("coupon_text")),
        "applied_coupon_text": _clean_text(payload.get("applied_coupon_text")),
        "has_coupon": bool(payload.get("has_coupon")),
        "model_number": _derive_model_from_title(product_title),
        "variants": [],
        "parent_item_count": 0,
        "current_url": payload.get("current_url") or page.url,
    }
    raw_html = await page.content()
    normalized_data = _normalize_amazon_data(data, raw_html=raw_html)
    return {"data": normalized_data, "error": None}


async def fetch_amazon_data_js(
    *,
    url: str,
    asin: str,
    profile_dir: str,
    headless: bool,
    user_agent: str,
    extra_args: list[str],
    log_progress,
) -> Dict[str, Any]:
    playwright = None
    context = None
    page = None
    try:
        log_progress(asin, "🛒 使用 Playwright + 页面内 JS 抓取 Amazon 数据...")
        playwright, context = await _open_amazon_context(
            profile_dir=profile_dir,
            headless=headless,
            user_agent=user_agent,
            extra_args=extra_args,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        for _ in range(20):
            payload = await _extract_amazon_product(page)
            if payload.get("product_title") or payload.get("main_price") or payload.get("page_state") != "ok":
                break
            await page.wait_for_timeout(500)

        payload = await _extract_amazon_product(page)
        return await extract_amazon_product_from_page(page)
    except asyncio.TimeoutError:
        return {"data": {}, "error": "Amazon Timeout"}
    except Exception as exc:
        return {"data": {}, "error": str(exc)}
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
