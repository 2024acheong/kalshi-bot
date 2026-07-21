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
