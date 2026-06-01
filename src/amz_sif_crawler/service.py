from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from amz_sif_crawler.fetchers.amazon import fetch_amazon_data
from amz_sif_crawler.fetchers.sif import fetch_sif_data
from amz_sif_crawler.runtime.cache import open_cache
from amz_sif_crawler.runtime.config import AppConfig, ensure_runtime_dirs, load_app_config
from amz_sif_crawler.utils import extract_asin, merge_failure_reason, normalize_amazon_input


logger = logging.getLogger(__name__)


def log_progress(asin: str, step: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    sys.stderr.write(f"[{timestamp}] [{asin}] {step}\n")
    sys.stderr.flush()


def _safe_get(data: dict[str, Any] | None, key: str, default: Any = "") -> Any:
    if not isinstance(data, dict):
        return default
    value = data.get(key, default)
    return default if value is None else value


async def crawl_urls(
    urls: list[str],
    *,
    config: AppConfig | None = None,
    outfile: str | None = None,
    mode: str = "both",
) -> list[dict[str, Any]]:
    app_config = config or load_app_config()
    ensure_runtime_dirs(app_config)
    cache = open_cache(str(app_config.cache_dir))
    results: list[dict[str, Any]] = []
    mode = (mode or "both").strip().lower()
    if mode not in {"both", "amazon", "sif"}:
        raise ValueError(f"Unsupported mode: {mode}")

    try:
        normalized_inputs: list[dict[str, str]] = []
        for raw_input in urls:
            normalized = normalize_amazon_input(raw_input)
            if normalized.get("ok"):
                normalized_inputs.append(
                    {
                        "url": normalized.get("url", ""),
                        "asin": normalized.get("asin", "UNKNOWN"),
                        "raw": str(raw_input),
                    }
                )
                continue

            asin = extract_asin(str(raw_input or "")) or "UNKNOWN"
            results.append(
                _build_result(
                    asin=asin,
                    amazon_data={},
                    sif_rankings=[],
                    failure_reason=f"Invalid Input: {normalized.get('error', 'Invalid input')}",
                )
            )

        output_path = _resolve_output_path(app_config.base_dir, outfile)
        for item in normalized_inputs:
            asin = item["asin"]
            url = item["url"]

            amazon_result = {"data": {}, "error": ""}
            sif_result = {"data": [], "error": ""}

            if mode in {"both", "amazon"}:
                cached_amazon = None if app_config.debug_mode else cache.get(f"amz_{asin}")
                if cached_amazon:
                    log_progress(asin, "🛒 命中 Amazon 缓存")
                    amazon_result = {"data": cached_amazon, "error": None}
                else:
                    amazon_result = await fetch_amazon_data(
                        url=url,
                        asin=asin,
                        profile_dir=str(app_config.amazon_profile_dir),
                        headless=app_config.amazon_headless,
                        log_progress=log_progress,
                    )
                    if not amazon_result.get("error"):
                        cache.set(f"amz_{asin}", amazon_result["data"], expire=app_config.cache_expiry_sec)

            if mode in {"both", "sif"}:
                cached_sif = None if app_config.debug_mode else cache.get(f"sif_{asin}")
                if cached_sif:
                    log_progress(asin, "🔍 命中 SIF 缓存")
                    sif_result = {"data": cached_sif, "error": None}
                else:
                    sif_result = await fetch_sif_data(
                        asin=asin,
                        profile_dir=str(app_config.sif_profile_dir),
                        headless=app_config.sif_headless,
                        log_progress=log_progress,
                        project_root=Path(app_config.base_dir),
                    )
                    if not sif_result.get("error"):
                        cache.set(f"sif_{asin}", sif_result["data"], expire=app_config.cache_expiry_sec)

            amazon_data = amazon_result.get("data", {})
            sif_rankings = sif_result.get("data", [])
            failure_reason = merge_failure_reason(
                amazon_result.get("error", ""),
                sif_result.get("error", ""),
                "" if mode == "sif" or _safe_get(amazon_data, "product_title") else "Amazon Empty Data",
                "" if mode == "amazon" or sif_rankings else "SIF Empty Data",
            )
            record = _build_result(
                asin=asin,
                amazon_data=amazon_data,
                sif_rankings=sif_rankings,
                failure_reason=failure_reason,
            )
            results.append(record)
            if output_path:
                with output_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        return results
    finally:
        cache.close()


def _resolve_output_path(base_dir: Path, outfile: str | None) -> Path | None:
    if not outfile:
        return None
    path = Path(outfile)
    if not path.is_absolute():
        path = base_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _build_result(
    *,
    asin: str,
    amazon_data: dict[str, Any],
    sif_rankings: list[dict[str, Any]],
    failure_reason: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asin": asin,
        "status": "SUCCESS" if not failure_reason else "PARTIAL",
        "failure_reason": failure_reason,
        "amazon_title": _safe_get(amazon_data, "product_title"),
        "amazon_price": _safe_get(amazon_data, "main_price"),
        "amazon_list_price": _safe_get(amazon_data, "list_price"),
        "amazon_savings_text": _safe_get(amazon_data, "savings_text"),
        "amazon_has_price_discount": bool(_safe_get(amazon_data, "has_price_discount", False)),
        "amazon_deal_type": _safe_get(amazon_data, "deal_type"),
        "amazon_is_limited_time_deal": bool(_safe_get(amazon_data, "is_limited_time_deal", False)),
        "amazon_coupon_text": _safe_get(amazon_data, "coupon_text"),
        "amazon_applied_coupon_text": _safe_get(amazon_data, "applied_coupon_text"),
        "amazon_has_coupon": bool(_safe_get(amazon_data, "has_coupon", False)),
        "amazon_model": _safe_get(amazon_data, "model_number"),
        "amazon_total_variants": _safe_get(amazon_data, "parent_item_count", 0),
        "amazon_variants": _safe_get(amazon_data, "variants", []),
        "sif_1_kw": _safe_get(sif_rankings[0] if sif_rankings else {}, "keyword"),
        "full_sif": sif_rankings,
    }


async def crawl_and_wrap(
    urls: list[str],
    *,
    config: AppConfig | None = None,
    outfile: str | None = None,
    mode: str = "both",
) -> dict[str, Any]:
    results = await crawl_urls(urls, config=config, outfile=outfile, mode=mode)
    return {"status": "success", "count": len(results), "results": results}


def run_cli(urls: list[str], outfile: str | None = None, mode: str = "both") -> dict[str, Any]:
    return asyncio.run(crawl_and_wrap(urls, outfile=outfile, mode=mode))
