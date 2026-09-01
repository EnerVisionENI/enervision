#!/usr/bin/env bash
set -euo pipefail

# Usage: remote-deploy.sh [deploy_path] [branch]
DEPLOY_PATH="${1:-/var/www/enervision}"
BRANCH="${2:-dev}"

echo "Deploy path: ${DEPLOY_PATH}"
echo "Branch: ${BRANCH}"

if [ ! -d "${DEPLOY_PATH}" ]; then
  echo "Directory ${DEPLOY_PATH} does not exist. Creating..."
  mkdir -p "${DEPLOY_PATH}"
fi

cd "${DEPLOY_PATH}"

echo "Fetching latest..."
git fetch --all --prune

echo "Resetting to origin/${BRANCH}..."
git reset --hard "origin/${BRANCH}"

echo "Pulling and (re)starting services with Docker Compose..."
if command -v docker > /dev/null 2>&1; then
  docker compose pull || docker-compose pull
  docker compose up -d --build --remove-orphans || docker-compose up -d --build --remove-orphans
else
  echo "Docker not found on the server; aborting." >&2
  exit 2
fi

echo "Deploy finished."
