# EnerVision

Plateforme de suivi et d'optimisation énergétique : collecte de mesures IoT,
pipeline de qualité de données (bronze → silver → gold), API REST et interface web.

## Architecture

Application multi-services conteneurisée, orchestrée par un unique `compose.yaml`
à la racine.

| Service | Dossier | Rôle | Stack |
|---|---|---|---|
| **api** | [`api/`](api/) | API REST (auth, alertes, sites) | FastAPI, SQLAlchemy, PostgreSQL |
| **front** | [`front/`](front/) | Interface web | Vue 3, Vite, servi par Nginx en prod |
| **etl** | [`etl/`](etl/) | Collecte des mesures + qualité de données | Python, APScheduler, boto3 |
| **postgres** | — | Base de données | PostgreSQL 16 |
| **minio** | — | Stockage objet S3 (bronze/silver/gold/quarantine/audit) | MinIO |
| **audit-sync** | [`infra/audit-sync/`](infra/audit-sync/) | Réplication chiffrée MinIO → Azure Blob | rclone |
| **traefik** | [`infra/traefik/`](infra/traefik/) | Reverse proxy TLS | Traefik v2 |

### Diagrammes

#### Vue d'ensemble des services

```mermaid
flowchart TB
    iot(["API Mock IoT<br/>(externe)"])

    subgraph stack["Stack Docker Compose (compose.yaml)"]
        direction TB

        subgraph always["toujours démarrés"]
            front["front<br/>Vue 3 · Nginx<br/>:3000"]
            api["api<br/>FastAPI<br/>:8000"]
            postgres[("postgres<br/>:5433")]
            minio[("minio<br/>:9000 / :9001")]
        end

        subgraph pETL["profile etl"]
            collect["etl-collect<br/>cycle 60s"]
            alerts["etl-alerts<br/>cycle 300s"]
            sites["etl-sites<br/>(stub, restart: no)"]
        end

        subgraph pAudit["profile audit"]
            auditsync["audit-sync<br/>rclone"]
        end

        subgraph pProxy["profile proxy"]
            traefik["traefik<br/>:80 / :443"]
        end
    end

    azure[("Azure Blob<br/>(externe)")]

    traefik -.->|TLS| front
    traefik -.->|TLS| api
    front -->|"/api/v1 (JWT)"| api
    api --> postgres
    api -->|"GET /sites/{id}/current<br/>(relais direct, sans stockage)"| iot

    collect -->|mesures| iot
    alerts -->|alertes| iot
    sites -.->|"stub : aucun appel réel"| iot

    collect -->|"bronze + audit (JSON)"| minio
    collect -->|"silver / gold (Parquet)"| minio
    collect -->|"silver / gold (SQL)"| postgres
    alerts --> postgres

    minio -->|bucket audit| auditsync
    auditsync -->|chiffré| azure
```

`etl-collect` appelle `quality.py` en in-process juste après chaque cycle de collecte
(voir le diagramme de pipeline plus bas) : c'est pour ça qu'un seul conteneur écrit à la
fois sur `bronze`/`audit` et sur `silver`/`gold`.

#### Flux de données (bronze → silver → gold)

```mermaid
flowchart LR
    iot(["API Mock IoT"]) -->|"GET /sites/{id}/current"| collect["collect.py<br/>(cycle 60s)"]

    collect -->|"1 objet JSON / mesure"| bronze[("bronze<br/>{site}/{date}/{heure}.json")]
    collect -->|"copie du SHA-256"| audit[("audit (WORM)<br/>bronze/{site}/...")]

    bronze --> quality["quality.py<br/>(in-process, après chaque cycle)"]

    quality -->|enregistrement valide| silver[("silver<br/>Parquet, append-only")]
    quality -->|"invalide (JSON corrompu,<br/>champ obligatoire manquant, ...)"| quarantine[("quarantine<br/>1 objet JSON / rejet")]
    silver -->|"agrégats recalculés<br/>par partition record_date/site_id"| gold[("gold<br/>daily / hourly")]

    silver -.->|réplication| pgsilver[("PostgreSQL<br/>measurements_silver")]
    gold -.->|réplication| pggold[("PostgreSQL<br/>aggregates_gold_daily/hourly")]

    quality -->|clés bronze déjà traitées| manifests[("manifests<br/>etl_state.json")]
```

MinIO reste la source de vérité ; les mêmes lignes silver/gold sont répliquées dans
PostgreSQL pour être interrogeables en SQL par l'API. Une panne PostgreSQL n'interrompt
pas l'écriture MinIO.

#### Pipeline ETL — déroulé d'un cycle

```mermaid
sequenceDiagram
    autonumber
    participant Sched as APScheduler (etl-collect)
    participant IoT as API Mock IoT
    participant S3 as MinIO
    participant Q as quality.py (in-process)
    participant PG as PostgreSQL

    loop toutes les 60s
        Sched->>IoT: GET /api/v1/sites
        IoT-->>Sched: liste des site_id
        loop pour chaque site
            Sched->>IoT: GET /sites/{id}/current
            IoT-->>Sched: mesure JSON
            Sched->>S3: put bronze/{site}/{date}/{heure}.json
            Sched->>S3: put audit/bronze/{site}/... (SHA-256)
        end
        Sched->>Sched: marquer_vivant() (heartbeat Docker)
        Sched->>Q: quality.main() (même process, appel direct)
        Q->>S3: liste bronze non traité (manifests/etl_state.json)
        Q->>S3: lit chaque objet bronze nouveau
        alt enregistrement valide
            Q->>S3: put silver/*.parquet
            Q->>PG: upsert measurements_silver
        else invalide
            Q->>S3: put quarantine/*.json
        end
        Q->>S3: recalcule et put gold/daily+hourly.parquet
        Q->>PG: upsert aggregates_gold_daily / aggregates_gold_hourly
        Q->>S3: put manifests/etl_state.json (clés traitées)
    end
```

Une erreur dans `quality.py` est interceptée et loguée sans jamais arrêter le
planificateur (voir `lancer_quality()` dans [`etl/collect.py`](etl/collect.py)) : au pire,
le prochain cycle rattrape les objets bronze non traités.

#### Authentification et requêtes API

```mermaid
sequenceDiagram
    autonumber
    participant U as Utilisateur
    participant F as front (Vue)
    participant A as api (FastAPI)
    participant DB as PostgreSQL

    U->>F: saisit email + mot de passe
    F->>A: POST /api/v1/auth/login (form)
    A->>DB: SELECT users WHERE email = ...
    DB-->>A: user + password_hash
    A->>A: bcrypt.checkpw()
    alt identifiants valides
        A-->>F: 200 { access_token JWT }
        F->>F: localStorage.setItem("enervision_token")
    else invalides
        A-->>F: 401
    end

    Note over F,A: Chaque appel API suivant

    F->>A: GET /api/v1/sites (Authorization: Bearer JWT)
    A->>A: décode le JWT, charge l'utilisateur
    alt token valide
        A->>DB: SELECT ...
        DB-->>A: résultat
        A-->>F: 200 JSON
    else token expiré / invalide
        A-->>F: 401
        F->>F: logout() + redirection /login
    end
```

Le rôle (`viewer` / `operator` / `admin`) est encodé dans le JWT et vérifié par
`require_role` / `require_min_role` (voir [`api/auth.py`](api/auth.py)) sur les routes qui
en ont besoin, par ex. `POST /api/v1/auth/users` réservé aux admins.

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
  (fait foi), les modèles SQLAlchemy restent partiels (`users`, `alerts`, `sites`).
  Introduire Alembic quand le modèle se stabilise.
- **`etl/sites.py` reste un stub** : le service `etl-sites` (`restart: "no"`) ne
  peuple pas encore la table `sites` automatiquement. Côté API, `GET /api/v1/sites`
  et `GET /api/v1/sites/{id}/current` existent désormais ([`api/routers/sites.py`](api/routers/sites.py)).
- **Tests front dans la CI** : `npm test` (Vitest) existe et passe en local, mais `.github/workflows/deploy-dev.yml` ne fait encore qu'un `npm run build` — l'ajouter au job `build-front`.
