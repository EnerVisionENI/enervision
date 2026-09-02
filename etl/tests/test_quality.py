"""
Tests du découplage silver / gold dans quality.py.
Aucun test ne dépend d'un vrai MinIO, d'un vrai Postgres ni du réseau : le client S3 est
remplacé par un faux en mémoire et la connexion Postgres est neutralisée.
"""

import io
import json

import pytest
import quality
from botocore.exceptions import ClientError


class FauxClientS3:
    """Faux client boto3 : un dict {bucket: {clé: octets}} et de quoi lister/lire/écrire.
    `echecs_lecture` permet de simuler une partition silver illisible."""

    def __init__(self):
        self.objets = {}
        self.echecs_lecture = set()

    # -- API boto3 utilisée par quality.py -------------------------------------
    def head_bucket(self, Bucket):
        return {}

    def create_bucket(self, Bucket):
        self.objets.setdefault(Bucket, {})
        return {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objets.setdefault(Bucket, {})[Key] = Body

    def get_object(self, Bucket, Key):
        if Key in self.echecs_lecture:
            raise ClientError({"Error": {"Code": "500", "Message": "lecture simulée en échec"}}, "GetObject")
        try:
            corps = self.objets[Bucket][Key]
        except KeyError:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "absent"}}, "GetObject") from None
        return {"Body": io.BytesIO(corps)}

    def get_paginator(self, operation_name):
        return _FauxPaginator(self)

    # -- confort pour les tests -------------------------------------------------
    def cles(self, bucket, prefixe=""):
        return sorted(k for k in self.objets.get(bucket, {}) if k.startswith(prefixe))


class _FauxPaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, Bucket, Prefix=""):
        contenu = [{"Key": k} for k in self.client.cles(Bucket, Prefix)]
        yield {"Contents": contenu}


@pytest.fixture()
def s3():
    return FauxClientS3()


@pytest.fixture(autouse=True)
def pas_de_postgres(monkeypatch):
    """Aucun test ne doit ouvrir de connexion Postgres : make_pg_connection échoue,
    quality.py doit alors continuer en écrivant seulement sur MinIO."""
    import psycopg2

    def refuser():
        raise psycopg2.OperationalError("Postgres indisponible (test)")

    monkeypatch.setattr(quality.postgres_writer, "make_pg_connection", refuser)


def mesure(site_id="SITE001", horodatage="2026-09-01T09:57:43+00:00", consommation=42.0):
    return {
        "timestamp": horodatage,
        "site_id": site_id,
        "site_type": "usine",
        "consumption_kw": consommation,
        "consumption_kwh": consommation / 60,
        "data_quality": "good",
    }


def args_par_defaut(**surcharges):
    argv = []
    for cle, valeur in surcharges.items():
        option = f"--{cle.replace('_', '-')}"
        argv.extend([option] if valeur is True else [option, str(valeur)])
    return quality.build_parser().parse_args(argv)


# ------------------------------------------------------------- process_batch

def test_process_batch_ne_recalcule_pas_le_gold_par_defaut(s3):
    """Le chemin appelé chaque minute écrit le silver mais ne touche pas au gold :
    c'est tout l'objet du découplage."""
    lignes, quarantaine, partitions = quality.process_batch(
        s3,
        [("SITE001/2026-09-01/095743.json", mesure())],
        silver_bucket="silver",
        gold_bucket="gold",
        quarantine_bucket="quarantine",
    )

    assert (lignes, quarantaine) == (1, 0)
    assert partitions == {("2026-09-01", "SITE001")}
    assert s3.cles("silver")
    assert s3.cles("gold") == []


def test_process_batch_recalcule_le_gold_si_demande(s3):
    """--with-gold (run manuel) doit toujours produire les deux grains d'agrégats."""
    quality.process_batch(
        s3,
        [("SITE001/2026-09-01/095743.json", mesure())],
        silver_bucket="silver",
        gold_bucket="gold",
        quarantine_bucket="quarantine",
        rebuild_gold=True,
    )

    assert s3.cles("gold") == [
        "daily/record_date=2026-09-01/site_id=SITE001/daily.parquet",
        "hourly/record_date=2026-09-01/site_id=SITE001/hourly.parquet",
    ]


# ------------------------------------------------------ file des partitions

def test_file_gold_ajout_et_retrait(s3):
    """Ajout et retrait relisent l'objet : la boucle silver peut empiler pendant
    qu'un run gold dépile, sans que l'un écrase le travail de l'autre."""
    quality.add_pending_gold(s3, "manifests", "gold_pending.json", {("2026-09-01", "SITE001")})
    quality.add_pending_gold(s3, "manifests", "gold_pending.json", {("2026-09-01", "SITE002")})

    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {
        ("2026-09-01", "SITE001"),
        ("2026-09-01", "SITE002"),
    }

    quality.remove_pending_gold(s3, "manifests", "gold_pending.json", {("2026-09-01", "SITE001")})

    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2026-09-01", "SITE002")}


def test_file_gold_absente_ou_corrompue_vaut_file_vide(s3):
    """Un manifeste illisible ne doit pas faire échouer le run : on repart d'une file vide,
    le recalcul quotidien rattrapera les partitions perdues."""
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()

    s3.put_object(Bucket="manifests", Key="gold_pending.json", Body=b"{pas du json")
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()


def test_partitions_for_date_liste_les_sites_de_la_journee(s3):
    s3.put_object(Bucket="silver", Key="record_date=2026-09-01/site_id=SITE001/batch_a.parquet", Body=b"x")
    s3.put_object(Bucket="silver", Key="record_date=2026-09-01/site_id=SITE002/batch_b.parquet", Body=b"x")
    s3.put_object(Bucket="silver", Key="record_date=2026-09-02/site_id=SITE003/batch_c.parquet", Body=b"x")

    assert quality.partitions_for_date(s3, "silver", "2026-09-01") == {
        ("2026-09-01", "SITE001"),
        ("2026-09-01", "SITE002"),
    }


# ------------------------------------------------------------- run_silver

def test_run_silver_empile_les_partitions_sans_ecrire_de_gold(s3):
    """Bout en bout du cycle minute : silver écrit, gold vide, partition en attente."""
    s3.put_object(
        Bucket="bronze",
        Key="SITE001/2026-09-01/095743.json",
        Body=json.dumps(mesure()).encode("utf-8"),
    )

    assert quality.run_silver(s3, args_par_defaut()) == 0

    assert s3.cles("silver")
    assert s3.cles("gold") == []
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2026-09-01", "SITE001")}


# --------------------------------------------------------------- run_gold

def test_run_gold_recalcule_puis_vide_la_file(s3):
    s3.put_object(
        Bucket="bronze",
        Key="SITE001/2026-09-01/095743.json",
        Body=json.dumps(mesure()).encode("utf-8"),
    )
    quality.run_silver(s3, args_par_defaut())

    assert quality.run_gold(s3, args_par_defaut(gold_only=True)) == 0

    assert s3.cles("gold") == [
        "daily/record_date=2026-09-01/site_id=SITE001/daily.parquet",
        "hourly/record_date=2026-09-01/site_id=SITE001/hourly.parquet",
    ]
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == set()


def test_run_gold_sans_partition_en_attente_ne_fait_rien(s3):
    assert quality.run_gold(s3, args_par_defaut(gold_only=True)) == 0
    assert s3.cles("gold") == []


def test_run_gold_poursuit_et_laisse_en_attente_une_partition_en_echec(s3):
    """Une partition illisible ne doit ni interrompre les autres, ni être retirée de la
    file : sinon son agrégat resterait figé sur un silver périmé."""
    for site in ("SITE001", "SITE002"):
        s3.put_object(
            Bucket="bronze",
            Key=f"{site}/2026-09-01/095743.json",
            Body=json.dumps(mesure(site_id=site)).encode("utf-8"),
        )
    quality.run_silver(s3, args_par_defaut())

    cle_cassee = next(k for k in s3.cles("silver", "record_date=2026-09-01/site_id=SITE001/"))
    s3.echecs_lecture.add(cle_cassee)

    assert quality.run_gold(s3, args_par_defaut(gold_only=True)) == 0

    assert s3.cles("gold", "daily/record_date=2026-09-01/site_id=SITE002/")
    assert s3.cles("gold", "daily/record_date=2026-09-01/site_id=SITE001/") == []
    assert quality.load_pending_gold(s3, "manifests", "gold_pending.json") == {("2026-09-01", "SITE001")}


def test_run_gold_avec_date_reprend_une_partition_absente_de_la_file(s3):
    """Filet du recalcul quotidien : la partition n'est plus en attente (file perdue),
    --gold-date la retrouve quand même à partir du silver."""
    s3.put_object(
        Bucket="bronze",
        Key="SITE001/2026-09-01/095743.json",
        Body=json.dumps(mesure()).encode("utf-8"),
    )
    quality.run_silver(s3, args_par_defaut())
    quality.save_pending_gold(s3, "manifests", "gold_pending.json", set())

    assert quality.run_gold(s3, args_par_defaut(gold_only=True, gold_date="2026-09-01")) == 0

    assert s3.cles("gold", "daily/record_date=2026-09-01/site_id=SITE001/")


# ------------------------------------------------------------------- main

def test_main_gold_only_ne_lit_pas_le_bronze(s3, monkeypatch):
    """--gold-only ne doit jamais toucher au bronze ni à l'état incrémental :
    les deux plannings écrivent des manifestes différents."""
    monkeypatch.setattr(quality.storage, "get_s3", lambda: s3)
    s3.put_object(
        Bucket="bronze",
        Key="SITE001/2026-09-01/095743.json",
        Body=json.dumps(mesure()).encode("utf-8"),
    )

    assert quality.main(["--gold-only"]) == 0

    assert s3.cles("silver") == []
    assert "etl_state.json" not in s3.objets.get("manifests", {})
