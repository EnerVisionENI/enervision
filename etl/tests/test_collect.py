"""
Tests pour EV-011 / EV-017, collecte vers MinIO.
Aucun test ne dépend d'un vrai serveur MinIO ni du réseau :
le client S3 est remplacé par un faux qui enregistre chaque appel.
"""

import hashlib
import json

import pytest

import collect


class FauxClientS3:
    """Remplace le client boto3 : garde en mémoire chaque put_object pour vérification."""

    def __init__(self):
        self.appels = []

    def put_object(self, **kwargs):
        self.appels.append(kwargs)


@pytest.fixture()
def s3(monkeypatch):
    """Injecte un faux client S3 dans collect (via storage.get_s3)."""
    faux = FauxClientS3()
    monkeypatch.setattr(collect.storage, "get_s3", lambda: faux)
    return faux


def test_envoyer_mesure_ecrit_dans_bronze_et_audit(s3):
    """Chaque mesure doit produire exactement deux écritures, une bronze, une audit."""
    mesure = {
        "timestamp": "2026-09-01T09:57:43.123456",
        "site_id": "SITE001",
        "consumption_kw": 87.34,
    }
    collect.envoyer_mesure("SITE001", mesure)

    assert len(s3.appels) == 2
    buckets = [appel["Bucket"] for appel in s3.appels]
    assert collect.MINIO_BUCKET_BRONZE in buckets
    assert collect.MINIO_BUCKET_AUDIT in buckets


def test_envoyer_mesure_cle_bronze_bien_formee(s3):
    """La clé bronze doit suivre site_id/date/heure.json."""
    mesure = {"timestamp": "2026-09-01T09:57:43.123456", "site_id": "SITE001"}
    collect.envoyer_mesure("SITE001", mesure)

    appel_bronze = next(a for a in s3.appels if a["Bucket"] == collect.MINIO_BUCKET_BRONZE)
    assert appel_bronze["Key"] == "SITE001/2026-09-01/095743.json"


def test_envoyer_mesure_hash_coherent_entre_bronze_et_audit(s3):
    """Le hash SHA-256 stocké en métadonnée bronze doit être identique
    à celui dupliqué dans l'enregistrement audit, et correspondre vraiment
    au contenu écrit, sinon l'audit ne sert à rien pour détecter une altération."""
    mesure = {
        "timestamp": "2026-09-01T09:57:43.123456",
        "site_id": "SITE001",
        "consumption_kw": 42.0,
    }
    collect.envoyer_mesure("SITE001", mesure)

    appel_bronze = next(a for a in s3.appels if a["Bucket"] == collect.MINIO_BUCKET_BRONZE)
    appel_audit = next(a for a in s3.appels if a["Bucket"] == collect.MINIO_BUCKET_AUDIT)

    hash_metadonnee_bronze = appel_bronze["Metadata"]["sha256"]
    enregistrement_audit = json.loads(appel_audit["Body"])
    hash_audit = enregistrement_audit["sha256"]
    hash_reel_du_contenu = hashlib.sha256(appel_bronze["Body"]).hexdigest()

    assert hash_metadonnee_bronze == hash_audit == hash_reel_du_contenu


def test_envoyer_mesure_cle_audit_prefixee_par_bucket_source(s3):
    """La clé audit doit être préfixée par le nom du bucket bronze,
    pour éviter une collision future avec silver ou gold sur le même site/date."""
    mesure = {"timestamp": "2026-09-01T09:57:43.123456", "site_id": "SITE001"}
    collect.envoyer_mesure("SITE001", mesure)

    appel_audit = next(a for a in s3.appels if a["Bucket"] == collect.MINIO_BUCKET_AUDIT)
    assert appel_audit["Key"] == f"{collect.MINIO_BUCKET_BRONZE}/SITE001/2026-09-01/095743.json"


def test_marquer_vivant_ecrit_un_horodatage(tmp_path, monkeypatch):
    """Le heartbeat doit écrire un vrai horodatage lisible, pour le HEALTHCHECK Docker."""
    chemin_heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(collect, "HEARTBEAT_PATH", str(chemin_heartbeat))

    collect.marquer_vivant()

    contenu = chemin_heartbeat.read_text(encoding="utf-8")
    assert contenu
    assert "T" in contenu


def test_cycle_collecte_continue_apres_une_erreur_minio(monkeypatch):
    """Une erreur d'écriture MinIO sur un site ne doit pas arrêter la collecte des autres."""
    from botocore.exceptions import ClientError

    monkeypatch.setattr(collect, "recuperer_liste_sites", lambda: ["SITE001", "SITE002"])
    monkeypatch.setattr(
        collect,
        "recuperer_mesure",
        lambda site_id: {"timestamp": "2026-09-01T09:00:00", "site_id": site_id},
    )

    sites_traites = []

    def fausse_envoyer_mesure(site_id, mesure):
        sites_traites.append(site_id)
        if site_id == "SITE001":
            raise ClientError({"Error": {"Code": "500", "Message": "erreur simulée"}}, "PutObject")

    monkeypatch.setattr(collect, "envoyer_mesure", fausse_envoyer_mesure)
    monkeypatch.setattr(collect, "marquer_vivant", lambda: None)
    monkeypatch.setattr(collect, "LANCER_QUALITY", False)

    collect.cycle_collecte()

    assert sites_traites == ["SITE001", "SITE002"]
