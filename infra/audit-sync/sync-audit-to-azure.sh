#!/bin/sh
set -eu

: "${MINIO_ROOT_USER:?variable requise}"
: "${MINIO_ROOT_PASSWORD:?variable requise}"

INTERVALLE="${SYNC_INTERVAL_SECONDES:-900}"
CONFIG="/tmp/rclone.conf"

echo "Synchro périodique MinIO -> Azure Blob (chiffré côté client), toutes les ${INTERVALLE}s"
echo "bronze/silver/gold -> container mutable, audit -> container séparé verrouillé (immutable storage)"

while true; do
  if [ -z "${AZURE_STORAGE_ACCOUNT:-}" ] || [ -z "${AZURE_STORAGE_KEY:-}" ] || \
     [ -z "${AZURE_STORAGE_CONTAINER:-}" ] || [ -z "${AZURE_STORAGE_CONTAINER_AUDIT:-}" ] || \
     [ -z "${RCLONE_CRYPT_PASSWORD_RAW:-}" ]; then
    echo "$(date -u +%FT%TZ) : AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY / AZURE_STORAGE_CONTAINER / AZURE_STORAGE_CONTAINER_AUDIT / RCLONE_CRYPT_PASSWORD_RAW manquants dans infra/audit-sync/.env, synchro suspendue"
    sleep "$INTERVALLE"
    continue
  fi

  if [ ! -f "$CONFIG" ]; then
    MOT_DE_PASSE_OBSCURCI="$(rclone obscure "$RCLONE_CRYPT_PASSWORD_RAW")"
    cat > "$CONFIG" <<EOF
[minio]
type = s3
provider = Minio
env_auth = false
access_key_id = ${MINIO_ROOT_USER}
secret_access_key = ${MINIO_ROOT_PASSWORD}
endpoint = http://minio:9000

[azure]
type = azureblob
account = ${AZURE_STORAGE_ACCOUNT}
key = ${AZURE_STORAGE_KEY}

[azure-crypt]
type = crypt
remote = azure:${AZURE_STORAGE_CONTAINER}
password = ${MOT_DE_PASSE_OBSCURCI}
filename_encryption = standard
directory_name_encryption = false

[azure-crypt-audit]
type = crypt
remote = azure:${AZURE_STORAGE_CONTAINER_AUDIT}
password = ${MOT_DE_PASSE_OBSCURCI}
filename_encryption = standard
directory_name_encryption = false
EOF
    chmod 600 "$CONFIG"
  fi

  for bucket in bronze silver gold; do
    echo "$(date -u +%FT%TZ) : synchro ${bucket} en cours (container principal)"
    if rclone --config "$CONFIG" sync "minio:${bucket}" "azure-crypt:${bucket}"; then
      echo "$(date -u +%FT%TZ) : synchro ${bucket} terminée"
    else
      echo "$(date -u +%FT%TZ) : échec de la synchro ${bucket}"
    fi
  done

  echo "$(date -u +%FT%TZ) : synchro audit en cours (container verrouillé)"
  if rclone --config "$CONFIG" sync "minio:audit" "azure-crypt-audit:audit"; then
    echo "$(date -u +%FT%TZ) : synchro audit terminée"
  else
    echo "$(date -u +%FT%TZ) : échec de la synchro audit"
  fi

  sleep "$INTERVALLE"
done
