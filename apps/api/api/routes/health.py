from functools import lru_cache

from fastapi import APIRouter
from supabase import Client

from api.redis_client import redis_client
from api.routes.strategy import get_supabase_client

router = APIRouter(tags=["health"])


@lru_cache(maxsize=1)
def _health_supabase_client() -> Client:
    return get_supabase_client()


async def _redis_ok() -> bool:
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False


def _supabase_ok() -> bool:
    try:
        response = (
            _health_supabase_client()
            .table("strategy_configs")
            .select("id")
            .limit(1)
            .execute()
        )
    except Exception:
        return False
    return getattr(response, "data", None) is not None


@router.get("/health")
async def health() -> dict[str, bool | str]:
    redis = await _redis_ok()
    supabase = _supabase_ok()
    if redis and supabase:
        return {"status": "ok", "redis": True, "supabase": True}
    return {"status": "degraded", "redis": redis, "supabase": supabase}
