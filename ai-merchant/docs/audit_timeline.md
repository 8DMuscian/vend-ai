# Audit Timeline — AI Merchant Platform

## Phase 1: Buyer Intent & Catalog

| # | Event | Actor | Details |
|---|-------|-------|---------|
| 1 | `BUYER_INTENT` | Buyer | Buyer expresses intent to purchase. LLM generates proposal with product preferences, budget, use case. |
| 2 | `CATALOG_SEARCH` | Policy Engine | Catalog API search with filters (category, stock, price range). Deterministic DB lookup. |
| 3 | `PRODUCT_RECOMMENDED` | Growth Brain | Recommendation engine scores products based on category (3×), use case (2×), budget (2×), stock (3×). |
| 4 | `UPSELL_PROPOSED` | Growth Brain | Cross-sell/upsell detection: compatible products, bundle generation, estimated order value. |

---

## Phase 2: Negotiation

| # | Event | Actor | Details |
|---|-------|-------|---------|
| 5 | `NEGOTIATION_STARTED` | Policy Engine | Negotiation state machine initialized: ACTIVE state, max 5 rounds, max 20 turns. |
| 6 | `QUOTE_CREATED` | Negotiation Service | Quote stored with policy decision. Both parties can propose counter-offers. |
| 7 | `NEGOTIATION_OFFER` | Buyer/Merchant | Party proposes new quote price/discount. Max 20 turns per round, max 5 rounds. |
| 8 | `POLICY_CHECK` | Deterministic Policy Engine | **Critical**: All quote proposals pass through `TransactionPolicy`:
  - Discount cap (max 20%)
  - Inventory check (stock ≥ requested qty)
  - Price floor (effective price ≥ list × (1-min_margin%))
  - Min/max order value (₹100–₹50,000)
  - Buyer budget check
  - Quote expiry (30 min validity)
  - Duplicate quote prevention |
| 9 | `USER_APPROVAL` | Buyer/Merchant | Party accepts or rejects the quote. State transitions: ACTIVE → QUOTE_PROPOSED → ACCEPTED/REJECTED/EXPIRED/CANCELLED. |
| 10 | `NEGOTIATION_ACCEPTED` | Policy Engine | Terminal state reached: ACCEPTED. Quote validated, order created. |
| 11 | `NEGOTIATION_REJECTED` | Policy Engine | Terminal state reached: REJECTED. No order created. |

---

## Phase 3: Transaction & Payment

| # | Event | Actor | Details |
|---|-------|-------|---------|
| 12 | `TRANSACTION_PROPOSED` | Policy Engine | **Gateway event**: All payment requests pass through `verify_policy_and_execute()` before Razorpay. |
| 13 | `PAYMENT_CREATED` | Razorpay Service | Order created in test mode via Razorpay SDK. Amount in paise (integer). Idempotency key generated. |
| 14 | `PAYMENT_FAILED` | Razorpay Service | Payment failed: insufficient funds, validation error, network timeout. Retry logic (max 3 attempts). |
| 15 | `PAYMENT_SUCCESS` | Razorpay Service | Payment captured: Razorpay reports `captured` status. State machine transitions to CAPTURED. |
| 16 | `RETRY_REQUESTED` | Policy Engine | Transient failure detected (network/timeout). Policy re-verified before retry. Max 3 retry attempts. |

---

## Phase 4: Audit & Compliance

| # | Event | Actor | Details |
|---|-------|-------|---------|
| 17 | `TRANSACTION_APPROVED` | Policy Engine | **Judge-facing event**: Transaction approved by deterministic code. |
|   |   |   | ```json |
|   |   |   | { |
|   |   |   |   "event": "TRANSACTION_APPROVED", |
|   |   |   | "timestamp": "2026-09-05T...Z", |
|   |   |   "actor": "policy_engine", |
|   |   |   "transaction_id": "txn_...", |
|   |   |   "amount": 69999,  /* paise */ |
|   |   |   "reason": "Within buyer and merchant limits", |
|   |   |   "policy_version": "v1.2" |
|   |   | } |
| 18 | `AUDIT_EVENT_LOGGED` | Audit Service | Every financial mutation creates an audit event with explanation. Required for all transactions. |
| 19 | `POLICY_VERSION_UPDATE` | Config | Policy limits (max_amount, max_discount_percent, require_confirmation_above) updated via config. |

---

## AGENTS.md Compliance Summary

| Concern | Where Enforced |
|---------|---------------|
| **LLM proposes, deterministic code disposes** | Policy engine (`TransactionPolicy`) |
| **Prices from database, not LLM** | Catalog API DB lookups |
| **All amounts in paise (integers)** | `create_order()`, `create_payment()` |
| **Max 5 rounds, 20 turns** | Negotiation state machine |
| **Quote expiry 30 min** | Policy validator |
| **Structured Pydantic outputs** | All services return typed models |
| **No LLM tool calls Razorpay directly** | `verify_policy_and_execute()` gateway |
| **Idempotency via client_order_id** | SHA-256 key generation |
| **Failed payments don't create duplicates** | State machine + idempotency key UNIQUE constraint |

---

## Event Flow Diagram

```
BUYER_INTENT
    │
    ▼
CATALOG_SEARCH → PRODUCT_RECOMMENDED → UPSELL_PROPOSED
    │                                       │
    └───────────────────────────────────────┘
                │
                ▼
            NEGOTIATION_STARTED
                │
                ▼
            QUOTE_CREATED
                │
                ▼
            NEGOTIATION_OFFER (buyer/merchant)
                │
                ▼
            POLICY_CHECK (deterministic)
                │
        ├───────────────┬───────────────┤
                │                       │
                ▼                       ▼
        USER_APPROVAL          NEGOTIATION_REJECTED
                │
                ▼
            NEGOTIATION_ACCEPTED
                │
                ▼
            TRANSACTION_PROPOSED
                │
                ▼
        ├───────────────┬───────────────┤
                │                       │
                ▼                       ▼
        PAYMENT_CREATED     PAYMENT_FAILED
                │                       │
                ▼                       ▼
            PAYMENT_SUCCESS     RETRY_REQUESTED
                │
                ▼
        TRANSACTION_APPROVED (judge-facing)
                │
                ▼
        AUDIT_EVENT_LOGGED
```

---

## Key Invariants

| # | Invariant | Enforcement |
|---|-----------|-------------|
| 1 | Transaction amount ≤ approved limit | `QuoteEngine` validates against `products.price_cents` |
| 2 | Discount ≤ merchant discount limit | `PolicyEngine` checks `discount_cap` rule |
| 3 | Every transaction must have a reason | `audit_events.explanation` is required |
| 4 | Every financial action must have an audit event | `AuditLogger.log()` called in every service method |
| 5 | Payment operations must be idempotent | `idempotency_key` UNIQUE constraint + Redis lock |
| 6 | Failed payments must never create duplicate charges | State machine prevents re-initiation without retry |
| 7 | LLM output must never be trusted as financial state | Prices, totals, and discounts recalculated server-side |