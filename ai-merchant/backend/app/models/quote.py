"""SQLAlchemy models for Quotes and Quote Items."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, INTEGER, DateTime, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class Quote(Base):
    """A bounded quote generated during negotiation.

    Every quote has a policy_decision (APPROVE/REJECT/ESCALATE) and
    policy_reason explaining the deterministic policy engine verdict.
    Quotes have bounded validity (valid_until) to prevent stale quotes.
    """

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    negotiation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True,
    )
    proposed_by: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft",
    )
    subtotal_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False,
    )
    discount_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False, default=0,
    )
    tax_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False, default=0,
    )
    total_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR",
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    policy_decision: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
    )
    policy_reason: Mapped[str | None] = mapped_column(
        String, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "proposed_by IN ('buyer', 'merchant', 'system')",
            name="ck_quote_proposed_by",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', "
            "'rejected', 'expired', 'accepted')",
            name="ck_quote_status",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"<Quote id={self.id} total={self.total_cents} "
            f"decision={self.policy_decision}>"
        )


class QuoteItem(Base):
    """An individual item within a Quote."""

    __tablename__ = "quote_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    quote_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
    )
    product_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(
        INTEGER, nullable=False,
    )
    unit_price_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_quoteitem_quantity_positive",
        ),
        CheckConstraint(
            "unit_price_cents > 0",
            name="ck_quoteitem_price_positive",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"<QuoteItem quote={self.quote_id} product={self.product_id} "
            f"qty={self.quantity} price={self.unit_price_cents}>"
        )