from amz_sif_crawler.utils import build_canonical_amazon_url, extract_asin, merge_failure_reason, normalize_amazon_input


def test_extract_asin_from_url():
    assert extract_asin("https://www.amazon.com/dp/B0CDX5XGLK") == "B0CDX5XGLK"


def test_normalize_amazon_input_from_asin():
    payload = normalize_amazon_input("B0CDX5XGLK")
    assert payload["ok"] is True
    assert payload["asin"] == "B0CDX5XGLK"
    assert payload["url"] == build_canonical_amazon_url("B0CDX5XGLK")


def test_normalize_amazon_input_rejects_non_amazon_without_scheme():
    payload = normalize_amazon_input("not-a-url")
    assert payload["ok"] is False


def test_merge_failure_reason_deduplicates_segments():
    assert merge_failure_reason("SIF Empty Data", "SIF Empty Data; Amazon Timeout") == "SIF Empty Data; Amazon Timeout"
