"""Deterministic tests for the Merchant Growth Brain.

Tests cover:
1. Budget constraints - products above budget are excluded
2. Unavailable products - out of stock products are handled correctly
3. Invalid discounts - discount caps are enforced
4. Incompatible products - incompatible products are not recommended together
5. Margin constraints - minimum order value is enforced

All tests use SQLite in-memory database for speed and isolation.
"""

from __future__ import annotations

import uuid
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base
from app.models.product import Product
from app.schemas.growth import BuyerIntent
from app.services.growth_brain import GrowthBrain


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Create a fresh SQLite in-memory database for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def _make_product(
    session: Session,
    *,
    name: str = "Test Product",
    price_cents: int = 1000,
    stock_qty: int = 10,
    categories: list[str] | None = None,
    use_cases: list[str] | None = None,
    compatible_product_ids: list[str] | None = None,
    ai_schema: dict | None = None,
    merchant_id: str | None = None,
) -> Product:
    """Helper to create a product in the test database."""
    product = Product(
        id=str(uuid.uuid4()),
        merchant_id=merchant_id or str(uuid.uuid4()),
        name=name,
        description=f"Test description for {name}",
        ai_schema=ai_schema or {"category": "test", "tags": ["test"]},
        price_cents=price_cents,
        currency="INR",
        stock_qty=stock_qty,
        categories=categories or ["test"],
        use_cases=use_cases or ["general"],
        compatible_product_ids=compatible_product_ids or [],
        is_active=True,
    )
    session.add(product)
    session.commit()
    return product


# ─── Test: Budget Constraints ─────────────────────────────────────────────────


class TestBudgetConstraints:
    """Test that products above budget are excluded from recommendations."""

    def test_products_within_budget_are_included(self, db_session: Session) -> None:
        """Products priced at or below budget should be recommended."""
        # Arrange: Create products within budget
        cheap = _make_product(
            db_session, name="Cheap Widget", price_cents=500, stock_qty=10
        )
        mid = _make_product(
            db_session, name="Mid Widget", price_cents=1000, stock_qty=10
        )
        expensive = _make_product(
            db_session, name="Expensive Widget", price_cents=2000, stock_qty=10
        )

        intent = BuyerIntent(
            query="I need a widget",
            budget_cents=1500,  # ₹15 budget
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Only products within budget are recommended
        recommended_ids = {r.product_id for r in output.recommendations}
        assert cheap.id in recommended_ids
        assert mid.id in recommended_ids
        assert expensive.id not in recommended_ids

    def test_products_above_budget_are_excluded(self, db_session: Session) -> None:
        """Products priced above budget should not be recommended."""
        # Arrange
        _make_product(db_session, name="Over Budget", price_cents=5000, stock_qty=10)

        intent = BuyerIntent(
            query="I need a widget",
            budget_cents=1000,  # ₹10 budget
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: No products should be recommended (all over budget)
        assert len(output.recommendations) == 0

    def test_no_budget_includes_all_products(self, db_session: Session) -> None:
        """When no budget is specified, all products should be considered."""
        # Arrange
        cheap = _make_product(db_session, name="Cheap", price_cents=100, stock_qty=10)
        expensive = _make_product(
            db_session, name="Expensive", price_cents=100000, stock_qty=10
        )

        intent = BuyerIntent(query="I need a widget")  # No budget

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Both products should be in recommendations
        recommended_ids = {r.product_id for r in output.recommendations}
        assert cheap.id in recommended_ids
        assert expensive.id in recommended_ids

    def test_budget_exact_match(self, db_session: Session) -> None:
        """Product priced exactly at budget should be included."""
        # Arrange
        exact = _make_product(db_session, name="Exact", price_cents=1000, stock_qty=10)

        intent = BuyerIntent(
            query="widget",
            budget_cents=1000,
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        recommended_ids = {r.product_id for r in output.recommendations}
        assert exact.id in recommended_ids


# ─── Test: Unavailable Products ───────────────────────────────────────────────


class TestUnavailableProducts:
    """Test that out-of-stock products are handled correctly."""

    def test_out_of_stock_products_are_scored_low(self, db_session: Session) -> None:
        """Out-of-stock products should get lower scores."""
        # Arrange
        in_stock = _make_product(
            db_session, name="In Stock", price_cents=1000, stock_qty=10
        )
        out_of_stock = _make_product(
            db_session, name="Out of Stock", price_cents=1000, stock_qty=0
        )

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Both should be in recommendations, but in_stock scores higher
        scores = {r.product_id: r.score for r in output.recommendations}
        assert scores[in_stock.id] > scores[out_of_stock.id]

    def test_out_of_stock_not_in_upsell(self, db_session: Session) -> None:
        """Out-of-stock products should not appear in upsell opportunities."""
        # Arrange
        base = _make_product(
            db_session, name="Base", price_cents=1000, stock_qty=10
        )
        upsell_oos = _make_product(
            db_session, name="Upsell OOS", price_cents=2000, stock_qty=0,
            categories=["test"],
        )

        intent = BuyerIntent(query="widget", budget_cents=5000)

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Upsell should not include out-of-stock product
        upsell_ids = {u.product_id for u in output.upsell_opportunities}
        assert upsell_oos.id not in upsell_ids

    def test_zero_stock_product_not_in_cross_sell(self, db_session: Session) -> None:
        """Out-of-stock products should not appear in cross-sell."""
        # Arrange
        base = _make_product(
            db_session,
            name="Headphones",
            price_cents=1000,
            stock_qty=10,
            compatible_product_ids=[],
        )
        accessory_oos = _make_product(
            db_session,
            name="Case OOS",
            price_cents=500,
            stock_qty=0,
            categories=["accessories"],
        )
        # Link them
        base.compatible_product_ids = [accessory_oos.id]
        db_session.commit()

        intent = BuyerIntent(query="headphones")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Cross-sell should not include OOS product
        cross_sell_ids = {
            u.product_id
            for u in output.upsell_opportunities
            if u.opportunity_type == "cross_sell"
        }
        assert accessory_oos.id not in cross_sell_ids


# ─── Test: Invalid Discounts ──────────────────────────────────────────────────


class TestInvalidDiscounts:
    """Test that discount caps are enforced in bundle generation."""

    def test_bundle_discount_does_not_exceed_cap(self, db_session: Session) -> None:
        """Bundle discount should not exceed the configured discount cap."""
        # Arrange: Create products for bundle
        p1 = _make_product(db_session, name="P1", price_cents=1000, stock_qty=10)
        p2 = _make_product(db_session, name="P2", price_cents=2000, stock_qty=10)

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Bundle savings should not exceed discount cap
        if output.bundles:
            bundle = output.bundles[0]
            assert bundle.savings_pct <= 20.0  # From config: discount_cap_pct
            assert bundle.bundle_price_cents > 0  # Price must be positive

    def test_bundle_price_is_positive(self, db_session: Session) -> None:
        """Bundle price must always be positive (no zero or negative prices)."""
        # Arrange
        _make_product(db_session, name="P1", price_cents=100, stock_qty=10)
        _make_product(db_session, name="P2", price_cents=100, stock_qty=10)

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        for bundle in output.bundles:
            assert bundle.bundle_price_cents > 0
            assert bundle.savings_cents >= 0
            assert bundle.original_price_cents > 0

    def test_bundle_savings_do_not_exceed_original_price(self, db_session: Session) -> None:
        """Savings should never exceed the original price."""
        # Arrange
        _make_product(db_session, name="P1", price_cents=500, stock_qty=10)
        _make_product(db_session, name="P2", price_cents=500, stock_qty=10)

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        for bundle in output.bundles:
            assert bundle.savings_cents <= bundle.original_price_cents
            assert bundle.bundle_price_cents >= bundle.original_price_cents - bundle.savings_cents


# ─── Test: Incompatible Products ──────────────────────────────────────────────


class TestIncompatibleProducts:
    """Test that incompatible products are not recommended together."""

    def test_compatible_products_appear_in_cross_sell(self, db_session: Session) -> None:
        """Products listed as compatible should appear in cross-sell."""
        # Arrange
        headphones = _make_product(
            db_session,
            name="Headphones",
            price_cents=1000,
            stock_qty=10,
            compatible_product_ids=[],
        )
        case = _make_product(
            db_session,
            name="Headphone Case",
            price_cents=500,
            stock_qty=10,
            categories=["accessories"],
        )
        # Link them
        headphones.compatible_product_ids = [case.id]
        db_session.commit()

        intent = BuyerIntent(query="headphones")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Case should appear in cross-sell
        cross_sell_ids = {
            u.product_id
            for u in output.upsell_opportunities
            if u.opportunity_type == "cross_sell"
        }
        assert case.id in cross_sell_ids

    def test_non_compatible_products_not_in_cross_sell(self, db_session: Session) -> None:
        """Products not listed as compatible should not appear in cross-sell."""
        # Arrange
        headphones = _make_product(
            db_session,
            name="Headphones",
            price_cents=1000,
            stock_qty=10,
            compatible_product_ids=[],  # No compatible products
        )
        random_item = _make_product(
            db_session,
            name="Random Item",
            price_cents=500,
            stock_qty=10,
            categories=["unrelated"],
        )

        intent = BuyerIntent(query="headphones")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Random item should not be in cross-sell
        cross_sell_ids = {
            u.product_id
            for u in output.upsell_opportunities
            if u.opportunity_type == "cross_sell"
        }
        assert random_item.id not in cross_sell_ids

    def test_exclude_product_ids_are_excluded(self, db_session: Session) -> None:
        """Products in exclude_product_ids should not appear in any recommendations."""
        # Arrange
        p1 = _make_product(db_session, name="P1", price_cents=1000, stock_qty=10)
        p2 = _make_product(db_session, name="P2", price_cents=1500, stock_qty=10)

        intent = BuyerIntent(
            query="widget",
            exclude_product_ids=[p1.id],
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: p1 should not be in recommendations
        recommended_ids = {r.product_id for r in output.recommendations}
        assert p1.id not in recommended_ids
        assert p2.id in recommended_ids


# ─── Test: Margin Constraints ─────────────────────────────────────────────────


class TestMarginConstraints:
    """Test that minimum order value constraints are respected."""

    def test_estimated_order_value_respects_budget(self, db_session: Session) -> None:
        """Estimated order value should not exceed budget significantly."""
        # Arrange
        _make_product(db_session, name="Widget", price_cents=800, stock_qty=10)

        intent = BuyerIntent(
            query="widget",
            budget_cents=1000,
            quantity=1,
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Base value should be within budget
        assert output.expected_order_value.base_value_cents <= 1000

    def test_quantity_multiplier_affects_eov(self, db_session: Session) -> None:
        """Higher quantity should increase estimated order value."""
        # Arrange
        _make_product(db_session, name="Widget", price_cents=1000, stock_qty=100)

        intent_single = BuyerIntent(query="widget", quantity=1)
        intent_bulk = BuyerIntent(query="widget", quantity=5)

        # Act
        brain = GrowthBrain(db_session)
        eov_single = brain.analyze(intent_single).expected_order_value
        eov_bulk = brain.analyze(intent_bulk).expected_order_value

        # Assert: Bulk order should have higher base value
        assert eov_bulk.base_value_cents > eov_single.base_value_cents
        assert eov_bulk.base_value_cents == eov_single.base_value_cents * 5

    def test_no_products_results_in_zero_eov(self, db_session: Session) -> None:
        """When no products match, EOV should be zero."""
        # Arrange: No products in database

        intent = BuyerIntent(query="nonexistent product")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        assert output.expected_order_value.base_value_cents == 0
        assert output.expected_order_value.total_estimated_cents == 0

    def test_eov_confidence_increases_with_better_match(self, db_session: Session) -> None:
        """Higher-scoring products should have higher confidence."""
        # Arrange: Create a perfect match
        _make_product(
            db_session,
            name="Perfect Match",
            price_cents=1000,
            stock_qty=10,
            categories=["electronics"],
            use_cases=["gym"],
        )

        intent = BuyerIntent(
            query="wireless headphones",
            category="electronics",
            use_case="gym",
            budget_cents=2000,
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Confidence should be high for good match
        if output.recommendations:
            assert output.expected_order_value.confidence >= 0.7


# ─── Test: Score Normalization ────────────────────────────────────────────────


class TestScoreNormalization:
    """Test that scores are properly normalized to 0-10 range."""

    def test_score_is_between_0_and_10(self, db_session: Session) -> None:
        """All scores should be normalized to 0-10 range."""
        # Arrange
        _make_product(db_session, name="P1", price_cents=1000, stock_qty=10)
        _make_product(db_session, name="P2", price_cents=500, stock_qty=0)

        intent = BuyerIntent(query="widget", category="test", use_case="general")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        for rec in output.recommendations:
            assert 0.0 <= rec.score <= 10.0

    def test_perfect_match_scores_high(self, db_session: Session) -> None:
        """A product matching all criteria should score near 10."""
        # Arrange
        _make_product(
            db_session,
            name="Perfect",
            price_cents=1000,
            stock_qty=10,
            categories=["electronics"],
            use_cases=["gym"],
        )

        intent = BuyerIntent(
            query="headphones",
            category="electronics",
            use_case="gym",
            budget_cents=2000,
        )

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        if output.recommendations:
            assert output.recommendations[0].score >= 8.0


# ─── Test: Empty States ──────────────────────────────────────────────────────


class TestEmptyStates:
    """Test handling of empty or invalid inputs."""

    def test_empty_catalog_returns_empty_recommendations(self, db_session: Session) -> None:
        """Empty catalog should return empty recommendations."""
        # Arrange: No products
        intent = BuyerIntent(query="anything")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        assert output.recommendations == []
        assert output.upsell_opportunities == []
        assert output.bundles == []
        assert output.expected_order_value.total_estimated_cents == 0

    def test_single_product_no_bundles(self, db_session: Session) -> None:
        """Single product should not generate bundles."""
        # Arrange
        _make_product(db_session, name="Solo", price_cents=1000, stock_qty=10)

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert: Bundles require at least 2 products
        assert output.bundles == []


# ─── Test: Explanation Generation ─────────────────────────────────────────────


class TestExplanationGeneration:
    """Test that explanations are generated correctly."""

    def test_explanation_mentions_top_product(self, db_session: Session) -> None:
        """Explanation should mention the top recommended product."""
        # Arrange
        product = _make_product(
            db_session, name="Top Pick", price_cents=1000, stock_qty=10
        )

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        assert "Top Pick" in output.explanation

    def test_explanation_mentions_savings_for_bundles(self, db_session: Session) -> None:
        """Explanation should mention savings when bundles are present."""
        # Arrange
        _make_product(db_session, name="P1", price_cents=1000, stock_qty=10)
        _make_product(db_session, name="P2", price_cents=2000, stock_qty=10)

        intent = BuyerIntent(query="widget")

        # Act
        brain = GrowthBrain(db_session)
        output = brain.analyze(intent)

        # Assert
        if output.bundles:
            assert "save" in output.explanation.lower() or "₹" in output.explanation