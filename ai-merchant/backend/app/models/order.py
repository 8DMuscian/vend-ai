"""SQLAlchemy models for Orders and Order Items."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, INTEGER, DateTime, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class Order(Base):
    """A completed order created from an accepted quote.

    Key invariants (AGENTS.md):
    - idempotency_key is UNIQUE - prevents duplicate charges
    - All monetary amounts are in paise (integer)
    - Status transitions are guarded by the transaction state machine
    """

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    quote_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True,
    )
    buyer_id: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    merchant_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created",
    )
    total_cents: Mapped[int] = mapped_column(
        INTEGER, nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR",
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False,
    )
    razorpay_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    payment_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String, nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'pending_payment', 'payment_initiated', "
            "'payment_captured', 'payment_failed', 'refunded', "
            "'cancelled', 'completed')",
            name="ck_order_status",
        ),
        CheckConstraint(
            "idempotency_key != ''",
            name="ck_order_idempotency_key_not_empty",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"<Order id={self.id} status={self.status} "
            f"total={self.total_cents} merchant={self.merchant_id}>"
        )


class OrderItem(Base):
    """An individual item within an Order."""

    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    order_id: Mapped[str] = mapped_column(
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
            name="ck_orderitem_quantity_positive",
        ),
        CheckConstraint(
            "unit_price_cents > 0",
            name="ck_orderitem_price_positive",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"<OrderItem order={self.order_id} product={self.product_id} "
            f"qty={self.quantity} price={self.unit_price_cents}>"
        )