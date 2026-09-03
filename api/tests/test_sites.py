from datetime import datetime, timedelta, timezone

import requests

from api.models import MeasurementSilver, Site
from api.tests.conftest import auth_headers, make_user


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data


def make_site(db_session, site_id: str, **kwargs) -> Site:
    site = Site(site_id=site_id, **kwargs)
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)
    return site


def test_list_sites_requires_token(client):
    response = client.get("/api/v1/sites")

    assert response.status_code == 401


def test_list_sites_returns_stored_sites(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(
        db_session,
        "SITE001",
        site_type="office",
        site_name="Bureau Paris La Défense",
        location="Paris, France",
        capacity_kw=200.0,
        status="active",
    )

    response = client.get("/api/v1/sites", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["site_id"] == "SITE001"
    assert body[0]["site_name"] == "Bureau Paris La Défense"
    assert body[0]["capacity_kw"] == 200.0


def test_list_sites_ordered_by_site_id(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(db_session, "SITE002")
    make_site(db_session, "SITE001")

    response = client.get("/api/v1/sites", headers=headers)

    assert [site["site_id"] for site in response.json()] == ["SITE001", "SITE002"]


def test_get_site_current_reading_requires_token(client):
    response = client.get("/api/v1/sites/SITE001/current")

    assert response.status_code == 401


def test_get_site_current_reading_returns_mock_data(client, db_session, monkeypatch):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    payload = {
        "timestamp": "2026-09-02T14:00:43.701732",
        "site_id": "SITE001",
        "site_type": "office",
        "consumption_kw": 179.61,
        "consumption_kwh": 179.61,
        "voltage_v": 404.6,
        "current_a": 269.8,
        "power_factor": 0.931,
        "temperature_celsius": 9.5,
        "humidity_percent": None,
        "null_reasons": ["humidity_sensor_failure"],
        "data_quality": "partial",
    }

    def fake_get(url, timeout):
        assert url.endswith("/api/v1/sites/SITE001/current")
        return FakeResponse(payload)

    monkeypatch.setattr("api.routers.sites.requests.get", fake_get)

    response = client.get("/api/v1/sites/SITE001/current", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["site_id"] == "SITE001"
    assert body["consumption_kw"] == 179.61
    assert body["null_reasons"] == ["humidity_sensor_failure"]
    assert body["data_quality"] == "partial"
    # Le mock renvoie un timestamp naïf ("...701732", sans fuseau) mais réellement
    # en UTC : l'API doit lui associer explicitement un fuseau, sinon
    # `new Date(...)` côté front l'interprète comme heure locale (décalage
    # silencieux).
    assert body["timestamp"] == "2026-09-02T14:00:43.701732Z"


def test_get_site_current_reading_returns_502_when_mock_unreachable(client, db_session, monkeypatch):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    def fake_get(url, timeout):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("api.routers.sites.requests.get", fake_get)

    response = client.get("/api/v1/sites/SITE001/current", headers=headers)

    assert response.status_code == 502


def test_get_site_current_reading_returns_502_when_site_unknown_upstream(client, db_session, monkeypatch):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    def fake_get(url, timeout):
        return FakeResponse({}, status_code=404)

    monkeypatch.setattr("api.routers.sites.requests.get", fake_get)

    response = client.get("/api/v1/sites/UNKNOWN/current", headers=headers)

    assert response.status_code == 502


def make_measurement(db_session, source_key: str, **kwargs) -> MeasurementSilver:
    measurement = MeasurementSilver(source_key=source_key, **kwargs)
    db_session.add(measurement)
    db_session.commit()
    db_session.refresh(measurement)
    return measurement


def test_list_site_measurements_requires_token(client):
    response = client.get("/api/v1/sites/SITE001/measurements")

    assert response.status_code == 401


def test_list_site_measurements_returns_recent_ordered_ascending(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    maintenant = datetime.now(timezone.utc)
    make_measurement(
        db_session,
        "M2",
        timestamp=maintenant - timedelta(minutes=1),
        site_id="SITE001",
        consumption_kw=60.0,
        data_quality="good",
    )
    make_measurement(
        db_session,
        "M1",
        timestamp=maintenant - timedelta(minutes=2),
        site_id="SITE001",
        consumption_kw=55.0,
        data_quality="good",
    )

    response = client.get("/api/v1/sites/SITE001/measurements", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["consumption_kw"] == 55.0
    assert body[1]["consumption_kw"] == 60.0


def test_list_site_measurements_excludes_rows_outside_the_window(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    maintenant = datetime.now(timezone.utc)
    make_measurement(
        db_session,
        "RECENT",
        timestamp=maintenant - timedelta(minutes=2),
        site_id="SITE001",
        consumption_kw=60.0,
    )
    make_measurement(
        db_session,
        "OLD",
        timestamp=maintenant - timedelta(hours=3),
        site_id="SITE001",
        consumption_kw=999.0,
    )

    response = client.get(
        "/api/v1/sites/SITE001/measurements",
        headers=headers,
        params={"depuis_minutes": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["consumption_kw"] == 60.0
