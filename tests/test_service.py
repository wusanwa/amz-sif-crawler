from amz_sif_crawler.service import _build_result
from amz_sif_crawler.runtime.config import load_app_config


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


def test_cache_disabled_by_default():
    config = load_app_config()
    assert config.cache_enabled is False
