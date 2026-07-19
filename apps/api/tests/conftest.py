import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / "apps/api", ROOT / "packages/core"):
    sys.path.insert(0, str(path))

from api.auth import create_access_token
from api.main import app


@pytest.fixture(autouse=True)
def api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("OPERATOR_PASSWORD", "correct-password")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token("operator")
    return {"Authorization": f"Bearer {token}"}
