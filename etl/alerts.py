"""
EV-032 : collecte des alertes depuis l'API Mock IoT, table `alerts` en PostgreSQL,
planifiée toutes les 24h avec APScheduler (upsert sur alert_id).

Installation :
    pip install -r requirements.txt

Usage :
    python alerts.py
"""

import os
from datetime import datetime, timezone

import psycopg2
import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from psycopg2.extras import execute_values

API_BASE = os.environ.get("API_BASE", "http://10.105.200.45:8000")
INTERVALLE_SECONDES = int(os.environ.get("ALERTS_INTERVALLE_SECONDES", "60"))

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "ev_monitoring")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "ev_admin")
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

HEARTBEAT_PATH = os.environ.get("HEARTBEAT_PATH", "/tmp/heartbeat")

UPSERT_SQL = """
    INSERT INTO alerts (alert_id, timestamp, site_id, severity, type, message, value, threshold)
    VALUES %s
    ON CONFLICT (alert_id) DO UPDATE SET
        timestamp = EXCLUDED.timestamp,
        site_id = EXCLUDED.site_id,
        severity = EXCLUDED.severity,
        type = EXCLUDED.type,
        message = EXCLUDED.message,
        value = EXCLUDED.value,
        threshold = EXCLUDED.threshold
"""


def se_connecter_postgres():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def recuperer_alertes():
    """Récupère la liste des alertes depuis l'API Mock IoT."""
    reponse = requests.get(f"{API_BASE}/api/v1/alerts")
    reponse.raise_for_status()
    return reponse.json()


def recuperer_sites_connus(conn):
    """Sites déjà présents en base. alerts.site_id référence sites(site_id) :
    tant qu'un site n'existe pas côté sites (table pas encore alimentée), ses
    alertes doivent être ignorées plutôt que de faire échouer tout le lot sur
    une violation de clé étrangère."""
    with conn.cursor() as cur:
        cur.execute("SELECT site_id FROM sites")
        return {ligne[0] for ligne in cur.fetchall()}


def vers_ligne(alerte):
    """Convertit une alerte JSON en tuple, dans l'ordre des colonnes de la table alerts."""
    return (
        alerte["alert_id"],
        alerte.get("timestamp"),
        alerte.get("site_id"),
        alerte.get("severity"),
        alerte.get("type"),
        alerte.get("message"),
        alerte.get("value"),
        alerte.get("threshold"),
    )


def enregistrer_alertes(conn, alertes):
    """Upsert des alertes en base (une transaction pour tout le lot).
    Ignore les alertes sans alert_id (clé primaire) et celles dont le site
    n'existe pas encore dans la table sites. Renvoie (enregistrées, ignorées)."""
    sites_connus = recuperer_sites_connus(conn)
    lignes = [
        vers_ligne(alerte)
        for alerte in alertes
        if alerte.get("alert_id") and (alerte.get("site_id") is None or alerte.get("site_id") in sites_connus)
    ]
    ignorees = len(alertes) - len(lignes)

    if lignes:
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, lignes)
        conn.commit()

    return len(lignes), ignorees


def marquer_vivant():
    """Touche un fichier local pour le HEALTHCHECK Docker."""
    with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def cycle_alertes():
    """Un cycle : récupère toutes les alertes de l'API, upsert en base.
    Appelé automatiquement par le planificateur toutes les 24h."""
    alertes = recuperer_alertes()

    conn = se_connecter_postgres()
    try:
        enregistrees, ignorees = enregistrer_alertes(conn, alertes)
    finally:
        conn.close()

    marquer_vivant()
    print(f"Cycle terminé, {enregistrees} alertes enregistrées, {ignorees} ignorées")


def main():
    print(f"Démarrage du planificateur d'alertes, cycle toutes les {INTERVALLE_SECONDES}s")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        cycle_alertes,
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
