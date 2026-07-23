from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType, SimpleNamespace

from worker.execution_repository import (
    _get_supabase,
    close_position,
    create_strategy_run,
    ensure_strategy_config,
    load_open_positions,
    persist_open_position,
)
from worker.storage import _get_supabase_credentials


class FakeTable:
    def __init__(self, response_id: str, response_data: list[dict] | None = None) -> None:
        self.response_id = response_id
        self.response_data = response_data
        self.insert_calls: list[dict] = []
        self.upsert_calls: list[tuple[dict, str | None]] = []
        self.update_calls: list[dict] = []
        self.filters: list[tuple[str, str, object]] = []
        self.select_calls: list[str] = []

    def insert(self, row: dict):
        self.insert_calls.append(row)
        return self

    def upsert(self, row: dict, on_conflict: str | None = None):
        self.upsert_calls.append((row, on_conflict))
        return self

    def update(self, row: dict):
        self.update_calls.append(row)
        return self

    def select(self, columns: str):
        self.select_calls.append(columns)
        return self

    def eq(self, column: str, value):
        self.filters.append(("eq", column, value))
        return self

    def gt(self, column: str, value):
        self.filters.append(("gt", column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.response_data or [{"id": self.response_id}])


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


def test_persist_open_position_upserts_position_metadata(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["positions"] = FakeTable(response_id="position-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    position_id = persist_open_position(
        run_id="run-1",
        ticker="KXTEST",
        side="yes",
        qty=5,
        avg_entry=Decimal("0.42"),
        opened_at=opened_at,
        metadata={"strategy_position_type": "mean_reversion"},
    )

    assert position_id == "position-1"
    table = fake.tables["positions"]
    row, conflict = table.upsert_calls[0]
    assert conflict == "run_id,ticker,side"
    assert row["run_id"] == "run-1"
    assert row["ticker"] == "KXTEST"
    assert row["side"] == "yes"
    assert row["qty"] == 5
    assert row["avg_entry"] == "0.42"
    assert row["metadata_json"] == {"strategy_position_type": "mean_reversion"}


def test_close_position_zeroes_qty(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["positions"] = FakeTable(response_id="position-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    close_position("run-1", "KXTEST", "yes")

    table = fake.tables["positions"]
    assert table.update_calls[0]["qty"] == 0
    assert table.filters == [
        ("eq", "run_id", "run-1"),
        ("eq", "ticker", "KXTEST"),
        ("eq", "side", "yes"),
    ]


def test_load_open_positions_filters_by_run_and_positive_qty(monkeypatch) -> None:
    rows = [{"run_id": "run-1", "ticker": "KXTEST", "qty": 5}]
    fake = FakeSupabase()
    fake.tables["positions"] = FakeTable(response_id="position-1", response_data=rows)
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    result = load_open_positions("run-1")

    assert result == rows
    assert fake.tables["positions"].filters == [
        ("eq", "run_id", "run-1"),
        ("gt", "qty", 0),
    ]
