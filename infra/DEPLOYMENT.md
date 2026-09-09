# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions unique dans `.github/workflows/ci-cd.yml`.

- Sur chaque pull request vers `dev` ou `main` : lint Python de tout le dépôt (ruff), tests API, tests ETL, tests
  du script de sauvegarde, build + tests + lint du frontend, puis le job `build` (construction des 4 images,
  scan de sécurité Trivy, publication sur GHCR), puis tests end-to-end sur une stack éphémère jetable
  (voir `compose.ci.yml`).
- Sur chaque push vers `dev` (donc aussi après un merge de PR) : la même chaîne de vérifications, puis un
  déploiement automatique sur le serveur si — et seulement si — les tests end-to-end sont passés.
- Rescan de sécurité Trivy quotidien sur `main` (CVE publiées depuis le dernier build, sans rebuild de code).

### Registry d'images (GHCR)

Les images sont construites **une seule fois par run**, dans le job `build`, et publiées sur
GitHub Container Registry :

| Image | Référence |
|-------|-----------|
| API   | `ghcr.io/enervisionani/enervision-api` |
| Front | `ghcr.io/enervisionani/enervision-front` |
| ETL   | `ghcr.io/enervisionani/enervision-etl` |
| ML    | `ghcr.io/enervisionani/enervision-ml` |

Chaque image porte un tag immuable `sha-<commit>`, plus un tag mouvant `dev` / `main` sur push de branche.
`test-e2e` et `deploy` font un `docker pull` du tag `sha-<commit>` du run : ils ne reconstruisent plus rien.

Deux raisons à ce découpage :

1. **Le scan Trivy et les tests e2e portent enfin sur l'artefact déployé.** Avant, chaque job rebuildait ses
   propres images : celle scannée, celle testée et celle mise en prod étaient trois builds distincts du même
   Dockerfile, et une base image ou une dépendance transitive peut bouger entre deux builds.
2. **Le déploiement ne compile plus sur le serveur de prod.** Le job `deploy` se réduit à un `pull` + un `up`.

L'ordre est **build → scan → push** : une image qui échoue au scan `CRITICAL/HIGH` n'atteint jamais la
registry. L'authentification utilise le `GITHUB_TOKEN` du run (permission `packages: write` sur le seul job
`build`) — aucun PAT à créer ni à faire tourner.

Les images sont **privées** par défaut, héritant de la visibilité du dépôt. Pour un `docker pull` manuel
depuis un poste :

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <votre-login> --password-stdin
docker pull ghcr.io/enervisionani/enervision-api:dev
```

(le token doit porter le scope `read:packages`).

#### Rollback

C'est le principal gain opérationnel : revenir à une version précédente ne demande plus de rebuild, seulement
de repointer les tags. Sur le serveur, à la racine du dépôt :

```bash
export API_IMAGE=ghcr.io/enervisionani/enervision-api:sha-<commit-connu-bon>
export FRONT_IMAGE=ghcr.io/enervisionani/enervision-front:sha-<commit-connu-bon>
export ETL_IMAGE=ghcr.io/enervisionani/enervision-etl:sha-<commit-connu-bon>
docker compose --profile etl --profile audit --profile proxy --profile mlflow pull
docker compose --profile etl --profile audit --profile proxy --profile mlflow up -d --no-build
```

Ces trois variables sont celles que `compose.yaml` interpole dans les champs `image:` des services buildés.
Non définies, elles retombent sur des tags locaux (`enervision-api:local`, …), donc **le workflow de dev local
ne change pas** : `docker compose up -d --build` continue de builder depuis les sources.

### Runner self-hosted

Le serveur de déploiement (`10.105.200.44`) n'est joignable que depuis le réseau interne : le job `deploy`
tourne donc sur un **runner GitHub Actions self-hosted installé directement sur ce serveur**, plutôt que sur
un runner hébergé (`ubuntu-latest`) qui ne pourrait pas l'atteindre en SSH. Le job fait un `actions/checkout`,
assemble le fichier `.env` de prod (racine) à partir de secrets GitHub Actions, se connecte à GHCR, puis lance
`docker compose --profile etl --profile audit --profile proxy --profile mlflow pull` suivi de
`… up -d --no-build --remove-orphans`
(ou `docker-compose` si le plugin `docker compose` v2 n'est pas installé). Le `pull` est séparé du `up` pour
que la stack en cours reste intacte si la registry est injoignable.

Toute la stack est décrite dans un unique `compose.yaml` à la racine du dépôt. Les services optionnels sont
derrière des profils Compose :

| Profil | Services | |
|--------|----------|---|
| _(aucun)_ | `postgres`, `minio`, `minio-init`, `api`, `front` | toujours démarrés |
| `etl`   | `etl-collect`, `etl-alerts` | collecte temps réel des mesures + des alertes |
| `audit` | `audit-sync`  | synchro MinIO → Azure Blob |
| `observability` | `prometheus`, `grafana`, `node-exporter`, `cadvisor` | métriques serveur (hôte + conteneurs) |
| `mlflow` | `mlflow`     | tracking/registry des modèles ML (voir `infra/mlflow/DEPLOYMENT.md`) |

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

Le `.env` que ce script doit charger est le **`.env` racine complet** (celui que le job `deploy` régénère à
chaque déploiement, `infra/env/production.env` + les 6 secrets GitHub Actions ci-dessus, `POSTGRES_PASSWORD`
compris) — pas `infra/env/production.env` seul, qui ne contient que la partie non sensible et ne suffit pas à
faire tourner le script (`KeyError: 'POSTGRES_PASSWORD'` sinon). C'est exactement le même fichier que les
services `env_file: - .env` de `compose.yaml` (`api`, `etl-*`, `audit-sync`) lisent déjà pour tourner : il
vit sur le disque du serveur, dans le dossier où le runner self-hosted a cloné le dépôt et où `docker compose
up` est lancé — pas seulement le temps du job GitHub Actions.

Pour trouver ce dossier sans le deviner (utile aussi pour un test manuel, en dehors de tout cron) :

```bash
docker inspect enervision-postgres --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
```

**Crontab** (`crontab -e`, sur l'utilisateur qui a accès au dépôt et à Docker) — `<dossier_du_depot>` est le
résultat de la commande ci-dessus :

```cron
0 3 * * * cd <dossier_du_depot> && set -a && . .env && set +a && python3 infra/backup/backup.py >> /var/log/enervision-backup.log 2>&1
```

Une fois par jour à 3h UTC. `set -a` exporte automatiquement toutes les variables lues depuis `.env` vers
l'environnement du script (équivalent de `source` avec export) — c'est l'équivalent, à la main, de ce que
`env_file:` fait pour Docker Compose.

> Si un jour le serveur devient joignable depuis Internet (VPN site-to-site, IP publique, etc.), on peut
> repasser le job `deploy` sur `ubuntu-latest` avec une connexion SSH classique (secrets `DEPLOY_HOST`,
> `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_PORT`, `DEPLOY_PATH`).

### Réentraînement mensuel des modèles de prévision (profile `mlflow`, service `ml`)

`ml/train.py` **est** un service `compose.yaml` (profile `mlflow`, comme `mlflow` et `ml-predict`) — il a
besoin de l'image `enervision-ml:local` (dépendances lourdes : LightGBM, statsmodels, pandas) et du réseau
Docker interne pour joindre `minio:9000` et `mlflow:5000`. Il n'est jamais démarré avec `up -d` :
`ml/Dockerfile` lance `python train.py` une fois puis le conteneur se termine (`restart: "no"`).

Sans `--site`, `train.py` détecte et réentraîne **tous** les sites présents dans le gold (pas de liste codée
en dur, voir `ml/core/data.py::list_available_sites`). Les fenêtres train/calib/test glissent automatiquement
par rapport à la date du run (90/7/7 jours par défaut, réglable via `TRAIN_WINDOW_DAYS`/`CALIB_WINDOW_DAYS`/
`TEST_WINDOW_DAYS` dans `.env`) — sans ça, un réentraînement mensuel répéterait indéfiniment sur la même
fenêtre figée. Chaque site dont le MASE passe sous le seuil (`MASE_PROMOTION_THRESHOLD`) obtient une nouvelle
version dans le Model Registry MLflow (stage `Staging`, jamais `Production` automatiquement).

**Déclenchement manuel** (ce qui existe aujourd'hui) :
```bash
docker compose --profile mlflow run --rm ml
```
`docker compose run --rm` reconstruit l'image si le code de `ml/` a changé depuis le dernier run, exécute
`train.py`, puis supprime le conteneur (`--rm`) — rien ne traîne entre deux réentraînements.

**Pas encore automatisé** — volontairement, tant que le gold n'a pas assez de volume (TOWT est calendaire,
`C(time_of_week)` sur 168 créneaux : `train` seul a besoin d'au moins 7 jours pleins pour les couvrir, voir
`ml/README.md`). Une fois le volume suffisant, la planification se fera par un **scheduler Docker-natif
déclaré dans `compose.yaml`** (ex. `ofelia`, cf. le commentaire au-dessus du service `ml`) plutôt qu'une
crontab système à installer à la main sur le serveur — `docker compose up` suffira alors à tout activer, sans
étape manuelle oubliable.

> Avec peu d'historique réel (premiers mois de collecte), le jeu `calib` ou `test` peut être vide pour
> certains sites — `train.py` le journalise site par site (`⚠️ <site> : échec du réentraînement — ...`) sans
> bloquer les autres, exit code global 0 tant qu'au moins un site est détecté dans le gold (échec silencieux
> site par site, pas au niveau du script). Seule l'absence totale de site détecté fait sortir en erreur. Un
> site en échec récurrent n'a simplement pas encore assez de gold, ce n'est pas une panne du script.

### Observabilité — métriques serveur (profile `observability`)

Quatre services, tous optionnels et sans impact sur l'application :

| Service | Image | Rôle | Port hôte |
|---|---|---|---|
| `node-exporter` | `prom/node-exporter` | métriques de l'hôte : CPU, RAM, disque, réseau, load | 9100 |
| `cadvisor` | `gcr.io/cadvisor/cadvisor` | métriques par conteneur : CPU, mémoire, réseau | 8081 |
| `prometheus` | `prom/prometheus` | scrape les deux exporters, rétention 15 j (volume `promdata`) | 9090 |
| `grafana` | `grafana/grafana` | dashboards (volume `grafanadata`) | 3001 |

```bash
docker compose --profile observability up -d
```

Grafana est **provisionné automatiquement** au premier démarrage depuis
[`infra/grafana/`](grafana/) : la datasource Prometheus et le dashboard
*EnerVision — Serveur (hôte & conteneurs)* apparaissent sans aucune action
manuelle. Les fichiers montés en lecture seule sont la source de vérité — éditer
un dashboard dans l'UI ne le persiste pas, il faut modifier le JSON dans
[`infra/grafana/dashboards/`](grafana/dashboards/).

- Grafana : http://localhost:3001 — identifiants `GRAFANA_ADMIN_USER` /
  `GRAFANA_ADMIN_PASSWORD` (défaut `admin` / `admin` si non définis).
- Prometheus : http://localhost:9090 (cibles sous *Status → Targets*).

La config de scrape est dans [`infra/prometheus/prometheus.yml`](prometheus/prometheus.yml).
Pour ajouter des métriques applicatives plus tard (API FastAPI, PostgreSQL via
`postgres-exporter`, …), déclarer le service dans `compose.yaml` avec
`profiles: ["observability"]` et ajouter un bloc `scrape_configs`.

**Prérequis hôte pour les métriques par conteneur (cAdvisor).** Sur Docker
Engine ≥ 28, deux points bloquent cAdvisor et laissent la row *Conteneurs* du
dashboard vide (les métriques hôte via node-exporter, elles, continuent) :

1. **API Docker.** Engine ≥ 28 refuse l'API 1.41 que cAdvisor 0.49 code en dur
   (`client version 1.41 is too old. Minimum supported API version is 1.44`) →
   son *docker factory* ne s'enregistre pas, les séries `container_*` sortent
   sans label `name`. Corrigé par l'image **`cadvisor:v0.52.1`** (≥ 0.50
   négocie l'API) déjà épinglée dans `compose.yaml`. `DOCKER_API_VERSION` en
   variable d'env **ne suffit pas** (cAdvisor l'ignore).
2. **Storage driver.** cAdvisor ne lit les couches d'images que via `overlay2`.
   Si `docker info` affiche `Storage Driver: overlayfs` (= *containerd
   snapshotter*, défaut d'Engine ≥ 28), il enregistre le *docker factory* mais
   **jette chaque conteneur** (`failed to identify the read-write layer ID …
   image/overlayfs/layerdb/…: no such file or directory`). Repasser le démon en
   `overlay2` :

```bash
# /etc/docker/daemon.json — ajouter (ou fusionner avec l'existant) :
{ "features": { "containerd-snapshotter": false } }

sudo systemctl restart docker
docker info --format '{{.Driver}}'      # doit afficher: overlay2
```

`systemctl restart docker` arrête les conteneurs le temps du redémarrage, et les
images du *containerd store* ne sont plus visibles par le store `overlay2` : le
`docker compose … up -d --build` suivant les reconstruit / re-télécharge. À faire
pendant une fenêtre de maintenance, pipeline CI au repos. Une fois les deux
points réglés, `container_*{name="enervision-api"}` etc. se peuplent
automatiquement.

**Activer en production (CI/CD).** Le profile `observability` n'est pas déployé
par défaut. Pour l'ajouter :

1. Créer le secret GitHub Actions `GRAFANA_ADMIN_PASSWORD` (`gh secret set
   GRAFANA_ADMIN_PASSWORD`), au même titre que les 6 autres du tableau plus haut.
2. Dans [`.github/workflows/ci-cd.yml`](../.github/workflows/ci-cd.yml), ajouter
   `GRAFANA_ADMIN_PASSWORD: ${{ secrets.GRAFANA_ADMIN_PASSWORD }}` au step
   *Restore production env file* du job `deploy` et l'écho correspondant dans le
   `.env` reconstruit.
3. Ajouter `--profile observability` à la commande `docker compose … up` du step
   *Deploy with docker compose*.
4. Ne pas exposer les ports 9090 / 3001 / 8081 / 9100 sur Internet — soit les
   laisser sur le réseau interne uniquement, soit les passer derrière un reverse
   proxy avec authentification.

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

# Stack complète (ETL + synchro audit)
docker compose --profile etl --profile audit up -d --build

# Visualiser les logs
docker compose logs -f front
docker compose logs -f api
```

### 4. Accéder à l'application
```
Frontend:  http://localhost:3000
API:       http://localhost:8000
```

## Commandes utiles

Toutes depuis la racine du dépôt.

### Arrêter les services
```bash
docker compose --profile etl --profile audit down
```

### Redémarrer un service
```bash
docker compose restart front
docker compose restart api
```

### Supprimer tout (volumes inclus — DESTRUCTIF)
```bash
docker compose --profile etl --profile audit down -v
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
├── prometheus/           # Config de scrape Prometheus (profil "observability")
└── grafana/              # Datasource + dashboards provisionnés (profil "observability")

api/Dockerfile            # Image API (contexte de build = racine)
front/Dockerfile          # Build Vue + Nginx (contexte de build = racine)
etl/Dockerfile            # Image ETL (contexte de build = etl/)
```

## Notes de production

1. **HTTPS** : Configurer un reverse proxy TLS (certificats Let's Encrypt) en amont
2. **Variables d'env** : Utiliser des fichiers `.env`
3. **Logs** : Configurer ELK ou autre solution de logging
4. **Backup** : `users`/`sites` couverts par `infra/backup/backup.py` (EV-040, cron quotidien) ; le reste
   du schéma (`alerts`, `measurements_silver`, `aggregates_gold_*`, ...) n'a pas encore de sauvegarde dédiée
5. **Monitoring** : profile `observability` (Prometheus + Grafana + node-exporter + cAdvisor) — voir ci-dessous
