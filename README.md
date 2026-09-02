# EnerVision

Plateforme de suivi et d'optimisation énergétique : collecte de mesures IoT,
pipeline de qualité de données (bronze → silver → gold), API REST et interface web.

## Architecture

Application multi-services conteneurisée, orchestrée par un unique `compose.yaml`
à la racine.

| Service | Dossier | Rôle | Stack |
|---|---|---|---|
| **api** | [`api/`](api/) | API REST (auth, alertes ; à venir : sites) | FastAPI, SQLAlchemy, PostgreSQL |
| **front** | [`front/`](front/) | Interface web | Vue 3, Vite, servi par Nginx en prod |
| **etl** | [`etl/`](etl/) | Collecte des mesures + qualité de données | Python, APScheduler, boto3 |
| **postgres** | — | Base de données | PostgreSQL 16 |
| **minio** | — | Stockage objet S3 (bronze/silver/gold/quarantine/audit) | MinIO |
| **audit-sync** | [`infra/audit-sync/`](infra/audit-sync/) | Réplication chiffrée MinIO → Azure Blob | rclone |
| **traefik** | [`infra/traefik/`](infra/traefik/) | Reverse proxy TLS | Traefik v2 |

### Flux de données

```
API Mock IoT ──(HTTP)──> etl/collect.py ──> MinIO bucket "bronze"  (1 objet JSON / mesure)
                                              │        + hash SHA-256 dupliqué dans "audit" (WORM)
                                              ▼
                              etl/quality.py ──> "silver" (Parquet nettoyé)
                              rejets ──────────> "quarantine"

                              etl/quality.py --gold-only ──> "gold" (agrégats daily / hourly)
                              (à l'heure : partitions modifiées ; au jour : rattrapage veille)
```

Le gold est recalculé sur son propre planning, pas à chaque collecte : un recalcul relit
tout le silver de la partition, et le faire chaque minute faisait déborder le cycle de
collecte (silver alimenté toutes les 2 min au lieu d'une). Les partitions touchées sont
empilées dans `manifests/gold_pending.json` et reprises par `--gold-only`. Fréquences
réglables via `GOLD_CRON_HORAIRE` / `GOLD_CRON_QUOTIDIEN` ; le rattrapage initial
(`etl-bootstrap`) les consolide de la même façon, en un seul passage à la fin du drainage.

## Démarrage local

Prérequis : Docker Desktop en cours d'exécution.

```bash
cp .env.example .env          # valeurs de dev déjà utilisables telles quelles
docker compose up -d --build  # cœur : postgres, minio, api, front
```

| | URL |
|---|---|
| Front | http://localhost:3000 |
| API (OpenAPI) | http://localhost:8000/docs |
| Console MinIO | http://localhost:9001 |
| PostgreSQL | `localhost:5433` |

Services optionnels, derrière des profils Compose :

```bash
docker compose --profile etl up -d --build                        # + pipeline ETL
docker compose --profile etl --profile audit --profile proxy up -d --build   # tout
```

Créer le premier compte admin (aucune route publique de création) :

```bash
docker compose exec api python -m api.create_admin --email admin@enervision.fr
```

## Configuration

Toute la configuration passe par un seul fichier `.env` à la racine
(voir [`.env.example`](.env.example)). Le front a en plus un
[`front/.env.example`](front/.env.example) pour les variables `VITE_*`.

En Compose, le service `api` force `POSTGRES_HOST=postgres` / `POSTGRES_PORT=5432` ;
les valeurs `localhost:5433` du `.env` ne servent qu'à lancer l'API hors Docker.

## Tests

```bash
# API
pip install -r api/requirements-dev.txt
pytest api/tests

# ETL
pip install -r etl/requirements-dev.txt
cd etl && pytest

# Front
cd front && npm install && npm test
```

Les `requirements.txt` ne contiennent que le runtime (ce qui est installé dans les
images) ; pytest & co. sont dans les `requirements-dev.txt`.

## Qualité de code

`pre-commit` (ruff lint + format sur `api/` et `etl/`, normalisation des fins de
ligne) :

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Déploiement

CI/CD GitHub Actions sur push `dev` : voir [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md).

## Pistes connues (non traitées)

- **Migrations DB** : le schéma vit dans [`infra/postgres/init/`](infra/postgres/init/)
  (fait foi), les modèles SQLAlchemy ne couvrent que `users`. Introduire Alembic
  quand le modèle se stabilise.
- **Endpoints `sites`** : `etl/collect.py` appelle `/api/v1/sites` et
  `/api/v1/sites/{id}/current`, pas encore implémentés côté API. `etl/sites.py`
  est un stub (service `etl-sites` en `restart: "no"` en attendant).
- **Tests front dans la CI** : `npm test` (Vitest) existe et passe en local, mais `.github/workflows/deploy-dev.yml` ne fait encore qu'un `npm run build` — l'ajouter au job `build-front`.
