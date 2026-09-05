"""SQLAlchemy model for Merchants."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, JSON, Boolean, DateTime, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class Merchant(Base):
    """Merchant who owns a product catalog.

    Each merchant has an API key hash for authentication and a config
    JSON field for merchant-specific settings (discount caps, etc.).
    """

    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
        comment="BCrypt/Argon2 hash of merchant API key",
    )
    config: Mapped[dict] = mapped_column(
        JSON, default={}, nullable=False,
        comment="Merchant-specific configuration (discount caps, etc.)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Soft delete flag",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "name != ''",
            name="ck_merchant_name_not_empty",
        ),
    )

    def __repr__(self) -> str:  # noqa: D401
        return f"<Merchant id={self.id} name={self.name}>"