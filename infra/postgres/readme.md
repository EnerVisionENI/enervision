# EV-006 — Service PostgreSQL (Docker)

## Contenu
- `init/` : scripts de schéma, rejoués dans l'ordre alphabétique par l'image Postgres au tout
  premier démarrage (montés sur `/docker-entrypoint-initdb.d`), tous idempotents (`IF NOT EXISTS`) :
  - `01_core.sql` : sites, alerts, sensors_status, users
  - `02_silver.sql` : `measurements_silver` (réplique de la couche silver MinIO, une ligne par mesure)
  - `03_gold.sql` : `aggregates_gold_daily` / `aggregates_gold_hourly` (réplique de la couche gold MinIO)
  - `04_seed_sites.sql` : seed manuel des 7 sites (snapshot de l'API Mock IoT), en attendant
    que `etl/sites.py` (encore un stub) les synchronise automatiquement

Le service `postgres` est défini dans le `compose.yaml` à la racine du dépôt ; les variables
`POSTGRES_*` viennent du `.env` racine (voir `.env.example`). Le peuplement de
`measurements_silver` / `aggregates_gold_*` est fait par `etl/quality.py` (via
`etl/postgres_writer.py`), en plus de l'écriture Parquet sur MinIO — voir `etl/`.

## Déploiement

### 1. Configurer les identifiants
\`\`\`bash
# depuis la racine du dépôt
cp .env.example .env
nano .env   # changer POSTGRES_PASSWORD
\`\`\`

### 2. Lancer
\`\`\`bash
# depuis la racine du dépôt
docker compose up -d postgres
\`\`\`

Les scripts de `init/` sont joués automatiquement, dans l'ordre, à la première initialisation du volume `pgdata`.

### 3. Rejouer les scripts manuellement (volume déjà existant)
\`\`\`bash
# depuis la racine du dépôt
cat infra/postgres/init/*.sql | docker exec -i ev006-postgres psql -U ev_admin -d ev_monitoring
\`\`\`

### 4. Vérifier
\`\`\`bash
docker exec -it ev006-postgres psql -U ev_admin -d ev_monitoring -c "\dt"
\`\`\`

### Connexion externe (DBeaver, etc.)
Le conteneur expose PostgreSQL sur le port hôte **5433** (et non 5432) pour éviter tout conflit avec une instance PostgreSQL déjà installée en local. Connecte-toi avec :
- Host: `localhost`
- Port: `5433`
- Database: `ev_monitoring`
- User / Password: valeurs de `.env`