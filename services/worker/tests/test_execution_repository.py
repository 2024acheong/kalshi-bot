from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from worker.execution_repository import (
    _get_supabase,
    create_strategy_run,
    ensure_strategy_config,
)
from worker.storage import _get_supabase_credentials


class FakeTable:
    def __init__(self, response_id: str) -> None:
        self.response_id = response_id
        self.insert_calls: list[dict] = []
        self.upsert_calls: list[tuple[dict, str | None]] = []

    def insert(self, row: dict):
        self.insert_calls.append(row)
        return self

    def upsert(self, row: dict, on_conflict: str | None = None):
        self.upsert_calls.append((row, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=[{"id": self.response_id}])


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, FakeTable] = {}

    def table(self, name: str) -> FakeTable:
        return self.tables[name]


def test_get_supabase_uses_storage_module(monkeypatch) -> None:
    sentinel = object()
    storage = ModuleType("worker.storage")
    storage.supabase = sentinel

    monkeypatch.setitem(sys.modules, "worker.storage", storage)

    assert _get_supabase() is sentinel


def test_storage_accepts_supabase_secret_key(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "secret-key")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert _get_supabase_credentials() == ("https://example.supabase.co", "secret-key")


def test_ensure_strategy_config_upserts_and_returns_id(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["strategy_configs"] = FakeTable(response_id="config-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    config_id = ensure_strategy_config("dummy_strategy", 1, params={"qty": 10})

    assert config_id == "config-1"
    table = fake.tables["strategy_configs"]
    assert table.insert_calls == []
    assert table.upsert_calls == [
        (
            {
                "name": "dummy_strategy",
                "version": 1,
                "params_json": {"qty": 10},
            },
            "name,version",
        )
    ]


def test_create_strategy_run_inserts_config_and_mode(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["strategy_runs"] = FakeTable(response_id="run-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    run_id = create_strategy_run(config_id="config-1", mode="paper")

    assert run_id == "run-1"
    assert fake.tables["strategy_runs"].insert_calls == [
        {
            "config_id": "config-1",
            "mode": "paper",
        }
    ]
