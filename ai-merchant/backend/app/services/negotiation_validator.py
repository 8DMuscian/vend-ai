"""Deterministic Negotiation Constraint Validator.

This validator enforces all negotiation constraints server-side:
- Discount caps (per item and total)
- Minimum order value
- Maximum order value
- Inventory availability
- Price floor (cost-based minimum)
- Buyer budget
- Quote validity

The LLM proposes, deterministic code validates. Never trust LLM output for financial state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.product import Product
from app.models.quote import Quote
from app.schemas.negotiation import (
    PolicyCheckResult,
    PolicyEvaluationOutput,
    PolicyVerdict,
    QuoteItemInput,
    QuoteProposal,
    DEFAULT_DISCOUNT_CAP_PCT,
    MIN_ORDER_VALUE_CENTS,
    MAX_AUTO_APPROVE_CENTS,
    QUOTE_VALIDITY_MINUTES,
)


@dataclass
class ValidationContext:
    """Context for quote validation."""

    quote_proposal: QuoteProposal
    items_with_products: List[tuple[QuoteItemInput, Product]]
    buyer_budget_cents: Optional[int] = None
    merchant_config: Optional[dict] = None
    existing_quotes: Optional[List[Quote]] = None
    proposed_by: Optional[str] = None


def _get_discount_cap(merchant_config: Optional[dict]) -> float:
    """Get discount cap from merchant config or global default."""
    if merchant_config and "discount_cap_pct" in merchant_config:
        return float(merchant_config["discount_cap_pct"])
    return DEFAULT_DISCOUNT_CAP_PCT


def _get_min_margin(merchant_config: Optional[dict]) -> float:
    """Get minimum margin percentage from merchant config."""
    if merchant_config and "min_margin_pct" in merchant_config:
        return float(merchant_config["min_margin_pct"])
    return 10.0  # Default 10% minimum margin


def check_discount_cap(ctx: ValidationContext) -> PolicyCheckResult:
    """Check that discount doesn't exceed cap."""
    discount_cap = _get_discount_cap(ctx.merchant_config)
    requested = ctx.quote_proposal.discount_pct or 0.0

    if requested > discount_cap:
        return PolicyCheckResult(
            policy_id="discount_cap",
            verdict=PolicyVerdict.REJECT,
            passed=False,
            reason=f"Requested discount {requested:.1f}% exceeds maximum {discount_cap:.1f}%",
            details={"requested": requested, "max_allowed": discount_cap},
        )

    return PolicyCheckResult(
        policy_id="discount_cap",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason=f"Discount {requested:.1f}% within limit {discount_cap:.1f}%",
        details={"requested": requested, "max_allowed": discount_cap},
    )


def check_min_order_value(ctx: ValidationContext) -> PolicyCheckResult:
    """Check minimum order value."""
    subtotal = sum(
        item.quantity * product.price_cents
        for item, product in ctx.items_with_products
    )
    discount_pct = ctx.quote_proposal.discount_pct or 0.0
    discount_cents = int(subtotal * discount_pct / 100)
    total = subtotal - discount_cents

    if total < MIN_ORDER_VALUE_CENTS:
        return PolicyCheckResult(
            policy_id="min_order_value",
            verdict=PolicyVerdict.REJECT,
            passed=False,
            reason=f"Order total ₹{total/100:.2f} below minimum ₹{MIN_ORDER_VALUE_CENTS/100:.2f}",
            details={"total_cents": total, "min_cents": MIN_ORDER_VALUE_CENTS},
        )

    return PolicyCheckResult(
        policy_id="min_order_value",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason=f"Order total ₹{total/100:.2f} meets minimum",
        details={"total_cents": total, "min_cents": MIN_ORDER_VALUE_CENTS},
    )


def check_max_order_value(ctx: ValidationContext) -> PolicyCheckResult:
    """Check maximum auto-approve order value."""
    subtotal = sum(
        item.quantity * product.price_cents
        for item, product in ctx.items_with_products
    )
    discount_pct = ctx.quote_proposal.discount_pct or 0.0
    discount_cents = int(subtotal * discount_pct / 100)
    total = subtotal - discount_cents

    if total > MAX_AUTO_APPROVE_CENTS:
        return PolicyCheckResult(
            policy_id="max_order_value",
            verdict=PolicyVerdict.ESCALATE,
            passed=False,
            reason=f"Order total ₹{total/100:.2f} exceeds auto-approve limit ₹{MAX_AUTO_APPROVE_CENTS/100:.2f}",
            details={"total_cents": total, "max_cents": MAX_AUTO_APPROVE_CENTS},
        )

    return PolicyCheckResult(
        policy_id="max_order_value",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason=f"Order total ₹{total/100:.2f} within auto-approve limit",
        details={"total_cents": total, "max_cents": MAX_AUTO_APPROVE_CENTS},
    )


def check_inventory(ctx: ValidationContext) -> PolicyCheckResult:
    """Check inventory availability for all items."""
    for item, product in ctx.items_with_products:
        if product.stock_qty < item.quantity:
            return PolicyCheckResult(
                policy_id="inventory_check",
                verdict=PolicyVerdict.REJECT,
                passed=False,
                reason=f"Insufficient stock for {product.name}: requested {item.quantity}, available {product.stock_qty}",
                details={
                    "product_id": product.id,
                    "product_name": product.name,
                    "requested": item.quantity,
                    "available": product.stock_qty,
                },
            )

    return PolicyCheckResult(
        policy_id="inventory_check",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason="All items have sufficient inventory",
        details={"items_checked": len(ctx.items_with_products)},
    )


def check_price_floor(ctx: ValidationContext) -> PolicyCheckResult:
    """Check that unit prices don't go below cost floor."""
    min_margin_pct = _get_min_margin(ctx.merchant_config)

    for item, product in ctx.items_with_products:
        # Cost floor: product price minus max allowed discount based on min margin
        # Floor = price * (1 - min_margin_pct/100)
        min_price = int(product.price_cents * (1 - min_margin_pct / 100))

        # The effective price after discount
        effective_price = int(product.price_cents * (1 - (ctx.quote_proposal.discount_pct or 0) / 100))

        if effective_price < min_price:
            return PolicyCheckResult(
                policy_id="price_floor",
                verdict=PolicyVerdict.REJECT,
                passed=False,
                reason=f"Effective price for {product.name} below cost floor",
                details={
                    "product_id": product.id,
                    "product_name": product.name,
                    "list_price_cents": product.price_cents,
                    "effective_price_cents": effective_price,
                    "floor_price_cents": min_price,
                    "min_margin_pct": min_margin_pct,
                },
            )

    return PolicyCheckResult(
        policy_id="price_floor",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason="All prices above cost floor",
        details={"min_margin_pct": min_margin_pct},
    )


def check_buyer_budget(ctx: ValidationContext) -> PolicyCheckResult:
    """Check that order doesn't exceed buyer's stated budget."""
    if ctx.buyer_budget_cents is None:
        return PolicyCheckResult(
            policy_id="buyer_budget",
            verdict=PolicyVerdict.APPROVE,
            passed=True,
            reason="No budget constraint specified",
            details={},
        )

    subtotal = sum(
        item.quantity * product.price_cents
        for item, product in ctx.items_with_products
    )
    discount_pct = ctx.quote_proposal.discount_pct or 0.0
    discount_cents = int(subtotal * discount_pct / 100)
    total = subtotal - discount_cents

    if total > ctx.buyer_budget_cents:
        return PolicyCheckResult(
            policy_id="buyer_budget",
            verdict=PolicyVerdict.REJECT,
            passed=False,
            reason=f"Order total ₹{total/100:.2f} exceeds buyer budget ₹{ctx.buyer_budget_cents/100:.2f}",
            details={"total_cents": total, "budget_cents": ctx.buyer_budget_cents},
        )

    return PolicyCheckResult(
        policy_id="buyer_budget",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason=f"Order total ₹{total/100:.2f} within buyer budget",
        details={"total_cents": total, "budget_cents": ctx.buyer_budget_cents},
    )


def check_quote_expiry(ctx: ValidationContext) -> PolicyCheckResult:
    """Check that existing active quotes haven't expired.

    If there are existing quotes that are still in active status
    (pending_review/approved) but have passed their valid_until timestamp,
    this signals stale state. The new proposal is still allowed, but we
    flag the expiry for audit completeness.
    """
    if not ctx.existing_quotes:
        return PolicyCheckResult(
            policy_id="quote_expiry",
            verdict=PolicyVerdict.APPROVE,
            passed=True,
            reason="No existing quotes to check for expiry",
            details={},
        )

    expired_active = [
        q for q in ctx.existing_quotes
        if q.status in ("pending_review", "approved")
        and is_quote_expired(q.valid_until)
    ]

    if expired_active:
        return PolicyCheckResult(
            policy_id="quote_expiry",
            verdict=PolicyVerdict.APPROVE,
            passed=True,
            reason=f"{len(expired_active)} existing quote(s) expired; new proposal proceeds",
            details={
                "expired_quote_ids": [q.id for q in expired_active],
                "validity_minutes": QUOTE_VALIDITY_MINUTES,
            },
        )

    return PolicyCheckResult(
        policy_id="quote_expiry",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason="All existing quotes within validity period",
        details={"validity_minutes": QUOTE_VALIDITY_MINUTES},
    )


def check_no_duplicate_active_quotes(ctx: ValidationContext) -> PolicyCheckResult:
    """Check for duplicate active quotes from the SAME party in same negotiation."""
    if ctx.existing_quotes:
        proposed_by = ctx.proposed_by
        # Check for active quotes from the same proposer
        active_quotes = [
            q for q in ctx.existing_quotes
            if q.status in ("pending_review", "approved")
            and q.proposed_by == proposed_by
            and not is_quote_expired(q.valid_until)
        ]
        if active_quotes:
            return PolicyCheckResult(
                policy_id="duplicate_quote",
                verdict=PolicyVerdict.REJECT,
                passed=False,
                reason=f"Active quote already exists from {proposed_by} for this negotiation",
                details={"active_quote_count": len(active_quotes), "proposed_by": proposed_by},
            )

    return PolicyCheckResult(
        policy_id="duplicate_quote",
        verdict=PolicyVerdict.APPROVE,
        passed=True,
        reason="No conflicting active quotes from same party",
        details={},
    )


def is_quote_expired(valid_until: datetime) -> bool:
    """Check if a quote has expired.

    Handles both naive and aware datetimes by normalizing to UTC-aware.
    """
    now = datetime.now(timezone.utc)
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return now > valid_until


def evaluate_quote_proposal(
    session: Session,
    proposal: QuoteProposal,
    buyer_budget_cents: Optional[int] = None,
    merchant_config: Optional[dict] = None,
    existing_quotes: Optional[List[Quote]] = None,
    proposed_by: Optional[str] = None,
) -> PolicyEvaluationOutput:
    """
    Evaluate a quote proposal against all policies.

    This is the deterministic policy engine - it NEVER trusts LLM output.
    All prices are looked up from the database.

    Args:
        session: Database session
        proposal: The quote proposal to evaluate
        buyer_budget_cents: Buyer's maximum budget (optional)
        merchant_config: Merchant-specific configuration
        existing_quotes: Existing quotes in this negotiation

    Returns:
        PolicyEvaluationOutput with overall verdict and detailed checks
    """
    # Look up all products from database (never trust LLM prices)
    product_ids = [item.product_id for item in proposal.items]
    stmt = select(Product).filter(Product.id.in_(product_ids), Product.is_active == True)
    products = list(session.execute(stmt).scalars().all())
    product_map = {p.id: p for p in products}

    # Validate all products exist
    items_with_products: List[tuple[QuoteItemInput, Product]] = []
    for item in proposal.items:
        if item.product_id not in product_map:
            return PolicyEvaluationOutput(
                overall_verdict=PolicyVerdict.REJECT,
                checks=[
                    PolicyCheckResult(
                        policy_id="product_exists",
                        verdict=PolicyVerdict.REJECT,
                        passed=False,
                        reason=f"Product {item.product_id} not found or inactive",
                        details={"product_id": item.product_id},
                    )
                ],
                explanation=f"Product {item.product_id} not found in catalog",
            )
        items_with_products.append((item, product_map[item.product_id]))

    # Build validation context
    ctx = ValidationContext(
        quote_proposal=proposal,
        items_with_products=items_with_products,
        buyer_budget_cents=buyer_budget_cents,
        merchant_config=merchant_config,
        existing_quotes=existing_quotes,
        proposed_by=proposed_by,
    )

    # Run all policy checks in order
    checks: List[PolicyCheckResult] = [
        check_discount_cap(ctx),
        check_inventory(ctx),
        check_price_floor(ctx),
        check_min_order_value(ctx),
        check_max_order_value(ctx),
        check_buyer_budget(ctx),
        check_quote_expiry(ctx),
        check_no_duplicate_active_quotes(ctx),
    ]

    # Determine overall verdict
    # First hard REJECT stops, but we evaluate all for audit
    overall = PolicyVerdict.APPROVE
    for check in checks:
        if check.verdict == PolicyVerdict.REJECT:
            overall = PolicyVerdict.REJECT
            break
        elif check.verdict == PolicyVerdict.ESCALATE and overall == PolicyVerdict.APPROVE:
            overall = PolicyVerdict.ESCALATE

    # Build explanation
    passed = [c for c in checks if c.passed]
    failed = [c for c in checks if not c.passed]

    if overall == PolicyVerdict.APPROVE:
        explanation = f"All {len(passed)} policy checks passed. Order approved."
    elif overall == PolicyVerdict.REJECT:
        reasons = [f"{c.policy_id}: {c.reason}" for c in failed]
        explanation = f"Policy rejected: {'; '.join(reasons)}"
    else:  # ESCALATE
        reasons = [f"{c.policy_id}: {c.reason}" for c in failed]
        explanation = f"Policy escalated: {'; '.join(reasons)}"

    return PolicyEvaluationOutput(
        overall_verdict=overall,
        checks=checks,
        explanation=explanation,
    )


def calculate_quote_totals(
    items: List[tuple[QuoteItemInput, Product]],
    discount_pct: float = 0.0,
    tax_rate: float = 0.18,  # 18% GST default
) -> tuple[int, int, int, int]:
    """
    Calculate quote totals server-side.

    Args:
        items: List of (QuoteItemInput, Product) tuples
        discount_pct: Discount percentage (0-20)
        tax_rate: Tax rate as decimal (default 18%)

    Returns:
        (subtotal_cents, discount_cents, tax_cents, total_cents)
    """
    subtotal = sum(item.quantity * product.price_cents for item, product in items)
    discount_cents = int(subtotal * discount_pct / 100)
    taxable = subtotal - discount_cents
    tax_cents = int(taxable * tax_rate)
    total = taxable + tax_cents
    return subtotal, discount_cents, tax_cents, total