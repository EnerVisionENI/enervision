#!/bin/bash
set -e

MINIO_ALIAS="local"
MINIO_URL="${MINIO_URL:-http://minio:9000}"
MINIO_USER="${MINIO_ROOT_USER}"
MINIO_PASS="${MINIO_ROOT_PASSWORD}"

mc alias set $MINIO_ALIAS $MINIO_URL $MINIO_USER $MINIO_PASS

for bucket in bronze silver gold quarantine manifests mlflow-artifacts; do
  if ! mc ls $MINIO_ALIAS/$bucket >/dev/null 2>&1; then
    echo "Création du bucket $bucket..."
    mc mb $MINIO_ALIAS/$bucket
  else
    echo "Bucket $bucket déjà existant."
  fi
done

# Bucket audit : Object Lock (WORM) activé à la création, ne peut pas l'être après coup.
# Rétention par défaut en mode COMPLIANCE : aucun enregistrement ne peut être modifié ou
# supprimé avant expiration, y compris par le compte root MinIO.
if ! mc ls $MINIO_ALIAS/audit >/dev/null 2>&1; then
  echo "Création du bucket audit (Object Lock activé)..."
  mc mb --with-lock $MINIO_ALIAS/audit
  mc retention set --default COMPLIANCE 365d $MINIO_ALIAS/audit
else
  echo "Bucket audit déjà existant."
fi

echo "Buckets prêts : bronze, silver, gold, quarantine, manifests, mlflow-artifacts, audit"