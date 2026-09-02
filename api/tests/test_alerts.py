from datetime import datetime

from api.models import Alert
from api.tests.conftest import auth_headers, make_user


def make_alert(db_session, alert_id: str, **kwargs) -> Alert:
    alert = Alert(alert_id=alert_id, **kwargs)
    db_session.add(alert)
    db_session.commit()
    db_session.refresh(alert)
    return alert


def test_list_alerts_requires_token(client):
    response = client.get("/alerts")

    assert response.status_code == 401


def test_list_alerts_returns_stored_alerts(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_alert(
        db_session,
        "A1",
        timestamp=datetime(2026, 9, 1, 9, 0, 0),
        site_id="SITE001",
        severity="high",
        type="overload",
        message="Consommation excessive",
        value=120.5,
        threshold=100.0,
    )

    response = client.get("/alerts", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["alert_id"] == "A1"
    assert body[0]["site_id"] == "SITE001"
    assert body[0]["severity"] == "high"


def test_list_alerts_ordered_by_timestamp_desc(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_alert(db_session, "OLD", timestamp=datetime(2026, 1, 1, 0, 0, 0))
    make_alert(db_session, "NEW", timestamp=datetime(2026, 9, 1, 0, 0, 0))

    response = client.get("/alerts", headers=headers)

    assert [alert["alert_id"] for alert in response.json()] == ["NEW", "OLD"]


def test_list_alerts_respects_limit(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_alert(db_session, "A1", timestamp=datetime(2026, 1, 1))
    make_alert(db_session, "A2", timestamp=datetime(2026, 1, 2))

    response = client.get("/alerts", headers=headers, params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1
