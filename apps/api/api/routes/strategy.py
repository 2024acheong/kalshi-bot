import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client, create_client

from api.auth import get_current_operator
from api.redis_client import publish_command
from core.schemas.market import RunMode

WORKER_COMMAND_CHANNEL = "worker:commands"

logger = logging.getLogger(__name__)
router = APIRouter(tags=["strategy"])


class StartStrategyRequest(BaseModel):
    mode: Literal[RunMode.PAPER, RunMode.LIVE]


class KillSwitchRequest(BaseModel):
    scope: Literal["global", "strategy"]
    config_id: str | None = None


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    return create_client(url, key)


def strategy_config_exists(config_id: str) -> bool:
    response = (
        get_supabase_client()
        .table("strategy_configs")
        .select("id")
        .eq("id", config_id)
        .limit(1)
        .execute()
    )
    return bool(getattr(response, "data", None))


def _require_strategy_config(config_id: str) -> None:
    try:
        exists = strategy_config_exists(config_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured",
        ) from exc

    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy config not found",
        )


def _validate_kill_switch_request(request: KillSwitchRequest) -> None:
    if request.scope == "strategy" and not request.config_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="config_id is required when scope is strategy",
        )
    if request.scope == "global" and request.config_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="config_id is only valid when scope is strategy",
        )


def _log_kill_switch_call(action: str, request: KillSwitchRequest, operator: dict[str, Any]) -> None:
    logger.warning(
        action,
        extra={
            "operator": operator.get("sub"),
            "when": datetime.now(timezone.utc).isoformat(),
            "scope": request.scope,
            "config_id": request.config_id,
        },
    )


async def _publish(command: dict[str, Any]) -> dict[str, Any]:
    await publish_command(WORKER_COMMAND_CHANNEL, command)
    return {"status": "command_sent", "channel": WORKER_COMMAND_CHANNEL, "command": command}


@router.post("/strategies/{config_id}/start")
async def start_strategy(
    config_id: str,
    request: StartStrategyRequest,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    _require_strategy_config(config_id)
    return await _publish(
        {"command": "start_run", "config_id": config_id, "mode": request.mode.value}
    )


@router.post("/strategies/{config_id}/stop")
async def stop_strategy(
    config_id: str,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    return await _publish({"command": "stop_run", "config_id": config_id})


@router.post("/strategies/{config_id}/pause")
async def pause_strategy(
    config_id: str,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    return await _publish({"command": "pause_strategy", "config_id": config_id})


@router.post("/strategies/{config_id}/resume")
async def resume_strategy(
    config_id: str,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    return await _publish({"command": "resume_strategy", "config_id": config_id})


@router.post("/kill-switch/activate")
async def activate_kill_switch(
    request: KillSwitchRequest,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    _log_kill_switch_call("kill_switch_activate", request, operator)
    _validate_kill_switch_request(request)
    command = {"command": "kill_switch", "scope": request.scope}
    if request.config_id:
        command["config_id"] = request.config_id

    return await _publish(command)


@router.post("/kill-switch/deactivate")
async def deactivate_kill_switch(
    request: KillSwitchRequest,
    operator: Annotated[dict[str, Any], Depends(get_current_operator)],
) -> dict[str, Any]:
    _log_kill_switch_call("kill_switch_deactivate", request, operator)
    _validate_kill_switch_request(request)
    command = {"command": "clear_kill_switch", "scope": request.scope}
    if request.config_id:
        command["config_id"] = request.config_id

    return await _publish(command)
