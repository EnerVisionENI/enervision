# EV-006 — Service PostgreSQL (Docker)

## Contenu
- `init.sql` : schéma (sites, alerts, sensors_status, users), idempotent (IF NOT EXISTS)
- `docker-compose.yml` : service PostgreSQL 16 (alpine)
- `.env.example` : modèle de variables d'environnement

## Déploiement

### 1. Configurer les identifiants
\`\`\`bash
cp .env.example .env
nano .env   # changer POSTGRES_PASSWORD
\`\`\`

### 2. Lancer
\`\`\`bash
docker compose up -d
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