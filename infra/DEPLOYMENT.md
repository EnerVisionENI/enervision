# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions unique dans `.github/workflows/ci-cd.yml`.

- Sur chaque pull request vers `dev`, la pipeline lance les tests API et le build frontend.
- Sur chaque push vers `dev` (donc aussi après un merge de PR), la pipeline déploie automatiquement sur le serveur.

Le serveur de déploiement (`10.105.200.44`) n'est joignable que depuis le réseau interne : le job `deploy`
tourne donc sur un **runner GitHub Actions self-hosted installé directement sur ce serveur**, plutôt que sur
un runner hébergé (`ubuntu-latest`) qui ne pourrait pas l'atteindre en SSH. Le job fait un `actions/checkout`,
assemble le fichier `.env` de prod (racine) à partir de secrets GitHub Actions, puis lance
`docker compose --profile etl --profile audit --profile proxy up -d --build`
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

Le serveur (et donc le runner) doit avoir Docker et Docker Compose installés (`docker compose` v2 ou, à
défaut, `docker-compose` v1), avec l'utilisateur du runner membre du groupe `docker`.

Aucun fichier de secrets n'est stocké sur le disque du serveur. Le `.env` de prod est reconstruit à chaque
déploiement par le step *Restore production env file* du workflow, à partir de deux sources :

1. **`infra/env/production.env`** (versionné, non sensible) : identifiants non secrets (`POSTGRES_USER`,
   `POSTGRES_DB`, `MINIO_ROOT_USER`, …), `CORS_ORIGINS`, noms de containers Azure, surcharges ETL. À adapter
   directement dans le repo (PR classique) — voir les valeurs à compléter en tête du fichier.
2. **Secrets GitHub Actions** (Settings du dépôt > Secrets and variables > Actions > *New repository
   secret*) : uniquement les mots de passe et clés, un secret par valeur :

   | Secret GitHub                | Anciennement (fichier / variable)              |
   |-------------------------------|------------------------------------------------|
   | `POSTGRES_PASSWORD`           | `postgres.env` → `POSTGRES_PASSWORD`            |
   | `JWT_SECRET_KEY`              | `api.env` → `JWT_SECRET_KEY`                    |
   | `INGEST_API_KEY`              | `api.env` → `INGEST_API_KEY`                    |
   | `MINIO_ROOT_PASSWORD`         | `minio.env` → `MINIO_ROOT_PASSWORD`             |
   | `AZURE_STORAGE_KEY`           | `audit-sync.env` → `AZURE_STORAGE_KEY`          |
   | `RCLONE_CRYPT_PASSWORD_RAW`   | `audit-sync.env` → `RCLONE_CRYPT_PASSWORD_RAW`  |

Le step concatène `infra/env/production.env` puis ces 6 secrets pour former le `.env` racine, avant
`docker compose up`.

Première mise en place, depuis un poste avec [`gh`](https://cli.github.com/) authentifié sur le dépôt et un
accès aux vraies valeurs de prod (les valeurs actuelles vivent dans `/opt/enervision-secrets/*.env` sur le
serveur, ou sinon les régénérer) :

```bash
gh secret set POSTGRES_PASSWORD       # colle la valeur, Ctrl-D pour valider
gh secret set JWT_SECRET_KEY
gh secret set INGEST_API_KEY
gh secret set MINIO_ROOT_PASSWORD
gh secret set AZURE_STORAGE_KEY
gh secret set RCLONE_CRYPT_PASSWORD_RAW
```

(ou directement sur GitHub : Settings > Secrets and variables > Actions > *New repository secret*, un
secret par ligne du tableau ci-dessus).

Pense aussi à compléter `infra/env/production.env` (CORS_ORIGINS et les valeurs `AZURE_STORAGE_*` y sont
vides par défaut) et à le committer — sans `CORS_ORIGINS` correct le front ne pourra pas appeler l'API en
prod.

Pour changer un mot de passe (rotation, etc.), il suffit de refaire `gh secret set NOM` avec la nouvelle
valeur — aucun accès SSH au serveur n'est nécessaire, le prochain déploiement reconstruira le `.env` à jour.

> Les volumes `infra_pgdata` / `infra_miniodata` créés par l'ancien projet Compose `infra` sont réutilisés
> tels quels : `compose.yaml` épingle ces noms, donc aucune migration de données n'est nécessaire.

Ensuite, chaque push sur `dev` refera automatiquement le `checkout`, l'assemblage du `.env`, puis
`docker compose --profile etl --profile audit --profile proxy up -d --build`.

### Changements de schéma sur une base existante

Les scripts de `infra/postgres/init/` ne sont rejoués **que sur un volume vide** : sur un serveur déjà
déployé, une colonne ajoutée au schéma doit être passée à la main une fois (il n'y a pas encore d'outil de
migration, cf. *Pistes connues* du README). Pour la gestion des comptes utilisateurs (EV-027) :

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;"
```

Sans cette colonne, l'API renvoie une erreur 500 sur toutes les routes qui lisent un utilisateur.

### Sauvegarde users/sites vers Azure (EV-040)

Contrairement au reste de la stack, `infra/backup/backup.py` n'est **pas** un service `compose.yaml` : c'est
un script autonome planifié par la **crontab système du serveur**, pas par GitHub Actions. Il se connecte à
Postgres via le port exposé sur l'hôte (`localhost:5433`, comme un `psql` lancé à la main), pas via le réseau
Docker interne — donc `docker compose up` n'a pas besoin de tourner pour que ce script fonctionne, seul le
conteneur `postgres` doit être démarré.

Il réutilise le `.env` racine déjà en place, sans variable nouvelle : mêmes `AZURE_STORAGE_ACCOUNT` / `_KEY` /
`_CONTAINER` et `RCLONE_CRYPT_PASSWORD_RAW` que `audit-sync`, et envoie vers les dossiers `users/` et `sites/`
du **même** container Azure que bronze/silver/gold (`enervision-backup`), via le même remote rclone
`azure-crypt` — c'est ce remote, seul, qui chiffre les fichiers à l'envoi (une seule couche de chiffrement,
pas de chiffrement applicatif en plus côté script).

**Dépendances système à installer sur le serveur** (le script tourne hors Docker, ces outils doivent être
présents sur l'hôte) :

```bash
# rclone (déjà présent si le paquet Debian suffit ; sinon voir https://rclone.org/install.sh)
sudo apt install -y rclone

# postgresql-client-16, pour matcher exactement postgres:16-alpine (un client plus ancien
# refuse de dumper un serveur plus récent avec "aborting because of server version mismatch")
sudo apt install -y curl ca-certificates gnupg
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update && sudo apt install -y postgresql-client-16

# gzip est déjà présent sur toute distribution Linux standard.
```

**Crontab** (`crontab -e`, sur l'utilisateur qui a accès au dépôt et à Docker) :

```cron
0 3 * * * cd /opt/enervision && set -a && . .env && set +a && python3 infra/backup/backup.py >> /var/log/enervision-backup.log 2>&1
```

Une fois par jour à 3h UTC. `set -a` exporte automatiquement toutes les variables lues depuis `.env` vers
l'environnement du script (équivalent de `source` avec export).

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
├── env/production.env    # Valeurs de prod NON sensibles (versionné, voir CI/CD ci-dessus)
├── nginx.conf            # Config serveur web (copiée dans l'image front)
├── postgres/init/        # Schéma (rejoué dans l'ordre alphabétique)
├── minio/init-buckets.sh # Création des buckets
├── audit-sync/           # Script de synchro Azure (bronze/silver/gold/audit)
├── backup/               # Sauvegarde chiffrée users/sites vers Azure, via cron (EV-040)
└── traefik/              # Reverse proxy (profil "proxy")

api/Dockerfile            # Image API (contexte de build = racine)
front/Dockerfile          # Build Vue + Nginx (contexte de build = racine)
etl/Dockerfile            # Image ETL (contexte de build = etl/)
```

## Notes de production

1. **HTTPS** : Configurer certains et Traefik pour SSL
2. **Variables d'env** : Utiliser des fichiers `.env`
3. **Logs** : Configurer ELK ou autre solution de logging
4. **Backup** : `users`/`sites` couverts par `infra/backup/backup.py` (EV-040, cron quotidien) ; le reste
   du schéma (`alerts`, `measurements_silver`, `aggregates_gold_*`, ...) n'a pas encore de sauvegarde dédiée
5. **Monitoring** : Ajouter Prometheus/Grafana
