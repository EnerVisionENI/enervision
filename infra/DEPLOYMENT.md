# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions dans `.github/workflows/deploy-dev.yml`.

- Sur chaque pull request vers `dev`, la pipeline lance les tests API et le build frontend.
- Sur chaque push vers `dev` (donc aussi après un merge de PR), la pipeline déploie automatiquement sur le serveur.

Le serveur de déploiement (`10.105.200.44`) n'est joignable que depuis le réseau interne : le job `deploy`
tourne donc sur un **runner GitHub Actions self-hosted installé directement sur ce serveur**, plutôt que sur
un runner hébergé (`ubuntu-latest`) qui ne pourrait pas l'atteindre en SSH. Le job fait un `actions/checkout`,
restaure les fichiers `.env` de prod depuis un dossier stable, puis lance `docker compose up -d --build`
(ou `docker-compose` si le plugin `docker compose` v2 n'est pas installé).

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
   Les stocker par exemple dans `/opt/enervision-secrets/` (lisible uniquement par l'utilisateur du runner) :
   - `/opt/enervision-secrets/postgres.env` → copié vers `infra/postgres/.env` à chaque déploiement
   - `/opt/enervision-secrets/api.env` → copié vers `api/.env` à chaque déploiement

   Le step *Restore production env files* du workflow fait cette copie avant `docker compose up`.

Première mise en place (sur le serveur) :

```bash
mkdir -p /opt/enervision-secrets
nano /opt/enervision-secrets/postgres.env   # POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
nano /opt/enervision-secrets/api.env        # copie de api/.env.example avec les valeurs de prod
chown -R <user_runner>:<user_runner> /opt/enervision-secrets
chmod 700 /opt/enervision-secrets
chmod 600 /opt/enervision-secrets/*.env
```

Ensuite, chaque push sur `dev` refera automatiquement le `checkout`, la restauration des `.env`, puis
`docker compose up -d --build`.

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
chmod +x scripts/*.sh
```

### 3. Construire et démarrer les services
```bash
# Se positionner dans le dossier infra
cd infra

# Build et démarrage
docker-compose up -d --build

# Visualiser les logs
docker-compose logs -f front
docker-compose logs -f api
```

### 4. Accéder à l'application
```
Frontend:  http://localhost:3000
API:       http://localhost:8000
Traefik:   http://localhost:8080
```

## Commandes utiles

### Arrêter les services
```bash
cd infra
docker-compose down
```

### Redémarrer un service
```bash
docker-compose restart front
docker-compose restart api
```

### Supprimer tout (volumes inclus)
```bash
docker-compose down -v
```

### Vérifier les services
```bash
docker-compose ps
docker-compose logs
```

### Rebuild sans cache
```bash
docker-compose build --no-cache
docker-compose up -d
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
infra/
├── docker-compose.yml    # Orchestration services
├── nginx.conf            # Config serveur web
└── traefik/              # Reverse proxy (optionnel)

front/
└── Dockerfile            # Build et servir with Nginx
```

## Notes de production

1. **HTTPS** : Configurer certains et Traefik pour SSL
2. **Variables d'env** : Utiliser des fichiers `.env`
3. **Logs** : Configurer ELK ou autre solution de logging
4. **Backup** : Mettre en place une stratégie de backup
5. **Monitoring** : Ajouter Prometheus/Grafana
