"""Pydantic schemas for the Merchant Growth Brain.

The Growth Brain is a deterministic recommendation engine that:
1. Understands buyer intent
2. Retrieves candidate products
3. Scores products against the intent
4. Detects upsell/cross-sell opportunities
5. Generates bundles
6. Estimates expected order value
7. Returns structured explanation

All monetary values are in paise (integers) per AGENTS.md invariants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class OpportunityType(str, Enum):
    """Type of upsell/cross-sell opportunity."""
    UPSELL = "upsell"
    CROSS_SELL = "cross_sell"
    ACCESSORY = "accessory"


class ScoreReason(str, Enum):
    """Why a product was scored a certain way."""
    CATEGORY_MATCH = "category_match"
    USE_CASE_MATCH = "use_case_match"
    BUDGET_MATCH = "budget_match"
    IN_STOCK = "in_stock"
    HIGH_MARGIN = "high_margin"
    COMPATIBLE = "compatible"
    POPULAR = "popular"


# ─── Input Schemas ────────────────────────────────────────────────────────────

class BuyerIntent(BaseModel):
    """Input to the Growth Brain: what the buyer is looking for.

    This is the structured representation of buyer intent that the LLM
    agent extracts from natural language conversation.
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language description of what the buyer wants",
    )
    budget_cents: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum budget in paise (None = no budget limit)",
    )
    category: Optional[str] = Field(
        default=None,
        description="Preferred product category (e.g. 'electronics', 'clothing')",
    )
    use_case: Optional[str] = Field(
        default=None,
        description="Primary use case (e.g. 'gym', 'commute', 'work')",
    )
    quantity: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of units requested",
    )
    exclude_product_ids: List[str] = Field(
        default_factory=list,
        description="Product IDs to exclude from recommendations",
    )
    merchant_id: Optional[str] = Field(
        default=None,
        description="Filter to specific merchant (None = all merchants)",
    )


# ─── Output Schemas ───────────────────────────────────────────────────────────

class ProductScore(BaseModel):
    """A scored product recommendation with explanation.

    The score is deterministic and based on:
    - Category match (weight: 3)
    - Use case match (weight: 2)
    - Budget fit (weight: 2)
    - Stock availability (weight: 3)
    """

    product_id: str = Field(
        ...,
        description="UUID of the recommended product",
    )
    product_name: str = Field(
        ...,
        description="Human-readable product name",
    )
    price_cents: int = Field(
        ...,
        ge=0,
        description="Unit price in paise (from database, never from LLM)",
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Composite score (0-10, higher = better fit)",
    )
    reasons: List[ScoreReason] = Field(
        ...,
        description="Why this product scored well",
    )
    explanation: str = Field(
        ...,
        max_length=500,
        description="Human-readable explanation of the recommendation",
    )
    in_stock: bool = Field(
        ...,
        description="Whether the product is currently in stock",
    )
    stock_qty: int = Field(
        ...,
        ge=0,
        description="Current stock quantity",
    )


class UpsellOpportunity(BaseModel):
    """An upsell or cross-sell opportunity.

    The Growth Brain identifies complementary products based on:
    - Explicit compatible_product_ids relationships
    - Category adjacency
    - Use case overlap
    - Price tier upgrades
    """

    product_id: str = Field(
        ...,
        description="UUID of the upsell/cross-sell product",
    )
    product_name: str = Field(
        ...,
        description="Human-readable product name",
    )
    price_cents: int = Field(
        ...,
        ge=0,
        description="Unit price in paise",
    )
    opportunity_type: OpportunityType = Field(
        ...,
        description="Type of opportunity (upsell, cross_sell, accessory)",
    )
    reason: str = Field(
        ...,
        max_length=300,
        description="Why this is a good opportunity",
    )
    price_delta_cents: int = Field(
        ...,
        description="Price difference from the base product (positive = more expensive)",
    )
    in_stock: bool = Field(
        ...,
        description="Whether the product is in stock",
    )


class BundleItem(BaseModel):
    """A single item in a bundle recommendation."""

    product_id: str = Field(
        ...,
        description="UUID of the bundled product",
    )
    product_name: str = Field(
        ...,
        description="Human-readable product name",
    )
    quantity: int = Field(
        ...,
        ge=1,
        description="Quantity of this product in the bundle",
    )
    unit_price_cents: int = Field(
        ...,
        ge=0,
        description="Unit price in paise",
    )


class BundleRecommendation(BaseModel):
    """A bundle of products offered at a discount.

    Bundle price must be <= sum of individual prices.
    All prices are in paise (integers).
    """

    bundle_id: str = Field(
        ...,
        description="Unique bundle identifier",
    )
    name: str = Field(
        ...,
        max_length=200,
        description="Human-readable bundle name",
    )
    description: str = Field(
        ...,
        max_length=500,
        description="What's included and why it's a good deal",
    )
    items: List[BundleItem] = Field(
        ...,
        min_length=1,
        description="Products included in the bundle",
    )
    original_price_cents: int = Field(
        ...,
        ge=0,
        description="Sum of individual prices without discount (paise)",
    )
    bundle_price_cents: int = Field(
        ...,
        ge=0,
        description="Discounted bundle price (paise)",
    )
    savings_cents: int = Field(
        ...,
        ge=0,
        description="Total savings (original - bundle price, paise)",
    )
    savings_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Savings percentage",
    )
    explanation: str = Field(
        ...,
        max_length=300,
        description="Why this bundle is valuable",
    )


class ExpectedOrderValue(BaseModel):
    """Estimated order value based on recommendations.

    This is a recommendation, not a commitment. The actual order value
    is determined by the quote engine with server-side price lookups.
    """

    base_value_cents: int = Field(
        ...,
        ge=0,
        description="Value of the primary product(s) (paise)",
    )
    upsell_value_cents: int = Field(
        default=0,
        ge=0,
        description="Estimated value from upsell opportunities (paise)",
    )
    bundle_value_cents: int = Field(
        default=0,
        ge=0,
        description="Estimated value from bundle recommendations (paise)",
    )
    total_estimated_cents: int = Field(
        ...,
        ge=0,
        description="Total estimated order value (paise)",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in the estimate (0-1)",
    )


class GrowthBrainOutput(BaseModel):
    """Complete output from the Growth Brain analysis.

    This is a structured, typed response that the LLM agent can use
    to formulate its recommendation to the buyer. All financial values
    are server-side lookups, never from LLM output.

    The agent may recommend, but must not execute financial actions.
    """

    intent: BuyerIntent = Field(
        ...,
        description="The original buyer intent",
    )
    recommendations: List[ProductScore] = Field(
        default_factory=list,
        description="Scored product recommendations",
    )
    upsell_opportunities: List[UpsellOpportunity] = Field(
        default_factory=list,
        description="Upsell and cross-sell opportunities",
    )
    bundles: List[BundleRecommendation] = Field(
        default_factory=list,
        description="Bundle recommendations",
    )
    expected_order_value: ExpectedOrderValue = Field(
        ...,
        description="Estimated order value based on recommendations",
    )
    explanation: str = Field(
        ...,
        max_length=1000,
        description="Overall explanation of the recommendation strategy",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the analysis",
    )

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}