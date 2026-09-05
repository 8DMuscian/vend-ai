"""Negotiation Service - Orchestrates the buyer-merchant negotiation flow.

This service coordinates:
1. State machine transitions
2. Policy validation
3. Quote creation and management
4. Message logging
5. Audit trail

The LLM agents propose actions, this service validates and executes them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.negotiation import Negotiation, Message
from app.models.quote import Quote, QuoteItem
from app.models.product import Product
from app.services.audit_logger import log_audit_event
from app.schemas.negotiation import (
    NegotiationState,
    QuoteProposal,
    NegotiationStart,
    QuoteOutput,
    QuoteItemOutput,
    MessageOutput,
    NegotiationActionResult,
    PolicyEvaluationOutput,
    NegotiationStatus,
    QuoteStatus,
    ProposedBy,
    MessageRole,
    PolicyVerdict,
    MAX_ROUNDS,
    MAX_TURNS,
    QUOTE_VALIDITY_MINUTES,
)
from app.services.negotiation_state import (
    transition,
    is_terminal,
    calculate_valid_until,
    is_quote_expired,
    next_round,
    next_turn,
    StateMachineError,
    NegotiationTerminated,
    TERMINAL_STATES,
)
from app.services.negotiation_validator import (
    evaluate_quote_proposal,
    calculate_quote_totals,
)


class NegotiationService:
    """Service for managing negotiation sessions."""

    def __init__(self, session: Session):
        self._session = session

    # ─── Public API ──────────────────────────────────────────────────────────

    def start_negotiation(self, start: NegotiationStart) -> NegotiationActionResult:
        """
        Start a new negotiation session.

        Args:
            start: Negotiation start parameters

        Returns:
            NegotiationActionResult with new session state
        """
        # Create negotiation
        negotiation = Negotiation(
            id=str(uuid.uuid4()),
            buyer_id=start.buyer_id,
            merchant_id=start.merchant_id,
            status=NegotiationStatus.ACTIVE.value,
        )
        self._session.add(negotiation)
        self._session.flush()

        # Add initial message if provided
        if start.initial_message:
            msg = Message(
                id=str(uuid.uuid4()),
                negotiation_id=negotiation.id,
                role=MessageRole.BUYER.value,
                content=start.initial_message,
            )
            self._session.add(msg)
            negotiation.turn_count = 1  # Initial message counts as a turn

        self._session.commit()

        return NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
            termination_reason=None,
        )

    def propose_quote(
        self,
        negotiation_id: str,
        actor: str,  # 'buyer' or 'merchant'
        proposal: QuoteProposal,
        buyer_budget_cents: Optional[int] = None,
    ) -> NegotiationActionResult:
        """
        Propose a new quote in the negotiation.

        Args:
            negotiation_id: Negotiation UUID
            actor: Who is proposing ('buyer' or 'merchant')
            proposal: Quote proposal
            buyer_budget_cents: Buyer's budget constraint

        Returns:
            NegotiationActionResult with quote and updated state
        """
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return NegotiationActionResult(
                success=False,
                negotiation=None,
                error="Negotiation not found",
            )

        # Validate state transition
        try:
            current_status = NegotiationStatus(negotiation.status)
            new_status = transition(
                current_status,
                "propose_quote" if actor == "buyer" else "counter_propose",
                actor,
                negotiation.round_number if hasattr(negotiation, 'round_number') else 0,
                negotiation.turn_count if hasattr(negotiation, 'turn_count') else 0,
            )
        except (StateMachineError, NegotiationTerminated) as e:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error=str(e),
            )

        # Get merchant config for validation
        merchant_config = self._get_merchant_config(negotiation.merchant_id)

        # Get existing quotes for duplicate check
        existing_quotes = self._get_negotiation_quotes(negotiation_id)

        # Evaluate proposal with policy engine
        policy_eval = evaluate_quote_proposal(
            session=self._session,
            proposal=proposal,
            buyer_budget_cents=buyer_budget_cents,
            merchant_config=merchant_config,
            existing_quotes=existing_quotes,
            proposed_by=ProposedBy.BUYER.value if actor == "buyer" else ProposedBy.MERCHANT.value,
        )

        # Calculate totals
        items_with_products = self._get_items_with_products(proposal)
        subtotal, discount_cents, tax_cents, total_cents = calculate_quote_totals(
            items_with_products, proposal.discount_pct or 0.0
        )

        # Create quote
        quote = Quote(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            proposed_by=ProposedBy.BUYER.value if actor == "buyer" else ProposedBy.MERCHANT.value,
            status=QuoteStatus.PENDING_REVIEW.value,
            subtotal_cents=subtotal,
            discount_cents=discount_cents,
            tax_cents=tax_cents,
            total_cents=total_cents,
            currency="INR",
            valid_until=calculate_valid_until(),
            policy_decision=policy_eval.overall_verdict.value,
            policy_reason=policy_eval.explanation,
        )
        self._session.add(quote)
        self._session.flush()

        # Add quote items
        for item, product in items_with_products:
            quote_item = QuoteItem(
                id=str(uuid.uuid4()),
                quote_id=quote.id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price_cents=product.price_cents,
            )
            self._session.add(quote_item)

        # Update negotiation state
        negotiation.status = new_status.value
        negotiation.round_number = next_round(
            getattr(negotiation, 'round_number', 0)
        )
        negotiation.turn_count = next_turn(
            getattr(negotiation, 'turn_count', 0)
        )

        # Add message
        msg_content = f"{actor.capitalize()} proposed a quote: {proposal.message or 'No message'}"
        msg = Message(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            role=MessageRole.BUYER.value if actor == "buyer" else MessageRole.MERCHANT.value,
            content=msg_content,
            tool_calls={"quote_id": quote.id, "action": "propose"},
        )
        self._session.add(msg)

        self._session.commit()

        # Audit log for financial mutation
        log_audit_event(
            self._session,
            event_type="quote.proposed",
            aggregate_type="negotiation",
            aggregate_id=negotiation_id,
            actor_id=actor,
            actor_type=actor,
            payload={
                "quote_id": quote.id,
                "policy_decision": policy_eval.overall_verdict.value,
                "total_cents": total_cents,
                "discount_pct": proposal.discount_pct or 0.0,
            },
            explanation=policy_eval.explanation,
        )
        self._session.commit()

        # Build response
        quote_output = self._build_quote_output(quote, items_with_products)

        result = NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
            quote=quote_output,
            policy_evaluation=policy_eval,
        )

        # Check if negotiation should terminate
        if is_terminal(new_status):
            result.termination_reason = f"Negotiation {new_status.value}"

        return result

    def accept_quote(
        self,
        negotiation_id: str,
        actor: str,
        quote_id: str,
    ) -> NegotiationActionResult:
        """
        Accept a quote.

        Args:
            negotiation_id: Negotiation UUID
            actor: Who is accepting ('buyer' or 'merchant')
            quote_id: Quote UUID to accept

        Returns:
            NegotiationActionResult with final state
        """
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return NegotiationActionResult(
                success=False,
                negotiation=None,
                error="Negotiation not found",
            )

        quote = self._get_quote(quote_id)
        if not quote or quote.negotiation_id != negotiation_id:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error="Quote not found in this negotiation",
            )

        # Validate state transition
        try:
            current_status = NegotiationStatus(negotiation.status)
            new_status = transition(
                current_status,
                "accept_quote",
                actor,
                getattr(negotiation, 'round_number', 0),
                getattr(negotiation, 'turn_count', 0),
            )
        except (StateMachineError, NegotiationTerminated) as e:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error=str(e),
            )

        # Check quote is still valid
        if is_quote_expired(quote.valid_until):
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error="Quote has expired",
            )

        # Check policy decision
        if quote.policy_decision == PolicyVerdict.REJECT.value:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error="Cannot accept a rejected quote",
            )

        # Update quote status
        quote.status = QuoteStatus.ACCEPTED.value

        # Atomic stock decrement: prevents oversell from concurrent requests.
        # UPDATE products SET stock_qty = stock_qty - :qty WHERE id = :id AND stock_qty >= :qty
        for item in self._get_quote_items(quote.id):
            result = self._session.execute(
                update(Product)
                .where(Product.id == item.product_id, Product.stock_qty >= item.quantity)
                .values(stock_qty=Product.stock_qty - item.quantity)
            )
            if result.rowcount == 0:
                self._session.rollback()
                return NegotiationActionResult(
                    success=False,
                    negotiation=self._build_negotiation_state(negotiation),
                    error=f"Insufficient stock for product {item.product_id} at acceptance time",
                )

        # Update negotiation
        negotiation.status = new_status.value
        negotiation.resolved_at = datetime.now(timezone.utc)
        negotiation.round_number = next_round(
            getattr(negotiation, 'round_number', 0)
        )
        negotiation.turn_count = next_turn(
            getattr(negotiation, 'turn_count', 0)
        )

        # Add message
        msg = Message(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            role=MessageRole.BUYER.value if actor == "buyer" else MessageRole.MERCHANT.value,
            content=f"{actor.capitalize()} accepted quote {quote_id}",
            tool_calls={"quote_id": quote_id, "action": "accept"},
        )
        self._session.add(msg)

        self._session.commit()

        # Audit log for financial mutation (quote accepted = pending payment)
        log_audit_event(
            self._session,
            event_type="quote.accepted",
            aggregate_type="negotiation",
            aggregate_id=negotiation_id,
            actor_id=actor,
            actor_type=actor,
            payload={
                "quote_id": quote_id,
                "total_cents": quote.total_cents,
                "stock_decremented": True,
            },
            explanation=f"Quote {quote_id} accepted by {actor}. Stock decremented. Awaiting payment.",
        )
        self._session.commit()

        return NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
            quote=self._build_quote_output(quote),
            termination_reason=f"Quote accepted by {actor}",
        )

    def reject_quote(
        self,
        negotiation_id: str,
        actor: str,
        quote_id: str,
        reason: Optional[str] = None,
    ) -> NegotiationActionResult:
        """
        Reject a quote.

        Args:
            negotiation_id: Negotiation UUID
            actor: Who is rejecting ('buyer' or 'merchant')
            quote_id: Quote UUID to reject
            reason: Optional rejection reason

        Returns:
            NegotiationActionResult with updated state
        """
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return NegotiationActionResult(
                success=False,
                negotiation=None,
                error="Negotiation not found",
            )

        quote = self._get_quote(quote_id)
        if not quote or quote.negotiation_id != negotiation_id:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error="Quote not found in this negotiation",
            )

        # Validate state transition
        try:
            current_status = NegotiationStatus(negotiation.status)
            new_status = transition(
                current_status,
                "reject_quote",
                actor,
                getattr(negotiation, 'round_number', 0),
                getattr(negotiation, 'turn_count', 0),
            )
        except (StateMachineError, NegotiationTerminated) as e:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error=str(e),
            )

        # Update quote status
        quote.status = QuoteStatus.REJECTED.value

        # Update negotiation
        negotiation.status = new_status.value
        negotiation.resolved_at = datetime.now(timezone.utc)
        negotiation.turn_count = next_turn(
            getattr(negotiation, 'turn_count', 0)
        )

        # Add message
        msg = Message(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            role=MessageRole.BUYER.value if actor == "buyer" else MessageRole.MERCHANT.value,
            content=f"{actor.capitalize()} rejected quote: {reason or 'No reason given'}",
            tool_calls={"quote_id": quote_id, "action": "reject"},
        )
        self._session.add(msg)

        self._session.commit()

        # Audit log for financial mutation
        log_audit_event(
            self._session,
            event_type="quote.rejected",
            aggregate_type="negotiation",
            aggregate_id=negotiation_id,
            actor_id=actor,
            actor_type=actor,
            payload={
                "quote_id": quote_id,
                "rejection_reason": reason,
            },
            explanation=f"Quote {quote_id} rejected by {actor}: {reason or 'No reason given'}",
        )
        self._session.commit()

        return NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
            quote=self._build_quote_output(quote),
            termination_reason=f"Quote rejected by {actor}",
        )

    def cancel_negotiation(
        self,
        negotiation_id: str,
        actor: str,
    ) -> NegotiationActionResult:
        """Cancel a negotiation."""
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return NegotiationActionResult(
                success=False,
                negotiation=None,
                error="Negotiation not found",
            )

        try:
            current_status = NegotiationStatus(negotiation.status)
            new_status = transition(
                current_status,
                "cancel",
                actor,
                getattr(negotiation, 'round_number', 0),
                getattr(negotiation, 'turn_count', 0),
            )
        except (StateMachineError, NegotiationTerminated) as e:
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error=str(e),
            )

        negotiation.status = new_status.value
        negotiation.resolved_at = datetime.now(timezone.utc)

        msg = Message(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            role=MessageRole.BUYER.value if actor == "buyer" else MessageRole.MERCHANT.value,
            content=f"{actor.capitalize()} cancelled the negotiation",
            tool_calls={"action": "cancel"},
        )
        self._session.add(msg)

        self._session.commit()

        # Audit log for financial mutation
        log_audit_event(
            self._session,
            event_type="negotiation.cancelled",
            aggregate_type="negotiation",
            aggregate_id=negotiation_id,
            actor_id=actor,
            actor_type=actor,
            payload={"action": "cancel"},
            explanation=f"Negotiation cancelled by {actor}",
        )
        self._session.commit()

        return NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
            termination_reason=f"Cancelled by {actor}",
        )

    def get_negotiation_state(self, negotiation_id: str) -> Optional[NegotiationState]:
        """Get the current state of a negotiation."""
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return None
        return self._build_negotiation_state(negotiation)

    def add_message(
        self,
        negotiation_id: str,
        role: str,
        content: str,
        tool_calls: Optional[dict] = None,
    ) -> NegotiationActionResult:
        """Add a free-form message to the negotiation."""
        negotiation = self._get_negotiation(negotiation_id)
        if not negotiation:
            return NegotiationActionResult(
                success=False,
                negotiation=None,
                error="Negotiation not found",
            )

        if is_terminal(NegotiationStatus(negotiation.status)):
            return NegotiationActionResult(
                success=False,
                negotiation=self._build_negotiation_state(negotiation),
                error="Cannot add messages to terminated negotiation",
            )

        msg = Message(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            role=role,
            content=content,
            tool_calls=tool_calls or {},
        )
        self._session.add(msg)

        negotiation.turn_count = next_turn(
            getattr(negotiation, 'turn_count', 0)
        )

        self._session.commit()

        return NegotiationActionResult(
            success=True,
            negotiation=self._build_negotiation_state(negotiation),
        )

    # ─── Private Helpers ─────────────────────────────────────────────────────

    def _get_negotiation(self, negotiation_id: str) -> Optional[Negotiation]:
        stmt = select(Negotiation).filter(Negotiation.id == negotiation_id)
        return self._session.execute(stmt).scalar_one_or_none()

    def _get_quote(self, quote_id: str) -> Optional[Quote]:
        stmt = select(Quote).filter(Quote.id == quote_id)
        return self._session.execute(stmt).scalar_one_or_none()

    def _get_quote_items(self, quote_id: str) -> List[QuoteItem]:
        """Fetch all QuoteItem rows for a given quote."""
        stmt = select(QuoteItem).filter(QuoteItem.quote_id == quote_id)
        return list(self._session.execute(stmt).scalars().all())

    def _get_merchant_config(self, merchant_id: str) -> Optional[dict]:
        from app.models.merchant import Merchant
        stmt = select(Merchant).filter(Merchant.id == merchant_id)
        merchant = self._session.execute(stmt).scalar_one_or_none()
        return merchant.config if merchant else None

    def _get_negotiation_quotes(self, negotiation_id: str) -> List[Quote]:
        stmt = select(Quote).filter(Quote.negotiation_id == negotiation_id)
        return list(self._session.execute(stmt).scalars().all())

    def _get_items_with_products(
        self, proposal: QuoteProposal
    ) -> List[tuple[QuoteItemInput, Product]]:
        product_ids = [item.product_id for item in proposal.items]
        stmt = select(Product).filter(Product.id.in_(product_ids), Product.is_active == True)
        products = list(self._session.execute(stmt).scalars().all())
        product_map = {p.id: p for p in products}
        return [(item, product_map[item.product_id]) for item in proposal.items if item.product_id in product_map]

    def _build_negotiation_state(self, negotiation: Negotiation) -> NegotiationState:
        quotes = self._get_negotiation_quotes(negotiation.id)
        messages = self._get_messages(negotiation.id)

        current_quote = None
        if quotes:
            # Get the latest pending/approved quote
            active_quotes = [q for q in quotes if q.status in ("pending_review", "approved")]
            if active_quotes:
                current_quote = self._build_quote_output(active_quotes[-1])

        return NegotiationState(
            negotiation_id=negotiation.id,
            buyer_id=negotiation.buyer_id,
            merchant_id=negotiation.merchant_id,
            status=NegotiationStatus(negotiation.status),
            round_number=getattr(negotiation, 'round_number', 0),
            turn_count=getattr(negotiation, 'turn_count', 0),
            current_quote=current_quote,
            quote_history=[self._build_quote_output(q) for q in quotes],
            messages=[self._build_message_output(m) for m in messages],
            started_at=negotiation.started_at,
            resolved_at=negotiation.resolved_at,
        )

    def _get_messages(self, negotiation_id: str) -> List[Message]:
        stmt = select(Message).filter(Message.negotiation_id == negotiation_id).order_by(Message.created_at)
        return list(self._session.execute(stmt).scalars().all())

    def _build_quote_output(
        self, quote: Quote, items_with_products: Optional[List[tuple]] = None
    ) -> QuoteOutput:
        if items_with_products is None:
            # Fetch items
            stmt = select(QuoteItem).filter(QuoteItem.quote_id == quote.id)
            quote_items = list(self._session.execute(stmt).scalars().all())
            items = []
            for qi in quote_items:
                # Get product name
                stmt_p = select(Product).filter(Product.id == qi.product_id)
                product = self._session.execute(stmt_p).scalar_one_or_none()
                items.append(
                    QuoteItemOutput(
                        product_id=qi.product_id,
                        product_name=product.name if product else "Unknown",
                        quantity=qi.quantity,
                        unit_price_cents=qi.unit_price_cents,
                        line_total_cents=qi.quantity * qi.unit_price_cents,
                    )
                )
        else:
            items = [
                QuoteItemOutput(
                    product_id=item.product_id,
                    product_name=product.name,
                    quantity=item.quantity,
                    unit_price_cents=product.price_cents,
                    line_total_cents=item.quantity * product.price_cents,
                )
                for item, product in items_with_products
            ]

        discount_pct = 0.0
        if quote.subtotal_cents > 0:
            discount_pct = (quote.discount_cents / quote.subtotal_cents) * 100

        return QuoteOutput(
            id=quote.id,
            negotiation_id=quote.negotiation_id or "",
            proposed_by=ProposedBy(quote.proposed_by),
            status=QuoteStatus(quote.status),
            items=items,
            subtotal_cents=quote.subtotal_cents,
            discount_cents=quote.discount_cents,
            discount_pct=round(discount_pct, 2),
            tax_cents=quote.tax_cents,
            total_cents=quote.total_cents,
            currency=quote.currency,
            valid_until=quote.valid_until,
            policy_decision=PolicyVerdict(quote.policy_decision) if quote.policy_decision else None,
            policy_reason=quote.policy_reason,
            created_at=quote.created_at,
        )

    def _build_message_output(self, message: Message) -> MessageOutput:
        return MessageOutput(
            id=message.id,
            negotiation_id=message.negotiation_id,
            role=MessageRole(message.role),
            content=message.content,
            tool_calls=message.tool_calls,
            created_at=message.created_at,
        )