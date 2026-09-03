# Rapport — Persistance V1 des modèles de prévision

**Date** : 2026-09-03
**Script** : `ml/train_v1_csv.py`
**Registre** : MLflow local (`sqlite:///mlflow.db`), experiment `smart-energy-forecast-v1-csv`
**Stage** : `Staging` sur les 7 modèles — **aucun n'est en `Production`**, volontairement (voir Limites)

## Ce que ce rapport documente

Une première version entraînée et **réellement persistée** (contrairement au run précédent
`train_csv_experiment.py`, qui ne faisait qu'évaluer et écrire un JSON de métriques sans rien
enregistrer). Chaque modèle est maintenant :
- entraîné,
- loggé comme run MLflow (paramètres + métriques + artefact modèle),
- enregistré dans le Model Registry avec un nom stable (`enervision-forecast-{site_id}`),
- versionné (`v1`), avec une description et un tag `caveat` explicites sur ses limites.

## Méthodologie

- **Donnée** : CSV synthétique (`Downloads/Datasets/{SITE}.csv`, 2023-2024) — voir
  `RAPPORT_ENTRAINEMENT.md` pour pourquoi (le gold réel était encore trop creux pour entraîner
  au moment de l'écriture).
- **Découpage temporel** : train ≤ 2024-09-30, calibration jusqu'au 2024-11-15, test au-delà.
- **Champion figé par site** : pas re-sélectionné à ce run — repris tel quel de la comparaison
  déjà faite et documentée (`report_data.json`, `RAPPORT_ENTRAINEMENT.md`). `train_v1_csv.py` ne
  fait que rejouer et persister la décision déjà prise, pas la reprendre depuis zéro.
- **Intervalle de confiance** : marge conforme (split conformal) à 90 %, calibrée sur le jeu de
  calibration jamais vu à l'entraînement.

## Résultats — un modèle par site

| Site | Type | Champion | MASE (test) | Marge ±90 % | Couverture 90 % | Registre |
|------|------|----------|:---:|:---:|:---:|---|
| SITE001 | office | `tow_temp` | 0.640 | 41.2 kW | 92.4 % | `enervision-forecast-site001` v1 |
| SITE002 | factory | `lightgbm` | 0.598 | 316.6 kW | 89.7 % | `enervision-forecast-site002` v1 |
| SITE003 | datacenter | `tow_temp` | 0.582 | 198.3 kW | 85.7 % | `enervision-forecast-site003` v1 |
| SITE004 | retail | `lightgbm` | 0.574 | 137.5 kW | 94.4 % | `enervision-forecast-site004` v1 |
| SITE005 | hospital | `tow_temp` | 0.583 | 258.8 kW | 93.3 % | `enervision-forecast-site005` v1 |
| SITE006 | office | `tow_temp` | 0.734 | 39.9 kW | 95.3 % | `enervision-forecast-site006` v1 |
| SITE007 | factory | `lightgbm` | 0.584 | 253.3 kW | 92.4 % | `enervision-forecast-site007` v1 |

Tous les MASE sont < 1 (battent le naïf saisonnier). Couverture empirique dans la fourchette
cible 86–94 % sauf SITE003 (85.7 %, tout juste sous la borne — à surveiller) et SITE005/SITE006
(légèrement au-dessus, sur-couverture, pas un problème en soi).

Confirme le clivage déjà documenté : `tow_temp` gagne sur les sites pilotés par la température
(datacenter/hôpital, 24/7) et les bureaux ; `lightgbm` gagne sur factory/retail (usage plus
irrégulier, LightGBM capte des interactions que la régression linéaire simple ne capte pas).

## Comment y accéder

```bash
cd ml
MLFLOW_LOCAL_TRACKING_URI=sqlite:///mlflow.db mlflow ui --port 5000
```
Puis `http://localhost:5000` — experiment `smart-energy-forecast-v1-csv`, ou onglet "Models"
pour voir les 7 modèles enregistrés et leur version `Staging`.

Récupérer un modèle en code :
```python
import mlflow
mlflow.set_tracking_uri("sqlite:///mlflow.db")
model = mlflow.pyfunc.load_model("models:/enervision-forecast-site001/Staging")
```

## Limites — pourquoi `Staging`, jamais `Production`

1. **Entraînés sur du CSV synthétique**, pas sur la donnée réelle du pipeline. La comparaison
   aux points réels valides 2025-2026 (dans `report_data.json`) donne un MAPE de 7 % à 44 %
   selon le site — un écart de transfert (train/serve skew) attendu et documenté, pas un échec
   du modèle, mais qui interdit de servir ça en prod tel quel.
2. **Pas encore assez de volume réel** pour ré-entraîner proprement (voir échanges sur l'état de
   la collecte temps réel côté ETL, EV-041/042) — à refaire dès que le volume permet un
   découpage train/calib/test correct sur données réelles.
3. Le tag `caveat` sur chaque run MLflow répète ce point explicitement, pour que personne ne
   promeuve un de ces modèles en `Production` par erreur en lisant juste le registre.

## Prochaine étape

Dès que le gold réel a assez de volume (voir message à Clément) : relancer un script du même
type que `train_v1_csv.py` mais sur `data.py::load_gold_hourly`, comparer le MASE obtenu à celui
d'ici, et ne promouvoir en `Production` que si le modèle réel bat le naïf de façon nette et
durable (ADR-04).
