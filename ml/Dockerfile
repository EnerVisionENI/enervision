FROM python:3.12.9-slim

# Sans ça, les print() du planificateur restent bufferisés et n'apparaissent jamais dans
# `docker logs` (même piège que etl/Dockerfile).
ENV PYTHONUNBUFFERED=1

# libgomp1 : LightGBM se lie à OpenMP au chargement, et les images slim ne l'embarquent pas —
# `import lightgbm` échoue sinon sur « libgomp.so.1: cannot open shared object file », et
# predict.py l'importe via scripts/train_csv_experiment même pour un site TOWT.
# L'upgrade couvre les CVE du tag de base, comme côté etl.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances avant le code, pour garder le cache Docker tant que requirements.txt ne bouge pas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Utilisateur non-root, comme le conteneur etl
RUN useradd --create-home appuser
USER appuser

# Le cycle de prévision ne tourne qu'une fois par heure : la fenêtre de fraîcheur doit dépasser
# la période du cron, sinon le conteneur serait déclaré malade entre deux cycles normaux.
# predict.py ne touche le heartbeat que sur un cycle qui a produit des lignes : « sain » veut
# donc dire « écrit des prévisions », pas « processus encore vivant ».
HEALTHCHECK --interval=300s --timeout=5s --start-period=300s \
    CMD python -c "\
import os, time, sys; \
chemin = os.environ.get('HEARTBEAT_PATH', '/tmp/heartbeat'); \
sys.exit(0 if os.path.exists(chemin) and (time.time() - os.path.getmtime(chemin)) < 4500 else 1)"

# Entraînement à la demande par défaut. Le service ml-predict du compose surcharge avec
# `predict.py --schedule` (voir EF-12 : réentraînement sous condition de performance).
CMD ["python", "train.py"]
