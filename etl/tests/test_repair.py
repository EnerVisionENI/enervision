"""
Tests pour repair.py : reprise des fenêtres d'historique rapatriées à vide.

Aucun accès réseau ni MinIO réel. Le faux S3 en mémoire est celui de test_quality — le
scénario de reprise a besoin d'un magasin complet (put / get / list / delete + lecture
Parquet), le redéfinir ici n'apporterait qu'une copie à maintenir en double.

Le point sensible couvert ici est l'ordre des opérations : on ne purge une partition
qu'APRÈS avoir un nouveau tirage non vide en main. Purger d'abord ferait perdre la
partition si les capteurs retombaient en panne entre les deux appels.
"""

import collect
import pandas as pd
import pytest
import quality
import repair
import requests
from test_quality import FakeS3


@pytest.fixture()
def s3(monkeypatch):
    faux = FakeS3()
    monkeypatch.setattr(repair.storage, "get_s3", lambda: faux)
    monkeypatch.setattr(collect.storage, "get_s3", lambda: faux)
    return faux


def etat_capteurs(**par_site):
    """Réponse /api/v1/sensors/status : etat_capteurs(SITE001=True, SITE002=False)."""
    return {
        site: {
            "sensors": {
                nom: {"status": "ok" if sain else "failing"}
                for nom in ("network", "consumption", "electrical", "temperature", "humidity")
            },
            "overall": "ok" if sain else "critical",
        }
        for site, sain in par_site.items()
    }


class FausseReponse:
    def __init__(self, donnees):
        self._donnees = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self._donnees


def lecture(instant_iso, **extra):
    base = {
        "timestamp": instant_iso,
        "site_id": "SITE001",
        "site_type": "office",
        "consumption_kw": 42.0,
        "consumption_kwh": 42.0,
        "data_quality": "good",
    }
    base.update(extra)
    return base


def lecture_vide(instant_iso):
    """Ce que renvoie l'API quand le capteur est en panne : toutes les métriques nulles."""
    return lecture(
        instant_iso,
        consumption_kw=None, consumption_kwh=None,
        data_quality="critical", null_reasons=["network_loss"],
    )


def semer_gold_daily(s3, record_date, site_id, avg_consumption_kw):
    quality.s3_put_parquet(
        s3, "gold",
        f"daily/record_date={record_date}/site_id={site_id}/daily.parquet",
        pd.DataFrame([{
            "record_date": record_date,
            "site_id": site_id,
            "records_count": 24,
            "avg_consumption_kw": avg_consumption_kw,
        }]),
    )


# ------------------------------------------------------------- état des capteurs

def test_capteurs_sains_ne_retient_que_network_et_consumption(monkeypatch):
    """Exiger que TOUS les capteurs soient au vert serait trop strict : température et
    humidité tombent indépendamment sans empêcher de récupérer une consommation."""
    reponse = etat_capteurs(SITE001=True, SITE002=False)
    reponse["SITE003"] = {
        "sensors": {
            "network": {"status": "ok"},
            "consumption": {"status": "ok"},
            "temperature": {"status": "failing"},
        },
        "overall": "degraded",
    }
    monkeypatch.setattr(repair.requests, "get", lambda *a, **k: FausseReponse(reponse))

    assert repair.capteurs_sains() == {"SITE001", "SITE003"}


def test_capteurs_sains_vide_si_api_injoignable(monkeypatch):
    """Sans état capteur fiable on ne rejoue rien : réécrire du vide par-dessus du vide
    ferait perdre les objets d'origine pour rien."""
    def _boom(*a, **k):
        raise requests.RequestException("api hs")

    monkeypatch.setattr(repair.requests, "get", _boom)

    assert repair.capteurs_sains() == set()


def test_etat_capteurs_met_en_cache_puis_rafraichit(monkeypatch):
    """Un appel par partition serait du gâchis, un seul appel pour tout le parcours ferait
    tirer pendant les pannes : l'état est relu au plus toutes les TTL secondes."""
    appels = []

    def _faux(*a, **k):
        appels.append(1)
        return {"SITE001"} if len(appels) == 1 else {"SITE002"}

    monkeypatch.setattr(repair, "capteurs_sains", _faux)
    etat = repair.EtatCapteurs(ttl_secondes=60)

    assert etat.est_sain("SITE001")
    assert etat.est_sain("SITE001")          # servi par le cache
    assert len(appels) == 1

    etat._lu_a -= 61                          # TTL dépassée
    assert etat.est_sain("SITE002")
    assert len(appels) == 2


# ------------------------------------------------------ repérage des partitions

def test_partitions_a_rejouer_ne_retient_que_les_journees_sans_consommation(s3):
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    semer_gold_daily(s3, "2025-08-03", "SITE001", 57.2)
    semer_gold_daily(s3, "2025-08-02", "SITE002", None)

    assert repair.partitions_a_rejouer(s3, "gold") == [
        ("2025-08-02", "SITE001"),
        ("2025-08-02", "SITE002"),
    ]


def test_partitions_a_rejouer_groupe_par_site_puis_date(s3):
    """Les fenêtres de rétablissement durent quelques dizaines de secondes : on veut
    enchaîner les journées d'un même site, pas alterner les sites."""
    for jour in ("2025-08-01", "2025-08-02"):
        semer_gold_daily(s3, jour, "SITE001", None)
        semer_gold_daily(s3, jour, "SITE002", None)

    assert repair.partitions_a_rejouer(s3, "gold") == [
        ("2025-08-01", "SITE001"),
        ("2025-08-02", "SITE001"),
        ("2025-08-01", "SITE002"),
        ("2025-08-02", "SITE002"),
    ]


def test_echantillon_equilibre_repartit_sur_tous_les_sites():
    """Les partitions sont triées par site : un [:N] brut n'en garderait qu'un, et la
    campagne serait bornée par le rapport cyclique de ce seul capteur."""
    partitions = (
        [(f"2025-08-0{jour}", "SITE001") for jour in range(1, 5)]
        + [(f"2025-08-0{jour}", "SITE002") for jour in range(1, 5)]
    )

    retenues = repair.echantillon_equilibre(partitions, 4)

    assert sorted({site for _, site in retenues}) == ["SITE001", "SITE002"]
    assert len(retenues) == 4


def test_echantillon_equilibre_sans_limite_rend_tout():
    partitions = [("2025-08-01", "SITE001"), ("2025-08-02", "SITE001")]
    assert repair.echantillon_equilibre(partitions, 0) == partitions
    assert repair.echantillon_equilibre(partitions, 99) == partitions


def test_partitions_a_rejouer_filtre_sites_et_periode(s3):
    for jour in ("2025-08-01", "2025-08-02", "2025-08-03"):
        semer_gold_daily(s3, jour, "SITE001", None)
        semer_gold_daily(s3, jour, "SITE002", None)

    partitions = repair.partitions_a_rejouer(
        s3, "gold", sites={"SITE001"}, debut="2025-08-02", fin="2025-08-03",
    )

    assert partitions == [("2025-08-02", "SITE001")]


# ------------------------------------------------------- collecte par fusion

def test_collecter_journee_fusionne_les_tirages_successifs(monkeypatch):
    """Le cœur de la reprise. Un tirage ne rend qu'une fraction des heures (41,7 % mesuré),
    mais chaque appel re-tire indépendamment : l'heure absente d'un tirage revient au
    suivant. Un essai unique en tout ou rien jetterait ce qu'il obtient."""
    tirages = [
        [lecture("2025-08-02T00:00:00"), lecture_vide("2025-08-02T01:00:00")],
        [lecture_vide("2025-08-02T00:00:00"), lecture("2025-08-02T01:00:00")],
    ]
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: tirages.pop(0) if tirages else [])

    lectures = repair.collecter_journee("SITE001", "2025-08-02", pas_minutes=60, tentatives=3)

    assert sorted(x["timestamp"] for x in lectures) == [
        "2025-08-02T00:00:00", "2025-08-02T01:00:00",
    ]


def test_collecter_journee_s_arrete_des_que_la_journee_est_complete(monkeypatch):
    """Inutile de consommer le budget de tentatives une fois les 24 heures couvertes."""
    appels = []

    def _tirage(*a, **k):
        appels.append(1)
        return [lecture(f"2025-08-02T{h:02d}:00:00") for h in range(24)]

    monkeypatch.setattr(repair, "recuperer_journee", _tirage)

    lectures = repair.collecter_journee("SITE001", "2025-08-02", pas_minutes=60, tentatives=8)

    assert len(lectures) == 24
    assert len(appels) == 1


def test_collecter_journee_abandonne_si_le_capteur_tombe(monkeypatch):
    """Capteur rouge = 0 % de rendement quelle que soit la fenêtre : on n'insiste pas,
    la partition repassera à la passe suivante."""
    appels = []

    def _tirage(*a, **k):
        appels.append(1)
        return [lecture("2025-08-02T00:00:00")]

    monkeypatch.setattr(repair, "recuperer_journee", _tirage)
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: set())
    etat = repair.EtatCapteurs(ttl_secondes=60)

    lectures = repair.collecter_journee(
        "SITE001", "2025-08-02", pas_minutes=60, tentatives=8, etat=etat,
    )

    assert (lectures, appels) == ([], [])


# -------------------------------------------------------------- rejeu d'une partition

def test_rejouer_partition_ne_purge_rien_si_le_tirage_est_encore_vide(s3, monkeypatch):
    """Le cas dangereux : les capteurs sont retombés entre le listing et l'appel. Mieux
    vaut une partition vide qu'une partition supprimée."""
    s3.seed_bronze("SITE001/2025-08-02/000000.json", lecture_vide("2025-08-02T00:00:00"))
    monkeypatch.setattr(
        repair, "recuperer_journee",
        lambda *a, **k: [lecture_vide("2025-08-02T00:00:00")],
    )

    deposees, purgees = repair.rejouer_partition(
        s3, "SITE001", "2025-08-02",
        pas_minutes=60, bronze_bucket="bronze", silver_bucket="silver",
    )

    assert (deposees, purgees) == (0, [])
    assert s3.keys("bronze") == ["SITE001/2025-08-02/000000.json"]


def test_rejouer_partition_purge_l_ancien_vide_puis_depose(s3, monkeypatch):
    """Sans la purge, anciennes lignes vides et nouvelles mesures coexisteraient sous des
    clés différentes et gonfleraient records_count."""
    s3.seed_bronze("SITE001/2025-08-02/002147.json", lecture_vide("2025-08-02T00:21:47"))
    s3.seed_bronze("SITE001/2025-08-02/012147.json", lecture_vide("2025-08-02T01:21:47"))
    quality.s3_put_parquet(
        s3, "silver", "record_date=2025-08-02/site_id=SITE001/batch_ancien.parquet",
        pd.DataFrame([{"source_key": "SITE001/2025-08-02/002147.json"}]),
    )
    monkeypatch.setattr(
        repair, "recuperer_journee",
        lambda *a, **k: [lecture("2025-08-02T00:00:00"), lecture("2025-08-02T01:00:00")],
    )

    deposees, purgees = repair.rejouer_partition(
        s3, "SITE001", "2025-08-02",
        pas_minutes=60, bronze_bucket="bronze", silver_bucket="silver",
    )

    assert deposees == 2
    assert sorted(purgees) == ["SITE001/2025-08-02/002147.json", "SITE001/2025-08-02/012147.json"]
    assert s3.keys("bronze") == ["SITE001/2025-08-02/000000.json", "SITE001/2025-08-02/010000.json"]
    assert s3.keys("silver") == []


def test_rejouer_partition_ignore_les_lectures_sans_metrique(s3, monkeypatch):
    monkeypatch.setattr(
        repair, "recuperer_journee",
        lambda *a, **k: [lecture("2025-08-02T00:00:00"), lecture_vide("2025-08-02T01:00:00")],
    )

    deposees, _ = repair.rejouer_partition(
        s3, "SITE001", "2025-08-02",
        pas_minutes=60, bronze_bucket="bronze", silver_bucket="silver",
    )

    assert deposees == 1
    assert s3.keys("bronze") == ["SITE001/2025-08-02/000000.json"]


# -------------------------------------------------------------------- main (e2e)

def test_main_rejoue_purge_l_etat_et_empile_le_gold(s3, monkeypatch, capsys):
    """Les clés purgées doivent quitter etl_state.json, sinon quality.py les tient pour
    traitées et ne reprend jamais leur remplacement."""
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    s3.seed_bronze("SITE001/2025-08-02/002147.json", lecture_vide("2025-08-02T00:21:47"))
    quality.save_state(s3, "manifests", "etl_state.json", {
        "SITE001/2025-08-02/002147.json", "SITE002/2025-08-05/000000.json",
    })
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: {"SITE001"})
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: [lecture("2025-08-02T00:00:00")])

    assert repair.main([]) == 0

    assert "partitions=1" in capsys.readouterr().out
    assert quality.load_state(s3, "manifests", "etl_state.json") == {"SITE002/2025-08-05/000000.json"}
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2025-08-02", "SITE001")}


def test_main_saute_une_partition_dont_le_capteur_est_retombe(s3, monkeypatch, capsys):
    """Cas dominant d'une campagne longue : le site était vert au démarrage, il ne l'est
    plus quand son tour arrive. On le saute au lieu de tirer du vide."""
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    semer_gold_daily(s3, "2025-08-03", "SITE001", None)
    etats = [{"SITE001"}, set()]          # vert au démarrage, rouge ensuite
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: etats.pop(0) if etats else set())
    monkeypatch.setattr(repair, "TTL_ETAT_CAPTEURS", 0)      # relit à chaque partition
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: [lecture("2025-08-02T00:00:00")])

    assert repair.main([]) == 0

    sortie = capsys.readouterr().out
    assert "passe 1 | reprises=0 | mesures=0 | restantes=2" in sortie
    assert s3.keys("bronze") == []                        # rien tiré, rien écrit


def test_main_avec_duree_repasse_sur_ce_qui_restait_vide(s3, monkeypatch, capsys):
    """Chaque site n'est vert que ~59 % du temps : une passe unique abandonne les
    partitions dont le capteur était rouge à leur tour. --duree les rattrape."""
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    # rouge à la 1re passe, vert ensuite
    etats = [{"SITE001"}, set(), {"SITE001"}]
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: etats.pop(0) if etats else {"SITE001"})
    monkeypatch.setattr(repair, "TTL_ETAT_CAPTEURS", 0)
    monkeypatch.setattr(repair, "INTERVALLE_ATTENTE_SECONDES", 0)
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: [lecture("2025-08-02T00:00:00")])

    assert repair.main(["--duree", "60", "--tentatives", "1"]) == 0

    sortie = capsys.readouterr().out
    assert "passe 1" in sortie and "passe 2" in sortie
    assert "partitions=1" in sortie                       # rattrapée à la 2e passe


def test_main_une_seule_passe_par_defaut(s3, monkeypatch, capsys):
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: {"SITE001"})
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: [lecture("2025-08-02T00:00:00")])

    assert repair.main([]) == 0

    sortie = capsys.readouterr().out
    assert "passe 1" in sortie and "passe 2" not in sortie


def test_main_dry_run_n_ecrit_ni_ne_supprime_rien(s3, monkeypatch, capsys):
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    s3.seed_bronze("SITE001/2025-08-02/002147.json", lecture_vide("2025-08-02T00:21:47"))
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: {"SITE001"})
    monkeypatch.setattr(repair, "recuperer_journee", lambda *a, **k: [lecture("2025-08-02T00:00:00")])

    assert repair.main(["--dry-run"]) == 0

    assert "1 mesure(s) récupérable(s)" in capsys.readouterr().out
    assert s3.keys("bronze") == ["SITE001/2025-08-02/002147.json"]
    assert ("manifests", "gold_pending.json") not in s3.store


def test_main_sans_capteur_au_vert_ne_touche_a_rien(s3, monkeypatch, capsys):
    semer_gold_daily(s3, "2025-08-02", "SITE001", None)
    s3.seed_bronze("SITE001/2025-08-02/002147.json", lecture_vide("2025-08-02T00:21:47"))
    monkeypatch.setattr(repair, "capteurs_sains", lambda *a, **k: set())

    assert repair.main([]) == 0

    assert "Aucun site" in capsys.readouterr().out
    assert s3.keys("bronze") == ["SITE001/2025-08-02/002147.json"]
