import asyncio
from pathlib import Path

from amz_sif_crawler.daily_bindings import add_daily_asins, load_daily_bindings, remove_daily_asins
from amz_sif_crawler.runtime.config import load_app_config
from amz_sif_crawler.service import _build_result, build_daily_report_rows, crawl_urls, export_daily_report_csv


def test_build_result_success_shape():
    payload = _build_result(
        asin="B0CDX5XGLK",
        amazon_data={
            "product_title": "Example Product",
            "main_price": "$19.99",
            "parent_item_count": 2,
            "variants": [{"variant_name": "Blue", "price": "$19.99", "is_available": True}],
        },
        sif_rankings=[{"keyword": "sample", "organic_rank": "第1名", "ad_rank": "第2名"}],
        failure_reason="",
    )
    assert payload["status"] == "SUCCESS"
    assert payload["amazon_title"] == "Example Product"
    assert payload["sif_1_kw"] == "sample"
    assert payload["amazon_url"] == "https://www.amazon.com/dp/B0CDX5XGLK"


def test_build_daily_report_rows_shape():
    rows = build_daily_report_rows(
        [
            _build_result(
                asin="B0CDX5XGLK",
                amazon_data={
                    "product_title": "Example Product",
                    "main_price": "$49.99",
                    "model_number": "Redragon K673",
                    "parent_item_count": 6,
                    "current_url": "https://www.amazon.com/dp/B0CDX5XGLK",
                },
                sif_rankings=[
                    {"keyword": "redragon keyboard", "organic_rank": "P1-5", "ad_rank": "P1-1"},
                    {"keyword": "red dragon keyboard", "organic_rank": "P1-6", "ad_rank": "/"},
                ],
                failure_reason="",
            )
        ]
    )
    assert rows == [
        {
            "URL链接": "https://www.amazon.com/dp/B0CDX5XGLK",
            "产品型号": "Redragon K673",
            "价格": "49.99",
            "核心词-1": "redragon keyboard",
            "核心词自然位-1": "P1-5",
            "核心词广告位-1": "P1-1",
            "核心词-2": "red dragon keyboard",
            "核心词自然位-2": "P1-6",
            "核心词广告位-2": "/",
            "父体数量": "6",
        }
    ]


def test_export_daily_report_csv_uses_expected_headers(tmp_path: Path):
    csv_path = export_daily_report_csv(
        [
            _build_result(
                asin="B0CDX5XGLK",
                amazon_data={
                    "main_price": "$49.99",
                    "model_number": "Redragon K673",
                    "parent_item_count": 6,
                    "current_url": "https://www.amazon.com/dp/B0CDX5XGLK",
                },
                sif_rankings=[],
                failure_reason="",
            )
        ],
        tmp_path / "report.csv",
    )
    content = Path(csv_path).read_text(encoding="utf-8-sig")
    assert "URL链接,产品型号,价格,核心词-1" in content
    assert "https://www.amazon.com/dp/B0CDX5XGLK,Redragon K673,49.99" in content


def test_daily_bindings_add_and_remove(tmp_path: Path):
    add_daily_asins(tmp_path, "demo", ["B0CDX5XGLK", "B0CDX5XGLK", "B0TEST0001"])
    bindings = load_daily_bindings(tmp_path)
    assert bindings["demo"] == ["B0CDX5XGLK", "B0TEST0001"]

    updated = remove_daily_asins(tmp_path, "demo", ["B0CDX5XGLK"])
    assert updated == ["B0TEST0001"]


def test_cache_disabled_by_default():
    config = load_app_config()
    assert config.cache_enabled is False


def test_crawl_urls_runs_amazon_and_sif_batches_in_parallel(monkeypatch):
    events: list[str] = []
    amazon_release = asyncio.Event()
    sif_release = asyncio.Event()

    async def fake_fetch_amazon_for_asin(*, asin, url, app_config, cache):
        events.append(f"amazon:start:{asin}")
        if len([event for event in events if event.startswith("amazon:start:")]) == 2:
            sif_release.set()
        await amazon_release.wait()
        return {"data": {"current_url": url, "product_title": f"title-{asin}"}, "error": ""}

    async def fake_fetch_sif_for_asin(*, asin, app_config, cache):
        events.append(f"sif:start:{asin}")
        if len([event for event in events if event.startswith("sif:start:")]) == 2:
            amazon_release.set()
        await sif_release.wait()
        return {"data": [{"keyword": f"kw-{asin}", "organic_rank": "P1-1", "ad_rank": "/"}], "error": ""}

    monkeypatch.setattr("amz_sif_crawler.service._fetch_amazon_for_asin", fake_fetch_amazon_for_asin)
    monkeypatch.setattr("amz_sif_crawler.service._fetch_sif_for_asin", fake_fetch_sif_for_asin)

    results = asyncio.run(
        crawl_urls(
            ["https://www.amazon.com/dp/B0CDX5XGLK", "https://www.amazon.com/dp/B0FDVZ5X38"],
            mode="both",
        )
    )

    assert [item["asin"] for item in results] == ["B0CDX5XGLK", "B0FDVZ5X38"]
    assert {event for event in events if event.startswith("amazon:start:")} == {
        "amazon:start:B0CDX5XGLK",
        "amazon:start:B0FDVZ5X38",
    }
    assert {event for event in events if event.startswith("sif:start:")} == {
        "sif:start:B0CDX5XGLK",
        "sif:start:B0FDVZ5X38",
    }
