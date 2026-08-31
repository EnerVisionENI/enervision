# Déploiement EnerVision avec Nginx

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
