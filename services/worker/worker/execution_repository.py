from __future__ import annotations

from decimal import Decimal
from typing import Any

from core.execution.adapters import FillResult


def _get_supabase() -> Any:
    from worker.storage import supabase

    return supabase


def _response_id(response: Any, table: str) -> str:
    if not response.data:
        raise RuntimeError(f"Supabase returned no rows when writing {table}")
    return response.data[0]["id"]


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
