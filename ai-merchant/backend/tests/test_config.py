"""Phase 1 tests - configuration."""

import pytest
from app.config import settings


def test_settings_load():
    """Config must load from environment without error."""
    assert settings is not None


def test_discount_cap_positive():
    """Policy cap must be positive."""
    assert settings.discount_cap_pct > 0


def test_min_order_value_positive():
    """Minimum order value must be positive."""
    assert settings.min_order_value_cents > 0


def test_max_order_value_positive():
    """Maximum order value must be positive."""
    assert settings.max_order_value_cents > 0