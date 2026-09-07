import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_active_user
from api.models import User
from api.routers import sensors_failing

app = FastAPI()
app.include_router(sensors_failing.router, prefix="/api/v1")


def utilisateur_de_test():
    return User(user_id="test-id", email="test@enervision.fr", role="viewer")


app.dependency_overrides[get_active_user] = utilisateur_de_test
client = TestClient(app)


class FausseReponseMock:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erreur", request=None, response=self)


DONNEES_EXEMPLE = {
    "SITE001": {
        "site_name": "Bureau Paris La Défense",
        "sensors": {
            "consumption": {"status": "ok", "failing_until": None},
            "electrical": {"status": "ok", "failing_until": None},
            "temperature": {"status": "failing", "failing_until": "2026-09-02T09:16:43.298010"},
            "humidity": {"status": "ok", "failing_until": None},
            "network": {"status": "ok", "failing_until": None},
        },
        "overall": "degraded",
    },
    "SITE002": {
        "site_name": "Usine Lyon Vénissieux",
        "sensors": {
            "consumption": {"status": "ok", "failing_until": None},
            "electrical": {"status": "ok", "failing_until": None},
            "temperature": {"status": "ok", "failing_until": None},
            "humidity": {"status": "ok", "failing_until": None},
            "network": {"status": "ok", "failing_until": None},
        },
        "overall": "ok",
    },
    "SITE007": {
        "site_name": "Usine Nantes Rezé",
        "sensors": {
            "consumption": {"status": "failing", "failing_until": "2026-09-02T09:17:01.425063"},
            "electrical": {"status": "ok", "failing_until": None},
            "temperature": {"status": "ok", "failing_until": None},
            "humidity": {"status": "failing", "failing_until": "2026-09-02T09:16:38.076457"},
            "network": {"status": "ok", "failing_until": None},
        },
        "overall": "degraded",
    },
}


def test_retourne_tous_les_sites(monkeypatch):
    """La réponse doit contenir les trois sites de l'exemple, pas un sous-ensemble."""

    async def fausse_get(self, url, **kwargs):
        return FausseReponseMock(DONNEES_EXEMPLE)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get)

    reponse = client.get("/api/v1/sensors/failing")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert set(corps.keys()) == {"SITE001", "SITE002", "SITE007"}


def test_capteur_en_panne_inclut_failing_until(monkeypatch):
    """Chaque capteur en panne doit inclure son nom et son failing_until, pas juste le nom."""

    async def fausse_get(self, url, **kwargs):
        return FausseReponseMock(DONNEES_EXEMPLE)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get)

    reponse = client.get("/api/v1/sensors/failing")
    corps = reponse.json()

    site001 = corps["SITE001"]["capteurs_en_panne"]
    assert len(site001) == 1
    assert site001[0]["capteur"] == "temperature"
    assert site001[0]["failing_until"] == "2026-09-02T09:16:43.298010"


def test_site_sans_panne_donne_liste_vide(monkeypatch):
    """SITE002 n'a aucun capteur en panne, la liste doit être vide."""

    async def fausse_get(self, url, **kwargs):
        return FausseReponseMock(DONNEES_EXEMPLE)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get)

    reponse = client.get("/api/v1/sensors/failing")
    corps = reponse.json()
    assert corps["SITE002"]["capteurs_en_panne"] == []


def test_site_avec_deux_pannes_liste_les_deux(monkeypatch):
    """SITE007 a deux capteurs en panne, les deux doivent apparaître avec leur failing_until."""

    async def fausse_get(self, url, **kwargs):
        return FausseReponseMock(DONNEES_EXEMPLE)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get)

    reponse = client.get("/api/v1/sensors/failing")
    corps = reponse.json()

    noms = sorted(c["capteur"] for c in corps["SITE007"]["capteurs_en_panne"])
    assert noms == ["consumption", "humidity"]
    for capteur in corps["SITE007"]["capteurs_en_panne"]:
        assert capteur["failing_until"] is not None


def test_panne_reseau_donne_502(monkeypatch):
    """Si l'API Mock est injoignable, l'endpoint doit répondre 502."""

    async def fausse_get_qui_echoue(self, url, **kwargs):
        raise httpx.RequestError("connexion impossible", request=None)

    monkeypatch.setattr(httpx.AsyncClient, "get", fausse_get_qui_echoue)

    reponse = client.get("/api/v1/sensors/failing")
    assert reponse.status_code == 502
