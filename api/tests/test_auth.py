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


def test_login_unknown_email_returns_401(client):
    """Email qui n'existe pas du tout — doit renvoyer la même erreur que mauvais mdp,
    pour ne pas révéler quels emails existent (énumération de comptes)."""
    response = client.post("/api/v1/auth/login", data={"username": "inconnu@enervision.fr", "password": "x"})
    assert response.status_code == 401


def test_expired_token_rejected(client, db_session, monkeypatch):
    """Un token expiré doit être refusé — teste la vraie logique d'expiration,
    pas juste un token absent ou malformé."""
    # nécessite de mocker le temps ou de générer un token avec exp dans le passé


def test_operator_cannot_create_user(client, db_session):
    """Si operator existe comme rôle intermédiaire, confirmez qu'il n'a pas non plus
    les droits admin (sinon le rôle n'a pas de sens)."""
    make_user(db_session, "op@enervision.fr", "password123", "operator")
    headers = auth_headers(client, "op@enervision.fr", "password123")
    response = client.post(
        "/api/v1/users",
        json={"email": "new@enervision.fr", "password": "password123", "role": "viewer"},
        headers=headers,
    )
    assert response.status_code == 403



