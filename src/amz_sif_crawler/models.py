from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProductVariant(BaseModel):
    variant_name: str = Field(..., description="Variant label shown on the product page")
    price: str | None = Field(None, description="Variant price if present")
    is_available: bool = Field(..., description="Whether the variant is selectable")


class AmazonProduct(BaseModel):
    product_title: str = Field(..., description="Product title")
    main_price: str | None = Field(None, description="Current sale price")
    list_price: str | None = Field(None, description="Strikethrough or typical price")
    savings_text: str | None = Field(None, description="Savings text such as -10%")
    has_price_discount: bool = Field(False, description="Whether a visible price discount exists")
    deal_type: str | None = Field(None, description="Deal badge text")
    is_limited_time_deal: bool = Field(False, description="Whether the product has a limited time deal")
    coupon_text: str | None = Field(None, description="Coupon callout text")
    applied_coupon_text: str | None = Field(None, description="Applied coupon text")
    has_coupon: bool = Field(False, description="Whether the page displays a coupon")
    model_number: str | None = Field(None, description="Model derived from title")
    variants: list[ProductVariant] = Field(default_factory=list, description="Visible variants")
    parent_item_count: int = Field(0, description="Detected variant count")
    current_url: str | None = Field(None, description="Final page URL after navigation")


class SifKeywordRank(BaseModel):
    keyword: str = Field(..., description="Keyword text")
    organic_rank: str = Field(..., description="Organic rank text")
    ad_rank: str = Field(..., description="Ad rank text")


class CrawlResult(BaseModel):
    timestamp: str
    asin: str
    status: str
    failure_reason: str
    amazon_title: str
    amazon_price: str
    amazon_list_price: str
    amazon_savings_text: str
    amazon_has_price_discount: bool
    amazon_deal_type: str
    amazon_is_limited_time_deal: bool
    amazon_coupon_text: str
    amazon_applied_coupon_text: str
    amazon_has_coupon: bool
    amazon_model: str
    amazon_total_variants: int
    amazon_variants: list[dict[str, Any]]
    sif_1_kw: str
    full_sif: list[dict[str, Any]]
