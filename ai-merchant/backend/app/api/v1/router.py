"""API v1 routes for Vend.ai."""

from fastapi import APIRouter

router = APIRouter(tags=["v1"])


@router.get("/health")
async def health():
    return {"status": "ok at v1"}