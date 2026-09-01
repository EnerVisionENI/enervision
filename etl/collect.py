"""
EV-011 : collecte des données de consommation depuis l'API Mock IoT,
couche bronze, un fichier JSONL par site et par jour,
planifiée toutes les 60 secondes avec APScheduler.

Installation :
    pip install requests apscheduler

Usage :
    python collect.py
"""

import json
import os
from datetime import datetime, timezone
import requests
from apscheduler.schedulers.blocking import BlockingScheduler

API_BASE = os.environ.get("API_BASE", "http://10.105.200.45:8000")
DOSSIER_BRONZE = os.environ.get("DOSSIER_BRONZE", "etl/bronze")
INTERVALLE_SECONDES = int(os.environ.get("INTERVALLE_SECONDES", "60"))


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


def chemin_fichier_du_jour(site_id, dossier=None):
    """Un dossier par site, un fichier JSONL par jour dedans, ex : bronze/SITE001/2026-08-31.jsonl"""
    if dossier is None:
        dossier = DOSSIER_BRONZE
    date_du_jour = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dossier_site = os.path.join(dossier, site_id)
    os.makedirs(dossier_site, exist_ok=True)
    return os.path.join(dossier_site, f"{date_du_jour}.jsonl")


def ajouter_mesure(site_id, mesure):
    """Ajoute la mesure à la fin du fichier bronze du jour, pour ce site."""
    chemin = chemin_fichier_du_jour(site_id)
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(mesure, ensure_ascii=False) + "\n")


def cycle_collecte():
    """Un cycle : interroge chaque site, ajoute sa mesure au fichier du jour.
    Appelé automatiquement par le planificateur toutes les 60 secondes."""
    sites = recuperer_liste_sites()
    for site_id in sites:
        try:
            mesure = recuperer_mesure(site_id)
            ajouter_mesure(site_id, mesure)
        except requests.RequestException as erreur:
            print(f"{site_id} : erreur de collecte, {erreur}")
    print(f"Cycle terminé, {len(sites)} sites collectés")


def main():
    print(f"Démarrage du planificateur, cycle toutes les {INTERVALLE_SECONDES}s")
    print(f"Fichiers écrits dans {DOSSIER_BRONZE}/, un par site et par jour")

    scheduler = BlockingScheduler()
    scheduler.add_job(cycle_collecte, "interval", seconds=INTERVALLE_SECONDES, next_run_time=datetime.now())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Arrêt du planificateur")


if __name__ == "__main__":
    main()