from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType, SimpleNamespace

from worker.execution_repository import (
    _ENSURED_CATALOG_TICKERS,
    _get_supabase,
    close_position,
    create_strategy_run,
    ensure_market_catalog_entry,
    ensure_strategy_config,
    estimate_order_cash,
    get_or_create_paper_account,
    load_open_positions,
    paper_account_available_cash,
    persist_open_position,
    persist_signal,
    persist_worker_heartbeat,
    record_paper_fill_accounting,
    release_paper_order_cash,
    reserve_paper_order_cash,
    reset_paper_account,
)
from core.execution.adapters import FillResult
from core.risk.engine import OrderIntent
from core.schemas.market import OrderIntentStatus
from worker.storage import _get_supabase_credentials


class FakeTable:
    def __init__(
        self,
        response_id: str,
        response_data: list[dict] | None = None,
        response_sequence: list[list[dict]] | None = None,
    ) -> None:
        self.response_id = response_id
        self.response_data = response_data
        self.response_sequence = response_sequence or []
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

    def limit(self, value: int):
        self.filters.append(("limit", "limit", value))
        return self

    def execute(self):
        if self.response_sequence:
            data = self.response_sequence.pop(0)
        else:
            data = (
                self.response_data
                if self.response_data is not None
                else [{"id": self.response_id}]
            )
        return SimpleNamespace(data=data)


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, FakeTable] = {}
        self.table_calls: list[str] = []

    def table(self, name: str) -> FakeTable:
        self.table_calls.append(name)
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


def test_persist_worker_heartbeat_inserts_system_event(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["system_events"] = FakeTable(response_id="event-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    persist_worker_heartbeat({"watched_tickers": ["KXTEST"]})

    assert fake.tables["system_events"].insert_calls == [
        {
            "event_type": "worker_heartbeat",
            "payload_json": {"watched_tickers": ["KXTEST"]},
        }
    ]


def test_persist_open_position_upserts_position_metadata(monkeypatch) -> None:
    _ENSURED_CATALOG_TICKERS.clear()
    fake = FakeSupabase()
    fake.tables["market_catalog"] = FakeTable(response_id="KXTEST")
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


def test_persist_signal_upserts_signal_payload(monkeypatch) -> None:
    _ENSURED_CATALOG_TICKERS.clear()
    fake = FakeSupabase()
    fake.tables["market_catalog"] = FakeTable(response_id="KXTEST")
    fake.tables["signals"] = FakeTable(response_id="signal-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)

    signal_id = persist_signal(
        run_id="run-1",
        ticker="KXTEST",
        timestamp=timestamp,
        prob_estimate=0.62,
        edge=0.03,
        payload={"order_type": "market"},
        signal_id="signal-1",
    )

    assert signal_id == "signal-1"
    assert fake.tables["market_catalog"].insert_calls == [
        {
            "ticker": "KXTEST",
            "title": "KXTEST",
            "status": "open",
            "synced_at": fake.tables["market_catalog"].insert_calls[0]["synced_at"],
        }
    ]
    row, conflict = fake.tables["signals"].upsert_calls[0]
    assert conflict == "id"
    assert row["id"] == "signal-1"
    assert row["run_id"] == "run-1"
    assert row["ticker"] == "KXTEST"
    assert row["prob_estimate"] == 0.62
    assert row["edge"] == 0.03
    assert row["signal_payload"] == {"order_type": "market"}


def test_ensure_market_catalog_entry_is_cached(monkeypatch) -> None:
    _ENSURED_CATALOG_TICKERS.clear()
    fake = FakeSupabase()
    fake.tables["market_catalog"] = FakeTable(response_id="KXTEST")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    ensure_market_catalog_entry("KXTEST")
    ensure_market_catalog_entry("KXTEST")

    assert len(fake.tables["market_catalog"].insert_calls) == 1


def make_intent(**kwargs) -> OrderIntent:
    defaults = {
        "ticker": "KXTEST",
        "side": "yes",
        "price": Decimal("0.40"),
        "qty": 10,
        "estimated_edge": 0.05,
        "model_prob": 0.55,
        "run_id": "run-1",
    }
    defaults.update(kwargs)
    return OrderIntent(**defaults)


def test_get_or_create_paper_account_creates_one_per_config(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(
        response_id="account-1",
        response_sequence=[[], [{"id": "account-1"}]],
    )
    fake.tables["paper_ledger_entries"] = FakeTable(response_id="ledger-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    account = get_or_create_paper_account("config-1")

    assert account["id"] == "account-1"
    assert account["config_id"] == "config-1"
    assert account["cash_balance"] == "10000"
    assert fake.tables["paper_accounts"].insert_calls == [
        {
            "config_id": "config-1",
            "name": "default",
            "starting_cash": "10000",
            "cash_balance": "10000",
            "reserved_cash": "0",
            "status": "active",
        }
    ]
    assert fake.tables["paper_ledger_entries"].insert_calls[0]["entry_type"] == "initial_deposit"


def test_get_or_create_paper_account_reuses_existing_config_account(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(
        response_id="unused",
        response_data=[
            {
                "id": "account-2",
                "config_id": "config-2",
                "name": "default",
                "starting_cash": "10000",
                "cash_balance": "9000",
                "reserved_cash": "100",
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)

    account = get_or_create_paper_account("config-2")

    assert account["id"] == "account-2"
    assert fake.tables["paper_accounts"].insert_calls == []
    assert ("eq", "config_id", "config-2") in fake.tables["paper_accounts"].filters


def test_reserve_and_release_paper_order_cash(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(response_id="account-1")
    fake.tables["paper_ledger_entries"] = FakeTable(response_id="ledger-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    account = {
        "id": "account-1",
        "cash_balance": "100.00",
        "reserved_cash": "0",
    }
    intent = make_intent(price=Decimal("0.40"), qty=10)
    required = estimate_order_cash(intent)

    assert reserve_paper_order_cash(
        account=account,
        run_id="run-1",
        order_id="order-1",
        intent=intent,
    )
    assert paper_account_available_cash(account) == Decimal("100.00") - required

    released = release_paper_order_cash(
        account=account,
        run_id="run-1",
        order_id="order-1",
        intent=intent,
    )

    assert released == required
    assert paper_account_available_cash(account) == Decimal("100.00")
    assert [row["entry_type"] for row in fake.tables["paper_ledger_entries"].insert_calls] == [
        "reserve",
        "release_reserve",
    ]


def test_reserve_rejects_when_buying_power_is_insufficient(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(response_id="account-1")
    fake.tables["paper_ledger_entries"] = FakeTable(response_id="ledger-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    account = {
        "id": "account-1",
        "cash_balance": "1.00",
        "reserved_cash": "0",
    }

    assert not reserve_paper_order_cash(
        account=account,
        run_id="run-1",
        order_id="order-1",
        intent=make_intent(price=Decimal("0.40"), qty=10),
    )
    assert fake.tables["paper_accounts"].update_calls == []
    assert fake.tables["paper_ledger_entries"].insert_calls == []


def test_record_paper_fill_accounting_debits_cash_and_fees(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(response_id="account-1")
    fake.tables["paper_ledger_entries"] = FakeTable(response_id="ledger-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    account = {
        "id": "account-1",
        "cash_balance": "100.00",
        "reserved_cash": "0",
    }
    fill = FillResult(
        order_id="order-1",
        fill_price=Decimal("0.40"),
        fill_qty=10,
        fee=Decimal("0.70"),
        fill_latency_ms=200,
        fill_type="paper",
        status=OrderIntentStatus.FILLED,
    )

    record_paper_fill_accounting(
        account=account,
        run_id="run-1",
        order_id="order-1",
        fill_id="fill-1",
        intent=make_intent(price=Decimal("0.40"), qty=10),
        fill_result=fill,
    )

    assert account["cash_balance"] == "95.30"
    assert [row["entry_type"] for row in fake.tables["paper_ledger_entries"].insert_calls] == [
        "fill_debit",
        "fill_fee",
    ]


def test_reset_paper_account_restores_starting_cash(monkeypatch) -> None:
    fake = FakeSupabase()
    fake.tables["paper_accounts"] = FakeTable(response_id="account-1")
    fake.tables["paper_ledger_entries"] = FakeTable(response_id="ledger-1")
    monkeypatch.setattr("worker.execution_repository._get_supabase", lambda: fake)
    account = {
        "id": "account-1",
        "starting_cash": "10000.00",
        "cash_balance": "9420.50",
        "reserved_cash": "125.00",
    }

    reset_paper_account(account, run_id="run-1", reason="test_reset")

    assert account["cash_balance"] == "10000.00"
    assert account["reserved_cash"] == "0"
    assert fake.tables["paper_accounts"].update_calls[0]["cash_balance"] == "10000.00"
    ledger_row = fake.tables["paper_ledger_entries"].insert_calls[0]
    assert ledger_row["entry_type"] == "adjustment"
    assert ledger_row["amount"] == "579.50"
    assert ledger_row["metadata_json"]["reason"] == "test_reset"


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
