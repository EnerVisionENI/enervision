from api.models import User
from api.tests.conftest import auth_headers, make_user

ADMIN = ("admin@enervision.fr", "password123")


def admin_headers(client, db_session):
    make_user(db_session, ADMIN[0], ADMIN[1], "admin")
    return auth_headers(client, *ADMIN)


def test_list_users_requires_token(client):
    response = client.get("/api/v1/users")

    assert response.status_code == 401


def test_list_users_requires_admin(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.get("/api/v1/users", headers=headers)

    assert response.status_code == 403


def test_list_users_returns_all_accounts(client, db_session):
    headers = admin_headers(client, db_session)
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.get("/api/v1/users", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert {user["email"] for user in body} == {ADMIN[0], "viewer@enervision.fr"}
    assert "password_hash" not in body[0]


def test_create_user_requires_admin(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.post(
        "/api/v1/users",
        json={"email": "new@enervision.fr", "password": "password123", "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 403


def test_admin_can_create_user_with_temporary_password(client, db_session):
    headers = admin_headers(client, db_session)

    response = client.post(
        "/api/v1/users",
        json={"email": "new@enervision.fr", "password": "password123", "role": "operator"},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@enervision.fr"
    assert body["role"] == "operator"
    # Le cœur de la feature : le compte créé démarre sur un mot de passe temporaire.
    assert body["must_change_password"] is True


def test_cannot_create_duplicate_email(client, db_session):
    headers = admin_headers(client, db_session)

    response = client.post(
        "/api/v1/users",
        json={"email": ADMIN[0], "password": "password123", "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 409


def test_create_user_rejects_too_short_password(client, db_session):
    headers = admin_headers(client, db_session)

    response = client.post(
        "/api/v1/users",
        json={"email": "new@enervision.fr", "password": "court", "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 422


def test_admin_can_update_role(client, db_session):
    headers = admin_headers(client, db_session)
    user = make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.patch(f"/api/v1/users/{user.user_id}", json={"role": "operator"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["role"] == "operator"


def test_update_role_rejects_unknown_role(client, db_session):
    headers = admin_headers(client, db_session)
    user = make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.patch(f"/api/v1/users/{user.user_id}", json={"role": "root"}, headers=headers)

    assert response.status_code == 422


def test_admin_cannot_change_own_role(client, db_session):
    headers = admin_headers(client, db_session)
    admin = db_session.query(User).filter(User.email == ADMIN[0]).one()

    response = client.patch(f"/api/v1/users/{admin.user_id}", json={"role": "viewer"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Vous ne pouvez pas modifier votre propre rôle"


def test_update_unknown_user_returns_404(client, db_session):
    headers = admin_headers(client, db_session)

    response = client.patch(
        "/api/v1/users/00000000-0000-0000-0000-000000000000",
        json={"role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 404


def test_admin_reset_password_forces_change(client, db_session):
    headers = admin_headers(client, db_session)
    user = make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.post(
        f"/api/v1/users/{user.user_id}/password",
        json={"password": "temporaire1"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    assert (
        client.post(
            "/api/v1/auth/login", data={"username": "viewer@enervision.fr", "password": "temporaire1"}
        ).status_code
        == 200
    )


def test_admin_cannot_reset_his_own_password(client, db_session):
    headers = admin_headers(client, db_session)
    admin = db_session.query(User).filter(User.email == ADMIN[0]).one()

    response = client.post(
        f"/api/v1/users/{admin.user_id}/password",
        json={"password": "temporaire1"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Vous ne pouvez pas réinitialiser votre propre mot de passe"


def test_reset_password_requires_admin(client, db_session):
    user = make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.post(
        f"/api/v1/users/{user.user_id}/password",
        json={"password": "temporaire1"},
        headers=headers,
    )

    assert response.status_code == 403


def test_admin_can_delete_user(client, db_session):
    headers = admin_headers(client, db_session)
    user = make_user(db_session, "viewer@enervision.fr", "password123", "viewer")

    response = client.delete(f"/api/v1/users/{user.user_id}", headers=headers)

    assert response.status_code == 204
    assert db_session.get(User, user.user_id) is None


def test_admin_cannot_delete_himself(client, db_session):
    headers = admin_headers(client, db_session)
    admin = db_session.query(User).filter(User.email == ADMIN[0]).one()

    response = client.delete(f"/api/v1/users/{admin.user_id}", headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Vous ne pouvez pas supprimer votre propre compte"


def test_delete_unknown_user_returns_404(client, db_session):
    headers = admin_headers(client, db_session)

    response = client.delete("/api/v1/users/00000000-0000-0000-0000-000000000000", headers=headers)

    assert response.status_code == 404


def test_admin_with_pending_password_change_is_blocked(client, db_session):
    make_user(db_session, "temp-admin@enervision.fr", "password123", "admin", must_change_password=True)
    headers = auth_headers(client, "temp-admin@enervision.fr", "password123")

    response = client.get("/api/v1/users", headers=headers)

    assert response.status_code == 403
