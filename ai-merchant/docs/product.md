# Product Requirements

> AI-Native Merchant Platform for Razorpay AI Growth & Agentic Commerce Hackathon

---

## Vision

Enable any merchant to have an AI-powered sales agent that can negotiate with buyer agents in natural language, recommend products, generate quotes, and close transactions — all while keeping humans in control of financial decisions.

---

## User Personas

### Buyer

A person looking to purchase products. They interact via natural language, describing what they need, comparing options, and negotiating prices.

**Pain points:**
- Overwhelmed by large catalogs
- Doesn't know what accessories/bundles exist
- Wants to negotiate but lacks time
- Needs confidence in pricing fairness

### Merchant

A seller who wants to maximize revenue while providing good service. They configure their catalog, set discount limits, and let their AI agent handle routine negotiations.

**Pain points:**
- Repetitive buyer questions
- Manual quote generation
- Missing upsell opportunities
- No visibility into negotiation patterns

### Admin

Platform operator who monitors transactions, reviews escalated negotiations, and ensures policy compliance.

**Pain points:**
- Fraud detection
- Dispute resolution
- Policy tuning

---

## Core Features

### 1. AI-Readable Merchant Catalog

**User Story:** As a buyer agent, I can search a merchant's catalog with natural language and get structured product data.

**Acceptance Criteria:**
- Every product has an `ai_schema` JSONB field with structured metadata
- Products can be searched by intent, category, price range, tags
- AI schema includes: category, tags, specs, target_audience, use_cases, comparison_notes
- Products have upsell/cross-sell relationships defined
- Bundles are available with discount pricing

### 2. Natural Language Buyer Intent

**User Story:** As a buyer, I can describe what I want in plain English and get relevant product recommendations.

**Acceptance Criteria:**
- Buyer agent parses intent: category, features, budget, use case
- System returns ranked product matches
- Agent explains why each product matches
- Agent suggests relevant accessories/bundles

### 3. Product Recommendations

**User Story:** As a buyer, I discover related products I didn't know I needed.

**Acceptance Criteria:**
- Recommendations based on: upsell links, cross-sell links, bundle contents
- Recommendations are context-aware (e.g., gym use → waterproof products)
- Agent can explain why a product is recommended
- Recommendations respect budget constraints

### 4. Upsell/Cross-sell/Bundle Identification

**User Story:** As a merchant, my agent automatically suggests higher-margin or complementary products.

**Acceptance Criteria:**
- Merchant defines upsell/cross-sell relationships between products
- Merchant defines bundles with discount pricing
- Agent proactively suggests during negotiation
- Buyer can accept/reject suggestions without restarting negotiation

### 5. Bounded Quote Generation

**User Story:** As a buyer, I receive a quote that is fair, transparent, and within policy bounds.

**Acceptance Criteria:**
- Quote includes: items, quantities, unit prices, subtotal, discount, tax, total
- All prices are looked up from the database (not LLM-generated)
- Discount is capped by merchant configuration
- Quote has bounded validity (max 30 minutes)
- Quote includes policy_decision field showing approval/rejection reason

### 6. Buyer-Merchant Agent Negotiation

**User Story:** As a buyer, my agent negotiates with the merchant's agent to get the best deal.

**Acceptance Criteria:**
- Multi-turn conversation between agents
- Each agent has scoped tool permissions
- Messages are persisted with role (buyer/merchant/system)
- Maximum 20 turns before auto-resolution
- Human can intervene at any point
- Counter-proposals go through policy engine

### 7. Deterministic Policy Approval

**User Story:** As an admin, I know every financial action was reviewed by deterministic rules before execution.

**Acceptance Criteria:**
- Policy engine evaluates every quote proposal
- Rules include: discount cap, inventory check, price floor, fraud detection, rate limiting
- Hard violations block the action
- Soft violations escalate to admin
- Every decision is logged with explanation
- Policy engine operates independently of LLM

### 8. Razorpay Test-Mode Transactions

**User Story:** As a buyer, I can complete a purchase using Razorpay test mode.

**Acceptance Criteria:**
- Razorpay SDK integration (test mode only)
- Payment initiation creates Razorpay order
- Webhook handles payment.captured and payment.failed
- Signature verification on webhooks
- Test UPI/card/netbanking flows work

### 9. Payment Failure Handling

**User Story:** As a buyer, if my payment fails, I can retry without being double-charged.

**Acceptance Criteria:**
- Idempotency key required for every payment initiation
- UNIQUE constraint on idempotency_key in orders table
- Redis distributed lock prevents concurrent processing
- Failed payment allows retry (up to 3 attempts)
- No duplicate Razorpay orders for same idempotency key
- Failed payments logged with reason

### 10. Complete Audit Trail

**User Story:** As an admin, I can trace every action back to its origin.

**Acceptance Criteria:**
- Append-only audit_events table
- Every financial action creates an audit event
- Events include: event_type, aggregate_type/id, actor, payload, explanation
- Events are queryable by aggregate, type, time range
- No event can be modified or deleted

### 11. Financial Decision Explanation

**User Story:** As a buyer or admin, I can understand why a financial decision was made.

**Acceptance Criteria:**
- Every quote includes policy_decision with reason
- Every order has an explanation endpoint
- Explanations are human-readable, not technical
- Explanations trace the full decision path

### 12. LLM Cannot Control Money

**User Story:** As an admin, I am confident the LLM cannot execute unauthorized financial actions.

**Acceptance Criteria:**
- No tool exists for "execute payment" in any agent
- Prices are always fetched from DB, never from LLM output
- Discount calculations are server-side
- Policy engine is deterministic, no LLM involvement
- All financial mutations create audit events

---

## Non-Functional Requirements

### Performance
- API response time < 200ms (excluding LLM calls)
- LLM response time < 5s (depends on provider)
- Quote generation < 500ms
- Policy evaluation < 100ms

### Reliability
- No duplicate transactions (idempotency)
- Audit trail is append-only (no data loss)
- Graceful handling of LLM timeouts
- Graceful handling of Razorpay downtime

### Security
- No secrets in source code
- API key authentication for merchants
- Razorpay webhook signature verification
- Input validation on all endpoints
- Rate limiting per buyer session

### Observability
- Structured logging (JSON)
- Request ID tracking
- Audit event queryability
- Policy decision tracking

---

## Success Metrics

| Metric | Target |
|--------|--------|
| End-to-end demo flow | < 2 minutes |
| Policy evaluation | < 100ms |
| No duplicate transactions | 100% |
| Audit trail completeness | 100% of financial actions |
| LLM money control | 0 (never happens) |
