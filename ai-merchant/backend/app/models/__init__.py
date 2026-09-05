"""SQLAlchemy model package - ORM models for Vend.ai.

This package defines all database models using SQLAlchemy 2.0 ORM.
The Base class is used by all models for ORM mapping.

Naming convention for consistent constraint names across databases
"""
from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(column_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=naming_convention))

# Import all models so they are registered with Base
# noqa: F401,F811
from . import product  # noqa: F401
from . import merchant  # noqa: F401
from . import negotiation  # noqa: F401
from . import quote  # noqa: F401
from . import order  # noqa: F401
from . import audit  # noqa: F401

# Re-export key classes at the package level so they can be imported
# directly from 'app.models' (e.g. `from app.models import Product`)
Product = product.Product  # noqa: F811
Merchant = merchant.Merchant  # noqa: F811
Negotiation = negotiation.Negotiation  # noqa: F811
Quote = quote.Quote  # noqa: F811
QuoteItem = quote.QuoteItem  # noqa: F811
Order = order.Order  # noqa: F811
OrderItem = order.OrderItem  # noqa: F811
AuditEvent = audit.AuditEvent  # noqa: F811