from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:
    from supabase import create_client
except ImportError:  # pragma: no cover - optional in lightweight test envs
    create_client = None  # type: ignore[assignment]


@lru_cache(maxsize=1)
def get_supabase() -> Any:
    if create_client is None:
        raise RuntimeError("supabase>=2.4 is required for execution persistence")

    url, key = _get_supabase_credentials()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and a Supabase secret/service key are required for execution persistence"
        )

    return create_client(url, key)


def _get_supabase_credentials() -> tuple[str | None, str | None]:
    return (
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SECRET_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )


class _LazySupabase:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_supabase(), name)


supabase = _LazySupabase()
