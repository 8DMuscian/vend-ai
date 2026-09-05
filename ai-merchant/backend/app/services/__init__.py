"""Business logic services for Vend.ai.

Services contain deterministic business logic that:
- Validates inputs against policy rules
- Performs calculations server-side
- Returns structured Pydantic outputs

Services never:
- Call LLM APIs directly
- Execute payment operations
- Modify transaction limits
"""

from .growth_brain import GrowthBrain
from .negotiation_state import (
    transition,
    is_terminal,
    calculate_valid_until,
    is_quote_expired,
    next_round,
    next_turn,
    StateMachineError,
    NegotiationTerminated,
    MAX_ROUNDS,
    MAX_TURNS,
)
from .negotiation_validator import (
    evaluate_quote_proposal,
    calculate_quote_totals,
    check_discount_cap,
    check_inventory,
    check_price_floor,
    check_min_order_value,
    check_max_order_value,
    check_buyer_budget,
)
from .negotiation_service import NegotiationService
from .transaction_guard import TransactionPolicy, approve_transaction

__all__ = [
    "GrowthBrain",
    "transition",
    "is_terminal",
    "calculate_valid_until",
    "is_quote_expired",
    "next_round",
    "next_turn",
    "StateMachineError",
    "NegotiationTerminated",
    "MAX_ROUNDS",
    "MAX_TURNS",
    "evaluate_quote_proposal",
    "calculate_quote_totals",
    "check_discount_cap",
    "check_inventory",
    "check_price_floor",
    "check_min_order_value",
    "check_max_order_value",
    "check_buyer_budget",
    "NegotiationService",
    "TransactionPolicy",
    "approve_transaction",
]