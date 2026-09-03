"""
Tests pour history.py : rejeu d'un historique de lectures vers la couche bronze.
Aucun test ne touche un vrai MinIO ni le réseau : le client S3 et requests.get
sont remplacés par des faux qui enregistrent leurs appels.
"""

import json
from datetime import UTC, datetime

import collect
import history
import pytest
from botocore.exceptions import ClientError


class ClientErreurSimulee(ClientError):
    """Erreur boto3 prête à lever dans un test, sans construire le dict à chaque fois."""

    def __init__(self):
        super().__init__({"Error": {"Code": "500", "Message": "erreur simulée"}}, "PutObject")


class FauxClientS3:
    """Remplace le client boto3 : garde chaque put_object pour vérification."""

    def __init__(self):
        self.appels = []

    def put_object(self, **kwargs):
        self.appels.append(kwargs)


@pytest.fixture()
def s3(monkeypatch):
    """history.rejouer_site écrit via collect.envoyer_mesure -> collect.storage.get_s3."""
    faux = FauxClientS3()
    monkeypatch.setattr(collect.storage, "get_s3", lambda: faux)
    return faux


class FausseReponse:
    def __init__(self, donnees):
        self._donnees = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self._donnees


def lecture(instant_iso, **extra):
    base = {"timestamp": instant_iso, "site_id": "SITE001", "consumption_kw": 42.0}
    base.update(extra)
    return base


# --------------------------------------------------------------- reculer_de_mois

def test_reculer_de_mois_recule_de_douze_mois():
    depart = datetime(2026, 9, 2, tzinfo=UTC)
    assert history.reculer_de_mois(depart, 12) == datetime(2025, 9, 2, tzinfo=UTC)


def test_reculer_de_mois_rabote_le_jour_sur_un_mois_court():
    """31 mars - 1 mois n'existe pas : on attend le dernier jour de février."""
    depart = datetime(2026, 3, 31, 8, 0, tzinfo=UTC)
    assert history.reculer_de_mois(depart, 1) == datetime(2026, 2, 28, 8, 0, tzinfo=UTC)


# ----------------------------------------------------------------- rejouer_site

def test_rejouer_site_depose_chaque_lecture_dans_bronze_et_audit(s3, monkeypatch):
    lectures = [
        lecture("2026-01-01T00:00:00"),
        lecture("2026-01-01T01:00:00"),
        lecture("2026-01-01T02:00:00"),
    ]
    monkeypatch.setattr(history, "recuperer_historique", lambda *a, **k: lectures)

    deposees, ignorees = history.rejouer_site("SITE001", None, None, 1000, 60)

    assert (deposees, ignorees) == (3, 0)
    assert len(s3.appels) == 6  # 3 bronze + 3 audit
    assert {a["Bucket"] for a in s3.appels} == {collect.MINIO_BUCKET_BRONZE, collect.MINIO_BUCKET_AUDIT}


def test_rejouer_site_injecte_le_site_id_manquant(s3, monkeypatch):
    """L'API filtrée par site peut ne pas répéter site_id ; quality.py l'exige."""
    sans_site = {"timestamp": "2026-01-01T00:00:00", "consumption_kw": 1.0}
    monkeypatch.setattr(history, "recuperer_historique", lambda *a, **k: [sans_site])

    history.rejouer_site("SITE007", None, None, 1000, 60)

    appel_bronze = next(a for a in s3.appels if a["Bucket"] == collect.MINIO_BUCKET_BRONZE)
    assert json.loads(appel_bronze["Body"])["site_id"] == "SITE007"
    assert appel_bronze["Key"].startswith("SITE007/2026-01-01/")


def test_rejouer_site_ignore_une_lecture_sans_timestamp(s3, monkeypatch):
    lectures = [
        {"site_id": "SITE001", "consumption_kw": 1.0},  # pas de timestamp -> ignorée
        lecture("2026-01-01T00:00:00"),
    ]
    monkeypatch.setattr(history, "recuperer_historique", lambda *a, **k: lectures)

    deposees, ignorees = history.rejouer_site("SITE001", None, None, 1000, 60)

    assert (deposees, ignorees) == (1, 1)


def test_rejouer_site_continue_apres_une_erreur_minio(s3, monkeypatch):
    lectures = [lecture("2026-01-01T00:00:00"), lecture("2026-01-01T01:00:00")]
    monkeypatch.setattr(history, "recuperer_historique", lambda *a, **k: lectures)

    appels = {"n": 0}
    vrai_envoyer = collect.envoyer_mesure

    def envoyer_capricieux(site_id, mesure):
        appels["n"] += 1
        if appels["n"] == 1:
            raise ClientErreurSimulee
        return vrai_envoyer(site_id, mesure)

    monkeypatch.setattr(collect, "envoyer_mesure", envoyer_capricieux)

    deposees, ignorees = history.rejouer_site("SITE001", None, None, 1000, 60)

    assert (deposees, ignorees) == (1, 1)


# ----------------------------------------------------------- recuperer_historique

def test_recuperer_historique_decoupe_selon_limit_et_pas(monkeypatch):
    """limit=96, pas=15 min -> fenêtre = 96*15 = 1440 min = 1 jour pile."""
    debut = datetime(2025, 1, 1, tzinfo=UTC)
    fin = datetime(2025, 1, 4, 12, 0, tzinfo=UTC)  # 3,5 jours -> 4 fenêtres
    appels = []

    def faux_demander(site_id, borne_debut, borne_fin, limit):
        appels.append((borne_debut, borne_fin, limit))
        return [lecture(borne_debut.strftime("%Y-%m-%dT%H:%M:%S"))]

    monkeypatch.setattr(history, "_demander_page", faux_demander)

    history.recuperer_historique("SITE001", debut, fin, limit=96, pas_minutes=15)

    assert len(appels) == 4
    assert appels[0][0] == debut
    assert appels[0][1] == datetime(2025, 1, 2, tzinfo=UTC)      # +1 j
    assert appels[1][0] == datetime(2025, 1, 2, tzinfo=UTC)      # jointif
    assert appels[3][1] == fin
    assert appels[0][2] == 96   # 1440 min / 15 = 96 points
    assert appels[3][2] == 48   # dernière fenêtre = 720 min / 15 = 48


def test_recuperer_historique_pas_plus_fin_demande_plus_de_points(monkeypatch):
    """Même période, pas plus fin -> l'API est priée de renvoyer plus de points."""
    debut = datetime(2025, 1, 1, tzinfo=UTC)
    fin = datetime(2025, 1, 3, tzinfo=UTC)  # 2 jours = 2880 min, une seule fenêtre

    def demandes_pour(pas):
        vus = []
        monkeypatch.setattr(history, "_demander_page", lambda s, a, b, limit: vus.append(limit) or [])
        history.recuperer_historique("SITE001", debut, fin, limit=1000, pas_minutes=pas)
        return vus

    assert demandes_pour(60) == [48]    # 2880 / 60
    assert demandes_pour(15) == [192]   # 2880 / 15


def test_recuperer_historique_dedoublonne_l_instant_de_bordure(monkeypatch):
    debut = datetime(2025, 1, 1, tzinfo=UTC)
    fin = datetime(2025, 1, 16, tzinfo=UTC)  # 15 j, pas=60/limit=240 -> 2 fenêtres de 10 j
    partage = "2025-01-11T00:00:00"

    def faux_demander(site_id, borne_debut, borne_fin, limit):
        return [lecture(partage), lecture(borne_fin.strftime("%Y-%m-%dT%H:%M:%S"))]

    monkeypatch.setattr(history, "_demander_page", faux_demander)

    lectures = history.recuperer_historique("SITE001", debut, fin, limit=240, pas_minutes=60)

    assert sum(1 for item in lectures if item["timestamp"] == partage) == 1


def test_demander_page_formate_les_bornes_en_iso(monkeypatch):
    capte = {}

    def faux_get(url, params=None, **kwargs):
        capte["url"] = url
        capte["params"] = params
        return FausseReponse({"readings": [lecture("2025-01-01T00:00:00")]})

    monkeypatch.setattr(history.requests, "get", faux_get)

    lectures = history._demander_page(
        "SITE003",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 2, 1, tzinfo=UTC),
        744,
    )

    assert capte["url"].endswith("/api/v1/readings")
    assert capte["params"] == {
        "site_id": "SITE003",
        "start_time": "2025-01-01T00:00:00",
        "end_time": "2025-02-01T00:00:00",
        "limit": 744,
    }
    assert len(lectures) == 1  # réponse enveloppée {"readings": [...]} acceptée


# ------------------------------------------------------------------------- main

def test_main_traite_tous_les_sites_et_retourne_zero(monkeypatch):
    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001", "SITE002"])
    vus = []

    def faux_rejouer(site_id, debut, fin, limit, pas_minutes):
        vus.append((site_id, limit, pas_minutes))
        return (5, 1)

    monkeypatch.setattr(history, "rejouer_site", faux_rejouer)

    code = history.main(["--mois", "6"])

    assert code == 0
    assert vus == [("SITE001", 1000, 60), ("SITE002", 1000, 60)]


def test_main_transmet_le_pas_minutes(monkeypatch):
    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001"])
    vus = []
    monkeypatch.setattr(
        history, "rejouer_site",
        lambda s, d, f, limit, pas_minutes: vus.append(pas_minutes) or (0, 0),
    )

    history.main(["--pas-minutes", "15"])

    assert vus == [15]


def test_main_refuse_un_pas_minutes_invalide(monkeypatch):
    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001"])
    appelé = []
    monkeypatch.setattr(history, "rejouer_site", lambda *a, **k: appelé.append(a) or (0, 0))

    code = history.main(["--pas-minutes", "0"])

    assert code == 1
    assert appelé == []


def test_main_refuse_une_periode_vide(monkeypatch):
    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001"])
    appelé = []
    monkeypatch.setattr(history, "rejouer_site", lambda *a, **k: appelé.append(a) or (0, 0))

    code = history.main(["--debut", "2030-01-01T00:00:00", "--fin", "2029-01-01T00:00:00"])

    assert code == 1
    assert appelé == []


def test_main_borne_la_limite_a_mille(monkeypatch):
    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001"])
    limites = []
    monkeypatch.setattr(
        history, "rejouer_site",
        lambda s, d, f, limit, pas_minutes: limites.append(limit) or (0, 0),
    )

    history.main(["--limit", "50000"])

    assert limites == [1000]
