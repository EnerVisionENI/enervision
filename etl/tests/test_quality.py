"""
Tests pour quality.py : téléchargement parallèle des objets bronze
(`fetch_records`) et bout-en-bout de `main()` sur un petit lot.
Aucun accès réseau : S3 est un faux en mémoire.
"""

import json
import threading
import time

import psycopg2
import pytest
from botocore.exceptions import ClientError

import quality


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

    def get_paginator(self, name):
        return _Paginator(self)

    # --- utilitaires de test ---
    def seed_bronze(self, key, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.put_object("bronze", key, body)

    def keys(self, bucket):
        with self.lock:
            return sorted(k for (b, k) in self.store if b == bucket)


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
    gold_keys = s3.keys("gold")
    assert any(k.startswith("record_date=2025-01-01/site_id=SITE001/") for k in silver_keys)
    assert any(k.startswith("record_date=2025-01-02/site_id=SITE002/") for k in silver_keys)
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
    assert any(k.startswith("daily/") for k in s3.keys("gold"))
