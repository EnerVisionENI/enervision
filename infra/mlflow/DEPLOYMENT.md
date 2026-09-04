# Déploiement MLflow serveur

## Architecture

MLflow en prod = service `mlflow` dans le `compose.yaml` racine (profile `"mlflow"`), pas un
compose séparé — cohérent avec le reste du projet ("un unique `compose.yaml`", voir
`infra/DEPLOYMENT.md`). Réutilise l'infra existante, pas de nouveau conteneur de base de données :

1. **PostgreSQL** (service `postgres` existant) — nouvelle base `mlflow` dans la même instance.
2. **MLflow server** (nouveau service `mlflow`) — UI + API REST, image `ghcr.io/mlflow/mlflow:v2.22.0`.
3. **MinIO** (service `minio` existant) — bucket `mlflow-artifacts` (ajouté à
   `infra/minio/init-buckets.sh`, créé automatiquement par `minio-init`).

## Mise en place (serveur de déploiement 10.105.200.44)

### 1. Créer la base Postgres `mlflow` (une seule fois, à la main)

Les scripts d'init Postgres (`infra/postgres/init/*.sql`) ne rejouent **pas** sur un volume déjà
initialisé (`infra_pgdata` est réutilisé depuis l'ancien projet Compose) — donc pas d'automatisation
possible via ce mécanisme. Une seule commande à lancer sur le serveur, une seule fois :

```bash
docker compose exec postgres psql -U ev_admin -d ev_monitoring -c "CREATE DATABASE mlflow;"
```

### 2. Déploiement

Aucune commande spéciale : le service `mlflow` est dans le `compose.yaml` racine, profile
`"mlflow"`. Le pipeline CI/CD (`deploy-dev.yml`) l'inclut déjà :

```bash
docker compose --profile etl --profile audit --profile proxy --profile mlflow up -d --build
```

En local/manuel :

```bash
docker compose --profile mlflow up -d --build mlflow
```

Aucun nouveau secret nécessaire : le service réutilise `POSTGRES_USER`/`POSTGRES_PASSWORD` et
`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` déjà présents dans `.env`.

Une fois up, MLflow UI est accessible à :
```
http://10.105.200.44:5000
```

### 3. Configuration clients (ml/*, api/*, front/*)

Les clients parlent à MLflow via `MLFLOW_TRACKING_URI` (voir `ml/.env`) :

```bash
# En local (dev) — sqlite, pas de dépendance à un serveur
MLFLOW_TRACKING_URI=sqlite:///mlflow.db

# En prod (une fois le profile "mlflow" déployé)
MLFLOW_TRACKING_URI=http://10.105.200.44:5000
```

### 4. Pérennité des données

- **Métadonnées runs/modèles** → base Postgres `mlflow`, dans le volume `infra_pgdata` existant
  (même politique de backup que le reste).
- **Artifacts (modèles sérialisés)** → bucket MinIO `mlflow-artifacts`, synchronisé vers Azure
  comme les autres buckets via `audit-sync` si ce bucket est ajouté à sa configuration (à vérifier —
  pas fait par défaut, `audit-sync` cible `bronze/silver/gold/audit`).

## Monitoring

```bash
docker compose --profile mlflow logs -f mlflow
docker compose --profile mlflow ps
curl -I http://10.105.200.44:5000
```

## Rollback

```bash
docker compose --profile mlflow down
# Supprimer la base mlflow (DESTRUCTIF, perd l'historique des runs) :
docker compose exec postgres psql -U ev_admin -d ev_monitoring -c "DROP DATABASE mlflow;"
```

## Notes

- Image épinglée `v2.22.0` (pas `latest`) — **MLflow 3.x casse le serveur UI sur Windows en local**
  (500 sur tous les endpoints), pas testé sur l'image Linux du serveur mais on reste sur la 2.x
  pour rester cohérent entre dev et prod.
- Pas de proxy/TLS ici — réseau interne (10.105.200.44). Ajouter une route Traefik si accès externe
  souhaité un jour (profile `"proxy"`, déjà dans la stack).
