FROM python:3.12.9-slim

# Sans ça, les print() de collect.py restent bufferisés et n'apparaissent jamais
# dans `podman logs` / `docker logs` (rien à voir avec le pipeline lui-même).
ENV PYTHONUNBUFFERED=1

# OS_PATCH_DATE est référencé dans le RUN ci-dessous, et non seulement déclaré : BuildKit
# n'invalide le cache qu'à la PREMIÈRE UTILISATION d'un ARG, pas à sa définition. La CI y
# injecte la date du jour, ce qui force le patch à rejouer une fois par jour.
#
# Sans ce garde-fou, `cache-from` resservait indéfiniment la couche de patch d'un build
# antérieur : l'image repartait avec les paquets figés à cette date-là, alors que le correctif
# était disponible en dépôt, et Trivy échouait sur des CVE que ce RUN corrige pourtant.
ARG OS_PATCH_DATE=unknown
RUN echo "patch OS : $OS_PATCH_DATE" \
    && apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances installées avant le code, pour profiter du cache Docker
# tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collect.py quality.py storage.py alerts.py postgres_writer.py ./

# Utilisateur non-root, bonne pratique de sécurité pour un conteneur
RUN useradd --create-home appuser
USER appuser

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
    CMD python -c "\
import os, time, sys; \
chemin = os.environ.get('HEARTBEAT_PATH', '/tmp/heartbeat'); \
sys.exit(0 if os.path.exists(chemin) and (time.time() - os.path.getmtime(chemin)) < 150 else 1)"

CMD ["python", "collect.py"]