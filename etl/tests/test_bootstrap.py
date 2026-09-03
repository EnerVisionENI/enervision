"""
Tests pour bootstrap.py : enchaînement history -> drainage -> live, idempotence
via la phase en base, bascule en 'error'. Aucun vrai Postgres, aucun réseau :
psycopg2, history.main et quality.run sont remplacés par des faux.
"""

import pytest

import bootstrap
import quality


class FauxCurseur:
    def __init__(self, connexion):
        self.connexion = connexion

    def execute(self, sql, params=None):
        self.connexion.executions.append((sql, params))
        if sql.strip().lower().startswith("select phase"):
            self.connexion._fetchone = (
                (self.connexion.phase,) if self.connexion.phase is not None else None
            )

    def fetchone(self):
        return self.connexion._fetchone

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FausseConnexion:
    def __init__(self, phase="pending"):
        self.phase = phase
        self.executions = []
        self.commits = 0
        self.fermee = False
        self._fetchone = None

    def cursor(self):
        return FauxCurseur(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.fermee = True


def phases_ecrites(conn):
    """Suite des `phase` posées par maj_statut (toujours 1er paramètre SQL)."""
    out = []
    for sql, params in conn.executions:
        bas = sql.lower()
        if "update etl_status" in bas and "phase = %s" in bas:
            out.append(params[0])
    return out


@pytest.fixture()
def conn(monkeypatch):
    c = FausseConnexion()
    monkeypatch.setattr(bootstrap, "se_connecter_postgres", lambda: c)
    return c


@pytest.fixture()
def faux_history(monkeypatch):
    appels = []
    monkeypatch.setattr(bootstrap.history, "main", lambda argv=None: appels.append(argv) or 0)
    return appels


@pytest.fixture()
def faux_quality(monkeypatch):
    """quality.run renvoie successivement les RunResult de `sequence` (défaut : un
    seul passage, plus rien à traiter)."""
    box = {
        "sequence": [quality.RunResult(ok=True, objets_bronze=0, nouveaux=0, processed_total=0)],
        "gold": quality.GoldResult(ok=True),
    }
    appels = []
    appels_gold = []

    def _run(argv=None):
        appels.append(argv)
        return box["sequence"][min(len(appels) - 1, len(box["sequence"]) - 1)]

    def _run_gold(argv=None):
        appels_gold.append(argv)
        return box["gold"]

    monkeypatch.setattr(bootstrap.quality, "run", _run)
    monkeypatch.setattr(bootstrap.quality, "run_gold", _run_gold)
    box["appels"] = appels
    box["appels_gold"] = appels_gold
    return box


# --------------------------------------------------------------------------- cas

def test_ne_fait_rien_si_phase_live(conn, faux_history, faux_quality):
    conn.phase = "live"

    assert bootstrap.main() == 0
    assert faux_history == []            # history pas rejoué
    assert faux_quality["appels"] == []  # drainage pas lancé
    assert phases_ecrites(conn) == []
    assert conn.fermee is True


def test_enchaine_history_puis_drainage_puis_live(conn, faux_history, faux_quality):
    assert bootstrap.main() == 0

    assert len(faux_history) == 1                       # historique rejoué une fois
    assert faux_quality["appels"]                       # drainage lancé
    assert phases_ecrites(conn) == ["history", "draining", "gold", "live"]
    assert conn.fermee is True


def test_historique_avant_drainage(conn, monkeypatch):
    ordre = []
    monkeypatch.setattr(bootstrap.history, "main", lambda argv=None: ordre.append("history") or 0)
    monkeypatch.setattr(
        bootstrap.quality, "run",
        lambda argv=None: ordre.append("quality")
        or quality.RunResult(ok=True, objets_bronze=0, nouveaux=0),
    )

    bootstrap.main()

    assert ordre[0] == "history"
    assert "quality" in ordre[1:]


def test_drainage_boucle_jusqu_a_zero_nouveau(conn, faux_history, faux_quality):
    faux_quality["sequence"] = [
        quality.RunResult(ok=True, objets_bronze=15000, nouveaux=5000, processed_total=5000),
        quality.RunResult(ok=True, objets_bronze=15000, nouveaux=5000, processed_total=10000),
        quality.RunResult(ok=True, objets_bronze=15000, nouveaux=0, processed_total=15000),
    ]

    assert bootstrap.main() == 0
    assert len(faux_quality["appels"]) == 3
    assert phases_ecrites(conn)[-1] == "live"


def test_avancement_ecrit_en_base_pendant_le_drainage(conn, faux_history, faux_quality):
    faux_quality["sequence"] = [
        quality.RunResult(ok=True, objets_bronze=15000, nouveaux=5000, processed_total=5000),
        quality.RunResult(ok=True, objets_bronze=15000, nouveaux=0, processed_total=15000),
    ]

    bootstrap.main()

    maj_avancement = [
        params for sql, params in conn.executions
        if "bronze_total = %s" in sql.lower() and "bronze_done = %s" in sql.lower()
    ]
    assert maj_avancement                       # au moins une mise à jour d'avancement
    assert maj_avancement[0] == [15000, 5000]   # (bronze_total, bronze_done)


def test_reprise_en_phase_draining_saute_l_historique(faux_history, faux_quality, monkeypatch):
    c = FausseConnexion(phase="draining")
    monkeypatch.setattr(bootstrap, "se_connecter_postgres", lambda: c)

    assert bootstrap.main() == 0
    assert faux_history == []                              # historique non rejoué
    assert phases_ecrites(c) == ["draining", "gold", "live"]


def test_erreur_historique_passe_en_phase_error(conn, faux_quality, monkeypatch):
    monkeypatch.setattr(bootstrap.history, "main", lambda argv=None: 1)  # history.py échoue

    assert bootstrap.main() == 1
    assert phases_ecrites(conn) == ["history", "error"]
    assert faux_quality["appels"] == []                    # drainage jamais atteint
    assert conn.fermee is True


def test_erreur_quality_passe_en_phase_error(conn, faux_history, monkeypatch):
    monkeypatch.setattr(
        bootstrap.quality, "run",
        lambda argv=None: quality.RunResult(ok=False, message="MinIO indisponible"),
    )

    assert bootstrap.main() == 1
    assert phases_ecrites(conn) == ["history", "draining", "error"]
    assert conn.fermee is True


def test_consolidation_gold_en_un_seul_passage(conn, faux_history, faux_quality):
    """Le drainage empile les partitions sans agréger ; le gold est recalculé une
    seule fois à la fin, pas à chaque tranche."""
    faux_quality["sequence"] = [
        quality.RunResult(ok=True, objets_bronze=40, nouveaux=20, processed_total=20),
        quality.RunResult(ok=True, objets_bronze=40, nouveaux=20, processed_total=40),
        quality.RunResult(ok=True, objets_bronze=40, nouveaux=0, processed_total=40),
    ]

    assert bootstrap.main() == 0

    assert len(faux_quality["appels"]) == 3        # trois tranches de drainage
    assert faux_quality["appels_gold"] == [[]]     # un seul recalcul gold
    assert phases_ecrites(conn) == ["history", "draining", "gold", "live"]


def test_echec_gold_partiel_passe_quand_meme_en_live(conn, faux_history, faux_quality):
    """Une partition gold en échec reste en file et sera reprise par le recalcul
    horaire : bloquer le bootstrap en phase=error empêcherait etl-collect de
    démarrer alors que le silver, lui, est complet."""
    faux_quality["gold"] = quality.GoldResult(ok=True, partitions=3, recalculees=2, echecs=1)

    assert bootstrap.main() == 0

    assert phases_ecrites(conn)[-1] == "live"
    messages = [
        p
        for sql, params in conn.executions
        for p in (params or ())
        if isinstance(p, str)
    ]
    assert any("gold en attente" in m for m in messages)
