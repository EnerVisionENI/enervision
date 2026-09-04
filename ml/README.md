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
│                   (MASE, intervalle conforme), mlflow_tracking.py
├── scripts/        train_csv_experiment.py (comparaison des modèles),
│                   train_v1_csv.py (persistance MLflow des champions retenus)
├── reports/        Rapports (*.md versionnés) + JSON générés (ignorés par git)
└── train.py        Entraînement sur gold réel (production, pas encore utilisable)
```

## Utilisation

```bash
cd ml
pip install -r requirements.txt

# Comparer les modèles candidats sur le CSV, régénère reports/report_data.json
python scripts/train_csv_experiment.py

# Persister les champions retenus en MLflow (Staging)
MLFLOW_TRACKING_URI=sqlite:///mlflow.db python scripts/train_v1_csv.py
```

Configuration : variables dans le `.env` racine du projet (pas de `.env` local à `ml/`).

## État actuel

Voir `reports/RAPPORT_ENTRAINEMENT.md` (comparaison des modèles) et
`reports/RAPPORT_PERSISTANCE_V1.md` (modèles persistés, tous en `Staging`,
jamais `Production` — entraînés sur CSV synthétique, pas encore validés sur
données réelles).
