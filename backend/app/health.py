from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving. Deliberately touches nothing else
    (database, LLM, telephony), so a dependency outage never marks the process dead."""
    return {"status": "ok"}
