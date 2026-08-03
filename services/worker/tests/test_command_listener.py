from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from worker.command_listener import CommandListener


class FakePubSub:
    def __init__(self, messages: list[dict[str, str]]) -> None:
        self.messages = messages
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()

    async def listen(self):
        for message in self.messages:
            yield message


class FakeRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        return self._pubsub


def make_message(payload: dict[str, object]) -> dict[str, str]:
    return {"type": "message", "data": json.dumps(payload)}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_listener(
    monkeypatch: pytest.MonkeyPatch,
    runtime: MagicMock,
    messages: list[dict[str, str]],
    config_id: str | None = None,
) -> CommandListener:
    pubsub = FakePubSub(messages)
    monkeypatch.setattr(
        "worker.command_listener.redis.from_url",
        lambda *args, **kwargs: FakeRedis(pubsub),
    )
    return CommandListener(runtime=runtime, config_id=config_id)


@pytest.mark.anyio
async def test_pause_command_calls_runtime_pause(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "pause_strategy"})],
    )

    await listener.listen()

    runtime.pause.assert_called_once_with()


@pytest.mark.anyio
async def test_resume_command_calls_runtime_resume(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "resume_strategy"})],
    )

    await listener.listen()

    runtime.resume.assert_called_once_with()


@pytest.mark.anyio
async def test_kill_switch_calls_activate(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "kill_switch", "scope": "global"})],
    )

    await listener.listen()

    runtime.activate_kill_switch.assert_called_once_with()


@pytest.mark.anyio
async def test_clear_kill_switch_calls_deactivate(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "clear_kill_switch"})],
    )

    await listener.listen()

    runtime.deactivate_kill_switch.assert_called_once_with()


@pytest.mark.anyio
async def test_reset_paper_account_command_calls_runtime_reset(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "reset_paper_account"})],
    )

    await listener.listen()

    runtime.reset_paper_account.assert_called_once_with()


@pytest.mark.anyio
async def test_stop_run_calls_runtime_stop_and_listener_stop(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "stop_run"})],
    )

    await listener.listen()

    runtime.stop.assert_called_once_with()
    assert listener._running is False


@pytest.mark.anyio
async def test_scoped_command_ignored_for_different_config_id(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "pause_strategy", "config_id": "xyz"})],
        config_id="abc",
    )

    await listener.listen()

    assert runtime.method_calls == []


@pytest.mark.anyio
async def test_global_kill_switch_bypasses_config_scoping(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "kill_switch", "scope": "global", "config_id": "xyz"})],
        config_id="abc",
    )

    await listener.listen()

    runtime.activate_kill_switch.assert_called_once_with()


@pytest.mark.anyio
async def test_malformed_json_does_not_crash(monkeypatch) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [{"type": "message", "data": "not-json"}],
    )

    await listener.listen()

    assert runtime.method_calls == []


@pytest.mark.anyio
async def test_unknown_command_logged_and_ignored(monkeypatch, caplog) -> None:
    runtime = MagicMock()
    listener = make_listener(
        monkeypatch,
        runtime,
        [make_message({"command": "not_a_real_command"})],
    )

    await listener.listen()

    assert runtime.method_calls == []
    assert "Unknown command type" in caplog.text
