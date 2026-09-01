# Déploiement EnerVision avec Nginx

## CI/CD automatique

Le dépôt contient une pipeline GitHub Actions dans `.github/workflows/deploy-dev.yml`.

- Sur chaque pull request vers `dev`, la pipeline lance les tests API et le build frontend.
- Quand la PR est mergée dans `dev`, la pipeline déploie automatiquement sur le serveur.

### Secrets à créer dans GitHub

Ajoute ces secrets dans **Settings > Secrets and variables > Actions** :

- `DEPLOY_HOST` : IP ou nom de domaine du serveur
- `DEPLOY_USER` : utilisateur SSH
- `DEPLOY_SSH_KEY` : clé privée SSH sans passphrase, autorisée sur le serveur
- `DEPLOY_PORT` : port SSH, par défaut `22`
- `DEPLOY_PATH` : chemin du dépôt sur le serveur, par exemple `/srv/enervision`

### Préparation du serveur

Le serveur doit déjà contenir :

1. Le dépôt cloné dans `DEPLOY_PATH`
2. Docker et Docker Compose installés
3. Les fichiers `.env` de production déjà présents et non versionnés
4. Le dépôt configuré pour suivre la branche `dev`

Exemple de première mise en place :

```bash
git clone <url-du-repo> /srv/enervision
cd /srv/enervision
git checkout dev
cd infra
docker compose up -d --build
```

Ensuite, chaque merge vers `dev` fera simplement un `git reset --hard origin/dev` puis un `docker compose up -d --build`.

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
