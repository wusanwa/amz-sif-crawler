from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse


def extract_asin(candidate: str) -> str | None:
    if not candidate:
        return None
    match = re.search(r"\b(B[A-Z0-9]{9})\b", candidate.upper())
    return match.group(1) if match else None


def build_canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


def normalize_amazon_input(input_value: Any) -> dict[str, Any]:
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

    if not normalized.lower().startswith(("http://", "https://")):
        if asin_direct:
            return {"ok": True, "url": build_canonical_amazon_url(asin_direct), "asin": asin_direct}
        return {"ok": False, "error": "Invalid URL scheme"}

    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    is_amazon = "amazon." in host or host.endswith("amzn.to")
    if is_amazon and asin_direct:
        normalized = build_canonical_amazon_url(asin_direct)
    elif is_amazon and not asin_direct:
        return {"ok": False, "error": "Amazon URL missing ASIN"}

    return {"ok": True, "url": normalized, "asin": asin_direct or "UNKNOWN"}


def clean_text(raw_value: Any) -> str | None:
    value = re.sub(r"\s+", " ", str(raw_value or "")).strip()
    if not value:
        return None
    value = re.sub(r"\[[^\]]+\]\s*\{[^}]*\}", "", value).strip()
    value = re.sub(r"\s*Terms\s*$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def normalize_price_text(raw_value: Any) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    match = re.search(r"([$€£]\s*\d[\d,]*(?:\.\d{2})?)", value)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    return value or None


def derive_model_from_title(title: str) -> str | None:
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


def detect_amazon_parent_item_count(raw_html: str = "") -> int:
    structural_candidates: list[int] = []
    html_text = raw_html or ""

    def extract_candidate_li_tags(block: str) -> list[str]:
        return re.findall(r'<li\b[^>]*data-asin=["\'][^"\']+["\'][^>]*>', block, flags=re.IGNORECASE)

    def count_option_lis(block: str) -> int:
        li_tags = extract_candidate_li_tags(block)
        direct_option_count = 0
        seen_keys = set()

        for li_tag in li_tags:
            if re.search(r"(?i)\baok-hidden\b", li_tag):
                continue
            attrs = []
            for attr in ["data-asin", "data-defaultasin", "data-csa-c-item-id", "data-value", "data-dp-url", "title", "aria-label"]:
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
            import json

            dim_map = json.loads(match_dim.group(1))
            if isinstance(dim_map, dict):
                structural_candidates.append(len(dim_map))
        except Exception:
            pass

    for block in re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*(?:dimension-values-list|a-button-toggle-group)[^"\']*["\'][^>]*>.*?</ul>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        count = count_option_lis(block)
        if count > 0:
            structural_candidates.append(count)

    for block in re.findall(
        r'<(?:div|ul)\b[^>]+id=["\'](?:variation_[^"\']+|tp-inline-twister-[^"\']+|inline-twister-[^"\']+)["\'][^>]*>.*?</(?:div|ul)>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        count = count_option_lis(block)
        if count > 0:
            structural_candidates.append(count)

    global_twister_lis = extract_candidate_li_tags(html_text)
    if global_twister_lis:
        structural_candidates.append(count_option_lis("".join(global_twister_lis)))

    valid_counts = [count for count in structural_candidates if count > 0]
    if not valid_counts:
        return 0

    counter = Counter(valid_counts)
    most_common_count, most_common_freq = counter.most_common(1)[0]
    if most_common_freq >= 2:
        return most_common_count
    valid_counts.sort()
    return valid_counts[len(valid_counts) // 2]


def extract_amazon_variants(raw_html: str = "") -> list[dict[str, Any]]:
    html_text = raw_html or ""
    if not html_text:
        return []

    def find_li_blocks(block: str) -> list[str]:
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
    li_blocks: list[str] = []
    for block in list_blocks:
        li_blocks.extend(find_li_blocks(block))
    if not li_blocks:
        li_blocks = find_li_blocks(html_text)

    variants: list[dict[str, Any]] = []
    seen_asins = set()
    for li_block in li_blocks:
        asin_match = re.search(r'data-asin=["\']([^"\']+)["\']', li_block, flags=re.IGNORECASE)
        asin = asin_match.group(1).strip() if asin_match else ""
        if not asin or asin in seen_asins:
            continue
        seen_asins.add(asin)

        name = ""
        for pattern in [
            r'<img[^>]+alt=["\']([^"\']+)["\']',
            r'aria-label=["\']([^"\']+)["\']',
            r'title=["\']([^"\']+)["\']',
        ]:
            match = re.search(pattern, li_block, flags=re.IGNORECASE)
            if match:
                name = html.unescape(match.group(1)).strip()
                if name:
                    break
        if not name:
            name = asin

        price = None
        for pattern in [
            r'class=["\'][^"\']*apex-pricetopay-value[^"\']*["\'][^>]*>.*?<span aria-hidden=["\']true["\']>(.*?)</span>',
            r'class=["\'][^"\']*twister_swatch_price[^"\']*["\'][^>]*>.*?<span class=["\'][^"\']*olpWrapper[^"\']*["\']>(.*?)</span>',
            r'<span aria-hidden=["\']true["\']>\s*([^<]*?(?:[$€£]|JPY|USD)[^<]*)</span>',
        ]:
            match = re.search(pattern, li_block, flags=re.IGNORECASE | re.DOTALL)
            if match:
                candidate = re.sub(r"<[^>]+>", " ", match.group(1))
                candidate = re.sub(r"\s+", " ", html.unescape(candidate)).strip()
                if candidate:
                    price = candidate
                    break

        unavailable = bool(
            re.search(r'data-initiallyunavailable=["\']true["\']', li_block, flags=re.IGNORECASE)
            or re.search(r"\ba-button-unavailable\b", li_block, flags=re.IGNORECASE)
            or re.search(r"\bdefault-slot-unavailable\b", li_block, flags=re.IGNORECASE)
        )
        variants.append(
            {
                "variant_name": name,
                "price": normalize_price_text(price) if price else None,
                "is_available": not unavailable,
            }
        )
    return variants


def merge_failure_reason(*parts: str) -> str:
    merged: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        for seg in [x.strip() for x in text.split(";") if x.strip()]:
            if seg not in merged:
                merged.append(seg)
    return "; ".join(merged)
