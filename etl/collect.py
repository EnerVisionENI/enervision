"""
EV-011 / EV-017 : collecte des données de consommation depuis l'API Mock IoT,
couche bronze sur MinIO (S3-compatible), un objet JSON par mesure et par site,
planifiée toutes les 60 secondes avec APScheduler.

Installation :
    pip install -r requirements.txt

Usage :
    python collect.py
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from botocore.exceptions import BotoCoreError, ClientError

import quality
import storage

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
INTERVALLE_SECONDES = int(os.environ.get("INTERVALLE_SECONDES", "60"))
LANCER_QUALITY = os.environ.get("LANCER_QUALITY", "1") == "1"

MINIO_BUCKET_BRONZE = os.environ.get("MINIO_BUCKET_BRONZE", "bronze")
MINIO_BUCKET_AUDIT = os.environ.get("MINIO_BUCKET_AUDIT", "audit")

HEARTBEAT_PATH = os.environ.get("HEARTBEAT_PATH", "/tmp/heartbeat")


def recuperer_liste_sites():
    """Récupère la liste des sites depuis l'API, pas de liste codée en dur."""
    reponse = requests.get(f"{API_BASE}/api/v1/sites")
    reponse.raise_for_status()
    return [site["site_id"] for site in reponse.json()]


def recuperer_mesure(site_id):
    """Récupère la mesure instantanée d'un site."""
    reponse = requests.get(f"{API_BASE}/api/v1/sites/{site_id}/current")
    reponse.raise_for_status()
    return reponse.json()


def envoyer_mesure(site_id, mesure):
    """Dépose la mesure en JSON sur MinIO, un objet par mesure.
    Clé : {site_id}/{date}/{heure}.json, ex. bronze/SITE001/2026-09-01/095743.json

    Le SHA-256 du contenu est attaché en métadonnée sur l'objet bronze, ET dupliqué dans
    un enregistrement indépendant sur le bucket audit (verrouillé en WORM), préfixé par le
    nom du bucket source (ex. audit/bronze/SITE001/...). Un hash stocké uniquement sur
    l'objet lui-même ne prouve rien : qui peut modifier l'objet peut aussi modifier sa
    métadonnée. La copie dans audit/ rend l'altération du bronze détectable en comparant
    les deux. Le préfixe évite une collision de clé le jour où silver/gold seront aussi
    hashés (même site_id/date, bucket source différent)."""
    horodatage = datetime.fromisoformat(mesure["timestamp"])
    cle = f"{site_id}/{horodatage:%Y-%m-%d}/{horodatage:%H%M%S}.json"
    contenu = json.dumps(mesure, ensure_ascii=False).encode("utf-8")
    empreinte = hashlib.sha256(contenu).hexdigest()

    s3 = storage.get_s3()
    s3.put_object(
        Bucket=MINIO_BUCKET_BRONZE,
        Key=cle,
        Body=contenu,
        ContentType="application/json",
        Metadata={"sha256": empreinte},
    )

    cle_audit = f"{MINIO_BUCKET_BRONZE}/{cle}"
    enregistrement_audit = {
        "bucket": MINIO_BUCKET_BRONZE,
        "key": cle,
        "sha256": empreinte,
        "size_bytes": len(contenu),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=MINIO_BUCKET_AUDIT,
        Key=cle_audit,
        Body=json.dumps(enregistrement_audit, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def marquer_vivant():
    """Touche un fichier local pour le HEALTHCHECK Docker.
    Il n'y a plus de fichier bronze local à inspecter, la donnée part directement sur MinIO."""
    with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def lancer_quality():
    """Enchaîne quality.py (bronze -> silver/gold) juste après la collecte.
    Appel in-process : une erreur du traitement ne doit pas interrompre le planificateur."""
    try:
        code = quality.main([])
        if code != 0:
            print(f"quality.py a terminé avec le code {code}")
    except Exception as erreur:  # on ne tue jamais le scheduler pour une erreur de quality
        print(f"quality.py a échoué : {erreur}")


def cycle_collecte():
    """Un cycle : interroge chaque site, dépose sa mesure sur MinIO.
    Appelé automatiquement par le planificateur toutes les 60 secondes."""
    sites = recuperer_liste_sites()
    for site_id in sites:
        try:
            mesure = recuperer_mesure(site_id)
            envoyer_mesure(site_id, mesure)
        except (requests.RequestException, BotoCoreError, ClientError) as erreur:
            print(f"{site_id} : erreur de collecte, {erreur}")
    marquer_vivant()
    print(f"Cycle terminé, {len(sites)} sites collectés")

    if LANCER_QUALITY:
        lancer_quality()


def main():
    print(f"Démarrage du planificateur, cycle toutes les {INTERVALLE_SECONDES}s")
    print(f"Mesures déposées sur MinIO, bucket {MINIO_BUCKET_BRONZE}/")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        cycle_collecte,
        "interval",
        seconds=INTERVALLE_SECONDES,
        next_run_time=datetime.now(timezone.utc),
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Arrêt du planificateur")


if __name__ == "__main__":
    main()
