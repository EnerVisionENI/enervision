# e2e/test_e2e_pipeline.py
#
# Suppose que la stack tourne déjà : `docker compose --profile etl up -d --build`
# depuis la racine du dépôt (compose.yaml). Ne mocke rien : vraies requêtes HTTP
# contre le vrai serveur API (port 8000), vraie PostgreSQL, alimentée par les
# vrais services etl-alerts / etl-sites / etl-collect.
#
# Contrairement à api/tests/ (SQLite en mémoire, recréée à chaque run), la base
# ici est partagée et persistante entre les runs : les tests qui créent des
# données (ex. un nouvel utilisateur) doivent rester rejouables sans collision
# (email suffixé par un uuid), pas supposer une base vide.
#
# Pas de fixture qui lance collect.py / alerts.py en subprocess : ce sont des
# BlockingScheduler sans mode "run once" (aucun argparse, aucun flag --once), donc
# les lancer ici bloquerait le test indéfiniment. On attend le résultat de leurs
# cycles par polling à la place.

import os
import time
import uuid

import httpx
import pytest

# Surchargée par la CI (E2E_API_URL) pour pointer sur la stack éphémère de test-e2e
# (port 18000, cf. compose.ci.yml) au lieu de la vraie prod (port 8000).
API_URL = os.environ.get("E2E_API_URL", "http://localhost:8000")
API_V1 = f"{API_URL}/api/v1"

# Compte seedé par infra/postgres/init.sql, pas un compte de test créé à la volée.
ADMIN_EMAIL = "admin@enervision.io"
ADMIN_PASSWORD = "admin"


def poll(fn, *, timeout=90, interval=5):
    """Réessaie fn() jusqu'à ce qu'elle renvoie une valeur vraie, ou jusqu'au timeout.
    Nécessaire ici : les données transitent par des BlockingScheduler en tâche de
    fond (etl-alerts, etl-collect), pas par un appel synchrone qu'on contrôle."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def login(email, password):
    return httpx.post(f"{API_V1}/auth/login", data={"username": email, "password": password})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_health():
    """Le serveur API répond, avant même de parler auth ou DB."""
    resp = httpx.get(f"{API_URL}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.fixture(scope="module")
def admin_token():
    resp = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_login_wrong_password_rejected():
    """Un mauvais mot de passe sur un compte qui existe est bien refusé (pas de 500,
    pas de contournement)."""
    resp = login(ADMIN_EMAIL, "ce-nest-pas-le-bon-mot-de-passe")
    assert resp.status_code == 401


def test_admin_login_then_me(admin_token):
    """Le token émis par /auth/login est bien accepté par /auth/me."""
    resp = httpx.get(f"{API_V1}/auth/me", headers=auth_header(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"


def test_alerts_requires_auth():
    """Sans token, l'API refuse."""
    resp = httpx.get(f"{API_V1}/alerts")
    assert resp.status_code == 401


def test_full_user_lifecycle(admin_token):
    """Parcours complet du cycle utilisateur : un admin crée un compte viewer, ce
    compte peut se connecter et consulter les alertes, mais pas créer d'autres
    comptes (admin-only, cf. api/routers/users.py::create_user). Email suffixé par
    un uuid pour rester rejouable contre la base partagée et persistante (pas de
    reset entre deux runs, contrairement à api/tests/)."""
    new_email = f"e2e-viewer-{uuid.uuid4().hex[:8]}@enervision.io"
    new_password = "motdepasse123"

    create_resp = httpx.post(
        f"{API_V1}/users",
        json={"email": new_email, "password": new_password, "role": "viewer"},
        headers=auth_header(admin_token),
    )
    assert create_resp.status_code == 201, create_resp.text

    login_resp = login(new_email, new_password)
    assert login_resp.status_code == 200
    viewer_token = login_resp.json()["access_token"]

    me_resp = httpx.get(f"{API_V1}/auth/me", headers=auth_header(viewer_token))
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "viewer"

    alerts_resp = httpx.get(f"{API_V1}/alerts", headers=auth_header(viewer_token))
    assert alerts_resp.status_code == 200

    forbidden_resp = httpx.post(
        f"{API_V1}/users",
        json={"email": f"e2e-blocked-{uuid.uuid4().hex[:8]}@enervision.io", "password": "xxxxxxxx"},
        headers=auth_header(viewer_token),
    )
    assert forbidden_resp.status_code == 403


def test_alerts_pipeline_reaches_the_api(admin_token):
    """Traverse tout le pipeline alertes de bout en bout : API Mock IoT -> etl-alerts
    (upsert Postgres) -> notre API. etl-alerts tourne en tâche de fond (cycle toutes
    les 60s par défaut, ALERTS_INTERVALLE_SECONDES) : on poll au lieu de déclencher
    un cycle nous-mêmes, alerts.py n'a pas de mode "run once".

    Nécessite que la table `sites` soit peuplée (service etl-sites) : alerts.py
    ignore silencieusement toute alerte dont le site_id n'existe pas encore côté
    sites (contrainte FK, cf. etl/alerts.py::enregistrer_alertes). Tant que
    etl/sites.py reste un stub, ce test échoue par timeout — c'est le signal
    attendu, pas un faux positif de ce test.
    """

    def alertes_recues():
        resp = httpx.get(f"{API_V1}/alerts", headers=auth_header(admin_token))
        assert resp.status_code == 200
        return resp.json()

    data = poll(alertes_recues, timeout=90, interval=5)
    assert data, "Aucune alerte reçue après 90s : vérifier etl-sites (stub ?) et les logs de etl-alerts"
    premiere = data[0]
    assert premiere["alert_id"]
    assert premiere["site_id"], "site_id vide : alerts.py n'a pas pu résoudre le site (table sites vide ?)"
