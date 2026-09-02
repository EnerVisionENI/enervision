"""
Tests pour EV-032, collecte des alertes vers PostgreSQL.
Aucun test ne dépend d'un vrai serveur Postgres, execute_values est simulé
et les appels sont vérifiés.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts


class FauxCurseur:
    def __init__(self, sites_connus):
        self.sites_connus = sites_connus

    def execute(self, sql):
        pass

    def fetchall(self):
        return [(site_id,) for site_id in self.sites_connus]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FausseConnexion:
    def __init__(self, sites_connus=()):
        self.sites_connus = sites_connus
        self.commits = 0
        self.fermee = False

    def cursor(self):
        return FauxCurseur(self.sites_connus)

    def commit(self):
        self.commits += 1

    def close(self):
        self.fermee = True


def test_enregistrer_alertes_upsert_les_alertes_dont_le_site_est_connu(monkeypatch):
    """Une alerte dont le site_id existe dans sites doit être upsertée."""
    appels = []
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: appels.append(lignes))

    conn = FausseConnexion(sites_connus={"SITE001"})
    alertes = [
        {"alert_id": "A1", "timestamp": "2026-09-01T09:00:00", "site_id": "SITE001", "severity": "high"},
    ]

    enregistrees, ignorees = alerts.enregistrer_alertes(conn, alertes)

    assert enregistrees == 1
    assert ignorees == 0
    assert len(appels) == 1
    assert appels[0][0][0] == "A1"
    assert conn.commits == 1


def test_enregistrer_alertes_ignore_site_inconnu(monkeypatch):
    """Une alerte référençant un site absent de la table sites doit être ignorée,
    plutôt que de faire échouer tout le lot sur la contrainte de clé étrangère."""
    appels = []
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: appels.append(lignes))

    conn = FausseConnexion(sites_connus={"SITE001"})
    alertes = [
        {"alert_id": "A1", "site_id": "SITE001"},
        {"alert_id": "A2", "site_id": "SITE999"},
    ]

    enregistrees, ignorees = alerts.enregistrer_alertes(conn, alertes)

    assert enregistrees == 1
    assert ignorees == 1
    assert appels[0][0][0] == "A1"


def test_enregistrer_alertes_accepte_site_id_absent(monkeypatch):
    """Une alerte sans site_id (alerte globale) n'est pas bloquée par la clé étrangère."""
    appels = []
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: appels.append(lignes))

    conn = FausseConnexion(sites_connus=set())
    alertes = [{"alert_id": "A1", "site_id": None}]

    enregistrees, ignorees = alerts.enregistrer_alertes(conn, alertes)

    assert enregistrees == 1
    assert ignorees == 0


def test_enregistrer_alertes_ignore_sans_alert_id(monkeypatch):
    """alert_id est la clé primaire de la table, une alerte sans identifiant est ignorée."""
    appels = []
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: appels.append(lignes))

    conn = FausseConnexion(sites_connus={"SITE001"})
    alertes = [{"site_id": "SITE001", "severity": "high"}]

    enregistrees, ignorees = alerts.enregistrer_alertes(conn, alertes)

    assert enregistrees == 0
    assert ignorees == 1
    assert appels == []


def test_enregistrer_alertes_pas_d_appel_sql_si_lot_vide(monkeypatch):
    """Aucune écriture ne doit partir en base si toutes les alertes sont ignorées."""
    appels = []
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: appels.append(lignes))

    conn = FausseConnexion(sites_connus=set())
    alertes = [{"alert_id": "A1", "site_id": "SITE999"}]

    enregistrees, ignorees = alerts.enregistrer_alertes(conn, alertes)

    assert enregistrees == 0
    assert ignorees == 1
    assert appels == []
    assert conn.commits == 0


def test_cycle_alertes_ferme_la_connexion(monkeypatch):
    """La connexion Postgres doit être fermée après le cycle, même en cas de succès."""
    conn = FausseConnexion(sites_connus={"SITE001"})

    monkeypatch.setattr(alerts, "se_connecter_postgres", lambda: conn)
    monkeypatch.setattr(alerts, "recuperer_alertes", lambda: [{"alert_id": "A1", "site_id": "SITE001"}])
    monkeypatch.setattr(alerts, "execute_values", lambda cur, sql, lignes: None)
    monkeypatch.setattr(alerts, "marquer_vivant", lambda: None)

    alerts.cycle_alertes()

    assert conn.fermee is True
