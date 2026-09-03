from api.tests.conftest import auth_headers, make_user


def test_login_success(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.post("/api/v1/auth/login", data={"username": "viewer@enervision.fr", "password": "password123"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.post("/api/v1/auth/login", data={"username": "viewer@enervision.fr", "password": "wrong"})

    assert response.status_code == 401


def test_me_requires_token(client):
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401


def test_me_returns_current_user(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "viewer@enervision.fr"
    assert response.json()["role"] == "viewer"


def test_me_exposes_password_change_flag(client, db_session):
    make_user(db_session, "temp@enervision.fr", "password123", "viewer", must_change_password=True)
    headers = auth_headers(client, "temp@enervision.fr", "password123")

    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


def test_change_password_requires_token(client):
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "password123", "new_password": "nouveau123"},
    )

    assert response.status_code == 401


def test_change_password_clears_flag_and_updates_hash(client, db_session):
    make_user(db_session, "temp@enervision.fr", "password123", "viewer", must_change_password=True)
    headers = auth_headers(client, "temp@enervision.fr", "password123")

    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "password123", "new_password": "nouveau123"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is False
    # L'ancien mot de passe ne fonctionne plus, le nouveau oui.
    assert (
        client.post(
            "/api/v1/auth/login", data={"username": "temp@enervision.fr", "password": "password123"}
        ).status_code
        == 401
    )
    assert (
        client.post("/api/v1/auth/login", data={"username": "temp@enervision.fr", "password": "nouveau123"}).status_code
        == 200
    )


def test_change_password_rejects_wrong_current_password(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "mauvais", "new_password": "nouveau123"},
        headers=headers,
    )

    # 400 et non 401, sinon le front déconnecterait l'utilisateur (intercepteur axios).
    assert response.status_code == 400
    assert response.json()["detail"] == "Mot de passe actuel incorrect"


def test_change_password_rejects_identical_password(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "password123", "new_password": "password123"},
        headers=headers,
    )

    assert response.status_code == 400


def test_change_password_rejects_too_short_password(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": "password123", "new_password": "court"},
        headers=headers,
    )

    assert response.status_code == 422


def test_pending_password_change_blocks_other_routes(client, db_session):
    make_user(db_session, "temp@enervision.fr", "password123", "viewer", must_change_password=True)
    headers = auth_headers(client, "temp@enervision.fr", "password123")

    # Toutes les routes protégées, pas seulement /alerts : une nouvelle route qui
    # dépendrait de get_current_user au lieu de get_active_user ouvrirait une brèche.
    for route in ("/api/v1/alerts", "/api/v1/users", "/api/v1/sensors/failing"):
        response = client.get(route, headers=headers)

        assert response.status_code == 403, route
        assert "Mot de passe temporaire" in response.json()["detail"], route
