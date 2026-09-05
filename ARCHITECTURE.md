# AI-Native Merchant Platform — System Architecture

> Razorpay AI Growth & Agentic Commerce Hackathon

---

## 1. System Architecture

### Design Principles

1. **LLM generates intent, never executes money.** Every financial action passes through a deterministic policy gate.
2. **Monolith first.** One deployable unit. Modules separated by domain, not by network.
3. **Event sourcing for audit.** Every state mutation is an immutable event.
4. **Bounded autonomy.** Agents get narrow tool permissions. Escalation on ambiguity.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Buyer Agent  │  │ Merchant Agent│  │  Admin Dashboard (React) │  │
│  │  (LLM + Tool) │  │ (LLM + Tool) │  │                          │  │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘  │
└─────────┼─────────────────┼───────────────────────┼─────────────────┘
          │                 │                       │
          ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY                                  │
│            (FastAPI / rate limiting / auth / request ID)            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
┌─────────────────┐ ┌───────────────┐ ┌─────────────────┐
│  CATALOG MODULE │ │ NEGOTIATION   │ │ TRANSACTION     │
│                 │ │ MODULE        │ │ MODULE          │
│ - Product CRUD  │ │ - Sessions    │ │ - Quote engine  │
│ - AI-readable   │ │ - Agent conv  │ │ - State machine │
│   schema        │ │ - Proposals   │ │ - Razorpay ops  │
│ - Upsell graph  │ │ - Policy gate │ │ - Idempotency   │
└────────┬────────┘ └───────┬───────┘ └────────┬────────┘
         │                  │                   │
         ▼                  ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SHARED SERVICES                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ Policy Engine │  │ Audit Logger │  │ Razorpay Client          │  │
│  │ (deterministic│  │ (event store)│  │ (test-mode SDK)          │  │
│  │  rule engine) │  │              │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      DATA LAYER                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  PostgreSQL   │  │  Redis       │  │  File Storage (S3/local) │  │
│  │  (primary DB) │  │  (cache +    │  │  (product images, docs)  │  │
│  │               │  │   locks)     │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Why Monolith

| Concern | Decision |
|---------|----------|
| Hackathon timeline | Single deployable, single DB, no inter-service auth |
| Consistency | ACID transactions across catalog + quotes + orders |
| Complexity | No service discovery, no distributed tracing needed |
| Future split | Module boundaries are clean enough to extract later |

### Module Map

```
src/
├── main.py                  # FastAPI app, lifespan, middleware
├── config.py                # Settings, env vars
├── models/                  # SQLAlchemy ORM models
│   ├── product.py
│   ├── quote.py
│   ├── order.py
│   ├── negotiation.py
│   └── audit.py
├── schemas/                 # Pydantic request/response schemas
│   ├── catalog.py
│   ├── negotiation.py
│   ├── quote.py
│   └── transaction.py
├── api/                     # Route handlers
│   ├── catalog.py
│   ├── negotiation.py
│   ├── quote.py
│   ├── transaction.py
│   └── admin.py
├── services/                # Business logic
│   ├── catalog_service.py
│   ├── recommendation.py
│   ├── quote_engine.py
│   ├── negotiation_service.py
│   └── upsell.py
├── agents/                  # LLM agent orchestration
│   ├── buyer_agent.py
│   ├── merchant_agent.py
│   └── tools.py             # Tool definitions (JSON schema)
├── policy/                  # Deterministic policy engine
│   ├── engine.py
│   ├── rules/
│   │   ├── pricing.py
│   │   ├── discount.py
│   │   ├── fraud.py
│   │   └── compliance.py
│   └── approvals.py
├── transaction/             # State machine + Razorpay
│   ├── state_machine.py
│   ├── razorpay_client.py
│   └── idempotency.py
├── audit/                   # Event sourcing
│   ├── logger.py
│   ├── events.py
│   └── explanations.py
└── tests/
    ├── test_catalog.py
    ├── test_negotiation.py
    ├── test_quote.py
    ├── test_transaction.py
    └── test_policy.py
```

---

## 2. Component Boundaries

### Interface Contracts Between Modules

Each module exposes a typed service interface. Modules never import from each other's internals.

```
┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  CATALOG     │────▶│  CatalogService                                    │
│  MODULE      │     │  .search(query: str) -> list[Product]              │
│              │     │  .get_by_id(id: str) -> Product                    │
│              │     │  .get_ai_schema(id: str) -> AISchema              │
│              │     │  .get_upsell_graph(id: str) -> UpsellGraph        │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  RECOMMEND-  │────▶│  RecommendationService                             │
│  ATION       │     │  .recommend(intent: str, context: SessionCtx)      │
│  MODULE      │     │      -> list[Recommendation]                       │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  NEGOTIATION │────▶│  NegotiationService                                │
│  MODULE      │     │  .start_session(buyer_id, merchant_id) -> Session  │
│              │     │  .send_message(session_id, role, content)          │
│              │     │  .propose_quote(session_id, proposal) -> Quote     │
│              │     │  .resolve(session_id) -> NegotiationResult         │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  QUOTE       │────▶│  QuoteEngine                                       │
│  ENGINE      │     │  .generate(product_ids, quantities, context)       │
│              │     │      -> Quote                                      │
│              │     │  .validate(quote) -> ValidationResult              │
│              │     │  .apply_discounts(quote, codes) -> Quote           │
│              │     │  .enforce_bounds(quote) -> Quote                   │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  POLICY      │────▶│  PolicyEngine                                      │
│  ENGINE      │     │  .evaluate(action: Action) -> Decision             │
│              │     │      Decision = APPROVE | REJECT | ESCALATE        │
│              │     │  .list_policies(action_type) -> list[Policy]       │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  TRANSACTION │────▶│  TransactionService                                │
│  MODULE      │     │  .initiate(quote_id, idempotency_key) -> Order    │
│              │     │  .execute_payment(order) -> PaymentResult          │
│              │     │  .handle_failure(order, error) -> RecoveryAction   │
│              │     │  .confirm(order_id) -> Order                       │
└─────────────┘     └─────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────────────────────────────────────────────┐
│  AUDIT       │────▶│  AuditLogger                                       │
│  MODULE      │     │  .log(event: AuditEvent) -> None                   │
│              │     │  .explain(transaction_id) -> Explanation            │
│              │     │  .query(filters) -> list[AuditEvent]               │
└─────────────┘     └─────────────────────────────────────────────────────┘
```

### Dependency Direction

```
api/ → services/ → policy/ + audit/
services/ → models/ (via repository pattern)
agents/ → services/ + policy/ (never directly to models)
policy/ → models/ (read-only for rule evaluation)
audit/ → models/ (write-only append)
transaction/ → razorpay_client + policy/ + audit/
```

**Rule:** No circular dependencies. `policy/` and `audit/` are leaf nodes — they depend on nothing but models.

---

## 3. Database Schema

### Entity Relationship Diagram

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  products     │       │  product_upsell  │       │  product_bundle  │
│──────────────│       │  _links          │       │  _items          │
│ id (PK)      │◄──┐   │──────────────────│       │──────────────────│
│ merchant_id  │   │   │ id (PK)          │       │ id (PK)          │
│ name         │   │   │ source_id (FK)───│──┐    │ bundle_id (FK)───│──┐
│ description  │   │   │ target_id (FK)───│──┼──▶ │ product_id (FK)──│──┼──▶
│ ai_schema    │   │   │ relationship     │  │    │ quantity         │  │
│ price_cents  │   │   │ (upsell/cross/   │  │    │ discount_pct     │  │
│ currency     │   │   │  bundle)         │  │    └──────────────────┘  │
│ stock_qty    │   │   │ priority         │  │                          │
│ metadata     │   │   └──────────────────┘  │                          │
│ is_active    │   │                          │                          │
│ created_at   │   │   ┌──────────────────┐  │                          │
│ updated_at   │   │   │  bundles         │  │                          │
└──────┬───────┘   │   │──────────────────│  │                          │
       │           │   │ id (PK)          │  │                          │
       │           │   │ merchant_id      │  │                          │
       │           │   │ name             │  │                          │
       │           │   │ description      │  │                          │
       │           │   │ bundle_price_    │  │                          │
       │           │   │   cents          │  │                          │
       │           │   │ savings_pct      │  │                          │
       │           │   │ is_active        │  │                          │
       │           │   └──────────────────┘  │                          │
       │           │                          │                          │
       │           │   ┌──────────────────┐  │                          │
       │           │   │  negotiations    │  │                          │
       │           │   │──────────────────│  │                          │
       │           │   │ id (PK)          │  │                          │
       │           │   │ buyer_id         │  │                          │
       │           │   │ merchant_id      │  │                          │
       │           │   │ status           │  │                          │
       │           │   │ started_at       │  │                          │
       │           │   │ resolved_at      │  │                          │
       │           │   └────────┬─────────┘  │                          │
       │           │            │             │                          │
       │           │   ┌────────▼─────────┐  │                          │
       │           │   │  messages        │  │                          │
       │           │   │──────────────────│  │                          │
       │           │   │ id (PK)          │  │                          │
       │           │   │ negotiation_id   │  │                          │
       │           │   │ role (buyer/     │  │                          │
       │           │   │  merchant/system)│  │                          │
       │           │   │ content          │  │                          │
       │           │   │ tool_calls       │  │                          │
       │           │   │ created_at       │  │                          │
       │           │   └──────────────────┘  │                          │
       │           │                          │                          │
       │           │   ┌──────────────────┐  │                          │
       │           │   │  quotes          │  │                          │
       │           │   │──────────────────│  │                          │
       │           │   │ id (PK)          │  │                          │
       │           │   │ negotiation_id───│──┘                          │
       │           │   │ proposed_by      │                             │
       │           │   │ status           │                             │
       │           │   │ subtotal_cents   │                             │
       │           │   │ discount_cents   │                             │
       │           │   │ tax_cents        │                             │
       │           │   │ total_cents      │                             │
       │           │   │ currency         │                             │
       │           │   │ valid_until      │                             │
       │           │   │ policy_decision  │                             │
       │           │   │ policy_reason    │                             │
       │           │   │ created_at       │                             │
       │           │   └────────┬─────────┘                             │
       │           │            │                                        │
       │           │   ┌────────▼─────────┐                             │
       │           │   │  quote_items     │                             │
       │           │   │──────────────────│                             │
       │           │   │ id (PK)          │                             │
       │           │   │ quote_id (FK)    │                             │
       │           │◀──│ product_id (FK)  │                             │
       │           │   │ quantity         │                             │
       │           │   │ unit_price_cents │                             │
       │           │   └──────────────────┘                             │
       │           │                                                    │
       │           │   ┌──────────────────┐                             │
       │           │   │  orders          │                             │
       │           │   │──────────────────│                             │
       │           │   │ id (PK)          │                             │
       │           │   │ quote_id (FK)────│──▶ quotes.id                │
       │           │   │ buyer_id         │                             │
       │           │   │ merchant_id      │                             │
       │           │   │ status           │                             │
       │           │   │ total_cents      │                             │
       │           │   │ currency         │                             │
       │           │   │ idempotency_key  │                             │
       │           │   │ razorpay_order_id│                             │
       │           │   │ razorpay_pay_id  │                             │
       │           │   │ payment_method   │                             │
       │           │   │ created_at       │                             │
       │           │   │ paid_at          │                             │
       │           │   │ failed_at        │                             │
       │           │   │ failure_reason   │                             │
       │           │   └────────┬─────────┘                             │
       │           │            │                                        │
       │           │   ┌────────▼─────────┐                             │
       │           │   │  order_items     │                             │
       │           │   │──────────────────│                             │
       │           │   │ id (PK)          │                             │
       │           │   │ order_id (FK)    │                             │
       │           │◀──│ product_id (FK)  │                             │
       │           │   │ quantity         │                             │
       │           │   │ unit_price_cents │                             │
       │           │   └──────────────────┘                             │
       │           │                                                    │
       │           │   ┌──────────────────┐                             │
       │           │   │  audit_events    │                             │
       │           │   │──────────────────│                             │
       │           │   │ id (PK)          │                             │
       │           │   │ event_type       │                             │
       │           │   │ aggregate_type   │                             │
       │           │   │ aggregate_id     │                             │
       │           │   │ actor_id         │                             │
       │           │   │ actor_type       │                             │
       │           │   │ payload (JSONB)  │                             │
       │           │   │ explanation      │                             │
       │           │   │ created_at       │                             │
       │           │   └──────────────────┘                             │
       │                                                                │
       │           ┌──────────────────┐                                 │
       │           │  merchants       │                                 │
       │           │──────────────────│                                 │
       │           │ id (PK)          │                                 │
       └──────────▶│ name             │                                 │
                   │ api_key_hash     │                                 │
                   │ config (JSONB)   │                                 │
                   │ is_active        │                                 │
                   │ created_at       │                                 │
                   └──────────────────┘                                 │
```

### Table Definitions

```sql
-- Products: AI-readable catalog
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    ai_schema       JSONB NOT NULL,          -- structured data for LLM consumption
    price_cents     INTEGER NOT NULL CHECK (price_cents > 0),
    currency        VARCHAR(3) DEFAULT 'INR',
    stock_qty       INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    metadata        JSONB DEFAULT '{}',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- AI Schema Example:
-- {
--   "category": "electronics",
--   "tags": ["wireless", "bluetooth", "headphones"],
--   "specs": { "battery_life": "40h", "driver": "40mm" },
--   "target_audience": "music_lovers",
--   "use_cases": ["commute", "gym", "work"],
--   "comparison_notes": "Mid-range, good bass response"
-- }

-- Upsell/Cross-sell relationships
CREATE TABLE product_upsell_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES products(id),
    target_id       UUID NOT NULL REFERENCES products(id),
    relationship    VARCHAR(20) NOT NULL CHECK (relationship IN ('upsell', 'cross_sell', 'accessory')),
    priority        INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, target_id, relationship)
);

-- Bundles
CREATE TABLE bundles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    bundle_price_cents INTEGER NOT NULL,
    savings_pct     DECIMAL(5,2),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bundle_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bundle_id       UUID NOT NULL REFERENCES bundles(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    discount_pct    DECIMAL(5,2) DEFAULT 0
);

-- Negotiation sessions
CREATE TABLE negotiations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id        VARCHAR(255) NOT NULL,
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'quote_proposed', 'accepted', 'rejected', 'expired', 'cancelled')),
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id  UUID NOT NULL REFERENCES negotiations(id),
    role            VARCHAR(20) NOT NULL CHECK (role IN ('buyer', 'merchant', 'system')),
    content         TEXT NOT NULL,
    tool_calls      JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Quotes (bounded by policy)
CREATE TABLE quotes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id  UUID REFERENCES negotiations(id),
    proposed_by     VARCHAR(20) NOT NULL CHECK (proposed_by IN ('buyer', 'merchant', 'system')),
    status          VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'expired', 'accepted')),
    subtotal_cents  INTEGER NOT NULL,
    discount_cents  INTEGER DEFAULT 0,
    tax_cents       INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL,
    currency        VARCHAR(3) DEFAULT 'INR',
    valid_until     TIMESTAMPTZ NOT NULL,
    policy_decision VARCHAR(20),
    policy_reason   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE quote_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id        UUID NOT NULL REFERENCES quotes(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL
);

-- Orders and payments
CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id            UUID REFERENCES quotes(id),
    buyer_id            VARCHAR(255) NOT NULL,
    merchant_id         UUID NOT NULL REFERENCES merchants(id),
    status              VARCHAR(20) NOT NULL DEFAULT 'created'
                        CHECK (status IN (
                            'created', 'pending_payment', 'payment_initiated',
                            'payment_captured', 'payment_failed', 'refunded',
                            'cancelled', 'completed'
                        )),
    total_cents         INTEGER NOT NULL,
    currency            VARCHAR(3) DEFAULT 'INR',
    idempotency_key     VARCHAR(64) UNIQUE NOT NULL,
    razorpay_order_id   VARCHAR(64),
    razorpay_payment_id VARCHAR(64),
    payment_method      VARCHAR(32),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    paid_at             TIMESTAMPTZ,
    failed_at           TIMESTAMPTZ,
    failure_reason      TEXT
);

CREATE TABLE order_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL
);

-- Audit trail (append-only)
CREATE TABLE audit_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(100) NOT NULL,
    aggregate_type  VARCHAR(50) NOT NULL,
    aggregate_id    UUID NOT NULL,
    actor_id        VARCHAR(255),
    actor_type      VARCHAR(20) CHECK (actor_type IN ('buyer', 'merchant', 'system', 'policy', 'agent')),
    payload         JSONB NOT NULL,
    explanation     TEXT,                    -- human-readable explanation of why this happened
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_aggregate ON audit_events(aggregate_type, aggregate_id);
CREATE INDEX idx_audit_created ON audit_events(created_at);
```

---

## 4. API Contracts

### Catalog API

```
GET  /api/v1/products
     ?q={search_query}
     &category={category}
     &min_price={cents}
     &max_price={cents}
     &limit={n}
     &offset={n}
     Response: { items: Product[], total: int }

GET  /api/v1/products/{id}
     Response: Product

GET  /api/v1/products/{id}/ai-schema
     Response: AISchema            -- machine-readable product data for agents

GET  /api/v1/products/{id}/recommendations
     ?limit={n}
     Response: { items: Recommendation[] }

GET  /api/v1/products/{id}/upsell
     ?type={upsell|cross_sell|accessory}
     Response: { items: UpsellLink[] }

GET  /api/v1/bundles
     ?merchant_id={id}
     Response: { items: Bundle[] }
```

### Negotiation API

```
POST /api/v1/negotiations
     Body: { buyer_id, merchant_id }
     Response: { id, status, started_at }

GET  /api/v1/negotiations/{id}
     Response: NegotiationDetail (with messages, quotes)

POST /api/v1/negotiations/{id}/messages
     Body: { role: "buyer"|"merchant", content: string }
     Response: { message: Message, agent_reply?: Message }

POST /api/v1/negotiations/{id}/propose
     Body: { proposed_by, items: [{product_id, quantity}], discount_pct? }
     Response: Quote  (includes policy_decision)

POST /api/v1/negotiations/{id}/accept-quote
     Body: { quote_id }
     Response: Order

POST /api/v1/negotiations/{id}/reject-quote
     Body: { quote_id, reason? }
     Response: { status: "rejected" }

POST /api/v1/negotiations/{id}/cancel
     Response: { status: "cancelled" }
```

### Transaction API

```
POST /api/v1/orders
     Body: { quote_id, idempotency_key }
     Response: Order  (status: "created")

POST /api/v1/orders/{id}/pay
     Body: { payment_method: "card"|"upi"|"netbanking" }
     Response: { razorpay_order_id, razorpay_key_id, amount }

POST /api/v1/orders/{id}/verify
     Body: { razorpay_payment_id, razorpay_order_id, razorpay_signature }
     Response: Order (status: "completed" | "payment_failed")

GET  /api/v1/orders/{id}
     Response: OrderDetail

GET  /api/v1/orders/{id}/explanation
     Response: Explanation { steps: [{action, reason, policy_applied, timestamp}] }
```

### Webhook API

```
POST /api/v1/webhooks/razorpay
     Headers: X-Razorpay-Signature: {hmac}
     Body: RazorpayEvent
     Response: 200 OK
     Notes: Handles payment.captured, payment.failed, order.paid
```

### Admin API

```
GET  /api/v1/admin/audit
     ?aggregate_type={type}
     &aggregate_id={id}
     &event_type={type}
     &from={timestamp}
     &to={timestamp}
     &limit={n}
     Response: { items: AuditEvent[] }

GET  /api/v1/admin/orders
     ?status={status}
     &merchant_id={id}
     &from={timestamp}
     &to={timestamp}
     Response: { items: Order[] }

GET  /api/v1/admin/policies
     Response: { items: Policy[] }

POST /api/v1/admin/policies/{id}/toggle
     Response: Policy
```

### Common Response Envelope

```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "request_id": "req_abc123",
    "timestamp": "2026-09-05T10:30:00Z"
  }
}
```

### Error Response

```json
{
  "success": false,
  "error": {
    "code": "POLICY_REJECTED",
    "message": "Discount exceeds maximum allowed (15%)",
    "details": {
      "requested_discount": 25,
      "max_allowed": 15,
      "policy_id": "discount_cap"
    }
  },
  "meta": {
    "request_id": "req_abc123",
    "timestamp": "2026-09-05T10:30:00Z"
  }
}
```

---

## 5. Agent/Tool Contracts

### Buyer Agent

**Role:** Represents the buyer. Interprets natural language intent, searches catalog, initiates negotiation.

```yaml
buyer_agent:
  system_prompt: |
    You are a buyer's agent on a merchant platform.
    Your job: find products matching the buyer's intent, compare options,
    negotiate fair prices, and propose quotes.
    You CANNOT execute payments. Only humans confirm transactions.
    Always cite product IDs when referencing items.

  tools:
    - name: search_catalog
      description: "Search merchant catalog by intent or keywords"
      parameters:
        query: string        # natural language intent
        max_price: integer?  # budget cap in paise
        category: string?
        limit: integer       # default 5
      returns: list[ProductSummary]

    - name: get_product_details
      description: "Get full product info including specs and AI schema"
      parameters:
        product_id: string
      returns: Product

    - name: get_recommendations
      description: "Get products related to a given product"
      parameters:
        product_id: string
        relationship: "upsell" | "cross_sell" | "accessory" | "any"
        limit: integer
      returns: list[ProductSummary]

    - name: propose_quote
      description: "Submit a quote proposal to the merchant"
      parameters:
        negotiation_id: string
        items: list[{ product_id: string, quantity: int }]
        discount_request_pct: float?   # 0-50, policy will cap
      returns: Quote

    - name: respond_to_merchant
      description: "Send a message in the negotiation"
      parameters:
        negotiation_id: string
        content: string
      returns: Message

    - name: accept_quote
      description: "Accept the merchant's quote and initiate payment"
      parameters:
        negotiation_id: string
        quote_id: string
      returns: Order
```

### Merchant Agent

**Role:** Represents the merchant. Responds to buyer queries, manages inventory-aware offers, handles upsell.

```yaml
merchant_agent:
  system_prompt: |
    You are a merchant's agent on a commerce platform.
    Your job: respond to buyer inquiries, suggest products from your catalog,
    make counter-offers, and highlight bundles/upsells.
    You CANNOT modify prices beyond your configured discount limits.
    You CANNOT execute payments.

  tools:
    - name: search_own_catalog
      description: "Search the merchant's own product catalog"
      parameters:
        query: string
        in_stock_only: boolean
        limit: integer
      returns: list[ProductSummary]

    - name: get_inventory
      description: "Check stock level for a product"
      parameters:
        product_id: string
      returns: { product_id, stock_qty, is_available }

    - name: get_upsell_opportunities
      description: "Find upsell/cross-sell items for products in the negotiation"
      parameters:
        product_ids: list[string]
        type: "upsell" | "cross_sell" | "bundle" | "all"
      returns: list[UpsellOpportunity]

    - name: counter_propose
      description: "Submit a counter-offer with merchant-controlled discounts"
      parameters:
        negotiation_id: string
        items: list[{ product_id: string, quantity: int, unit_price_override?: int }]
        discount_pct: float?        # merchant's allowed max (checked by policy)
        message: string?            # optional note to buyer
      returns: Quote

    - name: respond_to_buyer
      description: "Send a message in the negotiation"
      parameters:
        negotiation_id: string
        content: string
      returns: Message
```

### Agent Execution Flow

```
Buyer says: "I need wireless headphones under 3000 for gym use"

1. Buyer Agent parses intent → extracts: category=headphones, features=wireless, budget=3000, use=gym
2. Buyer Agent calls search_catalog(query="wireless headphones gym", max_price=3000)
3. Returns: [Product A (₹2499), Product B (₹2999), Product C (out of stock)]
4. Buyer Agent calls get_recommendations(product_a_id, relationship="accessory")
5. Returns: [Carrying case (₹299), Ear tips (₹149)]
6. Buyer Agent presents options to buyer with accessories
7. Buyer picks Product A + carrying case
8. Buyer Agent calls propose_quote(negotiation_id, items=[...], discount_request_pct=10)
9. System runs policy engine → approves (within bounds)
10. Merchant Agent receives proposal, calls get_upsell_opportunities([product_a_id])
11. Merchant Agent suggests also buying premium ear tips (cross-sell)
12. Buyer accepts → calls accept_quote → initiates order
```

---

## 6. Policy Engine Design

### Architecture

The policy engine is a **deterministic rule evaluator**. It receives structured actions and returns binary/quaternary decisions. It never calls the LLM.

```
┌─────────────────────────────────────────────────┐
│                POLICY ENGINE                     │
│                                                  │
│  Input: Action { type, actor, payload }          │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │  Rule Pipeline (ordered)                   │  │
│  │                                            │  │
│  │  1. Fraud Detection Rules                  │  │
│  │  2. Discount Bounds Rules                  │  │
│  │  3. Inventory Rules                        │  │
│  │  4. Pricing Rules                          │  │
│  │  5. Compliance Rules                       │  │
│  │  6. Rate Limiting Rules                    │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  Output: Decision {                              │
│    verdict: APPROVE | REJECT | ESCALATE,         │
│    reason: string,                               │
│    applied_policies: list[PolicyResult],          │
│    explanation: string (for audit)               │
│  }                                               │
└─────────────────────────────────────────────────┘
```

### Decision Types

| Verdict | Meaning | Next Step |
|---------|---------|-----------|
| `APPROVE` | All rules pass | Proceed with action |
| `REJECT` | Hard rule violated | Block action, return reason |
| `ESCALATE` | Soft rule uncertain | Require human/admin review |

### Policy Rules

```yaml
# Rule structure (YAML-based, loaded at startup)
policies:
  - id: discount_cap
    type: quote_proposal
    description: "Maximum discount percentage per line item"
    severity: hard
    rule:
      field: items[*].discount_pct
      max: 20.0
    explanation: "Discount of {value}% exceeds merchant-configured cap of {max}%"

  - id: bundle_discount_cap
    type: quote_proposal
    description: "Maximum total discount on bundle orders"
    severity: hard
    rule:
      field: total_discount_pct
      max: 30.0
    explanation: "Bundle discount of {value}% exceeds allowed {max}%"

  - id: min_order_value
    type: quote_proposal
    description: "Minimum order value to proceed"
    severity: hard
    rule:
      field: total_cents
      min: 10000    # ₹100
    explanation: "Order value ₹{value} below minimum ₹{min}"

  - id: max_order_value
    type: quote_proposal
    description: "Orders above this need manual review"
    severity: soft
    rule:
      field: total_cents
      max: 5000000  # ₹50,000
    explanation: "Order value ₹{value} exceeds auto-approval limit of ₹{max}"

  - id: inventory_check
    type: quote_proposal
    description: "All requested items must be in stock"
    severity: hard
    rule:
      check: for_each(items, product.stock_qty >= item.quantity)
    explanation: "Product {product_id} has {stock} in stock, {requested} requested"

  - id: price_floor
    type: quote_proposal
    description: "Unit price cannot be below cost floor"
    severity: hard
    rule:
      check: for_each(items, item.unit_price >= product.cost_floor_cents)
    explanation: "Price ₹{value} below cost floor ₹{floor}"

  - id: rapid_fire
    type: transaction
    description: "Prevent rapid duplicate transaction attempts"
    severity: hard
    rule:
      check: count(orders WHERE buyer_id = actor.id AND created_at > now - 5min) < 3
    explanation: "Buyer {actor} attempted {count} transactions in 5 minutes"

  - id: negotiation_turn_limit
    type: negotiation
    description: "Maximum turns before auto-resolution"
    severity: soft
    rule:
      check: message_count <= 20
    explanation: "Negotiation exceeded {max} turns, auto-resolving"

  - id: quote_expiry
    type: quote_proposal
    description: "Quotes must have bounded validity"
    severity: hard
    rule:
      field: valid_until
      max_duration_from_now: 30m
    explanation: "Quote validity exceeds 30-minute maximum"
```

### Policy Evaluation Flow

```python
# Pseudocode
def evaluate(action: Action) -> Decision:
    policies = load_policies(action.type)  # filtered by action type
    results = []
    has_hard_failure = False
    has_soft_failure = False

    for policy in policies:
        result = policy.check(action)
        results.append(result)
        if result.passed:
            continue
        if policy.severity == "hard":
            has_hard_failure = True
        elif policy.severity == "soft":
            has_soft_failure = True

    if has_hard_failure:
        return Decision(
            verdict="REJECT",
            reason=first_hard_failure.explanation,
            applied_policies=results
        )
    if has_soft_failure:
        return Decision(
            verdict="ESCALATE",
            reason="Requires manual review",
            applied_policies=results
        )
    return Decision(verdict="APPROVE", applied_policies=results)
```

### Integration Point

Every action that mutates financial state goes through policy:

```
Agent calls propose_quote()
  → QuoteEngine.validate(quote)
    → PolicyEngine.evaluate(QuoteProposalAction)
      → Decision (APPROVE/REJECT/ESCALATE)
  → Store decision with quote
  → Return to agent
```

---

## 7. Transaction State Machine

### States

```
                    ┌──────────────────────────────────────────────────────┐
                    │                                                      │
                    │  ┌──────────┐                                        │
                    │  │ CREATED  │  Quote accepted, order object created  │
                    │  └────┬─────┘                                        │
                    │       │                                              │
                    │       ▼                                              │
                    │  ┌────────────────┐                                  │
                    │  │ PENDING_PAYMENT│  Waiting for buyer to initiate   │
                    │  └────┬───────────┘                                  │
                    │       │                                              │
                    │       ▼                                              │
                    │  ┌──────────────────┐                                │
                    │  │ PAYMENT_INITIATED│  Razorpay order created        │
                    │  └────┬─────────────┘                                │
                    │       │                                              │
                    │       ├──── payment.captured ──▶ ┌──────────────────┐│
                    │       │                          │ PAYMENT_CAPTURED ││
                    │       │                          └────────┬─────────┘│
                    │       │                                   │          │
                    │       │                                   ▼          │
                    │       │                          ┌──────────────┐   │
                    │       │                          │  COMPLETED   │   │
                    │       │                          └──────────────┘   │
                    │       │                                              │
                    │       ├──── payment.failed ──▶ ┌─────────────────┐  │
                    │       │                        │ PAYMENT_FAILED  │  │
                    │       │                        └────────┬────────┘  │
                    │       │                                 │           │
                    │       │                    ┌────────────┼──────────┐│
                    │       │                    │            │          ││
                    │       │                    ▼            ▼          ││
                    │       │           ┌──────────────┐ ┌────────────┐ ││
                    │       │           │   CANCELLED  │ │  REFUNDED  │ ││
                    │       │           └──────────────┘ └────────────┘ ││
                    │       │                                            ││
                    │       └──── timeout ──▶ ┌────────────┐             ││
                    │                         │  CANCELLED  │             ││
                    │                         └────────────┘             ││
                    │                                                      │
                    └──────────────────────────────────────────────────────┘
```

### State Transitions

| From | To | Trigger | Guard | Side Effects |
|------|----|---------|-------|--------------|
| `CREATED` | `PENDING_PAYMENT` | Auto on creation | Quote is approved by policy | Emit `order.created` |
| `PENDING_PAYMENT` | `PAYMENT_INITIATED` | `POST /orders/{id}/pay` | Order not expired (15min) | Create Razorpay order |
| `PAYMENT_INITIATED` | `PAYMENT_CAPTURED` | Razorpay webhook `payment.captured` | Signature valid | Update `paid_at`, reduce stock |
| `PAYMENT_INITIATED` | `PAYMENT_FAILED` | Razorpay webhook `payment.failed` | Signature valid | Update `failed_at`, emit reason |
| `PAYMENT_FAILED` | `PENDING_PAYMENT` | Buyer retries | Retry count < 3 | Increment retry counter |
| `PAYMENT_FAILED` | `CANCELLED` | Buyer cancels or retry exhausted | — | Emit `order.cancelled` |
| `PAYMENT_CAPTURED` | `COMPLETED` | Auto after stock confirmed | Stock decremented | Emit `order.completed` |
| `PENDING_PAYMENT` | `CANCELLED` | Timeout (15min) or buyer cancel | — | Emit `order.cancelled` |
| `PAYMENT_INITIATED` | `CANCELLED` | Timeout (5min no webhook) | — | Emit `order.cancelled` |
| `PAYMENT_CAPTURED` | `REFUNDED` | Admin action | Reason provided | Initiate Razorpay refund |

### Idempotency

```python
# Every payment initiation requires an idempotency_key
# The system guarantees exactly-once processing:

def initiate_payment(order_id: str, idempotency_key: str) -> Order:
    # 1. Check if this key was already processed
    existing = db.query(Order).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return existing  # Return existing, don't create duplicate

    # 2. Acquire distributed lock on order
    with redis.lock(f"order:{order_id}", timeout=30):
        # 3. Verify order is in PENDING_PAYMENT state
        order = db.query(Order).get(order_id)
        if order.status != "PENDING_PAYMENT":
            raise InvalidStateError(f"Order {order.id} is {order.status}")

        # 4. Create Razorpay order
        rp_order = razorpay.create_order(amount=order.total_cents, receipt=order.id)

        # 5. Update order
        order.status = "PAYMENT_INITIATED"
        order.razorpay_order_id = rp_order.id
        order.idempotency_key = idempotency_key
        db.commit()

        # 6. Audit
        audit.log(OrderPaymentInitiated(order_id=order.id, rp_order_id=rp_order.id))

        return order
```

---

## 8. Audit Event Schema

### Event Structure

```json
{
  "id": "evt_a1b2c3d4",
  "event_type": "quote.proposed",
  "aggregate_type": "quote",
  "aggregate_id": "quote_xyz789",
  "actor_id": "buyer_session_abc",
  "actor_type": "buyer",
  "timestamp": "2026-09-05T10:30:00Z",
  "payload": {
    "negotiation_id": "neg_123",
    "items": [
      {"product_id": "prod_a", "quantity": 1, "unit_price_cents": 2499}
    ],
    "total_cents": 2499,
    "discount_pct": 0
  },
  "policy_decision": {
    "verdict": "APPROVE",
    "reason": null,
    "applied_policies": ["discount_cap:PASS", "min_order_value:PASS", "inventory_check:PASS"]
  },
  "explanation": "Buyer proposed a quote for 1x Wireless Headphones at ₹2499. No discount requested. All policy checks passed."
}
```

### Event Types Catalog

```
# Catalog Events
catalog.product.created
catalog.product.updated
catalog.product.deactivated

# Negotiation Events
negotiation.started
negotiation.message_sent
negotiation.quote_proposed
negotiation.quote_accepted
negotiation.quote_rejected
negotiation.resolved
negotiation.cancelled
negotiation.auto_resolved

# Quote Events
quote.proposed
quote.counter_proposed
quote.policy_evaluated
quote.approved
quote.rejected
quote.expired
quote.accepted

# Order Events
order.created
order.payment_initiated
order.payment_captured
order.payment_failed
order.payment_retry
order.cancelled
order.completed
order.refunded

# Policy Events
policy.violation_detected
policy.escalation_required

# Agent Events
agent.tool_called
.agent.tool_result
agent.error
```

### Explanation Generation

Every event with financial impact includes a human-readable `explanation` field:

```python
def explain_quote_proposal(quote: Quote, policy_result: Decision) -> str:
    items_desc = ", ".join(
        f"{item.quantity}x {item.product.name} @ ₹{item.unit_price_cents/100}"
        for item in quote.items
    )
    discount_desc = f" with {quote.discount_cents/100} discount" if quote.discount_cents > 0 else ""
    policy_desc = f"Policy check: {policy_result.verdict}. {policy_result.reason}" if policy_result.reason else "All policy checks passed."

    return (
        f"Quote proposed by {quote.proposed_by} for {items_desc}"
        f"{discount_desc}. Total: ₹{quote.total_cents/100}. {policy_desc}"
    )
```

---

## 9. Threat Model

### STRIDE Analysis

| Threat | Category | Mitigation |
|--------|----------|------------|
| **LLM hallucinates price** | Tampering | Prices always fetched from DB, never from LLM output. Quote engine validates against `products.price_cents`. |
| **LLM approves payment directly** | Elevation of Privilege | Agent tools are sandboxed. No tool exists for "execute payment". Only `accept_quote` exists, which goes through policy engine and requires human confirmation. |
| **Prompt injection in buyer message** | Tampering | Buyer input is sanitized. Agent system prompt includes hard instructions. Tool parameters are typed/validated. LLM output never directly executes SQL or payments. |
| **Duplicate transaction** | Tampering | Idempotency key enforced at DB level (UNIQUE constraint). Redis lock prevents concurrent processing of same order. |
| **Replay attack on webhooks** | Spoofing | Razorpay webhook signature verified (HMAC). Webhook events deduplicated by `payment_id`. |
| **Price manipulation via API** | Tampering | Server-side price calculation. Client sends product_id + quantity, server looks up price. No client-supplied prices accepted. |
| **Excessive discount via negotiation** | Information Disclosure | Policy engine caps discounts. Merchant agent cannot override policy. All discount attempts logged. |
| **Race condition on stock** | Denial of Service | `SELECT ... FOR UPDATE` on stock decrement. Pessimistic locking during payment capture. |
| **Agent escalation bypass** | Elevation of Privilege | ESCALATE verdict blocks action until admin reviews. Admin endpoint requires auth. |
| **Data exfiltration via agent** | Information Disclosure | Agent tools return only public product data. No PII in tool responses. Order details scoped to authorized parties. |
| **Webhook signature bypass** | Spoofing | HMAC-SHA256 verification. Reject requests without valid signature. Constant-time comparison. |
| **LLM cost exhaustion** | Denial of Service | Rate limiting per session. Max turns per negotiation (20). Max tool calls per message (5). |

### Security Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                    TRUST BOUNDARY: External                 │
│                                                             │
│  Buyer Agent ◄──── untrusted input ──── Buyer (human)       │
│  Merchant Agent ◄── untrusted input ── Merchant (human)     │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                    TRUST BOUNDARY: Platform                 │
│                                                             │
│  API Gateway ──▶ Auth ──▶ Rate Limit ──▶ Validation        │
│                                                             │
│  Policy Engine (deterministic, audited)                     │
│  Transaction State Machine (guarded transitions)            │
│  Razorpay Client (test-mode, signature verified)            │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                    TRUST BOUNDARY: Payment                  │
│                                                             │
│  Razorpay Test Mode                                         │
│  - Webhook signature verified                               │
│  - Payment amounts server-validated                         │
│  - No real money movement                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### What the LLM Can and Cannot Do

| Action | Allowed? | How Enforced |
|--------|----------|--------------|
| Search products | Yes | Tool returns from DB, no user input in SQL |
| Suggest products | Yes | Recommendation engine, not LLM pricing |
| Propose a quote | Yes | Quote engine validates prices from DB, applies policy |
| Apply discount | Limited | Policy engine caps max discount per merchant config |
| Execute payment | **NO** | No tool exists. Only human can confirm. |
| Refund payment | **NO** | Only admin endpoint. |
| Modify prices | **NO** | Prices come from DB only. |
| Access PII | **NO** | Agent tools don't return PII. |
| Bypass policy | **NO** | Policy evaluation is deterministic, outside LLM context. |

---

## 10. Demo Flow

### Scenario: Buyer wants wireless headphones

```
STEP 1: CATALOG SEED
─────────────────────
Merchant "TechZone" adds products:
  - Wireless Headphones Pro (₹2499) — ai_schema: { category: "headphones", wireless: true, gym: true }
  - Wireless Earbuds Lite (₹1499) — ai_schema: { category: "earbuds", wireless: true, budget: true }
  - Headphone Carrying Case (₹299) — ai_schema: { category: "accessory", compatible: ["headphones"] }
  - Premium Replacement Tips (₹149) — ai_schema: { category: "accessory", compatible: ["earbuds"] }
  - Bluetooth Audio Cable (₹499) — ai_schema: { category: "accessory", wired_backup: true }

Upsell links:
  - Headphones Pro → Carrying Case (accessory)
  - Earbuds Lite → Replacement Tips (accessory)
  - Headphones Pro → Audio Cable (cross_sell)
  - Headphones Pro → Earbuds Lite (upsell comparison)

Bundle:
  - "Gym Audio Kit": Headphones Pro + Carrying Case + Audio Cable = ₹2799 (saves ₹498, 15% off)

STEP 2: BUYER INITIATES
────────────────────────
Buyer agent receives: "I need good wireless headphones for gym, budget around 3000"

Agent calls: search_catalog(query="wireless headphones gym", max_price=300000)
Returns: [
  { id: "prod_a", name: "Wireless Headphones Pro", price: 2499, relevance: 0.95 },
  { id: "prod_b", name: "Wireless Earbuds Lite", price: 1499, relevance: 0.78 }
]

Agent calls: get_recommendations(product_id="prod_a", relationship="accessory")
Returns: [
  { id: "prod_c", name: "Carrying Case", price: 299 },
  { id: "prod_e", name: "Audio Cable", price: 499 }
]

Agent calls: get_recommendations(product_id="prod_a", type="bundle")
Returns: [
  { id: "bundle_gym", name: "Gym Audio Kit", price: 2799, savings: 498 }
]

STEP 3: NEGOTIATION
───────────────────
POST /negotiations { buyer_id: "buyer_001", merchant_id: "merchant_techzone" }
→ Negotiation created, status: "active"

Buyer agent messages: "Hi, I'm looking at the Wireless Headphones Pro. Is there any discount available?"

Merchant agent responds: "Great choice! The Pro has 40h battery and IPX7 waterproof — perfect for gym. We also have a Gym Audio Kit bundle at ₹2799 that includes the case and audio cable. That saves you ₹498 compared to buying separately."

Buyer agent: "The bundle looks good. Can you do 2700 for everything?"

Merchant agent calls: counter_propose(items=[...], discount_pct=3.3, message="I can offer ₹2750 for the bundle — that's my best price.")

Quote generated: { subtotal: 3297, discount: 547, total: 2750 }

STEP 4: POLICY GATE
────────────────────
PolicyEngine.evaluate(QuoteProposalAction):
  ✓ discount_cap: PASS (16.6% < 30% max for bundles)
  ✓ min_order_value: PASS (₹2750 > ₹100)
  ✓ inventory_check: PASS (all items in stock)
  ✓ price_floor: PASS (above cost)
  ✓ quote_expiry: PASS (30min validity)
→ Verdict: APPROVE

Quote stored with policy_decision: "APPROVE"

STEP 5: ACCEPT & PAY
─────────────────────
Buyer agent: "Deal! Let's proceed."

POST /negotiations/{id}/accept-quote { quote_id: "quote_xyz" }
→ Order created (status: "created")
→ Policy evaluated again at order creation → APPROVE

POST /orders/{order_id}/pay { payment_method: "upi" }
→ Idempotency key generated
→ Razorpay test-mode order created
→ Returns: { razorpay_order_id: "order_rp_abc", amount: 275000, key: "rzp_test_..." }

STEP 6: PAYMENT (Razorpay Checkout)
─────────────────────────────────────
Frontend opens Razorpay Checkout (test mode)
→ Buyer completes test payment
→ Razorpay sends webhook: payment.captured

POST /webhooks/razorpay
→ Signature verified
→ Order updated: status = "PAYMENT_CAPTURED"
→ Stock decremented
→ Audit event logged

STEP 7: EXPLANATION
────────────────────
GET /orders/{id}/explanation
→ Returns step-by-step explanation:
  1. "Buyer searched for wireless headphones for gym under ₹3000"
  2. "System recommended Wireless Headphones Pro (₹2499) with Gym Audio Kit bundle"
  3. "Buyer negotiated bundle price from ₹2799 to ₹2750"
  4. "Policy engine approved: discount within bounds, all items in stock"
  5. "Payment initiated via UPI test mode, order ₹275000 (paise)"
  6. "Razorpay confirmed payment, order completed"
  7. "Stock decremented: Headphones Pro (1), Carrying Case (1), Audio Cable (1)"
```

---

## 11. Implementation Phases

### Phase 1: Foundation (Day 1 — Morning)

**Goal:** Data layer + basic CRUD API

```
Tasks:
├── Initialize FastAPI project with dependencies
├── Set up PostgreSQL + SQLAlchemy models (all tables)
├── Create Alembic migrations
├── Implement catalog CRUD endpoints
├── Seed sample products with AI schemas
├── Add basic auth middleware (API key)
└── Write integration tests for catalog API

Deliverable:
  - Running FastAPI server
  - Product CRUD working
  - Database seeded with demo products
```

### Phase 2: AI Catalog + Recommendations (Day 1 — Afternoon)

**Goal:** AI-readable catalog + recommendation engine

```
Tasks:
├── Implement AI schema endpoint per product
├── Build recommendation service (up-sell, cross-sell, bundles)
├── Implement upsell link management
├── Build bundle management
├── Add search endpoint with intent parsing
└── Test with sample queries

Deliverable:
  - Products have structured AI schemas
  - Recommendation engine returns relevant products
  - Upsell/cross-sell links work
```

### Phase 3: Negotiation + Agents (Day 2 — Morning)

**Goal:** Multi-turn negotiation with agent orchestration

```
Tasks:
├── Implement negotiation session management
├── Build message persistence layer
├── Create buyer agent with tool definitions
├── Create merchant agent with tool definitions
├── Implement tool execution pipeline
├── Wire agents to negotiation flow
├── Add message forwarding between agents
└── Test: buyer searches → agent responds → negotiate → propose quote

Deliverable:
  - Two agents can negotiate via messages
  - Agents call tools correctly
  - Quote proposals generated from agent actions
```

### Phase 4: Policy Engine (Day 2 — Afternoon)

**Goal:** Deterministic policy evaluation

```
Tasks:
├── Build policy rule parser (YAML → rule objects)
├── Implement rule evaluation pipeline
├── Create all policy rules (discount, inventory, fraud, etc.)
├── Wire policy engine into quote generation
├── Add ESCALATE flow (admin review endpoint)
├── Write tests for each policy rule
└── Test: malicious quotes get rejected, valid ones pass

Deliverable:
  - Policy engine blocks bad quotes
  - Every quote has a policy_decision attached
  - Explanations generated for each decision
```

### Phase 5: Transactions (Day 3 — Morning)

**Goal:** Razorpay integration + state machine

```
Tasks:
├── Implement order state machine
├── Build Razorpay client (test mode)
├── Implement idempotency layer (Redis locks)
├── Create payment initiation endpoint
├── Create webhook handler (payment.captured, payment.failed)
├── Implement stock decrement on successful payment
├── Add payment retry logic
├── Test: full payment flow in Razorpay test mode
└── Test: duplicate transaction prevention

Deliverable:
  - Complete payment flow works in test mode
  - Webhooks handled correctly
  - Idempotency verified
  - Stock managed correctly
```

### Phase 6: Audit + Explanations (Day 3 — Afternoon)

**Goal:** Complete audit trail + explainability

```
Tasks:
├── Implement audit event logger (append-only)
├── Wire audit logging into all modules
├── Build explanation generator for financial decisions
├── Create admin audit query endpoint
├── Create order explanation endpoint
├── Add audit events to all state transitions
└── Test: query full history of a transaction

Deliverable:
  - Every action logged as audit event
  - Every financial decision explained
  - Admin can query full audit trail
```

### Phase 7: Frontend + Demo Polish (Day 4)

**Goal:** Working demo UI

```
Tasks:
├── Build minimal admin dashboard (React)
│   ├── Product management view
│   ├── Negotiation monitor
│   ├── Order list with status
│   └── Audit log viewer
├── Build buyer-facing demo page
│   ├── Chat interface for agent negotiation
│   ├── Product catalog view
│   └── Payment flow (Razorpay checkout)
├── Build merchant demo view
│   └── Incoming negotiations + responses
├── Create demo script / walkthrough
└── Record demo video

Deliverable:
  - Working UI for all three personas
  - End-to-end demo flow
  - Demo script ready for presentation
```

### Phase 8: Hardening (Day 5 — If Time)

**Goal:** Production-quality polish

```
Tasks:
├── Rate limiting
├── Error handling middleware
├── Structured logging
├── Health check endpoints
├── API documentation (OpenAPI)
├── Load test with concurrent negotiations
├── Security review
└── Final integration test suite

Deliverable:
  - Robust error handling
  - Clean API docs
  - Security verified
```

---

## Appendix A: Technology Choices

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | FastAPI ecosystem, LLM SDK support |
| Framework | FastAPI | Async, auto-docs, Pydantic validation |
| ORM | SQLAlchemy 2.0 | Mature, async support, Alembic migrations |
| Database | PostgreSQL | JSONB for AI schemas, ACID, reliable |
| Cache/Lock | Redis | Distributed locks for idempotency |
| LLM | OpenAI GPT-4o or Claude | Tool calling support, reasoning |
| Payment | Razorpay SDK (test) | Hackathon requirement, well-documented |
| Validation | Pydantic v2 | Request/response schemas, settings |
| Testing | pytest + httpx | FastAPI test client |
| Migrations | Alembic | Database versioning |

## Appendix B: Environment Variables

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/merchant_platform

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Razorpay (test mode)
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...

# App
APP_ENV=development
APP_PORT=8000
APP_SECRET_KEY=...
```

## Appendix C: Dependencies (pyproject.toml)

```toml
[project]
name = "razorpay-merchant-platform"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "redis[hiredis]>=5.0.0",
    "openai>=1.50.0",
    "razorpay>=1.4.0",
    "httpx>=0.27.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
```
