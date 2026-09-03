"""
Tests pour quality.py : téléchargement parallèle des objets bronze
(`fetch_records`), bout-en-bout de `main()` sur un petit lot, découplage
du recalcul gold (empilé par le passage silver, traité par `--gold-only`),
et complétude des couches silver / gold / quarantine.
Aucun accès réseau : S3 est un faux en mémoire.
"""

import io
import json
import threading
import time

import pandas as pd
import psycopg2
import pytest
import quality
from botocore.exceptions import ClientError


@pytest.fixture(autouse=True)
def _no_postgres(monkeypatch):
    """Les tests ne touchent aucun vrai Postgres : run() bascule alors sur
    pg_conn=None et n'écrit que dans MinIO (le faux S3)."""
    def _boom():
        raise psycopg2.OperationalError("pas de Postgres en test")

    monkeypatch.setattr(quality.postgres_writer, "make_pg_connection", _boom)


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _Paginator:
    def __init__(self, s3):
        self._s3 = s3

    def paginate(self, Bucket, Prefix=""):
        with self._s3.lock:
            contents = [
                {"Key": key}
                for (bucket, key) in self._s3.store
                if bucket == Bucket and key.startswith(Prefix)
            ]
        yield {"Contents": contents}


class FakeS3:
    """S3 en mémoire : put / get / list / head, avec injection d'erreurs et de
    latence par clé pour tester le parallélisme."""

    def __init__(self, *, errors=(), delays=None):
        self.store = {}            # (bucket, key) -> bytes
        self.buckets = set()
        self.errors = set(errors)
        self.delays = dict(delays or {})
        self.lock = threading.Lock()
        self.get_calls = []

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise ClientError({"Error": {"Code": "404", "Message": "no"}}, "HeadBucket")

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_object(self, Bucket, Key, Body, ContentType=None, Metadata=None):
        data = Body if isinstance(Body, (bytes, bytearray)) else bytes(Body)
        with self.lock:
            self.buckets.add(Bucket)
            self.store[(Bucket, Key)] = bytes(data)

    def get_object(self, Bucket, Key):
        if self.delays.get(Key):
            time.sleep(self.delays[Key])
        with self.lock:
            self.get_calls.append(Key)
        if Key in self.errors:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "GetObject")
        with self.lock:
            data = self.store.get((Bucket, Key))
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        return {"Body": _Body(data)}

    def delete_object(self, Bucket, Key):
        with self.lock:
            self.store.pop((Bucket, Key), None)

    def get_paginator(self, name):
        return _Paginator(self)

    # --- utilitaires de test ---
    def seed_bronze(self, key, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.put_object("bronze", key, body)

    def keys(self, bucket):
        with self.lock:
            return sorted(k for (b, k) in self.store if b == bucket)

    def parquet(self, bucket, key):
        with self.lock:
            return pd.read_parquet(io.BytesIO(self.store[(bucket, key)]))


def mesure(site_id="SITE001", timestamp="2025-01-01T00:00:00", **extra):
    base = {
        "timestamp": timestamp,
        "site_id": site_id,
        "site_type": "office",
        "consumption_kw": 42.0,
        "consumption_kwh": 42.0,
        "data_quality": "good",
    }
    base.update(extra)
    return base


# ----------------------------------------------------------------- fetch_records

def test_fetch_records_recupere_et_decode_tout():
    s3 = FakeS3()
    keys = [f"SITE001/2025-01-01/00000{i}.json" for i in range(5)]
    for i, k in enumerate(keys):
        s3.seed_bronze(k, mesure(timestamp=f"2025-01-01T00:0{i}:00"))

    records, errors = quality.fetch_records(s3, "bronze", keys, workers=8)

    assert errors == 0
    assert [k for k, _ in records] == keys              # sortie ordonnée comme l'entrée
    assert all(rec["site_id"] == "SITE001" for _, rec in records)


def test_fetch_records_ordre_stable_malgre_arrivee_desordonnee():
    # la 1re clé est la plus lente : elle arrive en dernier mais doit rester 1re
    s3 = FakeS3(delays={"SITE001/2025-01-01/000000.json": 0.15})
    keys = [f"SITE001/2025-01-01/00000{i}.json" for i in range(4)]
    for k in keys:
        s3.seed_bronze(k, mesure())

    records, _ = quality.fetch_records(s3, "bronze", keys, workers=4)

    assert [k for k, _ in records] == keys


def test_fetch_records_erreur_s3_comptee_et_cle_omise():
    mauvaise = "SITE001/2025-01-01/000002.json"
    s3 = FakeS3(errors={mauvaise})
    keys = [f"SITE001/2025-01-01/00000{i}.json" for i in range(5)]
    for k in keys:
        s3.seed_bronze(k, mesure())

    records, errors = quality.fetch_records(s3, "bronze", keys, workers=8)

    assert errors == 1
    assert mauvaise not in [k for k, _ in records]
    assert len(records) == 4


def test_fetch_records_json_invalide_reste_en_records():
    s3 = FakeS3()
    s3.seed_bronze("SITE001/2025-01-01/000000.json", b"pas du json")
    s3.seed_bronze("SITE001/2025-01-01/000001.json", mesure())

    records, errors = quality.fetch_records(
        s3, "bronze",
        ["SITE001/2025-01-01/000000.json", "SITE001/2025-01-01/000001.json"],
        workers=4,
    )

    assert errors == 0            # décodage raté != erreur de lecture
    assert len(records) == 2
    assert records[0][1]["_parse_error"]   # partira en quarantaine plus loin


def test_fetch_records_liste_vide_ne_touche_pas_s3():
    s3 = FakeS3()
    assert quality.fetch_records(s3, "bronze", [], workers=8) == ([], 0)
    assert s3.get_calls == []


def test_fetch_records_un_seul_worker_sequentiel():
    s3 = FakeS3()
    keys = [f"SITE001/2025-01-01/00000{i}.json" for i in range(3)]
    for k in keys:
        s3.seed_bronze(k, mesure())

    records, errors = quality.fetch_records(s3, "bronze", keys, workers=1)

    assert errors == 0
    assert [k for k, _ in records] == keys


def test_fetch_records_cadence_directe_inchangee():
    """Cas 'direct' (collect.py dépose ~7 objets par cycle) : rien ne change."""
    s3 = FakeS3()
    keys = [f"SITE00{i}/2025-01-01/000000.json" for i in range(1, 8)]
    for i in range(1, 8):
        s3.seed_bronze(keys[i - 1], mesure(site_id=f"SITE00{i}"))

    records, errors = quality.fetch_records(s3, "bronze", keys, workers=16)

    assert errors == 0
    assert [rec["site_id"] for _, rec in records] == [f"SITE00{i}" for i in range(1, 8)]


def test_fetch_records_borne_workers_au_nombre_de_cles():
    s3 = FakeS3()
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())
    records, errors = quality.fetch_records(
        s3, "bronze", ["SITE001/2025-01-01/000000.json"], workers=64,
    )
    assert (len(records), errors) == (1, 0)


# ------------------------------------------------------------------- main (e2e)

def test_main_bout_en_bout_puis_idempotent(monkeypatch, capsys):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)

    seeds = {
        "SITE001/2025-01-01/000000.json": mesure("SITE001", "2025-01-01T00:00:00"),
        "SITE001/2025-01-01/010000.json": mesure("SITE001", "2025-01-01T01:00:00", consumption_kw=44.0),
        "SITE002/2025-01-02/000000.json": mesure("SITE002", "2025-01-02T00:00:00"),
        "SITE002/2025-01-02/010000.json": mesure("SITE002", "2025-01-02T01:00:00", consumption_kw=None),
    }
    for k, v in seeds.items():
        s3.seed_bronze(k, v)

    assert quality.main([]) == 0
    sortie = capsys.readouterr().out
    assert "nouveaux=4" in sortie
    assert "lignes_silver=4" in sortie

    silver_keys = s3.keys("silver")
    assert any(k.startswith("record_date=2025-01-01/site_id=SITE001/") for k in silver_keys)
    assert any(k.startswith("record_date=2025-01-02/site_id=SITE002/") for k in silver_keys)

    # Le gold n'est pas produit par le passage silver, seulement empilé.
    assert s3.keys("gold") == []
    assert "gold_en_attente=2" in sortie

    assert quality.main(["--gold-only"]) == 0
    gold_keys = s3.keys("gold")
    assert any(k.startswith("daily/record_date=2025-01-01/site_id=SITE001/") for k in gold_keys)
    assert any(k.startswith("hourly/record_date=2025-01-02/site_id=SITE002/") for k in gold_keys)

    state = json.loads(s3.store[("manifests", "etl_state.json")])
    assert state["processed_count"] == 4
    assert sorted(state["processed"]) == sorted(seeds)

    # 2e passage : plus aucun nouvel objet -> aucun batch silver en plus
    silver_avant = set(s3.keys("silver"))
    assert quality.main([]) == 0
    sortie2 = capsys.readouterr().out
    assert "nouveaux=0" in sortie2
    assert set(s3.keys("silver")) == silver_avant


def test_main_bronze_vide_retourne_zero(monkeypatch, capsys):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)

    assert quality.main([]) == 0
    assert "nouveaux=0" in capsys.readouterr().out


def test_main_supporte_un_lot_entierement_null(monkeypatch, capsys):
    """L'API mock injecte des null en rafale : un lot où consumption_kw est
    partout null ne doit pas faire planter le pipeline (regression : abs(None))."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    for i in range(4):
        s3.seed_bronze(
            f"SITE001/2025-01-01/0{i}0000.json",
            mesure("SITE001", f"2025-01-01T0{i}:00:00", consumption_kw=None, consumption_kwh=None),
        )

    assert quality.main([]) == 0
    out = capsys.readouterr().out
    assert "lignes_silver=4" in out
    assert s3.keys("silver")            # silver écrit malgré la consommation absente

    assert quality.main(["--gold-only"]) == 0
    assert any(k.startswith("daily/") for k in s3.keys("gold"))


# ------------------------------------------------------- découplage du gold

def test_process_batch_ne_recalcule_pas_le_gold_par_defaut():
    """Le chemin appelé chaque minute (et à chaque tranche du rattrapage) écrit le
    silver mais ne touche pas au gold : c'est tout l'objet du découplage."""
    s3 = FakeS3()
    lignes, quarantaine, partitions = quality.process_batch(
        s3,
        [("SITE001/2025-01-01/000000.json", mesure())],
        silver_bucket="silver",
        gold_bucket="gold",
        quarantine_bucket="quarantine",
    )

    assert (lignes, quarantaine) == (1, 0)
    assert partitions == {("2025-01-01", "SITE001")}
    assert s3.keys("silver")
    assert s3.keys("gold") == []


def test_process_batch_recalcule_le_gold_si_demande():
    """--with-gold (passage manuel ponctuel) produit toujours les deux grains."""
    s3 = FakeS3()
    quality.process_batch(
        s3,
        [("SITE001/2025-01-01/000000.json", mesure())],
        silver_bucket="silver",
        gold_bucket="gold",
        quarantine_bucket="quarantine",
        rebuild_gold=True,
    )

    assert s3.keys("gold") == [
        "daily/record_date=2025-01-01/site_id=SITE001/daily.parquet",
        "hourly/record_date=2025-01-01/site_id=SITE001/hourly.parquet",
    ]


def test_file_gold_ajout_et_retrait():
    """Ajout et retrait relisent l'objet : le passage silver peut empiler pendant
    qu'un passage gold dépile, sans que l'un écrase le travail de l'autre."""
    s3 = FakeS3()
    quality.add_pending_gold(s3, "manifests", "gold_pending.json", {("2025-01-01", "SITE001")})
    quality.add_pending_gold(s3, "manifests", "gold_pending.json", {("2025-01-01", "SITE002")})

    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {
        ("2025-01-01", "SITE001"),
        ("2025-01-01", "SITE002"),
    }

    quality.remove_pending_gold(s3, "manifests", "gold_pending.json", {("2025-01-01", "SITE001")})

    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2025-01-01", "SITE002")}


def test_file_gold_absente_ou_corrompue_vaut_file_vide():
    """Un manifeste illisible ne fait pas échouer le passage : on repart d'une file
    vide, le recalcul quotidien rattrapera les partitions perdues."""
    s3 = FakeS3()
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()

    s3.put_object("manifests", "gold_pending.json", b"{pas du json")
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()


def test_partitions_for_date_liste_les_sites_de_la_journee():
    s3 = FakeS3()
    s3.put_object("silver", "record_date=2025-01-01/site_id=SITE001/batch_a.parquet", b"x")
    s3.put_object("silver", "record_date=2025-01-01/site_id=SITE002/batch_b.parquet", b"x")
    s3.put_object("silver", "record_date=2025-01-02/site_id=SITE003/batch_c.parquet", b"x")

    assert quality.partitions_for_date(s3, "silver", "2025-01-01") == {
        ("2025-01-01", "SITE001"),
        ("2025-01-01", "SITE002"),
    }


def test_run_gold_recalcule_puis_vide_la_file(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())
    quality.run([])

    resultat = quality.run_gold([])

    assert (resultat.ok, resultat.partitions, resultat.recalculees, resultat.echecs) == (True, 1, 1, 0)
    assert s3.keys("gold") == [
        "daily/record_date=2025-01-01/site_id=SITE001/daily.parquet",
        "hourly/record_date=2025-01-01/site_id=SITE001/hourly.parquet",
    ]
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()


def test_run_gold_sans_partition_en_attente_ne_fait_rien(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)

    resultat = quality.run_gold([])

    assert (resultat.ok, resultat.partitions) == (True, 0)
    assert s3.keys("gold") == []


def test_run_gold_poursuit_et_laisse_en_attente_une_partition_en_echec(monkeypatch):
    """Une partition illisible ne doit ni interrompre les autres, ni être retirée de
    la file : sinon son agrégat resterait figé sur un silver périmé."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    for site in ("SITE001", "SITE002"):
        s3.seed_bronze(f"{site}/2025-01-01/000000.json", mesure(site))
    quality.run([])

    cassee = next(k for k in s3.keys("silver") if "site_id=SITE001/" in k)
    s3.errors.add(cassee)

    resultat = quality.run_gold([])

    assert (resultat.recalculees, resultat.echecs) == (1, 1)
    assert any(k.startswith("daily/record_date=2025-01-01/site_id=SITE002/") for k in s3.keys("gold"))
    assert not any("site_id=SITE001/" in k for k in s3.keys("gold"))
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2025-01-01", "SITE001")}


def test_run_gold_avec_date_reprend_une_partition_absente_de_la_file(monkeypatch):
    """Filet du recalcul quotidien : la partition n'est plus en file (manifeste perdu),
    --gold-date la retrouve quand même à partir du silver."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())
    quality.run([])
    quality.save_pending_gold(s3, "manifests", "gold_pending.json", set())

    resultat = quality.run_gold(["--gold-date", "2025-01-01"])

    assert resultat.recalculees == 1
    assert any(k.startswith("daily/record_date=2025-01-01/site_id=SITE001/") for k in s3.keys("gold"))


def _gold(s3, grain, record_date="2025-01-01", site_id="SITE001"):
    return s3.parquet("gold", f"{grain}/record_date={record_date}/site_id={site_id}/{grain}.parquet")


def mesure_vide(site_id="SITE001", timestamp="2025-01-01T00:00:00"):
    """Lecture bien formée mais sans aucune métrique : ce que renvoie l'API mock quand le
    capteur réseau est en panne (null_reasons = network_loss, data_quality = critical)."""
    return mesure(
        site_id, timestamp,
        consumption_kw=None, consumption_kwh=None,
        data_quality="critical", null_reasons=["network_loss"],
    )


# ------------------------------------------------------------- score de qualité

def test_score_qualite_classe_critical_au_pire():
    """critical tombait dans la branche par défaut (-5) et sortait donc MIEUX noté que
    partial (-10) et degraded (-25) : sur les données réelles, 55 contre 40 de moyenne."""
    scores = {
        niveau: quality.compute_quality_score({
            "data_quality": niveau, "consumption_kw": 42.0, "timestamp": "x", "site_id": "SITE001",
        })
        for niveau in ("good", "partial", "degraded", "critical")
    }

    assert scores["good"] > scores["partial"] > scores["degraded"] > scores["critical"]


def test_score_qualite_niveau_inconnu_reste_penalise():
    inconnu = quality.compute_quality_score({
        "data_quality": "farfelu", "consumption_kw": 42.0, "timestamp": "x", "site_id": "SITE001",
    })
    parfait = quality.compute_quality_score({
        "data_quality": "good", "consumption_kw": 42.0, "timestamp": "x", "site_id": "SITE001",
    })
    assert inconnu < parfait


# ------------------------------------------------------- complétude du silver

def test_lecture_sans_metrique_reste_en_silver_marquee_invalide(monkeypatch):
    """Le trou doit rester visible ET daté : la ligne n'est pas rejetée, mais elle ne doit
    pas être confondue avec une mesure."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure_vide())
    s3.seed_bronze("SITE001/2025-01-01/010000.json", mesure(timestamp="2025-01-01T01:00:00"))

    assert quality.main([]) == 0

    silver = s3.parquet("silver", s3.keys("silver")[0]).sort_values("timestamp")
    assert s3.keys("quarantine") == []                       # bien formée : pas un rejet
    assert list(silver["usable_metrics_count"]) == [0, 2]
    assert list(silver["is_valid"]) == [False, True]


# ------------------------------------------------------------ compteurs du gold

def test_compteurs_gold_partitionnent_records_count(monkeypatch):
    """good + partial + degraded + critical + unknown doit boucler sur records_count.
    Les lignes critical n'étaient comptées dans aucun compteur."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    niveaux = ["good", "partial", "degraded", "critical", "farfelu"]
    for index, niveau in enumerate(niveaux):
        s3.seed_bronze(
            f"SITE001/2025-01-01/0{index}0000.json",
            mesure(timestamp=f"2025-01-01T0{index}:00:00", data_quality=niveau),
        )

    quality.main([])
    quality.main(["--gold-only"])
    daily = _gold(s3, "daily").iloc[0]

    assert daily["records_count"] == 5
    somme = sum(daily[f"{n}_count"] for n in ("good", "partial", "degraded", "critical", "unknown"))
    assert somme == daily["records_count"]
    assert (daily["critical_count"], daily["unknown_count"]) == (1, 1)


def test_compteurs_gold_separent_vide_et_exploitable(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure_vide())
    s3.seed_bronze("SITE001/2025-01-01/010000.json", mesure_vide(timestamp="2025-01-01T01:00:00"))
    s3.seed_bronze("SITE001/2025-01-01/020000.json", mesure(timestamp="2025-01-01T02:00:00"))

    quality.main([])
    quality.main(["--gold-only"])
    daily = _gold(s3, "daily").iloc[0]

    assert (daily["records_count"], daily["usable_count"], daily["empty_count"]) == (3, 1, 2)


def test_total_kwh_vaut_nan_et_non_zero_quand_rien_n_est_mesure(monkeypatch):
    """`Series.sum()` renvoie 0.0 sur un groupe tout-NaN : le gold annonçait une
    consommation totale de 0 kWh pour une journée sans aucune mesure. Un modèle apprend
    alors une conso nulle réelle au lieu de voir un trou."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    for index in range(3):
        s3.seed_bronze(
            f"SITE001/2025-01-01/0{index}0000.json",
            mesure_vide(timestamp=f"2025-01-01T0{index}:00:00"),
        )

    quality.main([])
    quality.main(["--gold-only"])
    daily = _gold(s3, "daily").iloc[0]

    assert pd.isna(daily["total_consumption_kwh"])
    assert pd.isna(daily["avg_consumption_kw"])
    assert daily["missing_consumption_count"] == 3


def test_total_kwh_somme_normalement_des_qu_une_mesure_existe(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure_vide())
    s3.seed_bronze(
        "SITE001/2025-01-01/010000.json",
        mesure(timestamp="2025-01-01T01:00:00", consumption_kwh=12.5),
    )

    quality.main([])
    quality.main(["--gold-only"])

    assert _gold(s3, "daily").iloc[0]["total_consumption_kwh"] == 12.5


# --------------------------------------------------------- grille horaire 24 h

def test_gold_horaire_complete_les_24_heures(monkeypatch):
    """Une heure sans relevé doit exister avec records_count=0 : sinon elle est
    indiscernable d'une heure absente du jeu de données, et une série temporelle recolle
    deux heures non adjacentes sans le savoir."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure(timestamp="2025-01-01T00:00:00"))
    s3.seed_bronze("SITE001/2025-01-01/050000.json", mesure(timestamp="2025-01-01T05:00:00"))

    quality.main([])
    quality.main(["--gold-only"])
    hourly = _gold(s3, "hourly")

    assert len(hourly) == 24
    assert list(hourly["record_hour"])[:2] == ["2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z"]
    assert list(hourly["records_count"]) == [1, 0, 0, 0, 0, 1] + [0] * 18
    creuse = hourly[hourly["record_hour"] == "2025-01-01T03:00:00Z"].iloc[0]
    assert pd.isna(creuse["avg_consumption_kw"])            # trou explicite, pas un zéro
    assert creuse["site_id"] == "SITE001" and creuse["site_type"] == "office"


def test_gold_horaire_a_les_memes_metriques_que_le_journalier(monkeypatch):
    """L'horaire n'exposait que records_count / avg-min-max conso / avg_quality_score :
    impossible d'y distinguer « aucun relevé » de « relevés tous vides », alors que c'est
    le grain sur lequel s'entraînent les modèles de charge."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())

    quality.main([])
    quality.main(["--gold-only"])

    communes = set(quality.GOLD_COUNTER_NAMES) | set(quality.GOLD_METRIC_NAMES)
    assert communes <= set(_gold(s3, "hourly").columns)
    assert communes <= set(_gold(s3, "daily").columns)


# ------------------------------------------------------------------ anomalies

def test_anomalies_calculees_sur_toute_la_partition(monkeypatch):
    """En collecte temps réel un batch vaut une ligne par site : shift(1) et l'écart-type y
    sont toujours NaN, donc has_anomaly ne se déclenchait jamais (1 cas sur 9 478 lignes
    réelles). Le recalcul gold, lui, tient toute la partition."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    consos = [10.0, 10.0, 10.0, 10.0, 10.0, 100.0]
    for index, conso in enumerate(consos):
        s3.seed_bronze(
            f"SITE001/2025-01-01/0{index}0000.json",
            mesure(timestamp=f"2025-01-01T0{index}:00:00", consumption_kw=conso),
        )

    # un objet par passage : chaque batch silver ne contient qu'une ligne, comme en collecte
    for _ in consos:
        quality.main(["--max-objects", "1"])
    assert all(len(s3.parquet("silver", k)) == 1 for k in s3.keys("silver"))

    quality.main(["--gold-only"])

    assert _gold(s3, "daily").iloc[0]["anomaly_count"] == 1


def test_gold_dedoublonne_une_cle_bronze_reingeree(monkeypatch):
    """Le silver est append-only : si une clé est réingérée (état incrémental purgé, objet
    bronze redéposé), un batch s'ajoute sans retirer l'ancien. La mesure ne doit pas
    compter double."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())

    quality.main([])
    quality.save_state(s3, "manifests", "etl_state.json", set())   # état incrémental purgé
    quality.main([])
    assert len(s3.keys("silver")) == 2                             # deux batches, même mesure

    quality.main(["--gold-only"])

    assert _gold(s3, "daily").iloc[0]["records_count"] == 1


# ----------------------------------------------------------------- quarantaine

def test_quarantaine_ecrite_en_parquet_partitionne_sur_la_date_mesuree(monkeypatch, capsys):
    """Partitionnée comme silver et gold, pour qu'un rejet puisse être rapproché du trou
    qu'il laisse. L'ancienne disposition — un objet JSON par rejet sous la date de
    TRAITEMENT — obligeait à lister des milliers d'objets pour l'exploiter."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", {"timestamp": "2025-01-01T05:00:00"})

    assert quality.main([]) == 0
    assert "quarantined=1" in capsys.readouterr().out

    cles = s3.keys("quarantine")
    assert len(cles) == 1
    assert cles[0].startswith("record_date=2025-01-01/site_id=unknown/rejects_")
    rejets = s3.parquet("quarantine", cles[0])
    assert list(rejets.columns) == quality.QUARANTINE_COLUMNS
    rejet = rejets.iloc[0]
    assert rejet["error_type"] == "missing_mandatory_fields"
    assert rejet["error_message"] == "site_id"
    assert json.loads(rejet["raw_record"])["timestamp"] == "2025-01-01T05:00:00"


def test_quarantaine_partition_unknown_si_l_enregistrement_est_illisible(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", b"pas du json")

    assert quality.main([]) == 0

    cles = s3.keys("quarantine")
    assert cles[0].startswith("record_date=unknown/site_id=unknown/rejects_")
    rejet = s3.parquet("quarantine", cles[0]).iloc[0]
    assert rejet["error_type"] == "parse_error"
    assert rejet["raw_record"] == "pas du json"


def test_quarantaine_regroupe_un_lot_en_un_parquet_par_partition(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    for index in range(4):
        s3.seed_bronze(f"SITE001/2025-01-01/00000{index}.json", {"timestamp": "2025-01-01T05:00:00"})

    assert quality.main([]) == 0

    cles = s3.keys("quarantine")
    assert len(cles) == 1                                # un objet, pas quatre
    assert len(s3.parquet("quarantine", cles[0])) == 4


def test_main_gold_only_ne_lit_pas_le_bronze(monkeypatch):
    """--gold-only ne touche ni au bronze ni à l'état incrémental : les deux
    plannings écrivent des manifestes différents."""
    s3 = FakeS3()
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.seed_bronze("SITE001/2025-01-01/000000.json", mesure())

    assert quality.main(["--gold-only"]) == 0

    assert s3.keys("silver") == []
    assert ("manifests", "etl_state.json") not in s3.store
