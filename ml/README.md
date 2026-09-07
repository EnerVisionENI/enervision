# ml/ — Prévision de consommation

Modèles de prévision de charge (kW/kWh) par site. Deux sources d'entraînement :
`data/csv/` (synthétique, seule base fiable aujourd'hui) et le gold MinIO réel
(pipeline prêt, mais bloqué par le volume — voir plus bas).

## Structure

```
ml/
├── data/csv/       Dataset d'entraînement (versionné, 7 sites, 2023-2024)
├── models/         Algos : naive.py (plancher), towt.py, challenger.py (LightGBM)
├── core/           Utilitaires partagés : data.py (accès MinIO gold + détection des
│                   sites), evaluation.py (MASE, intervalle conforme), mlflow_tracking.py
│                   (log + promotion Staging), features.py + postgres_store.py (inférence)
├── scripts/        train_csv_experiment.py (comparaison des modèles),
│                   train_v1_csv.py (persistance MLflow des champions retenus)
├── reports/        Rapports (*.md versionnés) + JSON générés (ignorés par git)
├── train.py        Réentraînement sur gold réel — tous les sites détectés
│                   automatiquement, fenêtres train/calib/test glissantes
└── predict.py      Inférence batch : registry MLflow -> table predictions_forecast
```

## Réentraînement sur gold réel — comment ça fonctionne

```
docker compose --profile mlflow run --rm ml     # manuel, ce qui existe aujourd'hui
```
Pas encore automatisé — volontairement, le temps que le gold accumule assez de volume
(voir plus bas). Une fois prêt, la planification se fera par un scheduler Docker-natif
déclaré directement dans `compose.yaml` (ex. `ofelia`, cf. le commentaire au-dessus du
service `ml`), pas une crontab système à installer à la main sur le serveur —
`docker compose up` suffira alors à tout activer.

Déroulé d'un run :
1. **Détection des sites** — liste les `site_id` réellement présents dans le gold
   MinIO des derniers jours (`core/data.py::list_available_sites`), pas de liste
   codée en dur.
2. **Fenêtres glissantes** — calcule train/calib/test par rapport à *maintenant*
   (90/7/7 jours par défaut, réglable via `TRAIN_WINDOW_DAYS`/`CALIB_WINDOW_DAYS`/
   `TEST_WINDOW_DAYS`), pas des dates figées — sinon un cron mensuel réentraînerait
   indéfiniment sur la même fenêtre.
3. **Par site** : entraîne TOWT + LightGBM sur `train`, calcule le MASE contre le
   naïf, calibre l'intervalle conforme sur `calib`, vérifie la couverture sur `test`.
4. **Décision de promotion** (fusible, ADR-04) : si `MASE TOWT < MASE_PROMOTION_THRESHOLD`,
   une nouvelle version est enregistrée dans le Model Registry MLflow
   (`enervision-forecast-<site>`, stage `Staging`, ancienne version archivée). Sinon,
   le run est loggé mais rien n'est enregistré.
5. Un site en échec (pas assez de données, etc.) n'empêche jamais les autres.

### Ce qui bloque encore, concrètement

TOWT est calendaire (`C(time_of_week)`, 168 créneaux — un par heure de la semaine).
`train` seul a donc besoin d'**au moins 7 jours pleins** pour avoir une chance de
couvrir les 168 créneaux ; en dessous, `calib`/`test` tombent forcément sur une
heure-de-la-semaine jamais vue à l'entraînement et la prédiction échoue. Avec les
fenêtres par défaut (90/7/7 j), ça veut dire **~3 mois d'historique réel minimum**
avant qu'un run aboutisse pour de vrai. Rien à corriger dans le code : c'est un état
transitoire, le temps que la collecte accumule assez de gold.

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

## Ajouter un nouveau modèle en Production

Aucune promotion `Staging` → `Production` n'est automatique — décision volontaire
(un modèle de prévision énergétique sans supervision humaine est un risque métier,
pas juste technique). `predict.py` lit le stage `Production` par défaut
(`PREDICT_MODEL_STAGE`) : tant qu'aucune version n'y est promue, il ne trouve rien
et n'écrit aucune prévision — état normal, pas une panne.

Pour promouvoir manuellement, une fois un candidat jugé satisfaisant (MASE, marge
conforme, couverture — visibles dans l'UI MLflow, `http://localhost:5000` en local
ou `http://10.105.200.44:5000` sur le serveur) :

1. UI MLflow → **Models** → `enervision-forecast-<site>` → la version voulue →
   **Stage** → *Transition to* **Production**
2. Ou en script (`MlflowClient`) :
   ```python
   from mlflow.tracking import MlflowClient
   client = MlflowClient()
   client.transition_model_version_stage(
       name="enervision-forecast-SITE001", version=3,
       stage="Production", archive_existing_versions=True,
   )
   ```

`archive_existing_versions=True` retire automatiquement l'ancienne version de
Production. Une fois promu, le prochain cycle de `predict.py` (cron horaire,
`ml-predict`) le prend en compte sans redéploiement.

## État actuel

Voir `reports/RAPPORT_ENTRAINEMENT.md` (comparaison des modèles) et
`reports/RAPPORT_PERSISTANCE_V1.md` (modèles persistés, tous en `Staging`,
jamais `Production` — entraînés sur CSV synthétique, pas encore validés sur
données réelles).
