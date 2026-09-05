"""FastAPI application entry point - AGENTS.md compliant."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import product, merchant, negotiation, quote, order, audit  # noqa: F401
from app.api.v1 import api_router
from app.api.v1.catalog_router import catalog_router


# ── Structured logging ──────────────────────────────────────────────
# JSON logs written to stderr – AGENTS.md: "No secrets in source code.
# Structured logging aids audit and observability."
logger = logging.getLogger("ai_merchant")
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%{"level":"%(levelname)s","time":"%(asctime)s","name":"%(name)s,"message":"%(message)s"}'
)
handler.setFormatter(formatter)
logger.handlers = []
logger.addHandler(handler)
logger.setLevel(logging.INFO)


app = FastAPI(
    title="AI Merchant Platform",
    version="0.1.0",
    description="Razorpay AI Growth Hackathon – Merchant Platform",
)

# CORS – configured per environment, never wildcard + credentials in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)

# Include API routes (versioned)
app.include_router(api_router, prefix="/api/v1")

# Include AI catalog routes (already has /api/v1/ai prefix)
app.include_router(catalog_router)

# Health endpoint (unversioned, for load balangers)
@app.get("/health", include_in_schema=False)
async def health():
    logger.info("health_check_requested", extra={"service": "ai-merchant-platform", "env": settings.app_env})
    return {
        "status": "healthy",
        "service": "ai-merchant-platform",
        "env": settings.app_env,
    }

# Root
@app.get("/", include_in_schema=False)
async def root():
    return {"message": "AI Merchant Platform", "version": "0.1.0"}