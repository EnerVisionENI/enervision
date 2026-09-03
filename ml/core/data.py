"""
Accès en lecture au stockage MinIO (couche gold, granularité horaire).

C'est le seul fichier du module ML qui parle directement à MinIO.
Si le stockage change un jour (Azure Blob, S3 direct...), c'est le
seul fichier à toucher.

Buckets séparés par couche (voir infra/minio/init-buckets.sh) :
bronze, silver, gold, quarantine, manifests, audit — pas un bucket
unique avec des préfixes internes.

Le gold réel est partitionné par jour ET par site (pas un fichier
unique par site comme supposé initialement) :
    hourly/record_date=YYYY-MM-DD/site_id=SITE.../hourly.parquet
avec les colonnes : record_date, record_hour, site_id, site_type,
records_count, avg_consumption_kw, max_consumption_kw,
min_consumption_kw, avg_quality_score.

FIXME (bloquant, cf. message à l'auteur du gold) : cette couche ne
porte pas de température, alors que TOWT (models/towt.py) en a besoin
comme variable explicative. En attendant qu'un avg_temperature_c soit
ajouté à l'agrégation gold (silver l'a déjà : temperature_celsius,
mais quasi toujours nulle — panne capteur simulée par l'API mock),
on comble avec TEMPERATURE_FALLBACK_C ci-dessous. Uniquement utile
pour vérifier que le pipeline tourne bout en bout : le modèle ne peut
rien apprendre d'une température constante, donc son MASE réel n'a
aucune valeur tant que ce FIXME n'est pas levé.
"""

import io
import os
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

MINIO_ENDPOINT = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
MINIO_BUCKET_GOLD = os.environ.get("MINIO_BUCKET_GOLD", "gold")
MINIO_GOLD_HOURLY_PREFIX = os.environ.get("MINIO_GOLD_HOURLY_PREFIX", "hourly")

REQUIRED_COLUMNS = {"timestamp", "site_id", "consumption_kwh", "temperature_c", "data_quality"}

# Voir le FIXME ci-dessus : placeholder le temps que le gold porte la vraie température.
TEMPERATURE_FALLBACK_C = 20.0


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def _list_prefix(client, bucket: str, prefix: str, limit: int = 20) -> list[str]:
    """Aide au diagnostic : liste ce qu'il y a réellement sous un préfixe."""
    resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=limit)
    return [o["Key"] for o in resp.get("Contents", [])]


def _daterange(start: str, end: str):
    d = datetime.fromisoformat(start[:10])
    last = datetime.fromisoformat(end[:10])
    while d <= last:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def load_gold_hourly(site_id: str, start: str, end: str) -> pd.DataFrame:
    """
    Charge le gold horaire d'un site sur une période, depuis le
    bucket 'gold' de MinIO : une partition par jour, à concaténer
    sur la plage [start, end] (voir _daterange).
    """
    client = _s3_client()

    frames = []
    for day in _daterange(start, end):
        key = f"{MINIO_GOLD_HOURLY_PREFIX}/record_date={day}/site_id={site_id}/hourly.parquet"
        try:
            obj = client.get_object(Bucket=MINIO_BUCKET_GOLD, Key=key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                continue
            raise
        frames.append(pd.read_parquet(io.BytesIO(obj["Body"].read())))

    if not frames:
        existing = _list_prefix(client, MINIO_BUCKET_GOLD, MINIO_GOLD_HOURLY_PREFIX)
        hint = (
            "\n".join(existing) if existing
            else "(rien trouvé sous ce préfixe — le bucket est peut-être vide, "
                 "ou la structure est différente à la racine)"
        )
        raise FileNotFoundError(
            f"Aucune partition gold trouvée pour {site_id} entre {start} et {end} "
            f"sous '{MINIO_GOLD_HOURLY_PREFIX}/' dans le bucket '{MINIO_BUCKET_GOLD}'.\n\n"
            f"Objets réellement présents :\n{hint}\n\n"
            "→ Lance `python list_gold_keys.py` pour explorer."
        )

    df = pd.concat(frames, ignore_index=True)

    # Adapte le schéma réel du gold (voir docstring du module) à celui
    # attendu par les modèles (REQUIRED_COLUMNS).
    df = df.sort_values("record_hour").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["record_hour"], utc=True)
    # avg_consumption_kw sur une fenêtre d'une heure ~= consommation en kWh de l'heure.
    df["consumption_kwh"] = df["avg_consumption_kw"]
    # FIXME (trial run) : avg_consumption_kw est NaN sur ~99% des heures pour SITE001
    # (même cause que la température : la source ne renvoie une lecture exploitable
    # que sur une fraction des créneaux — voir good_count/degraded_count/
    # missing_consumption_count côté gold daily). Comblé ici par ffill/bfill pour que
    # le pipeline ait de quoi s'entraîner ; à retirer une fois que la volumétrie de
    # lectures valides est meilleure, sous peine de MASE artificiellement optimiste
    # (on prédit en partie des valeurs qu'on a soi-même comblées).
    n_missing = df["consumption_kwh"].isna().sum()
    if n_missing:
        print(
            f"[data.py] FIXME : consumption_kwh manquant sur {n_missing}/{len(df)} heures "
            f"({n_missing / len(df):.1%}) pour {site_id} — comblé par ffill/bfill (placeholder)."
        )
        df["consumption_kwh"] = df["consumption_kwh"].ffill().bfill()
    if "temperature_c" not in df.columns:
        print(
            f"[data.py] FIXME : pas de température dans le gold pour {site_id} — "
            f"repli sur une constante ({TEMPERATURE_FALLBACK_C} °C). "
            "Le MASE de TOWT n'est pas exploitable tant que ce n'est pas corrigé en amont."
        )
        df["temperature_c"] = TEMPERATURE_FALLBACK_C
    # avg_quality_score (0-100) -> catégories grossières, en attendant une vraie
    # colonne catégorielle côté gold (placeholder, seuils arbitraires).
    df["data_quality"] = pd.cut(
        df["avg_quality_score"],
        bins=[-1, 50, 80, 101],
        labels=["critical", "degraded", "good"],
    )

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Colonnes attendues manquantes après adaptation du schéma gold : {missing}. "
            f"Colonnes présentes : {sorted(df.columns)}."
        )

    mask = (df["timestamp"] >= pd.Timestamp(start, tz="UTC")) & (df["timestamp"] <= pd.Timestamp(end, tz="UTC"))
    df = df.loc[mask].sort_values("timestamp").reset_index(drop=True)

    if df.empty:
        raise ValueError(f"Aucune donnée pour {site_id} entre {start} et {end}.")

    return df


def split_train_calib_test(
    df: pd.DataFrame,
    train_start: str, train_end: str,
    calib_start: str, calib_end: str,
    test_start: str, test_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Découpe en 3 jeux temporellement disjoints. Le jeu de calibration
    ne doit JAMAIS être vu à l'entraînement : c'est lui qui garantit
    la couverture de l'intervalle conforme (voir evaluation.py).
    """
    ts = df["timestamp"]
    train_start, train_end, calib_start, calib_end, test_start, test_end = (
        pd.Timestamp(b, tz="UTC") for b in (train_start, train_end, calib_start, calib_end, test_start, test_end)
    )
    train = df[(ts >= train_start) & (ts < train_end)].reset_index(drop=True)
    calib = df[(ts >= calib_start) & (ts < calib_end)].reset_index(drop=True)
    test = df[(ts >= test_start) & (ts <= test_end)].reset_index(drop=True)

    for name, part in [("train", train), ("calib", calib), ("test", test)]:
        if part.empty:
            raise ValueError(f"Le jeu '{name}' est vide — vérifie les bornes de dates.")

    return train, calib, test


if __name__ == "__main__":
    # petit test manuel : python data.py
    df = load_gold_hourly(
        site_id=os.environ.get("DEFAULT_SITE_ID", "site_02"),
        start=os.environ.get("TRAIN_START", "2026-08-01"),
        end=os.environ.get("TEST_END", "2026-09-01"),
    )
    print(df.head())
    print(f"\n{len(df)} lignes, colonnes : {list(df.columns)}")
    print(f"Qualité des données :\n{df['data_quality'].value_counts()}")
