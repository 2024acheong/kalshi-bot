from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)

WORKER_COMMAND_CHANNEL = "worker:commands"


class CommandListener:
    """
    Subscribes to Redis pub/sub for commands from the API control plane
    and dispatches them to a TradingRuntime instance.
    """

    def __init__(self, runtime: Any, config_id: str | None = None) -> None:
        """
        runtime: the TradingRuntime instance this worker is running.
        config_id: this worker's own strategy_configs.id, used to determine
                   whether a scoped command applies to this worker instance. If
                   None, this worker responds to all commands.
        """
        self.runtime = runtime
        self.config_id = config_id
        self._redis = redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True,
        )
        self._running = False

    async def listen(self) -> None:
        """Subscribe and dispatch commands until stopped."""
        self._running = True
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(WORKER_COMMAND_CHANNEL)
        logger.info("Command listener subscribed to %s", WORKER_COMMAND_CHANNEL)

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                await self._handle_message(message["data"])
        finally:
            await pubsub.unsubscribe(WORKER_COMMAND_CHANNEL)

    async def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received malformed command payload: %s", raw[:200])
            return

        command = payload.get("command")
        target_config_id = payload.get("config_id")

        if self._is_scoped_to_another_config(command, payload, target_config_id):
            logger.debug("Ignoring command for different config_id: %s", payload)
            return

        logger.info("Handling command: %s", payload)

        if command == "pause_strategy":
            self.runtime.pause()
        elif command == "resume_strategy":
            self.runtime.resume()
        elif command == "stop_run":
            self.runtime.stop()
            self.stop()
        elif command == "kill_switch":
            scope = payload.get("scope")
            self.runtime.activate_kill_switch()
            logger.warning("KILL SWITCH ACTIVATED - scope=%s", scope)
        elif command == "clear_kill_switch":
            self.runtime.deactivate_kill_switch()
            logger.warning("Kill switch deactivated")
        elif command == "reset_paper_account":
            self.runtime.reset_paper_account()
        elif command == "start_run":
            logger.info(
                "start_run received - treating as resume() for now "
                "(cold start not yet implemented)"
            )
            self.runtime.resume()
        else:
            logger.warning("Unknown command type: %s", command)

    def _is_scoped_to_another_config(
        self,
        command: Any,
        payload: dict[str, Any],
        target_config_id: Any,
    ) -> bool:
        return (
            self.config_id is not None
            and target_config_id is not None
            and target_config_id != self.config_id
            and not (command == "kill_switch" and payload.get("scope") == "global")
        )

    def stop(self) -> None:
        self._running = False
