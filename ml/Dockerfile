FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Lance un entraînement à la demande. En prod, ce conteneur est
# déclenché par un cron/scheduler (voir EF-12 : réentraînement
# sous condition de performance), pas laissé en tâche de fond.
CMD ["python", "train.py"]
