"""
Petit utilitaire de reconnaissance : liste ce qu'il y a réellement
dans le bucket gold, pour caler le bon format de clé dans data.py
avant d'écrire du code qui devine dans le vide.

Usage :
    python list_gold_keys.py
    python list_gold_keys.py --bucket silver
    python list_gold_keys.py --prefix hourly/ --max 50
"""

import argparse
import os

import boto3
from dotenv import load_dotenv

load_dotenv()


def list_keys(bucket: str, prefix: str = "", max_keys: int = 30):
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
    )

    paginator = client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            print(f"{obj['Key']:<70} {obj['Size']:>10} octets   {obj['LastModified']}")
            count += 1
            if count >= max_keys:
                print(f"\n... arrêté après {max_keys} clés (utilise --max pour en voir plus)")
                return
    if count == 0:
        print(f"Aucun objet trouvé dans '{bucket}' avec le préfixe '{prefix}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default=os.environ.get("MINIO_BUCKET_GOLD", "gold"))
    parser.add_argument("--prefix", default="")
    parser.add_argument("--max", type=int, default=30)
    args = parser.parse_args()

    print(f"--- Contenu de '{args.bucket}' (préfixe='{args.prefix}') ---\n")
    list_keys(args.bucket, args.prefix, args.max)
