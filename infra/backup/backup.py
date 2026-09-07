"""
EV-040 : sauvegarde quotidienne des tables `users` et `sites` (PostgreSQL) vers Azure
Blob, dans le MÊME container que bronze/silver/gold (`enervision-backup`), dossiers
`users/` et `sites/`.

Exécution : PAS un job etl/ dockerisé — un script autonome, planifié par la crontab
système de la VM (voir infra/DEPLOYMENT.md), qui se connecte à Postgres via le port
exposé sur l'hôte (`localhost:5433`, mêmes défauts que `api/config.py`) puisqu'il
tourne hors du réseau Docker interne, contrairement aux jobs `etl-*` (qui utilisent
`postgres:5432`).

Pipeline :

    pg_dump --table <table>     (SQL en clair, en mémoire)
      | gzip                    (compression, en mémoire)
        -> écrit UNE FOIS sur disque : <table>_<date>.sql.gz (compressé, PAS chiffré)
          -> rclone copy vers Azure via le remote azure-crypt
            -> chiffrement côté client fait PAR RCLONE au moment de l'envoi
              -> fichier local supprimé

Une seule couche de chiffrement, pas deux : réutilise EXACTEMENT la même technique que
bronze/silver/gold (infra/audit-sync/sync-audit-to-azure.sh) — même remote rclone
`azure-crypt`, mêmes AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY / AZURE_STORAGE_CONTAINER
et RCLONE_CRYPT_PASSWORD_RAW. Le fichier local (`.sql.gz`) reste en clair entre l'écriture
disque et l'envoi, exactement comme bronze/silver/gold restent en clair dans MinIO avant
leur synchro périodique vers Azure : ce n'est pas un relâchement de sécurité spécifique à
ce script, c'est la même exposition, le même temps de vie court (supprimé juste après
l'envoi), que le reste du projet accepte déjà pour les trois autres dossiers.

Variables lues dans le MÊME .env racine que le reste de la stack (déjà généré par le job
`deploy` de .github/workflows/ci-cd.yml à partir de infra/env/production.env + secrets
GitHub Actions) : rien de nouveau à y ajouter, ce script ne fait qu'y piocher.

Dépendances système à installer sur la VM (pas dans un conteneur, voir
infra/DEPLOYMENT.md) : pg_dump (postgresql-client-16, pour matcher postgres:16-alpine),
rclone. gzip est déjà présent sur toute distribution Linux standard. Aucune dépendance
Python tierce : bibliothèque standard uniquement.

Usage :
    python3 backup.py

Cron (voir infra/DEPLOYMENT.md pour le détail) :
    0 3 * * * cd /opt/enervision && set -a && . .env && set +a && \
        python3 infra/backup/backup.py >> /var/log/enervision-backup.log 2>&1
"""

import os
import subprocess
from datetime import UTC, datetime

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "ev_monitoring")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "ev_admin")
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

# Tables couvertes par le ticket EV-040. Chacune a son propre fichier et son propre
# dossier Azure ; toute autre table (alerts, measurements_silver, ...) est
# volontairement exclue.
TABLES_SAUVEGARDEES = ("users", "sites")

AZURE_STORAGE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT", "")
AZURE_STORAGE_KEY = os.environ.get("AZURE_STORAGE_KEY", "")
# Même container que bronze/silver/gold (enervision-backup en production).
AZURE_STORAGE_CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER", "")
RCLONE_CRYPT_PASSWORD_RAW = os.environ.get("RCLONE_CRYPT_PASSWORD_RAW", "")

DOSSIER_SAUVEGARDE = os.environ.get("BACKUP_DIR", "/tmp/enervision-backup")
RCLONE_CONFIG_PATH = os.environ.get("RCLONE_CONFIG_PATH", "/tmp/rclone-backup.conf")


def nom_fichier_sauvegarde(table, instant=None):
    """Nom du fichier compressé (pas encore chiffré : ça, c'est rclone qui s'en
    charge à l'envoi), horodaté au jour (UTC)."""
    instant = instant or datetime.now(UTC)
    return f"{table}_{instant:%Y-%m-%d}.sql.gz"


def commande_pg_dump(table):
    """pg_dump en clair, format texte (plain), écrit sur stdout — jamais sur un
    fichier : cette sortie alimente directement compresser()."""
    return [
        "pg_dump",
        "--host",
        POSTGRES_HOST,
        "--port",
        str(POSTGRES_PORT),
        "--username",
        POSTGRES_USER,
        "--dbname",
        POSTGRES_DB,
        "--table",
        table,
    ]


def executer_pg_dump(table):
    """Renvoie le dump SQL en clair (bytes), jamais écrit sur le disque. Le mot de
    passe part par PGPASSWORD (lu nativement par les outils libpq), jamais en
    argument de commande (visible sinon via `ps aux`)."""
    environnement = {**os.environ, "PGPASSWORD": POSTGRES_PASSWORD}
    resultat = subprocess.run(commande_pg_dump(table), env=environnement, check=True, capture_output=True)
    return resultat.stdout


def compresser(donnees):
    """gzip des octets fournis, sans fichier intermédiaire sur le disque."""
    resultat = subprocess.run(["gzip"], input=donnees, check=True, capture_output=True)
    return resultat.stdout


def generer_config_rclone():
    """rclone.conf de structure identique à infra/audit-sync/sync-audit-to-azure.sh :
    mêmes noms de remote ([azure], [azure-crypt]), même container
    (AZURE_STORAGE_CONTAINER), même passphrase rclone crypt
    (RCLONE_CRYPT_PASSWORD_RAW) — la technique d'envoi (et donc de chiffrement) est
    partagée à l'identique avec bronze/silver/gold, pas réinventée pour users/sites."""
    mot_de_passe_obscurci = subprocess.run(
        ["rclone", "obscure", RCLONE_CRYPT_PASSWORD_RAW],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    contenu = (
        "[azure]\n"
        "type = azureblob\n"
        f"account = {AZURE_STORAGE_ACCOUNT}\n"
        f"key = {AZURE_STORAGE_KEY}\n"
        "\n"
        "[azure-crypt]\n"
        "type = crypt\n"
        f"remote = azure:{AZURE_STORAGE_CONTAINER}\n"
        f"password = {mot_de_passe_obscurci}\n"
        "filename_encryption = standard\n"
        "directory_name_encryption = false\n"
    )
    with open(RCLONE_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(contenu)
    os.chmod(RCLONE_CONFIG_PATH, 0o600)


def commande_rclone(table, chemin_fichier):
    """`copy` (pas `sync`) : on dépose un fichier individuel déjà nommé/daté, pas un
    dossier entier à refléter. Le "dossier" <table>/ apparaît de lui-même au premier
    envoi (Azure Blob n'a pas de vrais dossiers, seulement des préfixes de nom).
    C'est ce passage par azure-crypt (et non un remote azure simple) qui chiffre le
    fichier : aucun chiffrement n'a eu lieu avant cet appel."""
    return ["rclone", "--config", RCLONE_CONFIG_PATH, "copy", chemin_fichier, f"azure-crypt:{table}/"]


def televerser_vers_azure(table, chemin_fichier):
    """Génère la config rclone puis téléverse chemin_fichier vers le dossier de la
    table, via le remote azure-crypt partagé (chiffrement fait ici, à l'envoi)."""
    generer_config_rclone()
    subprocess.run(commande_rclone(table, chemin_fichier), check=True, capture_output=True)


def sauvegarder_table(table):
    """dump -> gzip (en mémoire) -> un seul fichier compressé écrit sur le disque ->
    téléversé (chiffré par rclone à ce moment-là) -> supprimé. Peut lever
    (CalledProcessError, ...) : c'est cycle_sauvegarde qui absorbe l'erreur pour ne
    jamais empêcher la tentative sur l'autre table. Le fichier local n'est jamais
    laissé sur le disque au-delà de cet appel, succès ou échec."""
    os.makedirs(DOSSIER_SAUVEGARDE, exist_ok=True)
    nom_fichier = nom_fichier_sauvegarde(table)
    chemin_local = os.path.join(DOSSIER_SAUVEGARDE, nom_fichier)

    brut = executer_pg_dump(table)
    compresse = compresser(brut)

    try:
        with open(chemin_local, "wb") as f:
            f.write(compresse)
        televerser_vers_azure(table, chemin_local)
        print(f"Sauvegarde terminée : {table}/{nom_fichier}")
    finally:
        if os.path.exists(chemin_local):
            os.remove(chemin_local)


def cycle_sauvegarde():
    """Sauvegarde chaque table de TABLES_SAUVEGARDEES. Une erreur sur une table ne
    doit jamais empêcher la tentative sur la suivante."""
    for table in TABLES_SAUVEGARDEES:
        try:
            sauvegarder_table(table)
        except Exception as erreur:
            print(f"Sauvegarde de '{table}' échouée : {erreur}")


if __name__ == "__main__":
    cycle_sauvegarde()
