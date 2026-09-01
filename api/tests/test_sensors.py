import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import sensors
from api.auth import get_current_user
from api.models import User

app = FastAPI()
app.include_router(sensors.router)


def utilisateur_de_test():
    return User(user_id="test-id", email="test@enervision.fr", role="viewer")


app.dependency_overrides[get_current_user] = utilisateur_de_test

client = TestClient(app)


class FausseReponseMock:
    """Simule la réponse HTTP de l'API Mock, sans vraie requête réseau."""

    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erreur", request=None, response=self)


@pytest.fixture
def donnee_capteurs_simulee():
    return {
        "SITE001": {
            "site_name": "Bureau Paris La Défense",
            "sensors": {"consumption": {"status": "ok", "failing_until": None}},
            "overall": "ok",
        }
    }


def test_sensors_status_relaie_la_reponse(monkeypatch, donnee_capteurs_simulee):
    """L'endpoint doit retourner exactement ce que renvoie l'API Mock, sans transformation."""

    async def fausse_get(self, url, **kwargs):
        return FausseReponseMock(donnee_capteurs_simulee)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get)

    reponse = client.get("/api/v1/sensors/status")
    assert reponse.status_code == 200
    assert reponse.json() == donnee_capteurs_simulee


def test_sensors_status_gere_une_panne_reseau(monkeypatch):
    """Si l'API Mock est injoignable, l'endpoint doit répondre 502, pas planter."""

    async def fausse_get_qui_echoue(self, url, **kwargs):
        raise httpx.RequestError("connexion impossible", request=None)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get_qui_echoue)

    reponse = client.get("/api/v1/sensors/status")
    assert reponse.status_code == 502


def test_sensors_status_sans_authentification():
    """Sans utilisateur authentifié, l'accès doit être refusé."""
    app.dependency_overrides.pop(get_current_user, None)

    def get_current_user_qui_refuse():
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Non authentifié")

    app.dependency_overrides[get_current_user] = get_current_user_qui_refuse
    reponse = client.get("/api/v1/sensors/status")
    assert reponse.status_code == 401

    app.dependency_overrides[get_current_user] = utilisateur_de_test