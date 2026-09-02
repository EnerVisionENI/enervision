"""Client S3 / MinIO partagé par collect.py et quality.py.

Une seule fabrique, construction paresseuse : importer ce module ne touche pas
à l'environnement, la connexion n'est créée qu'au premier appel de get_s3().
"""

import os
from functools import lru_cache

import boto3
from botocore.client import Config


@lru_cache
def get_s3():
    """Client S3 pointé sur MinIO.

    Variables lues dans l'environnement :
        MINIO_ENDPOINT      (obligatoire, ex. "minio:9000")
        MINIO_ACCESS_KEY    (obligatoire)
        MINIO_SECRET_KEY    (obligatoire)
        MINIO_USE_SSL       (optionnel, défaut "false")
    """
    endpoint = os.environ["MINIO_ENDPOINT"]
    use_ssl = os.environ.get("MINIO_USE_SSL", "false").lower() == "true"
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if use_ssl else 'http'}://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
