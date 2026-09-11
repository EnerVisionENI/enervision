# Outline — Wiki interne & Dex

# Déploiement Outline (wiki de documentation)

## Architecture

Contrairement à MLflow (service dans le `compose.yaml` racine), Outline tourne en **stack isolée** — `infra/outline/compose.yaml`, projet Compose `outline`, séparé d'`enervision` — et **100 % on-premise** : aucun appel vers l'extérieur.

| Service | Image | Rôle |
|---------|-------|------|
| `outline` | `outlinewiki/outline` | Application (UI + API), port hôte `${OUTLINE_PORT:-3002}` |
| `outline-dex` | `dexidp/dex` | Fournisseur d'identité OIDC, comptes en dur, port hôte `${OUTLINE_DEX_PORT:-5556}` |
| `outline-postgres` | `postgres:16-alpine` | Base **dédiée** (volume `outline_outline-pgdata`) |
| `outline-redis` | `redis:7-alpine` | Cache / websockets (volume `outline_outline-redis`) |

Réseau `outline-net` propre, pièces jointes stockées en local (`FILE_STORAGE=local`, volume `outline_outline-data`). **Aucun partage** avec la base applicative ni avec MinIO.

Pas de route Traefik : dans ce projet Traefik n'est branché sur aucun service (`traefik.yml` en `exposedByDefault: false`, zéro label). Outline et Dex sont joignables directement sur leurs ports hôte, comme `api`, `front`, `grafana`, `mlflow`.

## Authentification — pourquoi Dex

Outline **n'a pas** de connexion identifiant + mot de passe et ne permet pas de pré-créer des comptes. La seule méthode est un fournisseur d'identité externe *à Outline*.

« Externe à Outline » ≠ « sur Internet » : **Dex** est un fournisseur OIDC minuscule (\~20 Mo, pas de base) qui tourne dans la même stack. Les comptes sont définis **en dur** dans `dex-config.yaml` (section `staticPasswords`, mots de passe en bcrypt). Rien ne sort de la machine.

* Ajouter / retirer un compte = éditer `dex-config.yaml` + `docker compose restart outline-dex`.
* À la première connexion OIDC réussie, Outline **provisionne** automatiquement le compte. Le premier à se connecter devient administrateur du workspace.
* Aucun SMTP nécessaire (le bloc SMTP du `.env` ne sert qu'aux notifications e-mail).

### Le piège de l'`issuer`

`OUTLINE_OIDC_ISSUER` (dans `.env`) et `issuer:` (dans `dex-config.yaml`) doivent être **identiques**, et cette URL doit être joignable **à la fois** :

* par le **navigateur** des utilisateurs (redirection vers l'écran de login Dex) ;
* par le **conteneur** `**outline**` (échange du code contre un token, appel `userinfo`).

⇒ utiliser l'**IP + port publié de l'hôte**, jamais `outline-dex:5556` (invisible du navigateur) ni `localhost` (invisible du conteneur `outline` sur un serveur).

| Contexte | `issuer` |
|----------|--------|
| Poste local (tout sur la même machine) | `http://localhost:5556/dex` |
| Serveur de déploiement | `http://10.105.200.44:5556/dex` |
| Domaine + proxy TLS | `https://docs.mondomaine.fr/dex` (proxifier `/dex` vers `:5556`) |

## Mise en place

```bash
cd infra/outline
cp .env.example .env
cp dex-config.example.yaml dex-config.yaml
```

### 1. Secrets (`.env`)

```bash
openssl rand -hex 32   # -> OUTLINE_SECRET_KEY
openssl rand -hex 32   # -> OUTLINE_UTILS_SECRET
openssl rand -hex 32   # -> OUTLINE_OIDC_CLIENT_SECRET
```

Renseigner aussi `OUTLINE_URL`, `OUTLINE_OIDC_ISSUER`, `OUTLINE_DB_PASSWORD`.

### 2. Comptes (`dex-config.yaml`)

* `issuer:` = `OUTLINE_OIDC_ISSUER`.
* `staticClients[0].secret` = `OUTLINE_OIDC_CLIENT_SECRET`.
* `staticClients[0].redirectURIs[0]` = `<OUTLINE_URL>/auth/oidc.callback`.
* Un bloc `staticPasswords` par personne. Générer un hash bcrypt :

```bash
docker run --rm httpd:2.4-alpine htpasswd -nBC 10 "" | tr -d ':\n'
# saisir le mot de passe 2x ; copier la sortie qui commence par $2y$ dans `hash:`
```

### 3. Démarrage

```bash
docker compose up -d
docker compose logs -f outline
```

L'image applique ses migrations de schéma au démarrage. Si les logs signalent une base non migrée (montée de version majeure) : `docker compose run --rm outline yarn db:migrate`.

Une fois `outline` en `healthy` : UI sur `OUTLINE_URL`, bouton **« Continue with {OUTLINE_OIDC_DISPLAY_NAME} »** → écran Dex → login avec un compte de `dex-config.yaml`.

## Déploiement sur le serveur (10.105.200.44)

La stack Outline est **hors du pipeline CI/CD** (pas dans `.github/workflows/ci-cd.yml`, pas dans les profils du `compose.yaml` racine). Déploiement **manuel**, une fois, sur le serveur. Les images `outlinewiki/outline` et `dexidp/dex` sont publiques sur Docker Hub — pas de `docker login` GHCR.

### 1. Récupérer les fichiers versionnés

Le runner self-hosted fait déjà un `actions/checkout` du dépôt : après merge de cette branche sur `dev`/`main`, `infra/outline/{compose.yaml,.env.example,dex-config.example.yaml}` sont présents sur le serveur. Sinon `git pull` dans le dépôt.

### 2. Créer `.env` et `dex-config.yaml` (non versionnés)

Sur le serveur, dans `infra/outline/` :

```bash
cp .env.example .env
cp dex-config.example.yaml dex-config.yaml
openssl rand -hex 32   # -> OUTLINE_SECRET_KEY        (dans .env)
openssl rand -hex 32   # -> OUTLINE_UTILS_SECRET      (dans .env)
openssl rand -hex 32   # -> OUTLINE_OIDC_CLIENT_SECRET (dans .env ET dex-config.yaml)
```

Valeurs à mettre pour ce serveur (réseau interne, HTTP nu comme les autres services) :

| `.env` | valeur |
|------|--------|
| `OUTLINE_URL` | `http://10.105.200.44:3002` |
| `OUTLINE_OIDC_ISSUER` | `http://10.105.200.44:5556/dex` |
| `OUTLINE_DB_PASSWORD` | mot de passe fort |
| `OUTLINE_FORCE_HTTPS` | `false` |

| `dex-config.yaml` | valeur |
|-----------------|--------|
| `issuer`        | `http://10.105.200.44:5556/dex` (identique à `OUTLINE_OIDC_ISSUER`) |
| `staticClients[0].secret` | identique à `OUTLINE_OIDC_CLIENT_SECRET` |
| `staticClients[0].redirectURIs[0]` | `http://10.105.200.44:3002/auth/oidc.callback` |
| `staticPasswords` | un bloc par personne, hash bcrypt (voir §2 ci-dessus) |

Conserver `.env` + `dex-config.yaml` hors serveur (gestionnaire de secrets / secret GitHub de sauvegarde) : ils contiennent les seuls exemplaires des clés et des hash.

### 3. Ouvrir les ports

`3002` (Outline) et `5556` (Dex) doivent être joignables depuis les postes du réseau interne. Vérifier le pare-feu de la VM (`ufw`, règles cloud…).

### 4. Démarrer

```bash
cd infra/outline
docker compose up -d
docker compose ps
docker compose logs -f outline        # attendre "healthy" + migrations OK
```

`restart: unless-stopped` sur tous les services : la stack revient toute seule après un reboot (tant que le démon Docker démarre au boot).

### 5. Première connexion

Ouvrir `http://10.105.200.44:3002` → **« Continue with Compte EnerVision »** → login Dex. **Le premier compte à se connecter devient administrateur** — que ce soit la bonne personne.

### 6. Mises à jour (manuel, hors CI)

```bash
# éditer OUTLINE_VERSION / OUTLINE_DEX_VERSION dans .env
cd infra/outline
docker compose pull
docker compose up -d
```

## Ajouter un utilisateur plus tard


1. Nouveau bloc `staticPasswords` dans `dex-config.yaml` (email, `hash`, `username`, `userID` unique).
2. `docker compose restart outline-dex`.
3. La personne se connecte : son compte Outline est créé automatiquement.

Révoquer un accès = supprimer le bloc + `restart outline-dex`, puis suspendre le compte dans Outline (Settings > Members).

## Exposition HTTPS externe


1. `.env` : `OUTLINE_URL=https://docs.mondomaine.fr`, `OUTLINE_FORCE_HTTPS=true`, `OUTLINE_OIDC_ISSUER=https://docs.mondomaine.fr/dex`.
2. `dex-config.yaml` : `issuer` et `redirectURIs` alignés sur ces valeurs.
3. Proxy TLS en amont (Nginx / Caddy) :
   * `/`      → `http://<hôte>:${OUTLINE_PORT}` (transmettre `X-Forwarded-Proto: https`, gérer le WebSocket `Upgrade`) ;
   * `/dex`   → `http://<hôte>:${OUTLINE_DEX_PORT}`.

## Sauvegarde / pérennité

* **Contenu (documents, utilisateurs)** → base `outline-postgres`, volume `outline_outline-pgdata` :

  ```bash
  docker compose exec outline-postgres pg_dump -U outline outline > outline-$(date +%F).sql
  ```
* **Pièces jointes / images** → volume `outline_outline-data` :

  ```bash
  docker run --rm -v outline_outline-data:/data -v "$PWD":/backup alpine \
    tar czf /backup/outline-data-$(date +%F).tar.gz -C /data .
  ```
* **Comptes** → `dex-config.yaml` (à sauvegarder hors du serveur, contient les hash).
* Redis = cache uniquement, non sauvegardé.

Ces volumes ne sont **pas** couverts par `audit-sync` (qui cible les buckets MinIO) : à intégrer à la procédure de backup serveur si Outline devient critique.

## Exploitation

```bash
docker compose ps
docker compose logs -f outline
docker compose logs -f outline-dex
docker compose pull && docker compose up -d      # montée de version (après avoir bumpé les *_VERSION)
```

## Rollback

```bash
docker compose down                # stoppe, garde les données
docker compose down -v             # DESTRUCTIF : supprime base + pièces jointes
```

## Notes

* Images épinglées via `OUTLINE_VERSION` / `OUTLINE_DEX_VERSION` — jamais `latest`. Vérifier : <https://github.com/outline/outline/releases> et <https://github.com/dexidp/dex/releases>.
* Dex en `storage: memory` : aucun état à persister, tout vient de `dex-config.yaml`. Un `restart outline-dex` invalide les sessions en cours (les utilisateurs se reconnectent — sans ressaisir leur mot de passe si la session navigateur Outline est encore valide).
* `OIDC_ISSUER` requiert Outline ≥ 0.70 (défaut `1.10.0` ici). Sur une version antérieure, la validation du token échouerait car l'`iss` émis par Dex ne correspondrait pas.
* `PGSSLMODE=disable` : trafic Postgres interne au réseau `outline-net`, pas de TLS.
* `infra/outline/.env` et `infra/outline/dex-config.yaml` ne sont pas versionnés (`.gitignore`). La stack Outline est hors du `.env` racine et hors du pipeline CI/CD applicatif.