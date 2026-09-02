# EV-006 — Service PostgreSQL (Docker)

## Contenu
- `init.sql` : schéma (sites, alerts, sensors_status, users), idempotent (IF NOT EXISTS)

Le service `postgres` est défini dans le `compose.yaml` à la racine du dépôt ; les variables
`POSTGRES_*` viennent du `.env` racine (voir `.env.example`).

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

Le script `init.sql` est joué automatiquement à la première initialisation du volume `pgdata`.

### 3. Rejouer init.sql manuellement (volume déjà existant)
\`\`\`bash
docker exec -i ev006-postgres psql -U ev_admin -d ev_monitoring < init.sql
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