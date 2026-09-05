"""Deterministic tests for the Negotiation Engine.

Tests cover:
1. State machine transitions
2. Policy validation (discount caps, inventory, margins, budgets)
3. Round limits (max 5 rounds)
4. Turn limits (max 20 turns)
5. Quote expiry
6. Terminal states
7. Buyer/merchant actions

All tests use SQLite in-memory database for speed and isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base
from app.models.merchant import Merchant
from app.models.negotiation import Negotiation
from app.models.product import Product
from app.models.quote import Quote, QuoteItem
from app.schemas.negotiation import (
    NegotiationStart,
    QuoteProposal,
    QuoteItemInput,
    NegotiationStatus,
    QuoteStatus,
    PolicyVerdict,
    MAX_ROUNDS,
    MAX_TURNS,
)
from app.services.negotiation_service import NegotiationService
from app.services.negotiation_state import (
    transition,
    is_terminal,
    calculate_valid_until,
    is_quote_expired,
    StateMachineError,
    NegotiationTerminated,
)
from app.services.negotiation_validator import (
    evaluate_quote_proposal,
    calculate_quote_totals,
)


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


@pytest.fixture()
def merchant(db_session: Session) -> Merchant:
    """Create a test merchant."""
    m = Merchant(
        id=str(uuid.uuid4()),
        name="Test Merchant",
        config={"discount_cap_pct": 20.0, "min_margin_pct": 10.0},
    )
    db_session.add(m)
    db_session.commit()
    return m


@pytest.fixture()
def product(db_session: Session, merchant: Merchant) -> Product:
    """Create a test product."""
    p = Product(
        id=str(uuid.uuid4()),
        merchant_id=merchant.id,
        name="Wireless Headphones",
        description="Premium wireless headphones",
        ai_schema={"category": "electronics", "tags": ["wireless", "bluetooth"]},
        price_cents=15000,  # ₹150 - above min order value of ₹100
        currency="INR",
        stock_qty=10,
        categories=["electronics"],
        use_cases=["gym", "commute"],
        compatible_product_ids=[],
        is_active=True,
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def negotiation_service(db_session: Session) -> NegotiationService:
    """Create a negotiation service instance."""
    return NegotiationService(db_session)


def _make_quote_proposal(product_id: str, quantity: int = 1, discount_pct: float | None = None) -> QuoteProposal:
    """Helper to create a quote proposal."""
    return QuoteProposal(
        items=[QuoteItemInput(product_id=product_id, quantity=quantity)],
        discount_pct=discount_pct,
        message="Test proposal",
    )


# ─── Test: State Machine Transitions ──────────────────────────────────────────


class TestStateMachine:
    """Test the deterministic negotiation state machine."""

    def test_initial_state_is_active(self):
        """New negotiation starts in ACTIVE state."""
        from app.schemas.negotiation import NegotiationStatus
        # The state machine doesn't have an initial state function,
        # but ACTIVE is the default
        assert NegotiationStatus.ACTIVE.value == "active"

    def test_valid_transitions_from_active_buyer(self):
        """Buyer can propose quote from ACTIVE."""
        new_state = transition(
            NegotiationStatus.ACTIVE, "propose_quote", "buyer", 0, 0
        )
        assert new_state == NegotiationStatus.QUOTE_PROPOSED

    def test_valid_transitions_from_active_merchant(self):
        """Merchant cannot propose from ACTIVE (only buyer starts)."""
        with pytest.raises(StateMachineError):
            transition(NegotiationStatus.ACTIVE, "propose_quote", "merchant", 0, 0)

    def test_valid_transitions_merchant_counter(self):
        """Merchant can counter-propose from QUOTE_PROPOSED."""
        new_state = transition(
            NegotiationStatus.QUOTE_PROPOSED, "counter_propose", "merchant", 1, 2
        )
        assert new_state == NegotiationStatus.QUOTE_PROPOSED

    def test_valid_transitions_merchant_accept(self):
        """Merchant can accept quote from QUOTE_PROPOSED."""
        new_state = transition(
            NegotiationStatus.QUOTE_PROPOSED, "accept_quote", "merchant", 1, 2
        )
        assert new_state == NegotiationStatus.ACCEPTED

    def test_valid_transitions_merchant_reject(self):
        """Merchant can reject quote from QUOTE_PROPOSED."""
        new_state = transition(
            NegotiationStatus.QUOTE_PROPOSED, "reject_quote", "merchant", 1, 2
        )
        assert new_state == NegotiationStatus.REJECTED

    def test_valid_transitions_buyer_accept_counter(self):
        """Buyer can accept merchant's counter from QUOTE_PROPOSED."""
        new_state = transition(
            NegotiationStatus.QUOTE_PROPOSED, "accept_quote", "buyer", 2, 4
        )
        assert new_state == NegotiationStatus.ACCEPTED

    def test_valid_transitions_buyer_reject_counter(self):
        """Buyer can reject merchant's counter from QUOTE_PROPOSED."""
        new_state = transition(
            NegotiationStatus.QUOTE_PROPOSED, "reject_quote", "buyer", 2, 4
        )
        assert new_state == NegotiationStatus.REJECTED

    def test_terminal_states_are_final(self):
        """Terminal states cannot transition further."""
        for state in [NegotiationStatus.ACCEPTED, NegotiationStatus.REJECTED,
                      NegotiationStatus.EXPIRED, NegotiationStatus.CANCELLED]:
            with pytest.raises(NegotiationTerminated):
                transition(state, "propose_quote", "buyer", 0, 0)

    def test_cancel_from_active(self):
        """Both parties can cancel from ACTIVE."""
        for actor in ["buyer", "merchant"]:
            new_state = transition(NegotiationStatus.ACTIVE, "cancel", actor, 0, 0)
            assert new_state == NegotiationStatus.CANCELLED

    def test_cancel_from_quote_proposed(self):
        """Both parties can cancel from QUOTE_PROPOSED."""
        for actor in ["buyer", "merchant"]:
            new_state = transition(NegotiationStatus.QUOTE_PROPOSED, "cancel", actor, 1, 2)
            assert new_state == NegotiationStatus.CANCELLED

    def test_max_rounds_enforced(self):
        """Counter-propose fails after 5 rounds."""
        with pytest.raises(NegotiationTerminated):
            transition(
                NegotiationStatus.QUOTE_PROPOSED, "counter_propose", "merchant",
                MAX_ROUNDS, 10  # round 5 (0-indexed = 5 rounds done)
            )

    def test_max_turns_enforced(self):
        """Any action fails after 20 turns."""
        with pytest.raises(NegotiationTerminated):
            transition(NegotiationStatus.ACTIVE, "propose_quote", "buyer", 0, MAX_TURNS)

    def test_is_terminal(self):
        """Check terminal state detection."""
        assert is_terminal(NegotiationStatus.ACCEPTED)
        assert is_terminal(NegotiationStatus.REJECTED)
        assert is_terminal(NegotiationStatus.EXPIRED)
        assert is_terminal(NegotiationStatus.CANCELLED)
        assert not is_terminal(NegotiationStatus.ACTIVE)
        assert not is_terminal(NegotiationStatus.QUOTE_PROPOSED)


# ─── Test: Quote Validity ─────────────────────────────────────────────────────


class TestQuoteValidity:
    """Test quote expiry and validity."""

    def test_calculate_valid_until(self):
        """Valid until is 30 minutes in future."""
        valid_until = calculate_valid_until()
        now = datetime.now(timezone.utc)
        assert valid_until > now
        assert valid_until - now < timedelta(minutes=31)
        assert valid_until - now > timedelta(minutes=29)

    def test_quote_not_expired_immediately(self):
        """Fresh quote is not expired."""
        valid_until = calculate_valid_until()
        assert not is_quote_expired(valid_until)

    def test_quote_expired_in_past(self):
        """Past quote is expired."""
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert is_quote_expired(past)


# ─── Test: Policy Validator ───────────────────────────────────────────────────


class TestPolicyValidator:
    """Test deterministic policy validation."""

    def test_discount_cap_enforced(self, db_session: Session, product: Product):
        """Discount over 20% is rejected by policy engine."""
        # Create proposal with high discount - test policy validator directly
        # Bypass schema validation by calling evaluate_quote_proposal directly
        from app.services.negotiation_validator import ValidationContext, check_discount_cap
        from app.schemas.negotiation import QuoteItemInput

        class MockProposal:
            discount_pct = 25.0
            items = [QuoteItemInput(product_id=product.id, quantity=1)]

        ctx = ValidationContext(
            quote_proposal=MockProposal(),
            items_with_products=[(MockProposal().items[0], product)],
            merchant_config={},
        )
        result = check_discount_cap(ctx)

        assert result.verdict == PolicyVerdict.REJECT
        assert not result.passed
        assert "25.0" in result.reason

    def test_discount_within_cap_approved(self, db_session: Session, product: Product):
        """Discount within cap passes."""
        # Use 5% discount to pass both discount_cap (20%) and price_floor (10% margin)
        # Effective price = 15000 * 0.95 = 14250, floor = 15000 * 0.9 = 13500
        proposal = _make_quote_proposal(product.id, discount_pct=5.0)
        result = evaluate_quote_proposal(db_session, proposal, merchant_config={})

        assert result.overall_verdict == PolicyVerdict.APPROVE
        assert all(c.passed for c in result.checks if c.policy_id == "discount_cap")

    def test_inventory_check_rejects_oversell(self, db_session: Session, product: Product):
        """Requesting more than stock rejects."""
        proposal = _make_quote_proposal(product.id, quantity=15)  # Only 10 in stock
        result = evaluate_quote_proposal(db_session, proposal, merchant_config={})

        assert result.overall_verdict == PolicyVerdict.REJECT
        assert any(c.policy_id == "inventory_check" and not c.passed for c in result.checks)

    def test_inventory_check_passes_within_stock(self, db_session: Session, product: Product):
        """Requesting within stock passes."""
        proposal = _make_quote_proposal(product.id, quantity=5)
        result = evaluate_quote_proposal(db_session, proposal, merchant_config={})

        assert result.overall_verdict == PolicyVerdict.APPROVE

    def test_min_order_value_enforced(self, db_session: Session, product: Product):
        """Orders below ₹100 are rejected."""
        # Create cheap product
        cheap = Product(
            id=str(uuid.uuid4()),
            merchant_id=product.merchant_id,
            name="Cheap Item",
            description="Cheap",
            ai_schema={},
            price_cents=500,  # ₹5
            currency="INR",
            stock_qty=100,
            categories=["test"],
            use_cases=["test"],
            compatible_product_ids=[],
            is_active=True,
        )
        db_session.add(cheap)
        db_session.commit()

        proposal = _make_quote_proposal(cheap.id, quantity=1)
        result = evaluate_quote_proposal(db_session, proposal, merchant_config={})

        assert result.overall_verdict == PolicyVerdict.REJECT
        assert any(c.policy_id == "min_order_value" and not c.passed for c in result.checks)

    def test_max_order_value_escalates(self, db_session: Session, product: Product):
        """Orders above ₹50k escalate."""
        # Need enough quantity to exceed 5,000,000 cents
        # 15000 * quantity > 5,000,000 => quantity > 333
        # Also need sufficient stock
        product.stock_qty = 1000
        db_session.commit()
        
        proposal = _make_quote_proposal(product.id, quantity=400)
        result = evaluate_quote_proposal(db_session, proposal, merchant_config={})

        assert result.overall_verdict == PolicyVerdict.ESCALATE
        assert any(c.policy_id == "max_order_value" and not c.passed for c in result.checks)

    def test_buyer_budget_enforced(self, db_session: Session, product: Product):
        """Orders exceeding buyer budget are rejected."""
        proposal = _make_quote_proposal(product.id, quantity=1)  # ₹50
        result = evaluate_quote_proposal(
            db_session, proposal, buyer_budget_cents=3000, merchant_config={}  # ₹30 budget
        )

        assert result.overall_verdict == PolicyVerdict.REJECT
        assert any(c.policy_id == "buyer_budget" and not c.passed for c in result.checks)

    def test_buyer_budget_passes_within_limit(self, db_session: Session, product: Product):
        """Orders within buyer budget pass."""
        # Product is ₹150, need budget ≥ ₹150 + tax = ~₹177
        proposal = _make_quote_proposal(product.id, quantity=1)
        result = evaluate_quote_proposal(
            db_session, proposal, buyer_budget_cents=20000, merchant_config={}  # ₹200 budget
        )

        assert result.overall_verdict == PolicyVerdict.APPROVE

    def test_price_floor_enforced(self, db_session: Session, product: Product):
        """Effective price below cost floor is rejected."""
        # Floor is based on discount_cap (20%): floor = price * 0.8 = 15000 * 0.8 = 12000
        # With 15% discount: effective = 15000 * 0.85 = 12750 > 12000, passes
        # With 25% discount: would fail discount_cap first
        # To test price_floor, we need a discount that passes discount_cap but fails floor
        # But discount_cap is 20%, so max discount is 20%, floor is 80% of price
        # Any discount ≤ 20% will have effective price ≥ floor
        # So price_floor only rejects if there's a way to have effective price < floor
        # This happens if merchant_config has a higher discount_cap than default
        
        # Test with custom discount_cap of 30% and min_margin_pct of 10%
        # Floor with 10% margin = 15000 * 0.9 = 13500
        # Discount of 15%: effective = 15000 * 0.85 = 12750 < 13500, should reject
        proposal = _make_quote_proposal(product.id, discount_pct=15.0)
        result = evaluate_quote_proposal(
            db_session, proposal, 
            merchant_config={"discount_cap_pct": 30.0, "min_margin_pct": 10.0}
        )

        assert result.overall_verdict == PolicyVerdict.REJECT
        assert any(c.policy_id == "price_floor" and not c.passed for c in result.checks)

    def test_nonexistent_product_rejected(self, db_session: Session):
        """Non-existent product is rejected."""
        proposal = QuoteProposal(
            items=[QuoteItemInput(product_id=str(uuid.uuid4()), quantity=1)],
        )
        result = evaluate_quote_proposal(db_session, proposal)

        assert result.overall_verdict == PolicyVerdict.REJECT
        assert any(c.policy_id == "product_exists" and not c.passed for c in result.checks)


# ─── Test: Quote Totals Calculation ───────────────────────────────────────────


class TestQuoteTotals:
    """Test server-side quote total calculations."""

    def test_calculate_totals_no_discount(self, db_session: Session, product: Product):
        """Totals without discount."""
        items = [(_make_quote_proposal(product.id).items[0], product)]
        subtotal, discount, tax, total = calculate_quote_totals(items, 0.0)

        assert subtotal == 15000
        assert discount == 0
        assert tax == int(15000 * 0.18)  # 2700
        assert total == 15000 + 2700  # 17700

    def test_calculate_totals_with_discount(self, db_session: Session, product: Product):
        """Totals with 10% discount."""
        items = [(_make_quote_proposal(product.id).items[0], product)]
        subtotal, discount, tax, total = calculate_quote_totals(items, 10.0)

        assert subtotal == 15000
        assert discount == 1500  # 10% of 15000
        taxable = 15000 - 1500  # 13500
        assert tax == int(taxable * 0.18)  # 2430
        assert total == taxable + tax  # 15930

    def test_calculate_totals_multiple_items(self, db_session: Session, product: Product):
        """Totals with multiple quantities."""
        items = [(_make_quote_proposal(product.id, quantity=3).items[0], product)]
        subtotal, discount, tax, total = calculate_quote_totals(items, 10.0)

        assert subtotal == 45000
        assert discount == 4500
        taxable = 40500
        assert tax == int(taxable * 0.18)  # 7290
        assert total == taxable + tax  # 47790


# ─── Test: Negotiation Service - Full Flow ────────────────────────────────────


class TestNegotiationFlow:
    """Test complete negotiation flows."""

    def test_start_negotiation(self, negotiation_service: NegotiationService, merchant: Merchant):
        """Start a new negotiation."""
        start = NegotiationStart(
            buyer_id="buyer_123",
            merchant_id=merchant.id,
            initial_message="I want to buy headphones",
        )
        result = negotiation_service.start_negotiation(start)

        assert result.success
        assert result.negotiation.status == NegotiationStatus.ACTIVE
        assert result.negotiation.buyer_id == "buyer_123"
        assert result.negotiation.merchant_id == merchant.id
        assert result.negotiation.round_number == 0
        assert result.negotiation.turn_count == 1  # Initial message counts as turn

    def test_buyer_proposes_quote(self, negotiation_service: NegotiationService,
                                   merchant: Merchant, product: Product):
        """Buyer proposes a quote."""
        # Start negotiation
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        # Buyer proposes - need budget ≥ ₹177 (15000 + tax)
        proposal = _make_quote_proposal(product.id, quantity=1, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=20000)

        assert result.success
        assert result.negotiation.status == NegotiationStatus.QUOTE_PROPOSED
        assert result.negotiation.round_number == 1
        assert result.quote is not None
        assert result.quote.total_cents == 15930  # 15000 - 10% + 18% tax

    def test_merchant_counter_proposes(self, negotiation_service: NegotiationService,
                                        merchant: Merchant, product: Product):
        """Merchant counter-proposes."""
        # Start and buyer proposes
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=5.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)
        assert result.success

        # Merchant counter-proposes
        counter = _make_quote_proposal(product.id, discount_pct=0.0)
        result = negotiation_service.propose_quote(neg_id, "merchant", counter)

        assert result.success
        assert result.negotiation.status == NegotiationStatus.QUOTE_PROPOSED
        assert result.negotiation.round_number == 2  # Round incremented
        assert result.quote.discount_pct == 0.0

    def test_merchant_accepts_quote(self, negotiation_service: NegotiationService,
                                     merchant: Merchant, product: Product):
        """Merchant accepts buyer's quote."""
        # Start and buyer proposes
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=20000)
        quote_id = result.quote.id

        # Merchant accepts
        result = negotiation_service.accept_quote(neg_id, "merchant", quote_id)

        assert result.success
        assert result.negotiation.status == NegotiationStatus.ACCEPTED
        assert result.termination_reason is not None
        assert "accepted" in result.termination_reason.lower()

    def test_buyer_accepts_counter(self, negotiation_service: NegotiationService,
                                    merchant: Merchant, product: Product):
        """Buyer accepts merchant's counter."""
        # Start, buyer proposes, merchant counters
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)

        counter = _make_quote_proposal(product.id, discount_pct=5.0)
        result = negotiation_service.propose_quote(neg_id, "merchant", counter)
        quote_id = result.quote.id

        # Buyer accepts counter
        result = negotiation_service.accept_quote(neg_id, "buyer", quote_id)

        assert result.success
        assert result.negotiation.status == NegotiationStatus.ACCEPTED

    def test_reject_quote(self, negotiation_service: NegotiationService,
                           merchant: Merchant, product: Product):
        """Reject a quote."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)
        quote_id = result.quote.id

        # Merchant rejects
        result = negotiation_service.reject_quote(neg_id, "merchant", quote_id, "Price too low")

        assert result.success
        assert result.negotiation.status == NegotiationStatus.REJECTED
        assert "rejected" in result.termination_reason.lower()

    def test_cancel_negotiation(self, negotiation_service: NegotiationService,
                                 merchant: Merchant):
        """Cancel a negotiation."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        result = negotiation_service.cancel_negotiation(neg_id, "buyer")

        assert result.success
        assert result.negotiation.status == NegotiationStatus.CANCELLED

    def test_max_rounds_terminates(self, negotiation_service: NegotiationService,
                                    merchant: Merchant, product: Product):
        """Negotiation terminates after 5 rounds of counter-proposals."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        # Buyer proposes first (round 0)
        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)
        assert result.success

        # Merchant counter-proposes MAX_ROUNDS times
        for i in range(1, MAX_ROUNDS + 1):
            counter = _make_quote_proposal(product.id, discount_pct=float(i * 2))
            result = negotiation_service.propose_quote(neg_id, "merchant", counter)
            if not result.success:
                break

        # Next counter should fail due to round limit
        counter = _make_quote_proposal(product.id, discount_pct=15.0)
        result = negotiation_service.propose_quote(neg_id, "merchant", counter)

        assert not result.success
        assert "Maximum rounds" in result.error or "terminated" in result.error.lower() or "round" in result.error.lower()

    def test_expired_quote_cannot_be_accepted(self, negotiation_service: NegotiationService,
                                               merchant: Merchant, product: Product, db_session: Session):
        """Expired quote cannot be accepted."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)
        quote_id = result.quote.id

        # Manually expire the quote
        from sqlalchemy import select
        quote = db_session.execute(select(Quote).filter(Quote.id == quote_id)).scalar_one()
        quote.valid_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        # Try to accept
        result = negotiation_service.accept_quote(neg_id, "merchant", quote_id)

        assert not result.success
        assert "expired" in result.error.lower()

    def test_rejected_quote_cannot_be_accepted(self, negotiation_service: NegotiationService,
                                                merchant: Merchant, product: Product, db_session: Session):
        """Rejected quote cannot be accepted."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)
        quote_id = result.quote.id

        # Manually set policy decision to REJECT
        from sqlalchemy import select
        quote = db_session.execute(select(Quote).filter(Quote.id == quote_id)).scalar_one()
        quote.policy_decision = PolicyVerdict.REJECT.value
        db_session.commit()

        result = negotiation_service.accept_quote(neg_id, "merchant", quote_id)

        assert not result.success
        assert "rejected" in result.error.lower()

    def test_get_negotiation_state(self, negotiation_service: NegotiationService,
                                    merchant: Merchant, product: Product):
        """Get negotiation state with history."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id,
                                 initial_message="Let's negotiate")
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)

        # Get state
        state = negotiation_service.get_negotiation_state(neg_id)

        assert state is not None
        assert state.negotiation_id == neg_id
        assert len(state.messages) == 2  # Initial + quote proposal
        assert state.current_quote is not None
        assert len(state.quote_history) == 1


# ─── Test: Message Handling ───────────────────────────────────────────────────


class TestMessageHandling:
    """Test free-form message exchange."""

    def test_add_message(self, negotiation_service: NegotiationService, merchant: Merchant):
        """Add a message to negotiation."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id,
                                 initial_message="Initial message")
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        result = negotiation_service.add_message(neg_id, "buyer", "Hello, what's the best price?")

        assert result.success
        state = negotiation_service.get_negotiation_state(neg_id)
        assert len(state.messages) == 2
        assert state.messages[-1].content == "Hello, what's the best price?"

    def test_cannot_add_message_to_terminated(self, negotiation_service: NegotiationService,
                                               merchant: Merchant, product: Product):
        """Cannot add messages after termination."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        # Use a product with price above min_order_value so quote gets approved
        proposal = _make_quote_proposal(product.id, discount_pct=0.0)
        result = negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=20000)
        quote_id = result.quote.id

        # Merchant accepts
        negotiation_service.accept_quote(neg_id, "merchant", quote_id)

        # Try to add message after acceptance
        result = negotiation_service.add_message(neg_id, "buyer", "Thanks!")

        assert not result.success
        assert "terminated" in result.error.lower()


# ─── Test: Negotiation Persistence ────────────────────────────────────────────


class TestNegotiationPersistence:
    """Test that negotiation state persists correctly."""

    def test_round_number_persists(self, db_session: Session, negotiation_service: NegotiationService,
                                    merchant: Merchant, product: Product):
        """Round number increments and persists."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=10000)

        counter = _make_quote_proposal(product.id, discount_pct=5.0)
        negotiation_service.propose_quote(neg_id, "merchant", counter)

        # Check persisted round number
        stmt = select(Negotiation).filter(Negotiation.id == neg_id)
        neg = db_session.execute(stmt).scalar_one()
        assert neg.round_number == 2

    def test_turn_count_persists(self, db_session: Session, negotiation_service: NegotiationService,
                                  merchant: Merchant, product: Product):
        """Turn count increments and persists."""
        start = NegotiationStart(buyer_id="buyer_123", merchant_id=merchant.id)
        result = negotiation_service.start_negotiation(start)
        neg_id = result.negotiation.negotiation_id

        proposal = _make_quote_proposal(product.id, discount_pct=10.0)
        negotiation_service.propose_quote(neg_id, "buyer", proposal, buyer_budget_cents=20000)

        stmt = select(Negotiation).filter(Negotiation.id == neg_id)
        neg = db_session.execute(stmt).scalar_one()
        # No initial_message in start, so turn_count = 0 -> 1 after proposal
        assert neg.turn_count == 1


# ─── Import for TestNegotiationPersistence ────────────────────────────────────


from sqlalchemy import select
from app.models.quote import Quote