"""SQLAlchemy model for Products - AI-readable merchant catalog."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy import String, Integer, Boolean, DateTime, JSON, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


def _uuid_str() -> str:
    """Return a UUID string for primary key default."""
    return str(uuid.uuid4())


class Product(Base):
    """Product in the merchant catalog - AI-readable via structured ai_schema.

    Every product must have a structured ai_schema JSON field that
    the LLM agent can reason about. Prices are always in paise (cents)
    as integers - never floats.

    Attributes from AGENTS.md / architecture requirements:
    - categories: list of category strings
    - inventory: stock quantity
    - compatible_products: list of product IDs this pairs with
    - use_cases: list of use case strings (e.g. "gym", "commute", "work")
    """

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str, nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String, nullable=True,
    )
    ai_schema: Mapped[dict] = mapped_column(
        JSON, nullable=False,
        comment="Structured data for LLM consumption (category, tags, specs, etc.)",
    )
    price_cents: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Price in paise (1 INR = 100 paise). Integer to avoid float issues.",
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR",
    )
    stock_qty: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Inventory stock quantity. Check: >= 0.",
    )
    compatible_product_ids: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list,
        comment="List of product IDs compatible with this product",
    )
    use_cases: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list,
        comment="List of use case strings (e.g. 'gym', 'commute', 'work')",
    )
    categories: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list,
        comment="List of category strings this product belongs to",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Soft delete flag - inactive products are hidden from catalog",
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
            "price_cents > 0",
            name="ck_product_price_positive",
        ),
        CheckConstraint(
            "stock_qty >= 0",
            name="ck_product_stock_nonnegative",
        ),
    )

    # Relationships
    # Note: merchant relationship requires primaryjoin since merchant_id has no FK constraint
    # in the dev SQLite setup. Use lazy loading to avoid initialization issues.
    # merchant: Mapped["Merchant"] = relationship(
    #     "Merchant", back_populates="products", primaryjoin="Product.merchant_id == Merchant.id"
    # )

    def __repr__(self) -> str:  # noqa: D401
        return f"<Product id={self.id} name={self.name} price={self.price_cents}>"

    # Convenience properties for API responses

    @property
    def category_list(self) -> List[str]:
        return self.categories

    @property
    def use_case_list(self) -> List[str]:
        return self.use_cases

    @property
    def compatibility_list(self) -> List[str]:
        return self.compatible_product_ids

    @property
    def inventory_status(self) -> Dict[str, int | bool]:
        return {
            "stock_qty": self.stock_qty,
            "is_in_stock": self.stock_qty > 0,
            "is_out_of_stock": self.stock_qty == 0,
        }