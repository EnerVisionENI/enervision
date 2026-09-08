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

Le service réutilise `POSTGRES_USER`/`POSTGRES_PASSWORD` et `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`
déjà présents dans `.env`. Un seul secret à définir spécifiquement : `MLFLOW_ADMIN_USER` /
`MLFLOW_ADMIN_PASSWORD` (authentification, voir section suivante).

Une fois up, MLflow UI est accessible à :
```
http://10.105.200.44:5000
```
— une fenêtre de login s'affiche désormais (EV-064, voir plus bas).

### 3. Authentification (EV-064)

Le serveur tourne avec le plugin natif `--app-name basic-auth` de MLflow : sans ça, l'UI **et**
l'API sont ouvertes en écriture à quiconque atteint le port 5000, suppression de modèle du
Model Registry incluse (contrairement aux runs, `delete_registered_model` n'a pas de corbeille).

- Compte admin bootstrap : `MLFLOW_ADMIN_USER` / `MLFLOW_ADMIN_PASSWORD` (`.env`). Le fichier de
  config auth (`/etc/mlflow/basic_auth.ini`) est généré au démarrage du conteneur, jamais versionné.
- La table des comptes vit dans la base Postgres `mlflow` (même base que le tracking) — persiste
  aux redéploiements, contrairement à un SQLite local au conteneur.
- `default_permission = READ` : par défaut, un compte authentifié peut lire mais pas écrire — seul
  l'admin (ou un compte explicitement autorisé) peut enregistrer/promouvoir un modèle.

### 4. Configuration clients (ml/*, api/*, front/*)

Les clients parlent à MLflow via `MLFLOW_TRACKING_URI` (voir `ml/.env`) :

```bash
# En local (dev) — sqlite, pas de dépendance à un serveur, pas d'auth
MLFLOW_TRACKING_URI=sqlite:///mlflow.db

# En prod (une fois le profile "mlflow" déployé) — auth requise
MLFLOW_TRACKING_URI=http://10.105.200.44:5000
MLFLOW_TRACKING_USERNAME=admin
MLFLOW_TRACKING_PASSWORD=<MLFLOW_ADMIN_PASSWORD>
```

`MLFLOW_TRACKING_USERNAME`/`MLFLOW_TRACKING_PASSWORD` sont lues automatiquement par le client
Python `mlflow` (convention native de la librairie) : aucun code à changer dans `ml/`, seulement
les définir dans l'environnement avant de lancer un script qui cible le serveur prod. Sans elles,
n'importe quel appel (`train_v1_csv.py`, `predict.py`...) échoue avec une 401.

### 5. Pérennité des données

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
