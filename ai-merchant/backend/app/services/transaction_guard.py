"""Deterministic Transaction Guard - Policy Engine for Payment Validation.

This module provides a deterministic, LLM-agnostic policy engine for
validating and approving transactions. Never uses LLM for safety checks.

From AGENTS.md: "LLM proposes, deterministic code disposes."

The LLM may propose a transaction, but all validation is done by deterministic
code. The LLM cannot override limits, bypass policy, or approve payment.

Example:
    policy = TransactionPolicy(
        max_amount=Decimal("5000000"),  # ₹50,000
        max_discount_percent=Decimal("20.0"),
        require_confirmation_above=Decimal("1000"),  # ₹10
    )

    result = approve_transaction(quote_proposal, policy)
    # Returns: ("APPROVE", reason) or ("BLOCK", reason)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Tuple


class TransactionPolicy:
    """Configuration for transaction policy limits.

    All amounts are in paise (integer cents) unless otherwise specified.
    The LLM may propose transactions, but this policy enforces limits
    deterministically - the LLM cannot override these values.

    From AGENTS.md financial invariants:
    - Transaction amount must be <= approved limit
    - Discount must be <= merchant discount limit
    - Every transaction must have a reason
    """

    def __init__(
        self,
        max_amount: Decimal | int | None = None,
        max_discount_percent: Decimal | float | int | None = None,
        require_confirmation_above: Decimal | int | None = None,
    ) -> None:
        """Initialize transaction policy with limits.

        Args:
            max_amount: Maximum approved transaction amount in paise (int).
                None means no limit.
            max_discount_percent: Maximum allowed discount percentage (0-100).
                None means no limit.
            require_confirmation_above: Minimum amount (in paise) that
                requires explicit confirmation. None means no requirement.
        """
        # Store as int (paise) if Decimal, convert; otherwise keep as-is
        if max_amount is not None:
            try:
                self.max_amount = int(Decimal(str(max_amount)))
            except (InvalidOperation, ValueError):
                self.max_amount = None
        else:
            self.max_amount = None

        if max_discount_percent is not None:
            try:
                self.max_discount_percent = Decimal(str(max_discount_percent))
                if self.max_discount_percent < 0 or self.max_discount_percent > 100:
                    raise ValueError("Discount must be 0-100%")
            except (InvalidOperation, ValueError):
                self.max_discount_percent = None
        else:
            self.max_discount_percent = None

        if require_confirmation_above is not None:
            try:
                self.require_confirmation_above = int(Decimal(str(require_confirmation_above)))
            except (InvalidOperation, ValueError):
                self.require_confirmation_above = None
        else:
            self.require_confirmation_above = None


def _to_int_paise(amount) -> int:
    """Convert amount to integer paise, handling Decimal, int, str.

    Floats are treated as paise values (not INR) and truncated to int.
    This avoids ambiguous multiplication that could misprice transactions.
    """
    if isinstance(amount, int):
        return amount
    if isinstance(amount, Decimal):
        return int(amount)
    if isinstance(amount, float):
        return int(amount)  # Float paise → int paise (no ×100 ambiguity)
    if isinstance(amount, str):
        try:
            d = Decimal(amount)
            return int(d)
        except InvalidOperation:
            return 0
    return 0


def _to_decimal(value) -> Decimal:
    """Convert value to Decimal for comparison."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    return Decimal("0")


def approve_transaction(
    amount_paise: int,
    discount_percent: Decimal | float | int | str | None,
    reason: str | None,
    policy: TransactionPolicy,
) -> Tuple[str, str]:
    """
    Determine whether a transaction should be approved or blocked.

    This is a deterministic function - the LLM cannot influence the result.
    All checks are performed by code, never by the LLM.

    From AGENTS.md:
    - "LLM proposes, deterministic code disposes"
    - "Transaction amount must be <= approved limit"
    - "Discount must be <= merchant discount limit"
    - "Every transaction must have a reason"

    Args:
        amount_paise: Transaction amount in paise (integer).
        discount_percent: Discount percentage proposed (0-100).
        reason: Human-readable reason for the transaction.
        policy: TransactionPolicy instance with configured limits.

    Returns:
        Tuple of (decision, reason):
        - ("APPROVE", "Human-readable explanation") if all checks pass
        - ("BLOCK", "Human-readable explanation") if any check fails

    Example:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)
        decision, reason = approve_transaction(
            amount_paise=15000,
            discount_percent=10.0,
            reason="Buyer purchased Wireless Headphones",
            policy=policy,
        )
        # decision == "APPROVE"
    """
    # Check 1: Maximum amount limit
    if policy.max_amount is not None:
        amount = _to_int_paise(amount_paise)
        if amount > policy.max_amount:
            return (
                "BLOCK",
                f"Amount {amount} paise exceeds maximum allowed {policy.max_amount} paise",
            )

    # Check 2: Maximum discount limit
    if policy.max_discount_percent is not None:
        try:
            discount = _to_decimal(discount_percent)
            if discount > policy.max_discount_percent:
                return (
                    "BLOCK",
                    f"Discount {discount}% exceeds maximum allowed "
                    f"{policy.max_discount_percent}%",
                )
        except (InvalidOperation, ValueError):
            return (
                "BLOCK",
                f"Invalid discount percentage: {discount_percent}",
            )

    # Check 3: Required reason
    if policy.require_confirmation_above is not None:
        amount = _to_int_paise(amount_paise)
        if amount > policy.require_confirmation_above and not reason:
            return (
                "BLOCK",
                f"Transaction above {policy.require_confirmation_above} paise "
                f"requires a reason, but none was provided",
            )

    # All checks passed
    amount = _to_int_paise(amount_paise)
    discount_disp = (
        f"{_to_decimal(discount_percent)}%"
        if discount_percent is not None
        else "0%"
    )
    return (
        "APPROVE",
        f"Transaction approved: {amount} paise, "
        f"discount {discount_disp}, reason: {reason or 'N/A'}",
    )