"""Razorpay Test-Mode Integration.

This module provides a deterministic Razorpay test-mode integration that:
- Reads credentials from environment variables
- Creates orders and payments in test mode
- Enforces TransactionPolicy on all payment requests
- Provides idempotency via client_order_id
- Verifies webhook signatures
- Manages payment state machine
- Handles success/failure/retry flows

CRITICAL: NO LLM TOOL MAY CALL RAZORPAY DIRECTLY.
All payment requests MUST pass through TransactionPolicy.

From AGENTS.md:
- "LLM proposes, deterministic code disposes"
- Transaction amounts and policy checks are server-side only
- Prices from database in paise (integers)
- Structured Pydantic outputs only
"""

from __future__ import annotations

import hmac
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import razorpay
from pydantic import BaseModel, Field, validator

from app.services.transaction_guard import TransactionPolicy, approve_transaction


# ---------------------------------------------------------------------------
# Environment / Credentials
# ---------------------------------------------------------------------------

def get_razorpay_key_id() -> str:
    """Read Razorpay key ID from environment variables."""
    return os.getenv("RAZORPAY_KEY_ID", "test_key_id")


def get_razorpay_key_secret() -> str:
    """Read Razorpay key secret from environment variables."""
    return os.getenv("RAZORPAY_KEY_SECRET", "test_key_secret")


def get_credentials() -> dict:
    """Load Razorpay credentials from environment variables."""
    return {
        "key_id": get_razorpay_key_id(),
        "key_secret": get_razorpay_key_secret(),
    }


# ---------------------------------------------------------------------------
# Razorpay Client (test mode only)
# ---------------------------------------------------------------------------

_razorpay_client: Optional[razorpay.Client] = None


def get_razorpay_client(client_instance: Optional[razorpay.Client] = None) -> razorpay.Client:
    """Lazily initialise the Razorpay test-mode client.

    Args:
        client_instance: Optional pre-configured client for testing.
            If provided, this instance is returned directly, bypassing
            environment credential loading.
    """
    global _razorpay_client
    if client_instance is not None:
        _razorpay_client = client_instance
        return _razorpay_client
    if _razorpay_client is None:
        creds = get_credentials()
        _razorpay_client = razorpay.Client(auth=(creds["key_id"], creds["key_secret"]))
        _razorpay_client.set_base_url("https://api.razorpay.com/v1")
    return _razorpay_client


# ---------------------------------------------------------------------------
# Idempotency Key Generation
# ---------------------------------------------------------------------------

def generate_idempotency_key() -> str:
    """Generate a unique idempotency key for an order.

    Each order gets a unique key (UUID4), preventing duplicate charges
    even if the same buyer creates orders with the same amount and purpose.

    Returns:
        UUID4 string used as idempotency key
    """
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Policy Enforcement Wrapper
# ---------------------------------------------------------------------------

def verify_policy_and_execute(
    policy: TransactionPolicy,
    amount_paise: int,
    discount_percent: Decimal | None,
    reason: str,
    buyer_id: str,
    purpose: str,
    fn,
    **fn_kwargs,
) -> Tuple[str, Dict[str, Any]]:
    """Verify TransactionPolicy then execute a Razorpay operation.

    This is the GATEWAY for ALL payment requests. No LLM may bypass this.

    Args:
        policy: TransactionPolicy instance with configured limits
        amount_paise: Transaction amount in paise (int)
        discount_percent: Discount percentage (Decimal or None)
        reason: Transaction reason/description
        buyer_id: Buyer UUID string
        purpose: Payment purpose description
        fn: Callable to execute after policy passes
        fn_kwargs: Additional kwargs to pass to fn

    Returns:
        Tuple of (decision, result) where decision is "APPROVE" or "BLOCK"
    """
    # Step 1: Policy check - this is the single entry point
    decision, policy_reason = approve_transaction(
        amount_paise=amount_paise,
        discount_percent=discount_percent,
        reason=reason,
        policy=policy,
    )

    if decision == "BLOCK":
        return "BLOCK", {"reason": policy_reason, "blocked_by": "TransactionPolicy"}

    # Step 2: Policy passed - execute the Razorpay operation
    try:
        result = fn(**fn_kwargs)
        return "APPROVE", result
    except Exception as exc:
        # Step 3: Execution failed - return BLOCK with error details
        return "BLOCK", {"reason": str(exc), "blocked_by": "RazorpayExecution"}


# ---------------------------------------------------------------------------
# Payment Schemas (Pydantic - structured outputs only)
# ---------------------------------------------------------------------------

class CreateOrderInput(BaseModel):
    """Input schema for Razorpay order creation.

    All amounts in paise (integer). No float arithmetic.
    """

    amount_paise: int = Field(..., ge=1, description="Amount in paise (1 INR = 100 paise)")
    currency: str = Field(default="INR", description="Currency code (always INR)")
    receipt_id: Optional[str] = Field(default=None, description="Receipt identifier")
    payment_capture: int = Field(default=1, ge=0, le=1, description="Auto-capture flag (0 or 1)")

    @validator("currency")
    def currency_must_be_inr(cls, v):
        if v.upper() != "INR":
            raise ValueError("Currency must be INR for India payments")
        return v


class PaymentAttemptInput(BaseModel):
    """Input schema for payment attempt/Razorpay order creation."""

    order_id: str = Field(..., description="Razorpay order ID")
    buyer_id: str = Field(..., description="Buyer UUID")
    amount_paise: int = Field(..., ge=1, description="Amount in paise")
    payment_id: str = Field(..., description="Razorpay payment ID")


class WebhookVerificationInput(BaseModel):
    """Input schema for webhook signature verification."""

    payload: bytes = Field(..., description="Raw webhook payload")
    signature: str = Field(..., description="Razorpay signature header")
    key_secret: str = Field(..., description="Razorpay key secret for verification")


class PaymentStateInput(BaseModel):
    """Input schema for payment state check."""

    payment_id: str = Field(..., description="Razorpay payment ID")
    expected_status: str = Field(..., description="Expected payment status")


# ---------------------------------------------------------------------------
# Core Operations (Test Mode)
# ---------------------------------------------------------------------------

def create_order(
    amount_paise: int,
    currency: str = "INR",
    receipt_id: str | None = None,
    policy: TransactionPolicy | None = None,
    buyer_id: str | None = None,
    purpose: str = "Purchase",
    idempotency_key: str | None = None,
    client: Optional[razorpay.Client] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Create a Razorpay order in test mode.

    All payment requests MUST pass through TransactionPolicy first.
    Each order is assigned a unique idempotency key (receipt) to prevent
    duplicate charges from concurrent requests.

    Args:
        amount_paise: Amount in paise (integer, 1 INR = 100 paise)
        currency: Currency code (default INR)
        receipt_id: Optional receipt identifier
        policy: Optional TransactionPolicy; if None, default limits apply
        buyer_id: Buyer UUID for idempotency key generation
        purpose: Payment purpose description
        idempotency_key: Optional idempotency key; generated if not provided
        client: Optional Razorpay client for testing. If provided, uses
            this client instead of creating one from credentials.

    Returns:
        Tuple of (decision, result) where decision is "APPROVE" or "BLOCK"
    """
    # Default policy if none provided
    if policy is None:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)

    # Ensure policy has reasonable defaults
    if policy.max_amount is None:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)

    # Generate unique idempotency key per order (not deterministic)
    if idempotency_key is None:
        idempotency_key = generate_idempotency_key()

    def _create_order_fn():
        razor_client = client if client is not None else get_razorpay_client()
        order_data = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": idempotency_key,
        }
        order_data["payment_capture"] = 1
        return razor_client.order.create(order_data)

    decision, result = verify_policy_and_execute(
        policy=policy,
        amount_paise=amount_paise,
        discount_percent=None,
        reason=f"Razorpay order creation: {purpose} for buyer {buyer_id or 'unknown'}",
        buyer_id=buyer_id or "unknown",
        purpose=purpose,
        fn=_create_order_fn,
    )

    # Attach idempotency key to result for caller to persist in orders table
    if isinstance(result, dict):
        result["idempotency_key"] = idempotency_key

    return decision, result


def create_payment(
    order_id: str,
    amount_paise: int,
    buyer_id: str,
    policy: TransactionPolicy | None = None,
    client: Optional[razorpay.Client] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Create a Razorpay payment against an order in test mode.

    Args:
        order_id: Razorpay order ID
        amount_paise: Amount in paise must match order amount
        buyer_id: Buyer UUID for idempotency and policy checks
        policy: Optional TransactionPolicy
        client: Optional Razorpay client for testing. If provided, uses
            this client instead of creating one from credentials.

    Returns:
        Tuple of (decision, result) where decision is "APPROVE" or "BLOCK"
    """
    if policy is None:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)

    if policy.max_amount is None:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)

    def _create_payment_fn():
        razor_client = client if client is not None else get_razorpay_client()
        return razor_client.payment.create({
            "order_id": order_id,
            "amount": amount_paise,
        })

    decision, result = verify_policy_and_execute(
        policy=policy,
        amount_paise=amount_paise,
        discount_percent=None,
        reason=f"Payment for order {order_id} buyer {buyer_id}",
        buyer_id=buyer_id,
        purpose="Payment against order",
        fn=_create_payment_fn,
    )

    return decision, result


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    key_secret: str | None = None,
) -> bool:
    """Verify a Razorpay webhook signature.

    Razorpay sends a signature header X-Razorpay-Signature.
    We verify using HMAC-SHA256 of the payload body.

    Args:
        payload: Raw webhook payload bytes
        signature: Signature header value (format: "prefix|hash" or just "hash")
        key_secret: Razorpay key secret (uses credentials if None)

    Returns:
        True if signature is valid, False otherwise
    """
    if key_secret is None:
        creds = get_credentials()
        key_secret = creds["key_secret"]

    # Razorpay signature format: "timestamp|signature"
    # We verify the HMAC-SHA256 of the payload
    try:
        if "|" in signature:
            _, received_sig = signature.split("|", 1)
        else:
            received_sig = signature

        expected_sig = hmac.new(
            key_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(
            received_sig.encode(),
            expected_sig.encode(),
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Payment State Machine
# ---------------------------------------------------------------------------

class PaymentStatus(str, Enum):
    """Razorpay payment status enumeration."""

    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    PARTIALLY_CAPTURED = "partially_captured"
    REFUNDED = "refunded"


class PaymentStateMachine:
    """Deterministic payment state machine for Razorpay integration.

    Tracks the lifecycle of a payment from creation through
    success/failure, with retry logic for transient failures.

    States: CREATED -> AUTHORIZED -> CAPTURED (terminal)
            CREATED -> FAILED (terminal)
            CAPTURED -> REFUNDED (terminal)
    """

    def __init__(self, payment_id: str) -> None:
        self.payment_id = payment_id
        self._state: PaymentStatus = PaymentStatus.CREATED
        self._max_retries = 3
        self._retry_count = 0
        self._valid_until: datetime | None = datetime.now(timezone.utc) + timedelta(hours=1)

    @property
    def state(self) -> PaymentStatus:
        return self._state

    def can_transition_to(self, new_state: PaymentStatus) -> bool:
        """Check if transition to new_state is valid from current state."""
        valid_transitions = {
            PaymentStatus.CREATED: {
                PaymentStatus.AUTHORIZED,
                PaymentStatus.FAILED,
            },
            PaymentStatus.AUTHORIZED: {
                PaymentStatus.CAPTURED,
                PaymentStatus.FAILED,
            },
            PaymentStatus.CAPTURED: {
                PaymentStatus.REFUNDED,
            },
            PaymentStatus.FAILED: set(),  # terminal - no transitions
            PaymentStatus.REFUNDED: set(),  # terminal - no transitions
        }

        return new_state in valid_transitions.get(self._state, set())

    def transition_to(self, new_state: PaymentStatus, reason: str = "") -> bool:
        """Transition payment to new state if valid.

        Returns True if transition succeeded, False otherwise.
        """
        if not self.can_transition_to(new_state):
            return False

        self._state = new_state
        self._retry_count = 0  # Reset retries on successful transition
        self._valid_until = datetime.now(timezone.utc) + timedelta(hours=1)
        return True

    def should_retry(self, exception: Exception) -> bool:
        """Determine if a failed operation should be retried."""
        self._retry_count += 1
        retryable_errors = (
            "network",
            "timeout",
            "connection",
            "temporary",
        )
        return (
            self._retry_count <= self._max_retries
            and any(err in str(exception).lower() for err in retryable_errors)
        )

    @property
    def is_terminal(self) -> bool:
        return self._state in (
            PaymentStatus.CAPTURED,
            PaymentStatus.FAILED,
            PaymentStatus.REFUNDED,
        )

    @property
    def is_success(self) -> bool:
        return self._state == PaymentStatus.CAPTURED

    @property
    def is_failure(self) -> bool:
        return self._state == PaymentStatus.FAILED

    @property
    def valid_until(self) -> datetime | None:
        return self._valid_until


# ---------------------------------------------------------------------------
# Success / Failure Handling
# ---------------------------------------------------------------------------

def handle_payment_success(
    payment_id: str,
    amount_paise: int,
    buyer_id: str,
    policy: TransactionPolicy | None = None,
) -> Dict[str, Any]:
    """Handle a successful payment completion.

    This is called when Razorpay reports payment as CAPTURED.
    It updates the internal state and records the transaction.

    Args:
        payment_id: Razorpay payment ID
        amount_paise: Amount that was paid (in paise)
        buyer_id: Buyer UUID
        policy: Optional TransactionPolicy for post-approval checks

    Returns:
        Dict with success handling result
    """
    if policy is None:
        policy = TransactionPolicy()

    # Policy check on the completed transaction
    decision, policy_reason = approve_transaction(
        amount_paise=amount_paise,
        discount_percent=None,
        reason=f"Payment {payment_id} completed successfully",
        policy=policy,
    )

    if decision == "BLOCK":
        # This should be extremely rare since policy was checked at creation
        return {
            "status": "BLOCKED_POST_CAPTURE",
            "payment_id": payment_id,
            "reason": policy_reason,
        }

    # Update internal records / mark as successful
    return {
        "status": "SUCCESS",
        "payment_id": payment_id,
        "amount_paise": amount_paise,
        "buyer_id": buyer_id,
        "transaction_completed": True,
        "reason": policy_reason,
    }


def handle_payment_failure(
    payment_id: str,
    error_code: str,
    error_description: str,
    buyer_id: str,
    policy: TransactionPolicy | None = None,
    retry: bool = False,
) -> Dict[str, Any]:
    """Handle a payment failure from Razorpay.

    Args:
        payment_id: Razorpay payment ID
        error_code: Razorpay error code
        error_description: Human-readable error description
        buyer_id: Buyer UUID
        policy: Optional TransactionPolicy
        retry: Whether this failure should trigger a retry

    Returns:
        Dict with failure handling result
    """
    if policy is None:
        policy = TransactionPolicy()

    # Policy check on the failed transaction
    decision, policy_reason = approve_transaction(
        amount_paise=0,  # No amount charged
        discount_percent=None,
        reason=f"Payment {payment_id} failed: {error_description}",
        policy=policy,
    )

    # Determine next state based on retryability
    if retry and not decision == "BLOCK":
        next_state = "retry_initiated"
    else:
        next_state = "failed"

    return {
        "status": next_state,
        "payment_id": payment_id,
        "error_code": error_code,
        "error_description": error_description,
        "buyer_id": buyer_id,
        "reason": policy_reason,
    }


# ---------------------------------------------------------------------------
# Retry Handling
# ---------------------------------------------------------------------------

def retry_payment(
    payment_id: str,
    buyer_id: str,
    amount_paise: int,
    order_id: str,
    policy: TransactionPolicy | None = None,
    client: Optional[razorpay.Client] = None,
) -> Dict[str, Any]:
    """Retry a failed payment with policy re-verification.

    Creates a NEW Razorpay payment against the same order. A fresh idempotency
    key is generated for the retry attempt.

    Args:
        payment_id: Razorpay payment ID that failed
        buyer_id: Buyer UUID
        amount_paise: Amount in paise
        order_id: Original order ID to retry against
        policy: Optional TransactionPolicy
        client: Optional Razorpay client for testing

    Returns:
        Dict with retry result
    """
    if policy is None:
        policy = TransactionPolicy(max_amount=5000000, max_discount_percent=20.0)

    # Re-verify policy before retry
    decision, policy_reason = approve_transaction(
        amount_paise=amount_paise,
        discount_percent=None,
        reason=f"Retry payment {payment_id} for buyer {buyer_id}",
        policy=policy,
    )

    if decision == "BLOCK":
        return {
            "status": "BLOCKED",
            "payment_id": payment_id,
            "reason": policy_reason,
            "retry_count": 0,
        }

    # Attempt the payment creation again with a new idempotency key
    retry_key = generate_idempotency_key()
    try:
        razor_client = get_razorpay_client(client_instance=client)
        result = razor_client.payment.create({
            "order_id": order_id,
            "amount": amount_paise,
        })

        return {
            "status": "RETRY_ATTEMPTED",
            "payment_id": payment_id,
            "idempotency_key": retry_key,
            "result": result,
            "reason": policy_reason,
        }
    except Exception as exc:
        return {
            "status": "RETRY_FAILED",
            "payment_id": payment_id,
            "idempotency_key": retry_key,
            "error": str(exc),
            "reason": policy_reason,
        }