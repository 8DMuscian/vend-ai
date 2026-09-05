"""Pydantic schemas for the Negotiation Engine.

The negotiation engine orchestrates buyer-merchant interactions with
deterministic constraints and a bounded state machine.

Key constraints from AGENTS.md:
- Maximum 5 negotiation rounds
- Maximum 20 turns per session
- Discount cap: 20% per line item
- Minimum order value: ₹100 (10,000 paise)
- Maximum auto-approved order: ₹50,000 (5,000,000 paise)
- Quote validity: 30 minutes
- Inventory must be available
- LLM may propose, deterministic code disposes
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ─── Enums ────────────────────────────────────────────────────────────────────


class NegotiationStatus(str, Enum):
    """Negotiation session status."""

    ACTIVE = "active"
    QUOTE_PROPOSED = "quote_proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class MessageRole(str, Enum):
    """Message sender role."""

    BUYER = "buyer"
    MERCHANT = "merchant"
    SYSTEM = "system"


class QuoteStatus(str, Enum):
    """Quote status in the negotiation."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ACCEPTED = "accepted"


class PolicyVerdict(str, Enum):
    """Policy engine decision."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class ProposedBy(str, Enum):
    """Who proposed the quote."""

    BUYER = "buyer"
    MERCHANT = "merchant"
    SYSTEM = "system"


# ─── Input Schemas ────────────────────────────────────────────────────────────


class QuoteItemInput(BaseModel):
    """Input for creating a quote item."""

    product_id: str = Field(..., description="Product UUID")
    quantity: int = Field(..., ge=1, le=10000, description="Quantity requested")
    # unit_price_cents is determined server-side from product catalog


class QuoteProposal(BaseModel):
    """A quote proposal from either party."""

    items: List[QuoteItemInput] = Field(..., min_length=1, description="Items in the quote")
    discount_pct: Optional[float] = Field(
        default=None, ge=0, le=20, description="Requested discount percentage (0-20)"
    )
    message: Optional[str] = Field(
        default=None, max_length=2000, description="Optional message with the proposal"
    )

    @field_validator("discount_pct", mode="before")
    @classmethod
    def validate_discount(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0 or v > 20):
            raise ValueError("Discount must be between 0 and 20 percent")
        return v


class NegotiationStart(BaseModel):
    """Request to start a new negotiation."""

    buyer_id: str = Field(..., min_length=1, max_length=255, description="Buyer identifier")
    merchant_id: str = Field(..., min_length=1, max_length=36, description="Merchant UUID")
    initial_message: Optional[str] = Field(
        default=None, max_length=2000, description="Optional opening message"
    )


# ─── Output Schemas ───────────────────────────────────────────────────────────


class QuoteItemOutput(BaseModel):
    """A line item in a quote with server-side pricing."""

    product_id: str
    product_name: str
    quantity: int
    unit_price_cents: int = Field(..., description="Server-side price from catalog (paise)")
    line_total_cents: int = Field(..., description="quantity * unit_price_cents (paise)")


class QuoteOutput(BaseModel):
    """Complete quote with server-calculated totals."""

    id: str
    negotiation_id: str
    proposed_by: ProposedBy
    status: QuoteStatus
    items: List[QuoteItemOutput]
    subtotal_cents: int = Field(..., description="Sum of line totals before discount")
    discount_cents: int = Field(..., description="Discount amount (paise)")
    discount_pct: float = Field(..., description="Effective discount percentage")
    tax_cents: int = Field(..., description="Tax amount (paise)")
    total_cents: int = Field(..., description="Final total (paise)")
    currency: str
    valid_until: datetime
    policy_decision: Optional[PolicyVerdict] = None
    policy_reason: Optional[str] = None
    created_at: datetime

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class MessageOutput(BaseModel):
    """A message in the negotiation."""

    id: str
    negotiation_id: str
    role: MessageRole
    content: str
    tool_calls: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class NegotiationState(BaseModel):
    """Complete negotiation state for the agent."""

    negotiation_id: str
    buyer_id: str
    merchant_id: str
    status: NegotiationStatus
    round_number: int = Field(..., ge=0, le=5, description="Current round (0-5)")
    turn_count: int = Field(..., ge=0, le=20, description="Total messages exchanged")
    current_quote: Optional[QuoteOutput] = None
    quote_history: List[QuoteOutput] = Field(default_factory=list)
    messages: List[MessageOutput] = Field(default_factory=list)
    started_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class PolicyCheckResult(BaseModel):
    """Result of a deterministic policy check."""

    policy_id: str
    verdict: PolicyVerdict
    passed: bool
    reason: str
    details: dict = Field(default_factory=dict)


class PolicyEvaluationOutput(BaseModel):
    """Complete policy evaluation for a quote."""

    overall_verdict: PolicyVerdict
    checks: List[PolicyCheckResult]
    explanation: str


class NegotiationActionResult(BaseModel):
    """Result of a negotiation action (propose, counter, accept, reject)."""

    success: bool
    negotiation: NegotiationState
    quote: Optional[QuoteOutput] = None
    policy_evaluation: Optional[PolicyEvaluationOutput] = None
    error: Optional[str] = None
    termination_reason: Optional[str] = None


# ─── Constants ────────────────────────────────────────────────────────────────

MAX_ROUNDS = 5
MAX_TURNS = 20
QUOTE_VALIDITY_MINUTES = 30
DEFAULT_DISCOUNT_CAP_PCT = 20.0
MIN_ORDER_VALUE_CENTS = 10_000
MAX_AUTO_APPROVE_CENTS = 5_000_000