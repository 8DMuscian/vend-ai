"""Razorpay Integration Tests with Mocked Responses.

Tests the Razorpay test-mode integration with deterministic policy enforcement.
All payment requests pass through TransactionPolicy before reaching Razorpay.

NOTE: These tests mock the Razorpay client to avoid requiring actual API keys.
The TransactionPolicy enforcement is tested with real Policy objects.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import unittest.mock as mock

# Set environment variables for tests (mock credentials)
os.environ["RAZORPAY_KEY_ID"] = "test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "test_key_secret"

from app.services.razorpay_integration import (
    create_order,
    create_payment,
    verify_webhook_signature,
    PaymentStatus,
    PaymentStateMachine,
    handle_payment_success,
    handle_payment_failure,
    retry_payment,
    TransactionPolicy,
    approve_transaction,
)


# ---------------------------------------------------------------------------
# Fixtures - Policy and Credentials
# ---------------------------------------------------------------------------

@pytest.fixture
def default_policy():
    """Default TransactionPolicy for tests (₹50k max, 20% max discount)."""
    return TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)


@pytest.fixture
def strict_policy():
    """Strict TransactionPolicy for tests (₹10k max, 10% max discount)."""
    return TransactionPolicy(max_amount=1000000, max_discount_percent=10.0)


# ---------------------------------------------------------------------------
# Mock Razorpay Client Helper (per-test via unittest.mock)
# ---------------------------------------------------------------------------


class MockOrder:
    def __init__(self, order_id, amount, currency, receipt, status="created"):
        self.id = order_id
        self.amount = amount
        self.currency = currency
        self.receipt = receipt
        self.status = status


class MockPayment:
    def __init__(self, payment_id, amount, order_id, status="created"):
        self.id = payment_id
        self.amount = amount
        self.order_id = order_id
        self.status = status


class _OrderAPI:
    def __init__(self, client):
        self.client = client

    def create(self, data):
        return MockOrder(f"order_{id(data)}", data.get("amount", 0), data.get("currency", "INR"), None, "created")


class _PaymentAPI:
    def __init__(self, client):
        self.client = client

    def create(self, data):
        return MockPayment(f"pay_{id(data)}", data.get("amount", 0), data.get("order_id", ""), "created")


class MockClient:
    @property
    def order(self):
        return _OrderAPI(self)

    @property
    def payment(self):
        return _PaymentAPI(self)


def _make_mock_client():
    """Create a mock Razorpay client instance."""
    return MockClient()


# ---------------------------------------------------------------------------
# TransactionPolicy Tests (Integration with Policy)
# ---------------------------------------------------------------------------

class TestApproveTransactionIntegration:
    """Test approve_transaction with various policy configurations."""

    def test_approve_within_limits(self, default_policy):
        """Transaction within all limits should APPROVE."""
        decision, reason = approve_transaction(
            amount_paise=1500,
            discount_percent=5.0,
            reason="Test purchase",
            policy=default_policy,
        )
        assert decision == "APPROVE"
        assert "approved" in reason.lower()

    def test_block_exceeds_max_amount(self, default_policy):
        """Transaction exceeding max_amount should BLOCK."""
        decision, reason = approve_transaction(
            amount_paise=6000000,  # ₹60k exceeds ₹50k limit
            discount_percent=5.0,
            reason="Over amount test",
            policy=default_policy,
        )
        assert decision == "BLOCK"

    def test_block_exceeds_max_discount(self, default_policy):
        """Transaction exceeding max_discount_percent should BLOCK."""
        decision, reason = approve_transaction(
            amount_paise=1500,
            discount_percent=25.0,  # 25% exceeds 20% limit
            reason="High discount test",
            policy=default_policy,
        )
        assert decision == "BLOCK"

    def test_block_no_reason_above_threshold(self):
        """Transaction above require_confirmation_above needs a reason."""
        policy = TransactionPolicy(
            max_amount=5000000,
            require_confirmation_above=1000,  # ₹10 threshold
        )
        decision, reason = approve_transaction(
            amount_paise=5000,  # ₹50, above ₹10 threshold
            discount_percent=5.0,
            reason=None,  # No reason provided
            policy=policy,
        )
        assert decision == "BLOCK"

    def test_approve_below_threshold_no_reason_needed(self):
        """Transaction below threshold doesn't need a reason."""
        policy = TransactionPolicy(
            max_amount=5000000,
            require_confirmation_above=1000,
        )
        decision, reason = approve_transaction(
            amount_paise=500,  # ₹5, below ₹10 threshold
            discount_percent=5.0,
            reason=None,
            policy=policy,
        )
        assert decision == "APPROVE"


# ---------------------------------------------------------------------------
# Razorpay Integration Tests (with unittest.mock.patch)
# ---------------------------------------------------------------------------

class TestRazorpayIntegration:
    """Test Razorpay integration with policy enforcement.

    Each test independently mocks the Razorpay client using unittest.mock.patch.
    """

    def test_create_order_passes_policy(self, default_policy):
        """Create order should pass through TransactionPolicy."""
        mock_client = _make_mock_client()

        result = create_order(
            amount_paise=1500,  # ₹15
            buyer_id="buyer_001",
            purpose="Test Wireless Headphones",
            policy=default_policy,
            client=mock_client,
        )
        assert result[0] == "APPROVE", f"Expected APPROVE, got {result[0]}: {result[1]}"
        assert hasattr(result[1], "id") or isinstance(result[1], dict)

    def test_create_order_blocks_over_amount(self, strict_policy, monkeypatch):
        """Create order should block when amount exceeds policy limit."""
        mock_client = _make_mock_client()

        monkeypatch.setattr(
            "app.services.razorpay_integration.get_razorpay_client",
            mock_client,
        )

        result = create_order(
            amount_paise=2000000,  # ₹200 exceeds ₹10k strict limit
            buyer_id="buyer_002",
            purpose="Expensive item",
            policy=strict_policy,
        )
        assert result[0] == "BLOCK"
        assert "blocked_by" in result[1]
        assert result[1]["blocked_by"] == "TransactionPolicy"

    def test_create_order_below_threshold(self, default_policy):
        """Create order below policy limit should succeed."""
        mock_client = _make_mock_client()

        result = create_order(
            amount_paise=500,  # ₹5 well within ₹50k limit
            buyer_id="buyer_003",
            purpose="Cheap item",
            policy=default_policy,
            client=mock_client,
        )
        assert result[0] == "APPROVE"

    def test_create_payment_passes_policy(self, default_policy):
        """Create payment should pass through TransactionPolicy."""
        mock_client = _make_mock_client()

        # First create an order
        order_result = create_order(
            amount_paise=1500,
            buyer_id="buyer_004",
            purpose="Test payment",
            policy=default_policy,
            client=mock_client,
        )
        assert order_result[0] == "APPROVE", f"Order failed: {order_result}"
        order_id = getattr(order_result[1], "id", None) or "order_test123"

        # Then create payment
        pay_result = create_payment(
            order_id=order_id,
            amount_paise=1500,
            buyer_id="buyer_004",
            policy=default_policy,
            client=mock_client,
        )
        assert pay_result[0] == "APPROVE", f"Payment failed: {pay_result}"

    def test_create_payment_blocks_over_amount(self, strict_policy, monkeypatch):
        """Create payment should block when amount exceeds limit."""
        mock_client = _make_mock_client()

        monkeypatch.setattr(
            "app.services.razorpay_integration.get_razorpay_client",
            mock_client,
        )

        result = create_payment(
            order_id="order_test123",
            amount_paise=5000000,  # ₹50 exceeds ₹10k strict limit
            buyer_id="buyer_005",
            policy=strict_policy,
        )
        assert result[0] == "BLOCK"
        assert result[1]["blocked_by"] == "TransactionPolicy"


# ---------------------------------------------------------------------------
# Webhook Verification Tests
# ---------------------------------------------------------------------------

class TestWebhookVerification:
    """Test webhook signature verification."""

    def test_valid_signature(self):
        """Valid webhook signature should verify."""
        payload = b'{"status":"payment.captured"}'
        # Generate valid HMAC signature
        import hmac, hashlib

        key_secret = "test_key_secret"
        expected_sig = hmac.new(
            key_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        signature = f"|{expected_sig}"

        is_valid = verify_webhook_signature(payload, signature, key_secret)
        assert is_valid is True

    def test_invalid_signature(self):
        """Invalid webhook signature should fail."""
        payload = b'{"status":"payment.captured"}'
        is_valid = verify_webhook_signature(payload, "|invalid_signature", "test_key_secret")
        assert is_valid is False

    def test_wrong_key_secret(self):
        """Wrong key secret should fail verification."""
        payload = b'{"status":"payment.captured"}'
        is_valid = verify_webhook_signature(payload, "|invalid_signature", "wrong_key_secret")
        assert is_valid is False


# ---------------------------------------------------------------------------
# Payment State Machine Tests
# ---------------------------------------------------------------------------

class TestPaymentStateMachine:
    """Test deterministic payment state machine."""

    def test_created_state(self):
        """New payment starts in CREATED state."""
        sm = PaymentStateMachine("pay_001")
        assert sm.state == PaymentStatus.CREATED
        assert not sm.is_terminal
        assert sm.is_success is False
        assert sm.is_failure is False

    def test_created_to_authorized_transition(self):
        """Transition from CREATED to AUTHORIZED is valid."""
        sm = PaymentStateMachine("pay_002")
        assert sm.can_transition_to(PaymentStatus.AUTHORIZED)
        assert sm.transition_to(PaymentStatus.AUTHORIZED)

    def test_authorized_to_captured_transition(self):
        """Transition from AUTHORIZED to CAPTURED is valid."""
        sm = PaymentStateMachine("pay_003")
        sm.transition_to(PaymentStatus.AUTHORIZED)
        assert sm.can_transition_to(PaymentStatus.CAPTURED)
        assert sm.transition_to(PaymentStatus.CAPTURED)

    def test_captured_is_terminal(self):
        """CAPTURED state is terminal."""
        sm = PaymentStateMachine("pay_004")
        sm.transition_to(PaymentStatus.AUTHORIZED)
        sm.transition_to(PaymentStatus.CAPTURED)
        assert sm.is_terminal

    def test_failed_is_terminal(self):
        """FAILED state is terminal."""
        sm = PaymentStateMachine("pay_005")
        sm.transition_to(PaymentStatus.FAILED)
        assert sm.is_terminal

    def test_retry_logic(self):
        """Retry logic for transient errors."""
        sm = PaymentStateMachine("pay_006")
        # Simulate transient network error
        exc = Exception("Connection timeout during payment")
        should_retry = sm.should_retry(exc)
        assert should_retry is True  # Within retry count and error type


# ---------------------------------------------------------------------------
# Success / Failure Handling Tests
# ---------------------------------------------------------------------------

class TestPaymentHandling:
    """Test success and failure handling."""

    def test_handle_payment_success(self, default_policy):
        """Handle successful payment completion."""
        result = handle_payment_success(
            payment_id="pay_success_001",
            amount_paise=1500,
            buyer_id="buyer_007",
            policy=default_policy,
        )
        assert result["status"] == "SUCCESS"
        assert result["payment_id"] == "pay_success_001"
        assert result["transaction_completed"] is True

    def test_handle_payment_success_policy_block(self, strict_policy):
        """Handle payment success that's blocked by policy (edge case)."""
        result = handle_payment_success(
            payment_id="pay_block_001",
            amount_paise=5000000,  # ₹50 exceeds ₹10k limit
            buyer_id="buyer_008",
            policy=strict_policy,
        )
        # Should be blocked since amount exceeds limit
        assert result["status"] == "BLOCKED_POST_CAPTURE"

    def test_handle_payment_failure(self, default_policy):
        """Handle payment failure from Razorpay."""
        result = handle_payment_failure(
            payment_id="pay_fail_001",
            error_code="INSUFFICIENT_FUNDS",
            error_description="Insufficient balance in payment method",
            buyer_id="buyer_009",
            policy=default_policy,
        )
        assert result["status"] in ("failed", "retry_initiated")

    def test_handle_payment_failure_no_retry(self, strict_policy):
        """Handle failure that shouldn't retry."""
        result = handle_payment_failure(
            payment_id="pay_no_retry_001",
            error_code="VALIDATION_ERROR",
            error_description="Invalid payment parameters",
            buyer_id="buyer_010",
            policy=strict_policy,
            retry=False,
        )
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# Retry Handling Tests
# ---------------------------------------------------------------------------

class TestRetryHandling:
    """Test payment retry logic."""

    def test_retry_payment_passes_policy(self, default_policy, monkeypatch):
        """Retry payment should re-verify policy before attempting."""
        mock_client = _make_mock_client()

        monkeypatch.setattr(
            "app.services.razorpay_integration.get_razorpay_client",
            mock_client,
        )

        result = retry_payment(
            payment_id="pay_retry_001",
            buyer_id="buyer_011",
            amount_paise=1500,
            order_id="order_original_001",
            policy=default_policy,
        )
        assert result["status"] in ("RETRY_ATTEMPTED", "RETRY_FAILED")

    def test_retry_policy_blocked(self, strict_policy, monkeypatch):
        """Retry should be blocked if amount exceeds policy limit."""
        mock_client = _make_mock_client()

        monkeypatch.setattr(
            "app.services.razorpay_integration.get_razorpay_client",
            mock_client,
        )

        result = retry_payment(
            payment_id="pay_blocked_retry_001",
            buyer_id="buyer_012",
            amount_paise=2000000,  # ₹200 exceeds ₹10k limit
            order_id="order_original_001",
            policy=strict_policy,
        )
        assert result["status"] == "BLOCKED"
        assert "reason" in result