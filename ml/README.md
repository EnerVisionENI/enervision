# ml/ — Prévision de consommation

Modèles de prévision de charge (kW/kWh) par site, entraînés sur `data/csv/`
(notre seule base d'entraînement actuellement — le gold MinIO réel est encore
trop creux, voir `reports/RAPPORT_ENTRAINEMENT.md`).

## Structure

```
ml/
├── data/csv/       Dataset d'entraînement (versionné, 7 sites, 2023-2024)
├── models/         Algos : naive.py (plancher), towt.py, challenger.py (LightGBM)
├── core/           Utilitaires partagés : data.py (accès MinIO gold), evaluation.py
│                   (MASE, intervalle conforme), mlflow_tracking.py,
│                   features.py + postgres_store.py (inférence)
├── scripts/        train_csv_experiment.py (comparaison des modèles),
│                   train_v1_csv.py (persistance MLflow des champions retenus)
├── reports/        Rapports (*.md versionnés) + JSON générés (ignorés par git)
├── train.py        Entraînement sur gold réel (production, pas encore utilisable)
└── predict.py      Inférence batch : registry MLflow -> table predictions_forecast
```

## Utilisation

```bash
cd ml
pip install -r requirements.txt

# Comparer les modèles candidats sur le CSV, régénère reports/report_data.json
python scripts/train_csv_experiment.py

# Persister les champions retenus en MLflow (Staging)
MLFLOW_TRACKING_URI=sqlite:///mlflow.db python scripts/train_v1_csv.py

# Prévoir les 24 prochaines heures pour tous les sites et écrire en base
python predict.py

# Contrôler le modèle contre le gold réel avant d'écrire quoi que ce soit
python predict.py --check --days 14 --probe-tz
```

`predict.py` ne produit que du pas horaire : les champions sont calendaires
(`C(time_of_week)`, 168 créneaux par semaine), donc structurellement incapables de
descendre sous l'heure. L'horizon, lui, est libre — aucun retard de consommation n'entre
dans ces modèles, prédire +1 h ou +7 j est le même calcul.

Les sites dont le champion est LightGBM ne sont pas inférables en l'état : leurs features
incluent `solar_irradiance_wm2`, que ni `aggregates_gold_hourly` ni `measurements_silver`
ne portent. `predict.py` les écarte en le disant, et traite les autres.

Configuration : variables dans le `.env` racine du projet (pas de `.env` local à `ml/`).

## État actuel

Voir `reports/RAPPORT_ENTRAINEMENT.md` (comparaison des modèles) et
`reports/RAPPORT_PERSISTANCE_V1.md` (modèles persistés, tous en `Staging`,
jamais `Production` — entraînés sur CSV synthétique, pas encore validés sur
données réelles).
