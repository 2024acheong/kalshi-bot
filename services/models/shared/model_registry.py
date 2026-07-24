from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

try:
    from supabase import create_client
except ImportError:  # pragma: no cover - optional in lightweight test envs
    create_client = None  # type: ignore[assignment]

load_dotenv()


@lru_cache(maxsize=1)
def get_supabase_client() -> Any:
    if create_client is None:
        raise RuntimeError("supabase>=2.4 is required for model registry access")

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY or SUPABASE_SECRET_KEY are required"
        )

    return create_client(url, key)


def register_model(
    name: str,
    version: str,
    artifact_path: str,
    train_metrics: dict,
) -> str:
    """
    Insert a row into model_registry and return the new row's id.

    This service creates its own Supabase client from SUPABASE_URL and
    SUPABASE_SERVICE_KEY/SUPABASE_SECRET_KEY so it does not depend on worker internals.
    """
    row = {
        "name": name,
        "version": version,
        "artifact_path": artifact_path,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_metrics_json": train_metrics,
    }
    response = get_supabase_client().table("model_registry").insert(row).execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise RuntimeError("model_registry insert returned no rows")
    return str(rows[0]["id"])


def get_latest_model(name: str) -> dict[str, Any] | None:
    """
    Return the most recently trained registry row for name, or None.
    """
    rows = get_recent_models(name, limit=1)
    return rows[0] if rows else None


def get_recent_models(name: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Return recent registry rows for name, newest first.
    """
    response = (
        get_supabase_client()
        .table("model_registry")
        .select("id,version,artifact_path,train_metrics_json")
        .eq("name", name)
        .order("trained_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return list(rows)
