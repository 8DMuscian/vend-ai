# Vend.ai

> Razorpay AI Growth & Agentic Commerce Hackathon

An AI-native merchant platform where buyer and merchant agents negotiate in natural language, with deterministic policy enforcement ensuring every financial action is safe, explainable, and auditable.

## What It Does

1. **AI-Readable Catalog** — Products with structured metadata that agents can search and reason about.
2. **Natural Language Negotiation** — Buyers describe what they want; agents find products, suggest bundles, and negotiate prices.
3. **Bounded Quotes** — Policy engine caps discounts, validates inventory, and prevents fraud before any quote is finalized.
4. **Safe Transactions** — Razorpay test-mode payments with idempotency guarantees and automatic failure handling.
5. **Complete Audit Trail** — Every action, every decision, every financial move is logged with a human-readable explanation.

## Core Invariant

**The LLM proposes. Deterministic code disposes.**

The LLM never touches money directly. Every financial action passes through a policy engine that operates independently of the LLM's reasoning.

## Quick Start

```bash
# 1. Clone and enter the project
cd ai-merchant

# 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# 3. Set up database
createdb ai_merchant
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/ai_merchant"
alembic upgrade head

# 4. Seed sample data
python -m seed.seed_products

# 5. Configure Razorpay test credentials
export RAZORPAY_KEY_ID="rzp_test_..."
export RAZORPAY_KEY_SECRET="..."
export RAZORPAY_WEBHOOK_SECRET="..."

# 6. Run the server
uvicorn backend.app.main:app --reload --port 8000

# 7. Run tests
pytest backend/tests/ -v
```

## Project Structure

```
ai-merchant/
├── AGENTS.md              # Engineering constitution (READ THIS FIRST)
├── README.md
├── docs/
│   ├── architecture.md    # System design, schema, API contracts
│   ├── product.md         # Product requirements, user stories
│   ├── security.md        # Threat model, security rules
│   └── demo.md            # Step-by-step demo walkthrough
├── backend/
│   ├── app/
│   │   ├── agents/        # LLM agent orchestration
│   │   ├── api/           # FastAPI route handlers
│   │   ├── models/        # SQLAlchemy ORM models
│   │   ├── services/      # Business logic
│   │   ├── policies/      # Deterministic policy engine
│   │   ├── tools/         # Agent tool implementations
│   │   └── audit/         # Event logging + explanations
│   └── tests/
├── frontend/              # Demo UI (React)
└── seed/                  # Database seed scripts
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12+ |
| Framework | FastAPI |
| Database | PostgreSQL + SQLAlchemy 2.0 |
| Cache | Redis |
| LLM | OpenAI GPT-4o / Anthropic Claude |
| Payments | Razorpay SDK (test mode) |
| Validation | Pydantic v2 |

## Documentation

- [Architecture](docs/architecture.md) — System design, database schema, API contracts, state machine
- [Product](docs/product.md) — Requirements, user stories, acceptance criteria
- [Security](docs/security.md) — Threat model, security boundaries, LLM sandbox rules
- [Demo](docs/demo.md) — Step-by-step demo walkthrough with sample data

## License

Hackathon use only. Not for production deployment.
