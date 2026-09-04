FROM python:3.12-slim

WORKDIR /app

# libgomp1 (runtime OpenMP) : requis par lightgbm au chargement (dlopen de
# libgomp.so.1), absent de l'image slim par défaut. Sans lui : "OSError:
# libgomp.so.1: cannot open shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Lance un entraînement à la demande. En prod, ce conteneur est
# déclenché par un cron/scheduler (voir EF-12 : réentraînement
# sous condition de performance), pas laissé en tâche de fond.
CMD ["python", "train.py"]
