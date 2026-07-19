def test_login_with_correct_password_returns_token(client) -> None:
    response = client.post("/auth/login", json={"password": "correct-password"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_login_with_wrong_password_returns_401(client) -> None:
    response = client.post("/auth/login", json={"password": "wrong-password"})

    assert response.status_code == 401


def test_protected_endpoint_without_token_returns_401(client) -> None:
    response = client.post("/strategies/config-1/stop")

    assert response.status_code == 401
