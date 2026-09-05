"""Phase 1 tests - SQLAlchemy models."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Product, Merchant, Base