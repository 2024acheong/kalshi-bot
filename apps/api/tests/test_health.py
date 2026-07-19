def test_health_returns_200_without_auth(client, monkeypatch) -> None:
    async def ping() -> bool:
        return True

    monkeypatch.setattr("api.routes.health.redis_client.ping", ping)
    monkeypatch.setattr("api.routes.health._supabase_ok", lambda: True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "redis": True, "supabase": True}
