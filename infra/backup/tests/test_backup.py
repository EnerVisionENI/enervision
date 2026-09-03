"""
Tests pour EV-040 : sauvegarde users/sites vers Azure.

Aucun test ne touche un vrai Postgres ni un vrai compte Azure : subprocess.run est
simulé pour pg_dump et rclone. En revanche, la compression est testée avec un VRAI
appel à gzip (aucun mock) : ça prouve un cycle compression -> décompression réel, pas
une simple vérification d'arguments de commande.

Le chiffrement lui-même n'est PAS testé ici : il est délégué entièrement à rclone
(remote azure-crypt), déjà utilisé et éprouvé par infra/audit-sync/sync-audit-to-azure.sh
pour bronze/silver/gold. Ce que ce script ajoute, c'est la génération de la même
configuration rclone (test ci-dessous) et la commande d'envoi vers le bon dossier.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup


def test_commande_pg_dump_cible_exactement_la_table_demandee():
    commande = backup.commande_pg_dump("users")

    assert commande[0] == "pg_dump"
    assert commande[-2:] == ["--table", "users"]


def test_commande_pg_dump_ne_contient_jamais_le_mot_de_passe():
    """Le mot de passe part par PGPASSWORD (variable d'environnement), jamais en
    argument : visible sinon via `ps aux`."""
    commande = backup.commande_pg_dump("sites")

    assert backup.POSTGRES_PASSWORD not in commande


def test_executer_pg_dump_passe_le_mot_de_passe_par_variable_environnement(monkeypatch):
    appels = []

    def faux_run(commande, env=None, **kwargs):
        appels.append((commande, env))

        class FauxResultat:
            stdout = b"dump factice"

        return FauxResultat()

    monkeypatch.setattr(backup.subprocess, "run", faux_run)

    resultat = backup.executer_pg_dump("users")

    assert resultat == b"dump factice"
    commande, env = appels[0]
    assert commande == backup.commande_pg_dump("users")
    assert env["PGPASSWORD"] == backup.POSTGRES_PASSWORD


def test_compression_reelle_reduit_la_taille_et_se_decompresse_a_l_identique():
    """Vrai gzip, pas de mock : du texte répétitif se compresse réellement, et
    `gzip -d` doit restituer exactement l'original."""
    original = b"SELECT * FROM users;\n" * 200

    compresse = backup.compresser(original)

    assert compresse != original
    assert len(compresse) < len(original)

    decompresse = subprocess.run(["gzip", "-d"], input=compresse, check=True, capture_output=True).stdout
    assert decompresse == original


def test_nom_fichier_sauvegarde_inclut_la_table_et_la_date():
    instant = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)

    assert backup.nom_fichier_sauvegarde("users", instant) == "users_2026-09-03.sql.gz"
    assert backup.nom_fichier_sauvegarde("sites", instant) == "sites_2026-09-03.sql.gz"


def test_generer_config_rclone_reutilise_la_meme_structure_que_audit_sync(tmp_path, monkeypatch):
    """Mêmes noms de remote ([azure], [azure-crypt]) et même mécanisme d'obscurcissement
    que infra/audit-sync/sync-audit-to-azure.sh — pas une config différente. C'est ce
    remote azure-crypt, seul, qui chiffre le fichier au moment de l'envoi."""
    chemin_config = tmp_path / "rclone-backup.conf"
    monkeypatch.setattr(backup, "RCLONE_CONFIG_PATH", str(chemin_config))
    monkeypatch.setattr(backup, "AZURE_STORAGE_ACCOUNT", "moncompte")
    monkeypatch.setattr(backup, "AZURE_STORAGE_KEY", "maclef")
    monkeypatch.setattr(backup, "AZURE_STORAGE_CONTAINER", "enervision-backup")
    monkeypatch.setattr(backup, "RCLONE_CRYPT_PASSWORD_RAW", "motdepasse-en-clair")

    def faux_run(commande, **kwargs):
        assert commande == ["rclone", "obscure", "motdepasse-en-clair"]

        class FauxResultat:
            stdout = "MOT-DE-PASSE-OBSCURCI\n"

        return FauxResultat()

    monkeypatch.setattr(backup.subprocess, "run", faux_run)

    backup.generer_config_rclone()

    contenu = chemin_config.read_text(encoding="utf-8")
    assert "[azure]" in contenu
    assert "[azure-crypt]" in contenu
    assert "remote = azure:enervision-backup" in contenu
    assert "password = MOT-DE-PASSE-OBSCURCI" in contenu
    # Jamais le mot de passe en clair dans le fichier écrit sur disque.
    assert "motdepasse-en-clair" not in contenu


def test_commande_rclone_pointe_vers_le_dossier_de_la_table():
    commande_users = backup.commande_rclone("users", "/tmp/users.sql.gz")
    commande_sites = backup.commande_rclone("sites", "/tmp/sites.sql.gz")

    assert commande_users[-1] == "azure-crypt:users/"
    assert commande_sites[-1] == "azure-crypt:sites/"
    assert "/tmp/users.sql.gz" in commande_users


def test_sauvegarder_table_nettoie_le_fichier_local_apres_televersement(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DOSSIER_SAUVEGARDE", str(tmp_path))
    monkeypatch.setattr(backup, "executer_pg_dump", lambda table: b"dump brut")
    monkeypatch.setattr(backup, "compresser", lambda donnees: b"compresse")

    appels = []
    monkeypatch.setattr(
        backup, "televerser_vers_azure", lambda table, chemin: appels.append((table, chemin))
    )

    backup.sauvegarder_table("users")

    assert len(appels) == 1
    assert appels[0][0] == "users"
    chemin_utilise = appels[0][1]
    assert not os.path.exists(chemin_utilise), "le fichier local ne doit pas survivre à l'appel"


def test_sauvegarder_table_nettoie_meme_si_le_televersement_echoue(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DOSSIER_SAUVEGARDE", str(tmp_path))
    monkeypatch.setattr(backup, "executer_pg_dump", lambda table: b"dump")
    monkeypatch.setattr(backup, "compresser", lambda donnees: donnees)

    def echoue(table, chemin):
        raise backup.subprocess.CalledProcessError(1, "rclone")

    monkeypatch.setattr(backup, "televerser_vers_azure", echoue)

    with pytest.raises(backup.subprocess.CalledProcessError):
        backup.sauvegarder_table("sites")

    assert list(tmp_path.iterdir()) == []


def test_cycle_sauvegarde_traite_chaque_table_independamment(monkeypatch):
    """Un échec sur users ne doit pas empêcher la tentative sur sites."""
    appels = []

    def faux_sauvegarder_table(table):
        appels.append(table)
        if table == "users":
            raise RuntimeError("panne simulée sur users")

    monkeypatch.setattr(backup, "sauvegarder_table", faux_sauvegarder_table)

    backup.cycle_sauvegarde()  # ne doit lever aucune exception

    assert appels == ["users", "sites"]
