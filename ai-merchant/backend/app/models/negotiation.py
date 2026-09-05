"""SQLAlchemy models for Negotiation Sessions and Messages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, CheckConstraint, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class Negotiation(Base):
    """A negotiation session between a buyer and a merchant."""

    __tablename__ = "negotiations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    buyer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_id: Mapped[str] = mapped_column(
        String(36), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active",
    )
    round_number: Mapped[int] = mapped_column(
        default=0, nullable=False,
        comment="Current negotiation round (0-5)"
    )
    turn_count: Mapped[int] = mapped_column(
        default=0, nullable=False,
        comment="Total messages exchanged (0-20)"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'quote_proposed', 'accepted', 'rejected', "
            "'expired', 'cancelled')",
            name="ck_negotiation_status",
        ),
        CheckConstraint(
            "round_number >= 0 AND round_number <= 5",
            name="ck_negotiation_round_range",
        ),
        CheckConstraint(
            "turn_count >= 0 AND turn_count <= 20",
            name="ck_negotiation_turn_range",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return f"<Negotiation id={self.id} status={self.status}>"


class Message(Base):
    """A message within a negotiation session."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    negotiation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    tool_calls: Mapped[dict] = mapped_column(JSON, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('buyer', 'merchant', 'system')",
            name="ck_message_role",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return f"<Message id={self.id} role={self.role} negotiation={self.negotiation_id}>"