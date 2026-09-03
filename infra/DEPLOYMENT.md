# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions dans `.github/workflows/deploy-dev.yml`.

- Sur chaque pull request vers `dev`, la pipeline lance les tests API et le build frontend.
- Sur chaque push vers `dev` (donc aussi après un merge de PR), la pipeline déploie automatiquement sur le serveur.

Le serveur de déploiement (`10.105.200.44`) n'est joignable que depuis le réseau interne : le job `deploy`
tourne donc sur un **runner GitHub Actions self-hosted installé directement sur ce serveur**, plutôt que sur
un runner hébergé (`ubuntu-latest`) qui ne pourrait pas l'atteindre en SSH. Le job fait un `actions/checkout`,
assemble le fichier `.env` de prod (racine) en concaténant les fichiers de secrets stockés dans un dossier
stable, puis lance `docker compose --profile etl --profile audit --profile proxy up -d --build`
(ou `docker-compose` si le plugin `docker compose` v2 n'est pas installé).

Toute la stack est décrite dans un unique `compose.yaml` à la racine du dépôt. Les services optionnels sont
derrière des profils Compose :

| Profil | Services | |
|--------|----------|---|
| _(aucun)_ | `postgres`, `minio`, `minio-init`, `api`, `front` | toujours démarrés |
| `etl`   | `etl-collect`, `etl-alerts`, `etl-sites` | collecte temps réel mesures + alertes + sites |
| `audit` | `audit-sync`  | synchro MinIO → Azure Blob |
| `proxy` | `traefik`     | reverse proxy TLS |

En local : `docker compose up -d --build` suffit pour le cœur de la stack ; ajouter `--profile etl` au besoin.

### Collecte de l'ETL (`etl-collect`)

Collecte **100 % temps réel**, aucun rejeu d'historique. À chaque cycle (`INTERVALLE_SECONDES`, défaut 60 s) :

1. `etl-collect` interroge l'API pour les 7 sites et dépose un objet JSON par mesure dans `bronze` (+ hash SHA-256 dans `audit`) ;
2. dans la foulée, `quality.py` promeut les nouveaux objets bronze → `silver` (Parquet), les rejets structurels → `quarantine` ;
3. le gold n'est pas recalculé ici : les partitions touchées sont empilées dans `manifests/gold_pending.json` et reprises par le planning `GOLD_CRON_HORAIRE` (`quality.py --gold-only`), plus une consolidation complète de la veille via `GOLD_CRON_QUOTIDIEN`.

`etl-collect` ne dépend que de `minio` / `minio-init` / `postgres` : `docker compose up -d` rend la main dès que la collecte est lancée. La donnée commence au premier cycle — il n'y a pas d'antériorité.

### Installer le runner self-hosted sur le serveur

1. Sur GitHub : **Settings > Actions > Runners > New self-hosted runner**, choisir Linux.
2. Suivre les commandes affichées (elles contiennent un token à usage unique, à copier depuis la page) pour
   télécharger et extraire le runner sur `10.105.200.44`.
3. Configurer le runner **avec un utilisateur non-root** dédié (`config.sh` refuse de s'exécuter en root) :
   membre du groupe `docker`, propriétaire du dossier du runner.
4. Installer et démarrer le service depuis ce dossier : `./svc.sh install <user> && ./svc.sh start`.
5. Vérifier que le runner apparaît "Idle" dans la liste des runners du repo.

Aucun secret SSH n'est nécessaire avec cette approche (plus de `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY`
/ `DEPLOY_PORT` / `DEPLOY_PATH`) : le job s'exécute déjà sur la machine cible.

### Préparation du serveur

Le serveur (et donc le runner) doit avoir :

1. Docker et Docker Compose installés (`docker compose` v2 ou, à défaut, `docker-compose` v1), avec
   l'utilisateur du runner membre du groupe `docker`.
2. Les fichiers `.env` de production stockés **en dehors du dossier de travail du runner** (`_work/...`) :
   ce dossier est recréé par `actions/checkout` au tout premier run (il vide le contenu existant avant de
   cloner, même avec `clean: false`, qui ne protège que les runs suivants une fois un `.git` déjà en place).
   Les stocker par exemple dans `/opt/enervision-secrets/` (lisible uniquement par l'utilisateur du runner).
   Le step *Restore production env file* du workflow concatène ces fichiers en un seul `.env` à la racine
   du dépôt, avant `docker compose up` :
   - `postgres.env` — `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
   - `api.env` — `JWT_SECRET_KEY`, `INGEST_API_KEY`, `CORS_ORIGINS`, … (voir `.env.example`)
   - `minio.env` — `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
   - `audit-sync.env` — variables `AZURE_STORAGE_*`, `RCLONE_CRYPT_PASSWORD_RAW`, `SYNC_INTERVAL_SECONDES`
   - `etl.env` — surcharges ETL éventuelles (`API_BASE`, `INTERVALLE_SECONDES`,
     `GOLD_CRON_HORAIRE`, `GOLD_CRON_QUOTIDIEN`) ; peut être vide

   Les clés en double entre fichiers (`POSTGRES_*`) doivent porter les mêmes valeurs.

Première mise en place (sur le serveur) :

```bash
mkdir -p /opt/enervision-secrets
nano /opt/enervision-secrets/postgres.env    # POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
nano /opt/enervision-secrets/api.env         # JWT_SECRET_KEY / INGEST_API_KEY / CORS_ORIGINS ...
nano /opt/enervision-secrets/minio.env       # MINIO_ROOT_USER / MINIO_ROOT_PASSWORD
nano /opt/enervision-secrets/audit-sync.env  # AZURE_STORAGE_* / RCLONE_CRYPT_PASSWORD_RAW
touch /opt/enervision-secrets/etl.env        # vide, sauf surcharge ETL
chown -R <user_runner>:<user_runner> /opt/enervision-secrets
chmod 700 /opt/enervision-secrets
chmod 600 /opt/enervision-secrets/*.env
```

> Les volumes `infra_pgdata` / `infra_miniodata` créés par l'ancien projet Compose `infra` sont réutilisés
> tels quels : `compose.yaml` épingle ces noms, donc aucune migration de données n'est nécessaire.

Ensuite, chaque push sur `dev` refera automatiquement le `checkout`, l'assemblage du `.env`, puis
`docker compose --profile etl --profile audit --profile proxy up -d --build`.

> Si un jour le serveur devient joignable depuis Internet (VPN site-to-site, IP publique, etc.), on peut
> repasser le job `deploy` sur `ubuntu-latest` avec une connexion SSH classique (secrets `DEPLOY_HOST`,
> `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_PORT`, `DEPLOY_PATH`).

## Commandes de déploiement

### 1. Dépendances système
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y docker.io docker-compose

# Ou installer Docker Desktop (Windows/Mac)
```

### 2. Préparer le serveur
```bash
# Aller au dossier du projet
cd /path/to/enervision

# Donner les permissions correctes
sudo chown -R $USER:$USER .

# Créer le .env racine (voir .env.example)
cp .env.example .env && nano .env
```

### 3. Construire et démarrer les services
```bash
# Depuis la racine du dépôt (compose.yaml y est détecté automatiquement)

# Cœur de la stack
docker compose up -d --build

# Stack complète (ETL + synchro audit + proxy)
docker compose --profile etl --profile audit --profile proxy up -d --build

# Visualiser les logs
docker compose logs -f front
docker compose logs -f api
```

### 4. Accéder à l'application
```
Frontend:  http://localhost:3000
API:       http://localhost:8000
Traefik:   http://localhost:8080
```

## Commandes utiles

Toutes depuis la racine du dépôt.

### Arrêter les services
```bash
docker compose --profile etl --profile audit --profile proxy down
```

### Redémarrer un service
```bash
docker compose restart front
docker compose restart api
```

### Supprimer tout (volumes inclus — DESTRUCTIF)
```bash
docker compose --profile etl --profile audit --profile proxy down -v
```

### Vérifier les services
```bash
docker compose ps
docker compose logs
```

### Rebuild sans cache
```bash
docker compose build --no-cache
docker compose up -d
```

## Configuration Nginx

La configuration Nginx est dans `nginx.conf` avec :
- ✅ Vue Router fallback (important pour SPAs)
- ✅ Compression Gzip
- ✅ Cache des assets statiques (30 jours)
- ✅ Proxy vers l'API backend
- ✅ Health check

## Structure des fichiers
```
compose.yaml              # Orchestration de TOUTE la stack (racine)
.env / .env.example       # Configuration unique de la stack (racine)

infra/
├── nginx.conf            # Config serveur web (copiée dans l'image front)
├── postgres/init/        # Schéma (rejoué dans l'ordre alphabétique)
├── minio/init-buckets.sh # Création des buckets
├── audit-sync/           # Script de synchro Azure
└── traefik/              # Reverse proxy (profil "proxy")

api/Dockerfile            # Image API (contexte de build = racine)
front/Dockerfile          # Build Vue + Nginx (contexte de build = racine)
etl/Dockerfile            # Image ETL (contexte de build = etl/)
```

## Notes de production

1. **HTTPS** : Configurer certains et Traefik pour SSL
2. **Variables d'env** : Utiliser des fichiers `.env`
3. **Logs** : Configurer ELK ou autre solution de logging
4. **Backup** : Mettre en place une stratégie de backup
5. **Monitoring** : Ajouter Prometheus/Grafana
