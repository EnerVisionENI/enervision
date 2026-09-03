"""
Accès en lecture au stockage MinIO (couche gold, granularité horaire).

C'est le seul fichier du module ML qui parle directement à MinIO.
Si le stockage change un jour (Azure Blob, S3 direct...), c'est le
seul fichier à toucher.

Buckets séparés par couche (voir infra/minio/init-buckets.sh) :
bronze, silver, gold, quarantine, manifests, audit — pas un bucket
unique avec des préfixes internes.
"""

import io
import os

import boto3
from botocore.exceptions import ClientError
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

MINIO_ENDPOINT = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ACCESS_KEY"]
MINIO_SECRET_KEY = os.environ["MINIO_SECRET_KEY"]
MINIO_BUCKET_GOLD = os.environ.get("MINIO_BUCKET_GOLD", "gold")
MINIO_GOLD_HOURLY_PREFIX = os.environ.get("MINIO_GOLD_HOURLY_PREFIX", "hourly")

REQUIRED_COLUMNS = {"timestamp", "site_id", "consumption_kwh", "temperature_c", "data_quality"}


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


def load_gold_hourly(site_id: str, start: str, end: str) -> pd.DataFrame:
    """
    Charge le gold horaire d'un site sur une période, depuis le
    bucket 'gold' de MinIO.

    Essaie d'abord le format {prefix}/{site_id}.parquet (un fichier
    par site). Si ça échoue, liste ce qui existe réellement sous le
    préfixe pour te dire quoi corriger plutôt que d'échouer en
    silence — lance `python list_gold_keys.py` pour voir la
    structure réelle avant d'ajuster MINIO_GOLD_HOURLY_PREFIX.
    """
    client = _s3_client()
    key = f"{MINIO_GOLD_HOURLY_PREFIX}/{site_id}.parquet"

    try:
        obj = client.get_object(Bucket=MINIO_BUCKET_GOLD, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            existing = _list_prefix(client, MINIO_BUCKET_GOLD, MINIO_GOLD_HOURLY_PREFIX)
            hint = (
                "\n".join(existing) if existing
                else "(rien trouvé sous ce préfixe — le bucket est peut-être vide, "
                     "ou la structure est différente à la racine)"
            )
            raise FileNotFoundError(
                f"Clé introuvable : s3://{MINIO_BUCKET_GOLD}/{key}\n\n"
                f"Objets réellement présents sous '{MINIO_GOLD_HOURLY_PREFIX}/' "
                f"dans le bucket '{MINIO_BUCKET_GOLD}' :\n{hint}\n\n"
                "→ Lance `python list_gold_keys.py` pour explorer, puis ajuste "
                "MINIO_GOLD_HOURLY_PREFIX dans .env et/ou le format de clé "
                "dans data.py (load_gold_hourly) en conséquence."
            ) from e
        raise

    df = pd.read_parquet(io.BytesIO(obj["Body"].read()))

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Colonnes attendues manquantes dans {key} : {missing}. "
            f"Colonnes présentes : {sorted(df.columns)}. "
            "Adapte REQUIRED_COLUMNS et les noms de colonnes dans data.py "
            "à ton schéma réel de la couche gold."
        )

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
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
