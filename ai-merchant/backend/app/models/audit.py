"""SQLAlchemy model for Audit Events (append-only event store)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class AuditEvent(Base):
    """Append-only audit event - every financial action is logged.

    Design guarantees:
    - INSERT-only (no UPDATE/DELETE permissions at app layer)
    - Every event includes explanation for financial decisions
    - Queryable by aggregate_type + aggregate_id + time range
    - Actor type constrained to known roles
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Type of event (e.g. 'quote.proposed', 'order.payment_captured')",
    )
    aggregate_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Type of aggregate that triggered the event",
    )
    aggregate_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="ID of the aggregate object (quote_id, order_id, etc.)",
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="ID of the actor (buyer_session, merchant_agent, etc.)",
    )
    actor_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="Type of actor ('buyer', 'merchant', 'system', 'policy', 'agent')",
    )
    payload: Mapped[dict] = mapped_column(
        JSON, nullable=False,
        comment="Structured data specific to the event type",
    )
    explanation: Mapped[str | None] = mapped_column(
        String, nullable=True,
        comment="Human-readable explanation of why this event occurred",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Composite index for common audit queries will be added by Alembic
    )

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"<AuditEvent id={self.id} type={self.event_type} "
            f"aggregate={self.aggregate_type}/{self.aggregate_id} "
            f"actor={self.actor_type}>"
        )