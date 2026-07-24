from __future__ import annotations

import sys
from pathlib import Path


def add_repo_paths() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    for path in (
        repo_root,
        repo_root / "packages" / "core",
        repo_root / "apps" / "api",
        repo_root / "services" / "models",
        repo_root / "services" / "worker",
        repo_root / "services" / "research",
    ):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    return repo_root
