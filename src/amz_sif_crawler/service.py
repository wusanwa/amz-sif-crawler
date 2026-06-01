from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from amz_sif_crawler.fetchers.amazon import fetch_amazon_data
from amz_sif_crawler.fetchers.sif import fetch_sif_data
from amz_sif_crawler.runtime.cache import open_cache
from amz_sif_crawler.runtime.daemon_client import call_daemon
from amz_sif_crawler.runtime.config import AppConfig, ensure_runtime_dirs, load_app_config
from amz_sif_crawler.utils import build_canonical_amazon_url, extract_asin, merge_failure_reason, normalize_amazon_input


logger = logging.getLogger(__name__)

DAILY_REPORT_HEADERS = [
    "URL链接",
    "产品型号",
    "价格",
    "核心词-1",
    "核心词自然位-1",
    "核心词广告位-1",
    "核心词-2",
    "核心词自然位-2",
    "核心词广告位-2",
    "父体数量",
]


def log_progress(asin: str, step: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    sys.stderr.write(f"[{timestamp}] [{asin}] {step}\n")
    sys.stderr.flush()


def _safe_get(data: dict[str, Any] | None, key: str, default: Any = "") -> Any:
    if not isinstance(data, dict):
        return default
    value = data.get(key, default)
    return default if value is None else value


async def _fetch_amazon_for_asin(
    *,
    asin: str,
    url: str,
    app_config: AppConfig,
    cache: Any,
) -> dict[str, Any]:
    cached_amazon = None
    if cache and not app_config.debug_mode:
        cached_amazon = cache.get(f"amz_{asin}")
    if cached_amazon:
        log_progress(asin, "🛒 命中 Amazon 缓存")
        return {"data": cached_amazon, "error": None}
    if app_config.amazon_daemon_url:
        log_progress(asin, "🛒 调用 Amazon daemon...")
        result = await call_daemon(
            base_url=app_config.amazon_daemon_url,
            path="/fetch",
            payload={"url": url, "asin": asin},
        )
    else:
        result = await fetch_amazon_data(
            url=url,
            asin=asin,
            profile_dir=str(app_config.amazon_profile_dir),
            headless=app_config.amazon_headless,
            log_progress=log_progress,
        )
    if cache and not result.get("error"):
        cache.set(f"amz_{asin}", result["data"], expire=app_config.cache_expiry_sec)
    return result


async def _fetch_sif_for_asin(
    *,
    asin: str,
    app_config: AppConfig,
    cache: Any,
) -> dict[str, Any]:
    cached_sif = None
    if cache and not app_config.debug_mode:
        cached_sif = cache.get(f"sif_{asin}")
    if cached_sif:
        log_progress(asin, "🔍 命中 SIF 缓存")
        return {"data": cached_sif, "error": None}
    if app_config.sif_daemon_url:
        log_progress(asin, "🔍 调用 SIF daemon...")
        result = await call_daemon(
            base_url=app_config.sif_daemon_url,
            path="/fetch",
            payload={"asin": asin},
        )
    else:
        result = await fetch_sif_data(
            asin=asin,
            profile_dir=str(app_config.sif_profile_dir),
            headless=app_config.sif_headless,
            log_progress=log_progress,
            project_root=Path(app_config.base_dir),
        )
    if cache and not result.get("error"):
        cache.set(f"sif_{asin}", result["data"], expire=app_config.cache_expiry_sec)
    return result


async def crawl_urls(
    urls: list[str],
    *,
    config: AppConfig | None = None,
    outfile: str | None = None,
    mode: str = "both",
) -> list[dict[str, Any]]:
    app_config = config or load_app_config()
    ensure_runtime_dirs(app_config)
    cache = open_cache(str(app_config.cache_dir)) if app_config.cache_enabled else None
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
        timings = {item["asin"]: time.perf_counter() for item in normalized_inputs}
        amazon_results: dict[str, dict[str, Any]] = {
            item["asin"]: {"data": {}, "error": ""} for item in normalized_inputs
        }
        sif_results: dict[str, dict[str, Any]] = {
            item["asin"]: {"data": [], "error": ""} for item in normalized_inputs
        }

        amazon_batch_task = None
        sif_batch_task = None

        if mode in {"both", "amazon"} and normalized_inputs:
            amazon_batch_task = asyncio.gather(
                *[
                    _fetch_amazon_for_asin(
                        asin=item["asin"],
                        url=item["url"],
                        app_config=app_config,
                        cache=cache,
                    )
                    for item in normalized_inputs
                ]
            )

        if mode in {"both", "sif"} and normalized_inputs:
            sif_batch_task = asyncio.gather(
                *[
                    _fetch_sif_for_asin(
                        asin=item["asin"],
                        app_config=app_config,
                        cache=cache,
                    )
                    for item in normalized_inputs
                ]
            )

        if amazon_batch_task and sif_batch_task:
            amazon_batch, sif_batch = await asyncio.gather(amazon_batch_task, sif_batch_task)
            amazon_results.update(
                {
                    item["asin"]: result
                    for item, result in zip(normalized_inputs, amazon_batch, strict=True)
                }
            )
            sif_results.update(
                {
                    item["asin"]: result
                    for item, result in zip(normalized_inputs, sif_batch, strict=True)
                }
            )
        elif amazon_batch_task:
            amazon_batch = await amazon_batch_task
            amazon_results.update(
                {
                    item["asin"]: result
                    for item, result in zip(normalized_inputs, amazon_batch, strict=True)
                }
            )
        elif sif_batch_task:
            sif_batch = await sif_batch_task
            sif_results.update(
                {
                    item["asin"]: result
                    for item, result in zip(normalized_inputs, sif_batch, strict=True)
                }
            )

        for item in normalized_inputs:
            asin = item["asin"]
            amazon_result = amazon_results[asin]
            sif_result = sif_results[asin]

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
            log_progress(asin, f"⏱️ 总耗时: {time.perf_counter() - timings[asin]:.2f}s")

        return results
    finally:
        if cache:
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
        "amazon_url": _safe_get(amazon_data, "current_url", build_canonical_amazon_url(asin)),
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


def _clean_csv_price(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("$"):
        return text[1:]
    return text


def _report_rank(rankings: list[dict[str, Any]], index: int, key: str) -> str:
    item = rankings[index] if len(rankings) > index and isinstance(rankings[index], dict) else {}
    value = item.get(key, "") if isinstance(item, dict) else ""
    return str(value or "").strip() or "/"


def build_daily_report_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        rankings = item.get("full_sif", []) if isinstance(item, dict) else []
        rows.append(
            {
                "URL链接": str(item.get("amazon_url") or build_canonical_amazon_url(str(item.get("asin") or "UNKNOWN"))),
                "产品型号": str(item.get("amazon_model") or ""),
                "价格": _clean_csv_price(item.get("amazon_price")),
                "核心词-1": _report_rank(rankings, 0, "keyword"),
                "核心词自然位-1": _report_rank(rankings, 0, "organic_rank"),
                "核心词广告位-1": _report_rank(rankings, 0, "ad_rank"),
                "核心词-2": _report_rank(rankings, 1, "keyword"),
                "核心词自然位-2": _report_rank(rankings, 1, "organic_rank"),
                "核心词广告位-2": _report_rank(rankings, 1, "ad_rank"),
                "父体数量": str(item.get("amazon_total_variants") or 0),
            }
        )
    return rows


def export_daily_report_csv(results: list[dict[str, Any]], output_path: str | Path) -> str:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_daily_report_rows(results)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_REPORT_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


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
