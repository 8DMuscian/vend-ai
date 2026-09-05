"""Merchant Growth Brain - Deterministic recommendation engine.

The Growth Brain analyzes buyer intent and generates structured recommendations
for the LLM agent to present. It is purely deterministic:

1. Scores products against buyer intent using weighted criteria
2. Detects upsell opportunities (higher-tier products)
3. Detects cross-sell opportunities (complementary products)
4. Generates bundle recommendations with deterministic pricing
5. Estimates expected order value based on recommendations

All prices come from database lookups. All financial calculations are
server-side. The LLM may recommend, but must not execute financial actions.

From AGENTS.md:
- "LLM proposes, deterministic code disposes"
- "Transaction amount must be <= approved limit"
- "Discount must be <= merchant discount limit"
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.product import Product
from app.schemas.growth import (
    BuyerIntent,
    BundleItem,
    BundleRecommendation,
    ExpectedOrderValue,
    GrowthBrainOutput,
    OpportunityType,
    ProductScore,
    ScoreReason,
    UpsellOpportunity,
)


class GrowthBrain:
    """Deterministic recommendation engine for merchant growth.

    The Growth Brain analyzes buyer intent and generates structured
    recommendations. It does NOT:
    - Call LLM APIs
    - Execute payments
    - Modify prices or discounts
    - Access buyer PII

    It DOES:
    - Score products against intent
    - Detect upsell/cross-sell opportunities
    - Generate bundle recommendations
    - Estimate expected order value
    - Return structured Pydantic output
    """

    # Scoring weights (deterministic, tunable)
    WEIGHT_CATEGORY = 3.0
    WEIGHT_USE_CASE = 2.0
    WEIGHT_BUDGET = 2.0
    WEIGHT_STOCK = 3.0

    # Maximum score (sum of all weights)
    MAX_SCORE = WEIGHT_CATEGORY + WEIGHT_USE_CASE + WEIGHT_BUDGET + WEIGHT_STOCK

    def __init__(self, session: Session) -> None:
        """Initialize the Growth Brain with a database session.

        Args:
            session: SQLAlchemy session for database queries.
        """
        self._session = session

    def analyze(self, intent: BuyerIntent) -> GrowthBrainOutput:
        """Main entry point: analyze buyer intent and generate recommendations.

        This is a deterministic function that:
        1. Retrieves candidate products from the catalog
        2. Scores each product against the intent
        3. Detects upsell and cross-sell opportunities
        4. Generates bundle recommendations
        5. Estimates expected order value
        6. Returns structured output for the LLM agent

        Args:
            intent: Structured buyer intent from the LLM agent.

        Returns:
            GrowthBrainOutput with recommendations, opportunities, and estimates.
        """
        # Step 1: Retrieve candidate products
        candidates = self._retrieve_candidates(intent)

        # Step 2: Score products against intent
        scored_products = self._score_products(candidates, intent)

        # Step 3: Detect upsell opportunities
        upsell_opportunities = self._detect_upsell(scored_products, intent)

        # Step 4: Detect cross-sell opportunities
        cross_sell_opportunities = self._detect_cross_sell(scored_products, intent)

        # Step 5: Generate bundle recommendations
        bundles = self._generate_bundles(scored_products, intent)

        # Step 6: Estimate expected order value
        eov = self._estimate_eov(scored_products, upsell_opportunities, bundles, intent)

        # Step 7: Generate explanation
        explanation = self._generate_explanation(
            scored_products, upsell_opportunities, cross_sell_opportunities, bundles, eov
        )

        return GrowthBrainOutput(
            intent=intent,
            recommendations=scored_products,
            upsell_opportunities=upsell_opportunities + cross_sell_opportunities,
            bundles=bundles,
            expected_order_value=eov,
            explanation=explanation,
        )

    def _retrieve_candidates(self, intent: BuyerIntent) -> List[Product]:
        """Retrieve candidate products from the catalog.

        Filters by:
        - is_active = True
        - category (if specified)
        - price <= budget (if budget specified)
        - in_stock = True (prefer in-stock)
        - merchant_id (if specified)
        - NOT in exclude_product_ids

        Args:
            intent: Buyer intent with filters.

        Returns:
            List of candidate Product objects.
        """
        stmt = select(Product).filter(Product.is_active == True)

        # Filter by merchant if specified
        if intent.merchant_id:
            stmt = stmt.filter(Product.merchant_id == intent.merchant_id)

        # Filter by category if specified
        if intent.category:
            # Python-level filter for SQLite compatibility
            pass

        # Filter by budget if specified
        if intent.budget_cents is not None:
            stmt = stmt.filter(Product.price_cents <= intent.budget_cents)

        # Execute query
        products = list(self._session.execute(stmt).scalars().all())

        # Apply Python-level filters for SQLite compatibility
        if intent.category:
            products = [p for p in products if intent.category in p.categories]

        # Exclude specific products
        if intent.exclude_product_ids:
            products = [p for p in products if p.id not in intent.exclude_product_ids]

        return products

    def _score_products(
        self, products: List[Product], intent: BuyerIntent
    ) -> List[ProductScore]:
        """Score each product against the buyer intent.

        Scoring criteria (deterministic):
        - Category match: +3 if product categories contain intent.category
        - Use case match: +2 if product use_cases contain intent.use_case
        - Budget fit: +2 if product price fits within budget
        - Stock availability: +3 if product is in stock

        Args:
            products: List of candidate products.
            intent: Buyer intent with preferences.

        Returns:
            List of ProductScore objects sorted by score (descending).
        """
        scored: List[ProductScore] = []

        for product in products:
            score, reasons = self._score_single_product(product, intent)

            # Normalize score to 0-10 range
            normalized_score = (score / self.MAX_SCORE) * 10.0

            # Generate explanation
            explanation = self._explain_score(product, reasons, intent)

            scored.append(
                ProductScore(
                    product_id=product.id,
                    product_name=product.name,
                    price_cents=product.price_cents,
                    score=round(normalized_score, 2),
                    reasons=reasons,
                    explanation=explanation,
                    in_stock=product.stock_qty > 0,
                    stock_qty=product.stock_qty,
                )
            )

        # Sort by score descending
        scored.sort(key=lambda x: x.score, reverse=True)

        return scored

    def _score_single_product(
        self, product: Product, intent: BuyerIntent
    ) -> Tuple[float, List[ScoreReason]]:
        """Score a single product against buyer intent.

        Args:
            product: Product to score.
            intent: Buyer intent.

        Returns:
            Tuple of (raw_score, list_of_reasons).
        """
        score = 0.0
        reasons: List[ScoreReason] = []

        # Category match (weight: 3)
        if intent.category and intent.category in product.categories:
            score += self.WEIGHT_CATEGORY
            reasons.append(ScoreReason.CATEGORY_MATCH)

        # Use case match (weight: 2)
        if intent.use_case and intent.use_case in product.use_cases:
            score += self.WEIGHT_USE_CASE
            reasons.append(ScoreReason.USE_CASE_MATCH)

        # Budget fit (weight: 2)
        if intent.budget_cents is not None:
            if product.price_cents <= intent.budget_cents:
                score += self.WEIGHT_BUDGET
                reasons.append(ScoreReason.BUDGET_MATCH)
        else:
            # No budget specified, give partial credit
            score += self.WEIGHT_BUDGET * 0.5
            reasons.append(ScoreReason.BUDGET_MATCH)

        # Stock availability (weight: 3)
        if product.stock_qty > 0:
            score += self.WEIGHT_STOCK
            reasons.append(ScoreReason.IN_STOCK)

        return score, reasons

    def _explain_score(
        self, product: Product, reasons: List[ScoreReason], intent: BuyerIntent
    ) -> str:
        """Generate a human-readable explanation of the score.

        Args:
            product: The scored product.
            reasons: Why it scored well.
            intent: Original buyer intent.

        Returns:
            Explanation string.
        """
        parts = [f"'{product.name}' is recommended because:"]

        if ScoreReason.CATEGORY_MATCH in reasons:
            parts.append(f"- Matches your preferred category ({intent.category})")
        if ScoreReason.USE_CASE_MATCH in reasons:
            parts.append(f"- Suitable for your use case ({intent.use_case})")
        if ScoreReason.BUDGET_MATCH in reasons:
            if intent.budget_cents:
                budget_inr = intent.budget_cents / 100
                price_inr = product.price_cents / 100
                parts.append(f"- Within your budget (₹{price_inr:.0f} of ₹{budget_inr:.0f})")
            else:
                parts.append("- Fits any budget")
        if ScoreReason.IN_STOCK in reasons:
            parts.append(f"- In stock ({product.stock_qty} units available)")
        else:
            parts.append("- Currently out of stock")

        return " ".join(parts)

    def _detect_upsell(
        self, scored_products: List[ProductScore], intent: BuyerIntent
    ) -> List[UpsellOpportunity]:
        """Detect upsell opportunities (higher-tier products).

        An upsell is a product that:
1. Is in the same category as a recommended product
2. Has a higher price (but still within budget if specified)
3. Has better specs or features

        Args:
            scored_products: Scored product recommendations.
            intent: Buyer intent.

        Returns:
            List of UpsellOpportunity objects.
        """
        if not scored_products:
            return []

        opportunities: List[UpsellOpportunity] = []
        seen_ids: set[str] = set()

        # Get the top recommended product as the base
        base_score = scored_products[0]
        base_product = self._get_product_by_id(base_score.product_id)

        if not base_product:
            return []

        # Find products in the same category with higher price
        stmt = select(Product).filter(
            Product.is_active == True,
            Product.id != base_product.id,
            Product.price_cents > base_product.price_cents,
        )

        if intent.budget_cents is not None:
            stmt = stmt.filter(Product.price_cents <= intent.budget_cents)

        higher_products = list(self._session.execute(stmt).scalars().all())

        for product in higher_products:
            if product.id in seen_ids:
                continue

            # Check if same category
            if base_product.categories and product.categories:
                if not set(base_product.categories).intersection(set(product.categories)):
                    continue

            # Check if in stock
            if product.stock_qty <= 0:
                continue

            price_delta = product.price_cents - base_product.price_cents

            opportunities.append(
                UpsellOpportunity(
                    product_id=product.id,
                    product_name=product.name,
                    price_cents=product.price_cents,
                    opportunity_type=OpportunityType.UPSELL,
                    reason=f"Upgrade from {base_product.name} to {product.name} for better features",
                    price_delta_cents=price_delta,
                    in_stock=product.stock_qty > 0,
                )
            )

            seen_ids.add(product.id)

            # Limit to 3 upsell opportunities
            if len(opportunities) >= 3:
                break

        return opportunities

    def _detect_cross_sell(
        self, scored_products: List[ProductScore], intent: BuyerIntent
    ) -> List[UpsellOpportunity]:
        """Detect cross-sell opportunities (complementary products).

        A cross-sell is a product that:
1. Is compatible with a recommended product (via compatible_product_ids)
2. Or is in a different category but useful for the same use case

        Args:
            scored_products: Scored product recommendations.
            intent: Buyer intent.

        Returns:
            List of UpsellOpportunity objects with type CROSS_SELL.
        """
        if not scored_products:
            return []

        opportunities: List[UpsellOpportunity] = []
        seen_ids: set[str] = set()

        # Check compatible products for each recommended product
        for score in scored_products[:3]:  # Top 3 recommendations
            product = self._get_product_by_id(score.product_id)
            if not product:
                continue

            # Check explicit compatible_product_ids
            for compat_id in product.compatible_product_ids:
                if compat_id in seen_ids:
                    continue

                compat_product = self._get_product_by_id(compat_id)
                if not compat_product or not compat_product.is_active:
                    continue

                if compat_product.stock_qty <= 0:
                    continue

                price_delta = compat_product.price_cents - product.price_cents

                opportunities.append(
                    UpsellOpportunity(
                        product_id=compat_product.id,
                        product_name=compat_product.name,
                        price_cents=compat_product.price_cents,
                        opportunity_type=OpportunityType.CROSS_SELL,
                        reason=f"Pairs well with {product.name}",
                        price_delta_cents=price_delta,
                        in_stock=compat_product.stock_qty > 0,
                    )
                )

                seen_ids.add(compat_id)

            # Limit to 2 cross-sell opportunities per product
            if len(opportunities) >= 6:
                break

        return opportunities[:6]  # Max 6 cross-sell opportunities

    def _generate_bundles(
        self, scored_products: List[ProductScore], intent: BuyerIntent
    ) -> List[BundleRecommendation]:
        """Generate bundle recommendations.

        A bundle groups complementary products at a discount.
        Bundle price must be <= sum of individual prices.
        Discount must be <= discount_cap_pct from config.

        Args:
            scored_products: Scored product recommendations.
            intent: Buyer intent.

        Returns:
            List of BundleRecommendation objects.
        """
        if len(scored_products) < 2:
            return []

        bundles: List[BundleRecommendation] = []

        # Create a "recommended bundle" from top 2-3 products
        top_products = scored_products[:3]
        bundle_items: List[BundleItem] = []
        original_price = 0

        for score in top_products:
            if not score.in_stock:
                continue

            bundle_items.append(
                BundleItem(
                    product_id=score.product_id,
                    product_name=score.product_name,
                    quantity=1,
                    unit_price_cents=score.price_cents,
                )
            )
            original_price += score.price_cents

        if len(bundle_items) < 2:
            return []

        # Apply discount (capped at discount_cap_pct)
        discount_pct = min(15.0, settings.discount_cap_pct)  # 15% bundle discount
        savings = int(original_price * discount_pct / 100)
        bundle_price = original_price - savings

        # Ensure bundle price is positive
        bundle_price = max(bundle_price, 1)

        bundles.append(
            BundleRecommendation(
                bundle_id=str(uuid.uuid4()),
                name=f"Bundle: {', '.join(item.product_name[:20] for item in bundle_items[:2])}",
                description=f"Get {len(bundle_items)} products together and save {discount_pct}%",
                items=bundle_items,
                original_price_cents=original_price,
                bundle_price_cents=bundle_price,
                savings_cents=savings,
                savings_pct=discount_pct,
                explanation=f"Save ₹{savings / 100:.0f} when you buy these together",
            )
        )

        return bundles

    def _estimate_eov(
        self,
        scored_products: List[ProductScore],
        upsell_opportunities: List[UpsellOpportunity],
        bundles: List[BundleRecommendation],
        intent: BuyerIntent,
    ) -> ExpectedOrderValue:
        """Estimate expected order value based on recommendations.

        This is a recommendation, not a commitment. The actual order value
        is determined by the quote engine with server-side price lookups.

        Args:
            scored_products: Scored product recommendations.
            upsell_opportunities: Upsell/cross-sell opportunities.
            bundles: Bundle recommendations.
            intent: Original buyer intent.

        Returns:
            ExpectedOrderValue with estimates.
        """
        # Base value: price of top recommendation * quantity
        base_value = 0
        if scored_products:
            base_value = scored_products[0].price_cents * intent.quantity

        # Upsell value: estimate if buyer takes the first upsell
        upsell_value = 0
        if upsell_opportunities:
            upsell_value = upsell_opportunities[0].price_cents

        # Bundle value: use the first bundle if available
        bundle_value = 0
        if bundles:
            bundle_value = bundles[0].bundle_price_cents

        # Total estimated value
        # Use max(base, bundle) to avoid double-counting the same products.
        # Upsell is additive (different product), but bundle replaces base.
        total = max(base_value, bundle_value) + upsell_value

        # Confidence based on score quality
        confidence = 0.5  # Base confidence
        if scored_products:
            top_score = scored_products[0].score
            if top_score >= 8.0:
                confidence = 0.9
            elif top_score >= 6.0:
                confidence = 0.7
            elif top_score >= 4.0:
                confidence = 0.6

        return ExpectedOrderValue(
            base_value_cents=base_value,
            upsell_value_cents=upsell_value,
            bundle_value_cents=bundle_value,
            total_estimated_cents=total,
            confidence=confidence,
        )

    def _generate_explanation(
        self,
        scored_products: List[ProductScore],
        upsell_opportunities: List[UpsellOpportunity],
        cross_sell_opportunities: List[UpsellOpportunity],
        bundles: List[BundleRecommendation],
        eov: ExpectedOrderValue,
    ) -> str:
        """Generate a comprehensive explanation of the recommendation strategy.

        Args:
            scored_products: Scored product recommendations.
            upsell_opportunities: Upsell opportunities.
            cross_sell_opportunities: Cross-sell opportunities.
            bundles: Bundle recommendations.
            eov: Expected order value estimate.

        Returns:
            Explanation string.
        """
        parts = []

        if scored_products:
            top = scored_products[0]
            parts.append(
                f"Based on your request, I recommend '{top.product_name}' "
                f"(₹{top.price_cents / 100:.0f}) as the best match "
                f"(score: {top.score}/10)."
            )
        else:
            parts.append("I couldn't find products matching your exact criteria.")

        if upsell_opportunities:
            parts.append(
                f"I found {len(upsell_opportunities)} upgrade option(s) "
                f"that might interest you."
            )

        if cross_sell_opportunities:
            parts.append(
                f"There are also {len(cross_sell_opportunities)} complementary "
                f"product(s) that pair well with your selection."
            )

        if bundles:
            bundle = bundles[0]
            parts.append(
                f"I've created a bundle that saves you ₹{bundle.savings_cents / 100:.0f} "
                f"({bundle.savings_pct}% off)."
            )

        parts.append(
            f"Estimated total: ₹{eov.total_estimated_cents / 100:.0f} "
            f"(confidence: {eov.confidence:.0%})."
        )

        return " ".join(parts)

    def _get_product_by_id(self, product_id: str) -> Optional[Product]:
        """Retrieve a product by ID.

        Args:
            product_id: UUID of the product.

        Returns:
            Product object or None if not found.
        """
        stmt = select(Product).filter(
            Product.id == product_id, Product.is_active == True
        )
        return self._session.execute(stmt).scalar_one_or_none()