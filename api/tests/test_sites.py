from datetime import UTC, datetime, timedelta

import requests

from api.models import AggregateGoldDaily, MeasurementSilver, PredictionForecast, Site
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
    maintenant = datetime.now(UTC)
    make_measurement(
        db_session,
        "M2",
        timestamp=maintenant - timedelta(minutes=1),
        site_id="SITE001",
        consumption_kw=60.0,
        voltage_v=400.5,
        current_a=150.2,
        power_factor=0.92,
        temperature_celsius=9.5,
        humidity_percent=41.0,
        quality_score=100,
        data_quality="good",
    )
    make_measurement(
        db_session,
        "M1",
        timestamp=maintenant - timedelta(minutes=2),
        site_id="SITE001",
        consumption_kw=None,
        data_quality="critical",
        null_reasons=["network_loss"],
    )

    response = client.get("/api/v1/sites/SITE001/measurements", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["consumption_kw"] is None
    assert body[0]["null_reasons"] == ["network_loss"]
    assert body[1]["consumption_kw"] == 60.0
    assert body[1]["null_reasons"] == []
    assert body[1]["voltage_v"] == 400.5
    assert body[1]["current_a"] == 150.2
    assert body[1]["power_factor"] == 0.92
    assert body[1]["temperature_celsius"] == 9.5
    assert body[1]["humidity_percent"] == 41.0
    assert body[1]["quality_score"] == 100


def test_list_site_measurements_excludes_rows_outside_the_window(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    maintenant = datetime.now(UTC)
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


def test_list_site_measurements_accepte_une_fenetre_de_sept_jours(client, db_session):
    """Le tableau de bord laisse saisir la période affichée : au-delà de 24 h, c'est cette
    borne qui décide, et elle doit couvrir la même profondeur que /predictions (168 h)."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    maintenant = datetime.now(UTC)
    make_measurement(
        db_session,
        "IL_Y_A_5_JOURS",
        timestamp=maintenant - timedelta(days=5),
        site_id="SITE001",
        consumption_kw=120.0,
    )

    dans_la_fenetre = client.get(
        "/api/v1/sites/SITE001/measurements",
        headers=headers,
        params={"depuis_minutes": 7 * 24 * 60},
    )
    hors_bornes = client.get(
        "/api/v1/sites/SITE001/measurements",
        headers=headers,
        params={"depuis_minutes": 7 * 24 * 60 + 1},
    )

    assert dans_la_fenetre.status_code == 200
    assert [ligne["consumption_kw"] for ligne in dans_la_fenetre.json()] == [120.0]
    assert hors_bornes.status_code == 422


def test_list_site_measurements_eclaircit_par_pas_sans_perdre_la_derniere(client, db_session):
    """7 jours à la minute font ~10 000 lignes par site : le pas ramène la réponse à ce que le
    graphique peut tracer, en ne renvoyant que des lectures réellement collectées."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    maintenant = datetime.now(UTC)
    # Dix lectures à la minute : 9 min d'écart entre la première et la dernière.
    for minutes in range(10):
        make_measurement(
            db_session,
            f"M{minutes}",
            timestamp=maintenant - timedelta(minutes=minutes),
            site_id="SITE001",
            consumption_kw=float(minutes),
        )

    response = client.get(
        "/api/v1/sites/SITE001/measurements",
        headers=headers,
        params={"depuis_minutes": 60, "pas_minutes": 3},
    )

    assert response.status_code == 200
    valeurs = [ligne["consumption_kw"] for ligne in response.json()]
    # Une lecture toutes les 3 minutes, comptées depuis la plus récente (0) : la dernière
    # lecture est toujours renvoyée, c'est elle que le front affiche comme lecture courante.
    assert valeurs == [9.0, 6.0, 3.0, 0.0]

    # Sans pas, la réponse reste celle d'avant : rien n'est éclairci par défaut.
    complet = client.get(
        "/api/v1/sites/SITE001/measurements",
        headers=headers,
        params={"depuis_minutes": 60},
    )
    assert len(complet.json()) == 10


def make_gold_daily(db_session, record_date, site_id: str, **kwargs) -> AggregateGoldDaily:
    aggregat = AggregateGoldDaily(record_date=record_date, site_id=site_id, **kwargs)
    db_session.add(aggregat)
    db_session.commit()
    db_session.refresh(aggregat)
    return aggregat


def test_get_site_daily_summary_requires_token(client):
    response = client.get("/api/v1/sites/SITE001/daily-summary")

    assert response.status_code == 401


def test_get_site_daily_summary_returns_todays_aggregate(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    aujourdhui = datetime.now(UTC).date()
    make_gold_daily(
        db_session,
        aujourdhui,
        "SITE001",
        records_count=203,
        good_count=34,
        avg_consumption_kw=111.29,
        max_consumption_kw=186.63,
        total_consumption_kwh=11685.34,
        avg_quality_score=68.53,
    )

    response = client.get("/api/v1/sites/SITE001/daily-summary", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["records_count"] == 203
    assert body["good_count"] == 34
    assert body["avg_consumption_kw"] == 111.29
    assert body["max_consumption_kw"] == 186.63
    assert body["total_consumption_kwh"] == 11685.34
    assert body["avg_quality_score"] == 68.53


def test_get_site_daily_summary_returns_null_when_not_yet_computed(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    hier = datetime.now(UTC).date() - timedelta(days=1)
    # Un agrégat existe, mais pas pour aujourd'hui : ne doit pas être renvoyé.
    make_gold_daily(db_session, hier, "SITE001", records_count=100, good_count=50)

    response = client.get("/api/v1/sites/SITE001/daily-summary", headers=headers)

    assert response.status_code == 200
    assert response.json() is None


def make_prediction(db_session, site_id: str, target_ts: datetime, **kwargs) -> PredictionForecast:
    defaults = {
        "step_minutes": 60,
        "predicted_kwh": 100.0,
        "lower_90": 80.0,
        "upper_90": 120.0,
        "model_name": f"enervision-forecast-{site_id.lower()}",
        "model_version": "1",
        "model_stage": "Production",
        "champion": "tow_temp",
        "data_source": "csv_synthetic",
        "temperature_celsius": 20.0,
        "temperature_source": "climatology_fallback",
        "predicted_at": datetime.now(UTC),
    }
    prediction = PredictionForecast(site_id=site_id, target_ts=target_ts, **{**defaults, **kwargs})
    db_session.add(prediction)
    db_session.commit()
    return prediction


def test_list_predictions_requires_token(client):
    response = client.get("/api/v1/sites/SITE001/predictions")

    assert response.status_code == 401


def test_list_predictions_refuse_mot_de_passe_temporaire(client, db_session):
    """Un compte encore sur son mot de passe d'amorçage n'accède pas aux prévisions :
    la garde est portée par l'API, pas seulement par la redirection du front."""
    make_user(db_session, "neuf@enervision.fr", "password123", "viewer", must_change_password=True)
    headers = auth_headers(client, "neuf@enervision.fr", "password123")

    response = client.get("/api/v1/sites/SITE001/predictions", headers=headers)

    assert response.status_code == 403


def test_list_predictions_retourne_les_heures_a_venir(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    prochaine_heure = datetime.now(UTC) + timedelta(hours=1)
    make_prediction(db_session, "SITE001", prochaine_heure, predicted_kwh=42.5)

    response = client.get("/api/v1/sites/SITE001/predictions", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["predicted_kwh"] == 42.5
    assert body[0]["lower_90"] == 80.0
    assert body[0]["upper_90"] == 120.0
    assert body[0]["step_minutes"] == 60
    # Provenance exposée : le front doit pouvoir signaler que ces modèles sont
    # entraînés sur CSV synthétique et non validés sur donnée réelle.
    assert body[0]["data_source"] == "csv_synthetic"
    assert body[0]["model_version"] == "1"


def test_list_predictions_exclut_les_heures_passees_par_defaut(client, db_session):
    """Sans historique_heures, seul le futur est servi : un appelant qui ne demande pas le
    passé ne doit pas le recevoir par surprise."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) - timedelta(hours=2), predicted_kwh=10.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=2), predicted_kwh=20.0)

    response = client.get("/api/v1/sites/SITE001/predictions", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [ligne["predicted_kwh"] for ligne in body] == [20.0]


def test_list_predictions_respecte_horizon_heures(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=1.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=30), predicted_kwh=2.0)

    response = client.get("/api/v1/sites/SITE001/predictions?horizon_heures=6", headers=headers)

    assert response.status_code == 200
    assert [ligne["predicted_kwh"] for ligne in response.json()] == [1.0]


def test_list_predictions_triees_par_horodatage(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=3), predicted_kwh=3.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=1.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=2), predicted_kwh=2.0)

    response = client.get("/api/v1/sites/SITE001/predictions", headers=headers)

    assert [ligne["predicted_kwh"] for ligne in response.json()] == [1.0, 2.0, 3.0]


def test_list_predictions_isole_les_sites(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=1.0)
    make_prediction(db_session, "SITE003", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=3.0)

    response = client.get("/api/v1/sites/SITE003/predictions", headers=headers)

    assert [ligne["predicted_kwh"] for ligne in response.json()] == [3.0]


def test_list_predictions_site_sans_modele_renvoie_liste_vide(client, db_session):
    """SITE002/004/007 ont un champion LightGBM non inférable (solar_irradiance_wm2 absente
    du gold) : absence de prévision est un état normal, pas un 404."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    response = client.get("/api/v1/sites/SITE002/predictions", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


def test_list_predictions_horizon_hors_bornes_rejete(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    assert client.get("/api/v1/sites/SITE001/predictions?horizon_heures=0", headers=headers).status_code == 422
    assert client.get("/api/v1/sites/SITE001/predictions?horizon_heures=999", headers=headers).status_code == 422


def test_list_predictions_bornes_absentes_restent_nulles(client, db_session):
    """Un run MLflow sans métrique de marge conforme donne des bornes NULL — elles ne doivent
    pas être repliées sur la valeur centrale, ce qui se lirait comme une certitude parfaite."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(
        db_session, "SITE001", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=50.0, lower_90=None, upper_90=None
    )

    body = client.get("/api/v1/sites/SITE001/predictions", headers=headers).json()

    assert body[0]["lower_90"] is None
    assert body[0]["upper_90"] is None
    assert body[0]["predicted_kwh"] == 50.0


def test_list_predictions_historique_ramene_les_heures_passees(client, db_session):
    """Les heures passées ne sont pas des prévisions périmées : chaque cycle réécrit le futur
    et laisse le passé intact, donc une ligne passée reste la prévision réellement émise pour
    cette heure-là. C'est ce qui permet de superposer prévu et réalisé sur le dashboard."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) - timedelta(hours=2), predicted_kwh=10.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) + timedelta(hours=2), predicted_kwh=20.0)

    response = client.get("/api/v1/sites/SITE001/predictions?historique_heures=6", headers=headers)

    assert response.status_code == 200
    assert [ligne["predicted_kwh"] for ligne in response.json()] == [10.0, 20.0]


def test_list_predictions_historique_borne_la_profondeur(client, db_session):
    """La borne est comptée depuis maintenant, pas depuis le plus ancien enregistrement :
    demander 3 h ne doit pas ramener une prévision vieille de 10 h."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_prediction(db_session, "SITE001", datetime.now(UTC) - timedelta(hours=10), predicted_kwh=1.0)
    make_prediction(db_session, "SITE001", datetime.now(UTC) - timedelta(hours=1), predicted_kwh=2.0)

    response = client.get("/api/v1/sites/SITE001/predictions?historique_heures=3", headers=headers)

    assert [ligne["predicted_kwh"] for ligne in response.json()] == [2.0]


def test_list_predictions_historique_hors_bornes_rejete(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    reponse = client.get("/api/v1/sites/SITE001/predictions?historique_heures=-1", headers=headers)
    assert reponse.status_code == 422
    reponse = client.get("/api/v1/sites/SITE001/predictions?historique_heures=999", headers=headers)
    assert reponse.status_code == 422


def test_list_recommendations_requires_token(client):
    assert client.get("/api/v1/sites/SITE001/recommendations").status_code == 401


def test_list_recommendations_refuse_mot_de_passe_temporaire(client, db_session):
    make_user(db_session, "neuf@enervision.fr", "password123", "viewer", must_change_password=True)
    headers = auth_headers(client, "neuf@enervision.fr", "password123")

    assert client.get("/api/v1/sites/SITE001/recommendations", headers=headers).status_code == 403


def test_list_recommendations_signale_un_depassement_a_venir(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(db_session, "SITE003", capacity_kw=800.0)
    make_prediction(
        db_session, "SITE003", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=850.0, upper_90=900.0
    )

    reponse = client.get("/api/v1/sites/SITE003/recommendations", headers=headers)

    assert reponse.status_code == 200
    body = reponse.json()
    assert len(body) == 1
    assert body[0]["niveau"] == "depassement_prevu"
    assert body[0]["depassement_max_kw"] == 50.0
    assert body[0]["capacity_kw"] == 800.0
    assert "Décaler ou lisser" in body[0]["message"]


def test_list_recommendations_ignore_les_heures_passees(client, db_session):
    """Recommander un ajustement sur une heure écoulée n'aurait aucun effet."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(db_session, "SITE003", capacity_kw=800.0)
    make_prediction(
        db_session, "SITE003", datetime.now(UTC) - timedelta(hours=2), predicted_kwh=999.0, upper_90=999.0
    )

    assert client.get("/api/v1/sites/SITE003/recommendations", headers=headers).json() == []


def test_list_recommendations_site_sans_capacite_renvoie_vide(client, db_session):
    """capacity_kw est nullable : sans seuil contractuel, il n'y a rien à dépasser."""
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(db_session, "SITE009", capacity_kw=None)
    make_prediction(db_session, "SITE009", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=9999.0)

    reponse = client.get("/api/v1/sites/SITE009/recommendations", headers=headers)

    assert reponse.status_code == 200
    assert reponse.json() == []


def test_list_recommendations_site_inconnu_renvoie_vide(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")

    reponse = client.get("/api/v1/sites/INCONNU/recommendations", headers=headers)

    assert reponse.status_code == 200
    assert reponse.json() == []


def test_list_recommendations_rien_a_signaler_sous_le_seuil(client, db_session):
    make_user(db_session, "viewer@enervision.fr", "password123", "viewer")
    headers = auth_headers(client, "viewer@enervision.fr", "password123")
    make_site(db_session, "SITE001", capacity_kw=200.0)
    make_prediction(
        db_session, "SITE001", datetime.now(UTC) + timedelta(hours=1), predicted_kwh=100.0, upper_90=150.0
    )

    assert client.get("/api/v1/sites/SITE001/recommendations", headers=headers).json() == []
