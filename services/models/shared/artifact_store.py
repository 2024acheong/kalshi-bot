from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

ARTIFACT_DIR = os.getenv("MODEL_ARTIFACT_DIR", "services/models/artifacts")


def save_artifact(model: Any, name: str, version: str) -> str:
    """
    Serialize model to ARTIFACT_DIR/{name}/{version}/model.pkl.

    Directories are created as needed. The returned path is a string so it can
    be stored directly in model_registry.artifact_path.
    """
    artifact_path = Path(ARTIFACT_DIR) / name / version / "model.pkl"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("wb") as file:
        pickle.dump(model, file)
    return str(artifact_path)


def load_artifact(artifact_path: str) -> Any:
    """Load and return a pickled model from the given path."""
    with Path(artifact_path).open("rb") as file:
        return pickle.load(file)


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
