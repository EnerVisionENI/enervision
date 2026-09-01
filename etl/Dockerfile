FROM python:3.12.9-slim

WORKDIR /app

# Dépendances installées avant le code, pour profiter du cache Docker
# tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collect.py quality.py .

# Utilisateur non-root, bonne pratique de sécurité pour un conteneur
RUN useradd --create-home appuser
USER appuser

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
    CMD python -c "\
import glob, time, sys; \
fichiers = glob.glob('/app/bronze/*/*.jsonl'); \
sys.exit(0 if fichiers and (time.time() - max(__import__('os').path.getmtime(f) for f in fichiers)) < 150 else 1)"

CMD ["python", "collect.py"]