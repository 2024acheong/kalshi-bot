from __future__ import annotations

from decimal import Decimal
from typing import Any

from core.execution.adapters import FillResult


def _get_supabase() -> Any:
    try:
        from worker.storage import supabase
    except ImportError:
        from worker.slice import supabase

    return supabase


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
    return response.data[0]["id"]


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
    return response.data[0]["id"]


def update_order_status(order_id: str, status: str) -> None:
    """Update an order's status field."""
    _get_supabase().table("orders").update({"status": status}).eq("id", order_id).execute()
