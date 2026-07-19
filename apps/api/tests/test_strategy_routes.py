import logging
from unittest.mock import AsyncMock


def test_start_strategy_publishes_command_for_valid_config(
    client,
    auth_headers,
    monkeypatch,
) -> None:
    publish = AsyncMock()
    monkeypatch.setattr("api.routes.strategy.publish_command", publish)
    monkeypatch.setattr("api.routes.strategy.strategy_config_exists", lambda config_id: True)

    response = client.post(
        "/strategies/config-1/start",
        json={"mode": "paper"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "command_sent"
    publish.assert_awaited_once_with(
        "worker:commands",
        {"command": "start_run", "config_id": "config-1", "mode": "paper"},
    )


def test_start_strategy_returns_404_for_invalid_config(
    client,
    auth_headers,
    monkeypatch,
) -> None:
    publish = AsyncMock()
    monkeypatch.setattr("api.routes.strategy.publish_command", publish)
    monkeypatch.setattr("api.routes.strategy.strategy_config_exists", lambda config_id: False)

    response = client.post(
        "/strategies/missing-config/start",
        json={"mode": "paper"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    publish.assert_not_called()


def test_kill_switch_activate_publishes_and_logs(
    client,
    auth_headers,
    monkeypatch,
    caplog,
) -> None:
    publish = AsyncMock()
    monkeypatch.setattr("api.routes.strategy.publish_command", publish)
    caplog.set_level(logging.WARNING, logger="api.routes.strategy")

    response = client.post(
        "/kill-switch/activate",
        json={"scope": "strategy", "config_id": "config-1"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    publish.assert_awaited_once_with(
        "worker:commands",
        {"command": "kill_switch", "scope": "strategy", "config_id": "config-1"},
    )
    assert "kill_switch_activate" in caplog.text


def test_strategy_endpoint_without_valid_bearer_token_returns_401(client) -> None:
    response = client.post(
        "/strategies/config-1/start",
        json={"mode": "paper"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
