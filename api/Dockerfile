FROM python:3.12-slim

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

COPY requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY . api/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
