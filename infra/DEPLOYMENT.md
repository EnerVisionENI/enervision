# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions dans `.github/workflows/deploy-dev.yml`.

- Sur chaque pull request vers `dev`, la pipeline lance les tests API et le build frontend.
- Sur chaque push vers `dev` (donc aussi après un merge de PR), la pipeline déploie automatiquement sur le serveur.

Le serveur de déploiement (`10.105.200.44`) n'est joignable que depuis le réseau interne : le job `deploy`
tourne donc sur un **runner GitHub Actions self-hosted installé directement sur ce serveur**, plutôt que sur
un runner hébergé (`ubuntu-latest`) qui ne pourrait pas l'atteindre en SSH. Le job fait simplement un
`actions/checkout` (qui met à jour le dépôt local du runner) puis lance `docker compose up -d --build`.

### Installer le runner self-hosted sur le serveur

1. Sur GitHub : **Settings > Actions > Runners > New self-hosted runner**, choisir Linux.
2. Suivre les commandes affichées (elles contiennent un token à usage unique, à copier depuis la page) pour
   télécharger, configurer (`./config.sh --url ... --token ...`) et installer le runner comme service
   (`sudo ./svc.sh install && sudo ./svc.sh start`) directement sur `10.105.200.44`.
3. Vérifier que le runner apparaît "Idle" dans la liste des runners du repo.

Aucun secret SSH n'est nécessaire avec cette approche (plus de `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY`
/ `DEPLOY_PORT` / `DEPLOY_PATH`) : le job s'exécute déjà sur la machine cible.

### Préparation du serveur

Le serveur (et donc le runner) doit avoir :

1. Docker et Docker Compose installés, avec l'utilisateur du runner autorisé à utiliser Docker
   (membre du groupe `docker`).
2. Les fichiers `.env` de production déjà présents et non versionnés, **dans le dossier de travail du
   runner** (celui où `actions/checkout` place le dépôt, typiquement `~/actions-runner/_work/enervision/enervision`) :
   - `infra/postgres/.env`
   - `api/.env`

   Le step `actions/checkout` est configuré avec `clean: false` pour ne pas supprimer ces fichiers non
   versionnés entre deux déploiements.

Première mise en place (sur le serveur, une fois le runner installé et lancé une première fois pour créer
le dossier de travail) :

```bash
cd ~/actions-runner/_work/enervision/enervision
mkdir -p infra/postgres
# copier/éditer infra/postgres/.env et api/.env avec les valeurs de prod
cd infra
docker compose up -d --build
```

Ensuite, chaque push sur `dev` refera automatiquement le `checkout` + `docker compose up -d --build`.

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
