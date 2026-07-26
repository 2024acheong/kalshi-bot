from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.execution.adapters import FillResult
from core.execution.fees import compute_kalshi_fee
from core.execution.resting_orders import RestingOrder
from core.risk.engine import OrderIntent
from core.schemas.market import OrderIntentStatus


def _get_supabase() -> Any:
    from worker.storage import supabase

    return supabase


_ENSURED_CATALOG_TICKERS: set[str] = set()
DEFAULT_PAPER_STARTING_CASH = Decimal("10000")
PAPER_BUYING_POWER_BLOCK = "insufficient_paper_buying_power"


def _response_id(response: Any, table: str) -> str:
    if not response.data:
        raise RuntimeError(f"Supabase returned no rows when writing {table}")
    return response.data[0]["id"]


def _is_duplicate_key_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "23505" in text or "duplicate key" in text


def _as_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    return Decimal(str(value))


def ensure_market_catalog_entry(ticker: str) -> None:
    """
    Ensure FK-backed execution tables can reference a live-discovered ticker.

    Catalog sync normally writes full market metadata before trading starts, but
    websocket/order processing can still encounter a ticker before that row is
    visible to PostgREST. Insert a minimal placeholder and let later catalog
    sync overwrite it with richer title/category/close_time data.
    """
    normalized = str(ticker)
    if normalized in _ENSURED_CATALOG_TICKERS:
        return

    row = {
        "ticker": normalized,
        "title": normalized,
        "status": "open",
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _get_supabase().table("market_catalog").insert(row).execute()
    except Exception as exc:
        if not _is_duplicate_key_error(exc):
            raise
    _ENSURED_CATALOG_TICKERS.add(normalized)


def ensure_strategy_config(name: str, version: int, params: dict | None = None) -> str:
    """
    Upsert a strategy_configs row for name+version and return its id.
    """
    row = {
        "name": name,
        "version": version,
        "params_json": params or {},
    }
    response = (
        _get_supabase()
        .table("strategy_configs")
        .upsert(row, on_conflict="name,version")
        .execute()
    )
    return _response_id(response, "strategy_configs")


def load_enabled_strategy_configs() -> list[dict[str, Any]]:
    response = (
        _get_supabase()
        .table("strategy_configs")
        .select("id,name,version,params_json,status")
        .eq("status", "enabled")
        .execute()
    )
    return getattr(response, "data", None) or []


def persist_worker_heartbeat(payload: dict[str, Any]) -> None:
    row = {
        "event_type": "worker_heartbeat",
        "payload_json": payload,
    }
    _get_supabase().table("system_events").insert(row).execute()


def create_strategy_run(config_id: str, mode: str = "paper") -> str:
    """
    Insert a strategy_runs row and return its id for use as the runtime run_id.
    """
    row = {
        "config_id": config_id,
        "mode": mode,
    }
    response = _get_supabase().table("strategy_runs").insert(row).execute()
    return _response_id(response, "strategy_runs")


def get_or_create_strategy_run(config_id: str, mode: str = "paper") -> str:
    response = (
        _get_supabase()
        .table("strategy_runs")
        .select("id")
        .eq("config_id", config_id)
        .eq("mode", mode)
        .is_("ended_at", "null")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if rows:
        return str(rows[0]["id"])
    return create_strategy_run(config_id=config_id, mode=mode)


def get_or_create_paper_account(
    config_id: str,
    *,
    starting_cash: Decimal = DEFAULT_PAPER_STARTING_CASH,
) -> dict[str, Any]:
    response = (
        _get_supabase()
        .table("paper_accounts")
        .select("id,config_id,name,starting_cash,cash_balance,reserved_cash,status")
        .eq("config_id", config_id)
        .eq("name", "default")
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if rows:
        return rows[0]

    row = {
        "config_id": config_id,
        "name": "default",
        "starting_cash": str(starting_cash),
        "cash_balance": str(starting_cash),
        "reserved_cash": "0",
        "status": "active",
    }
    created = _get_supabase().table("paper_accounts").insert(row).execute()
    account_id = _response_id(created, "paper_accounts")
    account = {
        "id": account_id,
        **row,
    }
    insert_paper_ledger_entry(
        account_id=account_id,
        entry_type="initial_deposit",
        amount=starting_cash,
        cash_balance_after=starting_cash,
        reserved_cash_after=Decimal("0"),
        metadata={"config_id": config_id},
    )
    return account


def paper_account_available_cash(account: dict[str, Any]) -> Decimal:
    return _as_decimal(account.get("cash_balance")) - _as_decimal(
        account.get("reserved_cash")
    )


def estimate_order_cash(intent: OrderIntent) -> Decimal:
    notional = intent.price * intent.qty
    fee = compute_kalshi_fee(intent.price, intent.qty)
    return notional + fee


def insert_paper_ledger_entry(
    *,
    account_id: str,
    entry_type: str,
    amount: Decimal,
    cash_balance_after: Decimal,
    reserved_cash_after: Decimal,
    run_id: str | None = None,
    order_id: str | None = None,
    fill_id: str | None = None,
    ticker: str | None = None,
    side: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    row = {
        "account_id": account_id,
        "run_id": run_id,
        "order_id": order_id,
        "fill_id": fill_id,
        "ticker": ticker,
        "side": side,
        "entry_type": entry_type,
        "amount": str(amount),
        "cash_balance_after": str(cash_balance_after),
        "reserved_cash_after": str(reserved_cash_after),
        "metadata_json": metadata or {},
    }
    response = _get_supabase().table("paper_ledger_entries").insert(row).execute()
    return _response_id(response, "paper_ledger_entries")


def update_paper_account_balances(
    account_id: str,
    *,
    cash_balance: Decimal,
    reserved_cash: Decimal,
) -> None:
    row = {
        "cash_balance": str(cash_balance),
        "reserved_cash": str(reserved_cash),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _get_supabase().table("paper_accounts").update(row).eq("id", account_id).execute()


def reserve_paper_order_cash(
    *,
    account: dict[str, Any],
    run_id: str,
    order_id: str,
    intent: OrderIntent,
) -> bool:
    required_cash = estimate_order_cash(intent)
    if paper_account_available_cash(account) < required_cash:
        return False

    account_id = str(account["id"])
    cash_balance = _as_decimal(account.get("cash_balance"))
    reserved_cash = _as_decimal(account.get("reserved_cash")) + required_cash
    update_paper_account_balances(
        account_id,
        cash_balance=cash_balance,
        reserved_cash=reserved_cash,
    )
    insert_paper_ledger_entry(
        account_id=account_id,
        run_id=run_id,
        order_id=order_id,
        ticker=intent.ticker,
        side=intent.side,
        entry_type="reserve",
        amount=required_cash,
        cash_balance_after=cash_balance,
        reserved_cash_after=reserved_cash,
        metadata={
            "price": str(intent.price),
            "qty": intent.qty,
            "estimated_fee": str(compute_kalshi_fee(intent.price, intent.qty)),
        },
    )
    account["reserved_cash"] = str(reserved_cash)
    return True


def release_paper_order_cash(
    *,
    account: dict[str, Any],
    run_id: str,
    order_id: str,
    intent: OrderIntent,
    qty: int | None = None,
    reason: str = "cancelled",
) -> Decimal:
    release_intent = OrderIntent(
        ticker=intent.ticker,
        side=intent.side,
        price=intent.price,
        qty=qty if qty is not None else intent.qty,
        estimated_edge=intent.estimated_edge,
        model_prob=intent.model_prob,
        run_id=intent.run_id,
        signal_id=intent.signal_id,
        is_closing_order=intent.is_closing_order,
    )
    release_amount = min(
        estimate_order_cash(release_intent),
        _as_decimal(account.get("reserved_cash")),
    )
    if release_amount <= 0:
        return Decimal("0")

    account_id = str(account["id"])
    cash_balance = _as_decimal(account.get("cash_balance"))
    reserved_cash = _as_decimal(account.get("reserved_cash")) - release_amount
    update_paper_account_balances(
        account_id,
        cash_balance=cash_balance,
        reserved_cash=reserved_cash,
    )
    insert_paper_ledger_entry(
        account_id=account_id,
        run_id=run_id,
        order_id=order_id,
        ticker=intent.ticker,
        side=intent.side,
        entry_type="release_reserve",
        amount=release_amount,
        cash_balance_after=cash_balance,
        reserved_cash_after=reserved_cash,
        metadata={"reason": reason, "qty": release_intent.qty},
    )
    account["reserved_cash"] = str(reserved_cash)
    return release_amount


def record_paper_fill_accounting(
    *,
    account: dict[str, Any],
    run_id: str,
    order_id: str,
    fill_id: str,
    intent: OrderIntent,
    fill_result: FillResult,
    release_reserved_qty: int = 0,
) -> None:
    if fill_result.fill_qty <= 0:
        return

    account_id = str(account["id"])
    notional = fill_result.fill_price * fill_result.fill_qty
    fee = fill_result.fee
    reserve_release = Decimal("0")
    if release_reserved_qty > 0:
        release_intent = OrderIntent(
            ticker=intent.ticker,
            side=intent.side,
            price=intent.price,
            qty=release_reserved_qty,
            estimated_edge=intent.estimated_edge,
            model_prob=intent.model_prob,
            run_id=intent.run_id,
            signal_id=intent.signal_id,
            is_closing_order=intent.is_closing_order,
        )
        reserve_release = min(
            estimate_order_cash(release_intent),
            _as_decimal(account.get("reserved_cash")),
        )

    cash_balance = _as_decimal(account.get("cash_balance")) - notional - fee
    reserved_cash = _as_decimal(account.get("reserved_cash")) - reserve_release
    if cash_balance < 0:
        raise RuntimeError(
            f"Paper account {account_id} cash would go negative for order {order_id}"
        )
    if reserved_cash < 0:
        reserved_cash = Decimal("0")

    update_paper_account_balances(
        account_id,
        cash_balance=cash_balance,
        reserved_cash=reserved_cash,
    )
    if reserve_release > 0:
        insert_paper_ledger_entry(
            account_id=account_id,
            run_id=run_id,
            order_id=order_id,
            fill_id=fill_id,
            ticker=intent.ticker,
            side=intent.side,
            entry_type="release_reserve",
            amount=reserve_release,
            cash_balance_after=cash_balance,
            reserved_cash_after=reserved_cash,
            metadata={"reason": "filled", "qty": release_reserved_qty},
        )
    insert_paper_ledger_entry(
        account_id=account_id,
        run_id=run_id,
        order_id=order_id,
        fill_id=fill_id,
        ticker=intent.ticker,
        side=intent.side,
        entry_type="fill_debit",
        amount=-notional,
        cash_balance_after=cash_balance,
        reserved_cash_after=reserved_cash,
        metadata={"fill_price": str(fill_result.fill_price), "fill_qty": fill_result.fill_qty},
    )
    if fee > 0:
        insert_paper_ledger_entry(
            account_id=account_id,
            run_id=run_id,
            order_id=order_id,
            fill_id=fill_id,
            ticker=intent.ticker,
            side=intent.side,
            entry_type="fill_fee",
            amount=-fee,
            cash_balance_after=cash_balance,
            reserved_cash_after=reserved_cash,
            metadata={"fill_qty": fill_result.fill_qty},
        )
    account["cash_balance"] = str(cash_balance)
    account["reserved_cash"] = str(reserved_cash)


def credit_paper_realized_value(
    *,
    account: dict[str, Any],
    run_id: str,
    order_id: str,
    fill_id: str | None,
    ticker: str,
    side: str,
    qty: int,
    reason: str,
) -> None:
    if qty <= 0:
        return

    account_id = str(account["id"])
    amount = Decimal(qty)
    cash_balance = _as_decimal(account.get("cash_balance")) + amount
    reserved_cash = _as_decimal(account.get("reserved_cash"))
    update_paper_account_balances(
        account_id,
        cash_balance=cash_balance,
        reserved_cash=reserved_cash,
    )
    insert_paper_ledger_entry(
        account_id=account_id,
        run_id=run_id,
        order_id=order_id,
        fill_id=fill_id,
        ticker=ticker,
        side=side,
        entry_type="realized_credit",
        amount=amount,
        cash_balance_after=cash_balance,
        reserved_cash_after=reserved_cash,
        metadata={"reason": reason, "qty": qty},
    )
    account["cash_balance"] = str(cash_balance)


def persist_order(
    run_id: str,
    ticker: str,
    intent: str,
    side: str,
    price: Decimal,
    qty: int,
    risk_decision: str,
    status: str,
    signal_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Insert a row into orders, return the new order id."""
    ensure_market_catalog_entry(ticker)
    row = {
        "run_id": run_id,
        "signal_id": signal_id,
        "ticker": ticker,
        "intent": intent,
        "side": side,
        "price": str(price),
        "qty": qty,
        "risk_decision": risk_decision,
        "status": status,
        "metadata_json": metadata or {},
    }
    response = _get_supabase().table("orders").insert(row).execute()
    return _response_id(response, "orders")


def persist_signal(
    *,
    run_id: str,
    ticker: str,
    timestamp: datetime,
    model_id: str | None = None,
    prob_estimate: float | None = None,
    edge: float | None = None,
    payload: dict | None = None,
    signal_id: str | None = None,
) -> str:
    ensure_market_catalog_entry(ticker)
    row = {
        "run_id": run_id,
        "ticker": ticker,
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "model_id": model_id,
        "prob_estimate": prob_estimate,
        "edge": edge,
        "signal_payload": payload or {},
    }
    if signal_id:
        row["id"] = signal_id
    response = _get_supabase().table("signals").upsert(row, on_conflict="id").execute()
    return _response_id(response, "signals")


def persist_fill(
    order_id: str,
    fill_result: FillResult,
) -> str:
    """Insert a row into fills, return the new fill id. Also update the parent order's status."""
    row = {
        "order_id": order_id,
        "fill_price": str(fill_result.fill_price),
        "fill_qty": fill_result.fill_qty,
        "fee": str(fill_result.fee),
        "fill_latency_ms": fill_result.fill_latency_ms,
        "fill_type": fill_result.fill_type,
    }
    response = _get_supabase().table("fills").insert(row).execute()
    update_order_status(order_id, fill_result.status.value)
    return _response_id(response, "fills")


def update_order_status(order_id: str, status: str) -> None:
    """Update an order's status field."""
    _get_supabase().table("orders").update({"status": status}).eq("id", order_id).execute()


def update_order_metadata(
    order_id: str,
    *,
    status: str | None = None,
    metadata: dict | None = None,
) -> None:
    row: dict[str, Any] = {}
    if status is not None:
        row["status"] = status
    if metadata is not None:
        row["metadata_json"] = metadata
    if not row:
        return
    _get_supabase().table("orders").update(row).eq("id", order_id).execute()


def resting_order_metadata(
    *,
    pair_id: str | None,
    max_resting_seconds: int,
    created_at: datetime,
    accumulated_fill_qty: int,
    total_qty: int,
    extra: dict | None = None,
) -> dict:
    remaining_qty = max(total_qty - accumulated_fill_qty, 0)
    metadata = {
        "resting_order": True,
        "order_type": "limit",
        "pair_id": pair_id,
        "max_resting_seconds": max_resting_seconds,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "accumulated_fill_qty": accumulated_fill_qty,
        "remaining_qty": remaining_qty,
    }
    if extra:
        metadata.update(extra)
    return metadata


def update_resting_order_state(
    order: RestingOrder,
    *,
    extra: dict | None = None,
) -> None:
    update_order_metadata(
        order.order_id,
        status=order.status.value,
        metadata=resting_order_metadata(
            pair_id=order.pair_id,
            max_resting_seconds=order.max_resting_seconds,
            created_at=order.created_at,
            accumulated_fill_qty=order.accumulated_fill_qty,
            total_qty=order.intent.qty,
            extra=extra,
        ),
    )


def persist_open_position(
    *,
    run_id: str,
    ticker: str,
    side: str,
    qty: int,
    avg_entry: Decimal,
    opened_at: datetime,
    metadata: dict | None = None,
) -> str:
    ensure_market_catalog_entry(ticker)
    row = {
        "run_id": run_id,
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "avg_entry": str(avg_entry),
        "unrealized_pnl": "0",
        "opened_at": opened_at.astimezone(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "metadata_json": metadata or {},
    }
    response = (
        _get_supabase()
        .table("positions")
        .upsert(row, on_conflict="run_id,ticker,side")
        .execute()
    )
    return _response_id(response, "positions")


def close_position(run_id: str, ticker: str, side: str) -> None:
    row = {
        "qty": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (
        _get_supabase()
        .table("positions")
        .update(row)
        .eq("run_id", run_id)
        .eq("ticker", ticker)
        .eq("side", side)
        .execute()
    )


def load_open_positions(run_id: str) -> list[dict[str, Any]]:
    response = (
        _get_supabase()
        .table("positions")
        .select(
            "run_id,ticker,side,qty,avg_entry,opened_at,metadata_json"
        )
        .eq("run_id", run_id)
        .gt("qty", 0)
        .execute()
    )
    return getattr(response, "data", None) or []


def load_open_resting_orders(run_id: str) -> list[RestingOrder]:
    response = (
        _get_supabase()
        .table("orders")
        .select(
            "id,run_id,ticker,side,price,qty,status,created_at,signal_id,metadata_json"
        )
        .eq("run_id", run_id)
        .in_("status", ["submitted", "partially_filled"])
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return [_resting_order_from_row(row) for row in rows if _is_resting_order(row)]


def _is_resting_order(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata_json") or {}
    return bool(metadata.get("resting_order"))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _resting_order_from_row(row: dict[str, Any]) -> RestingOrder:
    metadata = row.get("metadata_json") or {}
    qty = int(row["qty"])
    remaining_qty = metadata.get("remaining_qty")
    accumulated_fill_qty = metadata.get("accumulated_fill_qty")
    if accumulated_fill_qty is None and remaining_qty is not None:
        accumulated_fill_qty = qty - int(remaining_qty)
    accumulated_fill_qty = int(accumulated_fill_qty or 0)

    intent = OrderIntent(
        ticker=str(row["ticker"]),
        side=str(row["side"]),
        price=Decimal(str(row["price"])),
        qty=qty,
        estimated_edge=float(metadata.get("estimated_edge", 0.0)),
        model_prob=float(metadata.get("model_prob", row["price"])),
        run_id=str(row["run_id"]),
        signal_id=row.get("signal_id"),
    )
    return RestingOrder(
        order_id=str(row["id"]),
        intent=intent,
        order_type=str(metadata.get("order_type") or "limit"),
        created_at=_parse_datetime(metadata.get("created_at") or row["created_at"]),
        max_resting_seconds=int(metadata.get("max_resting_seconds") or 30),
        pair_id=metadata.get("pair_id"),
        status=OrderIntentStatus(str(row["status"])),
        accumulated_fill_qty=accumulated_fill_qty,
    )
