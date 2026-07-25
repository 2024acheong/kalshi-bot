from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ARTIFACT_DIR = os.getenv("MODEL_ARTIFACT_DIR", "services/models/artifacts")
MODEL_ARTIFACT_BUCKET = os.getenv("MODEL_ARTIFACT_BUCKET", "model-artifacts")
SUPABASE_URI_SCHEME = "supabase"


def save_artifact(model: Any, name: str, version: str) -> str:
    """
    Serialize model to ARTIFACT_DIR/{name}/{version}/model.pkl and optional storage.

    Directories are created as needed. The returned path is a string so it can
    be stored directly in model_registry.artifact_path.
    """
    artifact_path = Path(ARTIFACT_DIR) / name / version / "model.pkl"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = pickle.dumps(model)
    with artifact_path.open("wb") as file:
        file.write(payload)

    if _should_upload_to_supabase():
        object_path = f"{name}/{version}/model.pkl"
        _upload_supabase_artifact(object_path, payload)
        return _supabase_uri(MODEL_ARTIFACT_BUCKET, object_path)

    return str(artifact_path)


def load_artifact(artifact_path: str) -> Any:
    """Load and return a pickled model from the given path."""
    if artifact_path.startswith(f"{SUPABASE_URI_SCHEME}://"):
        bucket, object_path = _parse_supabase_uri(artifact_path)
        return pickle.loads(_download_supabase_artifact(bucket, object_path))

    with Path(artifact_path).open("rb") as file:
        return pickle.load(file)


def _should_upload_to_supabase() -> bool:
    backend = os.getenv("MODEL_ARTIFACT_STORAGE", "auto").strip().lower()
    if backend == "local":
        return False
    if backend == "supabase":
        return True
    if backend != "auto":
        raise ValueError("MODEL_ARTIFACT_STORAGE must be one of: auto, local, supabase")
    has_url = bool(os.getenv("SUPABASE_URL"))
    has_key = bool(os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SECRET_KEY"))
    return has_url and has_key


def _supabase_uri(bucket: str, object_path: str) -> str:
    return f"{SUPABASE_URI_SCHEME}://{bucket}/{object_path}"


def _parse_supabase_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != SUPABASE_URI_SCHEME or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid Supabase artifact URI: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def _bucket_name(bucket: Any) -> str | None:
    if isinstance(bucket, dict):
        value = bucket.get("name") or bucket.get("id")
    else:
        value = getattr(bucket, "name", None) or getattr(bucket, "id", None)
    return str(value) if value is not None else None


def _ensure_supabase_bucket(client: Any, bucket: str) -> None:
    try:
        existing = client.storage.list_buckets()
        if any(_bucket_name(item) == bucket for item in existing):
            return
    except Exception:
        pass

    try:
        client.storage.create_bucket(bucket, options={"public": False})
    except Exception as exc:
        text = str(exc).lower()
        if "already exists" not in text and "duplicate" not in text:
            raise


def _upload_supabase_artifact(object_path: str, payload: bytes) -> None:
    from services.models.shared.model_registry import get_supabase_client

    client = get_supabase_client()
    _ensure_supabase_bucket(client, MODEL_ARTIFACT_BUCKET)
    client.storage.from_(MODEL_ARTIFACT_BUCKET).upload(
        object_path,
        payload,
        {
            "content-type": "application/octet-stream",
            "upsert": "true",
        },
    )


def _download_supabase_artifact(bucket: str, object_path: str) -> bytes:
    from services.models.shared.model_registry import get_supabase_client

    client = get_supabase_client()
    return client.storage.from_(bucket).download(object_path)


def load_first_available_artifact(
    registry_entries: list[dict[str, Any]],
    *,
    model_name: str,
    logger: Any,
) -> Any | None:
    """
    Load the newest registry artifact that exists on this filesystem.

    Training jobs and workers may not share a filesystem, so a fresh registry row
    can point at a pickle that is absent in the worker deployment.
    """
    for entry in registry_entries:
        artifact_path = str(entry.get("artifact_path") or "")
        if not artifact_path:
            continue
        try:
            return load_artifact(artifact_path)
        except FileNotFoundError:
            logger.warning(
                "Registered %s artifact missing locally: version=%s path=%s",
                model_name,
                entry.get("version"),
                artifact_path,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load %s artifact version=%s path=%s: %s",
                model_name,
                entry.get("version"),
                artifact_path,
                exc,
            )
    return None
