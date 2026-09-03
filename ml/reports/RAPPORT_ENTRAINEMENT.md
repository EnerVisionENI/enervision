# Rapport — entraînement sur le CSV synthétique vs données réelles MinIO

**Contexte.** Le gold MinIO réel (pipeline `etl/`, backfill EV-040, 2025-08-02 → 2026-09-03) a un
taux de trous bien trop élevé pour entraîner quoi que ce soit (~99,5% de NaN sur `consumption_kwh`
en horaire, température quasi jamais présente). En attendant le refiltering ETL demandé, on a
entraîné sur le CSV synthétique fourni (`Downloads/Datasets/`, 7 sites, 2023-2024, ~2-3% de trous
seulement), puis on a appliqué ces modèles aux quelques points réels valides pour juger de la
cohérence.

Script : `ml/train_csv_experiment.py`. Données : `ml/report_data.json`.

**Deux évaluations distinctes (important) :**
- **MASE CSV (0.57–0.77)** = performance du modèle sur un test-set du CSV lui-même (partition
  temporelle : train/calib/test au sein de 2023-2024). Valide que le modèle apprend quelque chose
  du CSV, mais ne dit rien sur le transfert en prod.
- **MAPE réel (7–44%)** = performance du même modèle appliqué aux vraies données MinIO
  (2025-2026, points valides uniquement ~40–70 par site, 0,4–0,7% de couverture). Mesure le
  train/serve skew — l'écart entre "bon MASE sur CSV" et "marche en prod". Cet écart est indicatif
  et mélangé avec le bruit de l'imputation forcée (température à 0 ou 20°C puisque absente en réel),
  donc à interpréter prudemment.

## 1. Corrélation avec la consommation (kWh), par site

| Site | Type | hour | dow | month | weekend | working_h | temp | humid | solar |
|---|---|---|---|---|---|---|---|---|---|
| SITE001 | office | 0.17 | -0.19 | 0.12 | -0.24 | **0.53** | 0.12 | -0.08 | 0.51 |
| SITE002 | factory | 0.26 | -0.33 | 0.11 | -0.41 | **0.50** | 0.08 | -0.08 | 0.38 |
| SITE003 | datacenter | -0.07 | 0.02 | 0.24 | 0.02 | n/a (24/7) | **-0.28** | -0.20 | -0.12 |
| SITE004 | retail | 0.36 | -0.02 | 0.14 | -0.02 | **0.69** | 0.09 | -0.11 | 0.53 |
| SITE005 | hospital | -0.08 | -0.01 | 0.23 | -0.00 | n/a (24/7) | **-0.26** | -0.21 | -0.11 |
| SITE006 | office | 0.16 | -0.21 | 0.10 | -0.26 | **0.59** | 0.15 | -0.07 | 0.57 |
| SITE007 | factory | 0.25 | -0.31 | 0.09 | -0.40 | **0.48** | 0.09 | -0.08 | 0.38 |

**Deux régimes distincts, pas un seul modèle universel :**
- **office / factory / retail** (5 sites) : consommation pilotée par le planning d'occupation
  (`is_working_hours`, 0.48–0.69) — la corrélation température est faible (0.08–0.15). L'irradiance
  solaire corrèle fort (0.38–0.57) mais c'est probablement un proxy du jour/heure ouvrée, pas un
  effet causal direct.
- **datacenter / hospital** (2 sites, fonctionnement continu 24/7) : pas de cycle d'occupation, la
  **température est le meilleur corrélat** (-0.26 à -0.28) — cohérent avec une charge de
  climatisation dominante.

→ Confirme qu'il faut arrêter le TOWT uniforme et choisir la feature météo selon le type de site.

## 2. Performance des modèles (test CSV, MASE — < 1 bat le naïf saisonnier)

| Site | Type | TOW (sans T°) | TOW+Temp | LightGBM | Champion | Marge conforme 90% | Couverture |
|---|---|---|---|---|---|---|---|
| SITE001 | office | 0.665 | 0.640 | 0.646 | tow_temp | ±41.2 kW | 92.4% |
| SITE002 | factory | 0.642 | 0.613 | 0.598 | lightgbm | ±316.6 kW | 89.7% |
| SITE003 | datacenter | 0.687 | **0.582** | 0.635 | tow_temp | ±198.3 kW | 85.7% |
| SITE004 | retail | 0.618 | 0.647 | 0.574 | lightgbm | ±137.5 kW | 94.4% |
| SITE005 | hospital | 0.699 | **0.583** | 0.641 | tow_temp | ±258.9 kW | 93.3% |
| SITE006 | office | 0.750 | 0.735 | 0.766 | tow_temp | ±39.9 kW | 95.3% |
| SITE007 | factory | 0.597 | 0.597 | 0.584 | lightgbm | ±253.3 kW | 92.4% |

Tous les modèles battent le naïf (MASE < 1) sur les 7 sites — contrairement au run précédent sur
gold MinIO réel (MASE = 1,000 exact, artefact d'une donnée bouchée à 99,5%). Ici c'est un
entraînement réel.

Observation attendue confirmée : `tow_temp` gagne sur datacenter/hospital (gain net vs `tow`),
`lightgbm` gagne sur factory/retail (capte des interactions que l'OLS ne peut pas), `tow_temp`
gagne aussi de peu sur les deux office malgré une corrélation température faible — la
non-linéarité (terme quadratique) aide un peu même là où la corrélation brute est basse.

## 3. Cohérence face aux vraies données (2025-2026, MinIO)

| Site | Couverture réelle | n points comparés | MAE | MAPE |
|---|---|---|---|---|
| SITE001 (office) | 0,44% | 42 | 10,7 kW | 11,2% |
| SITE002 (factory) | 0,45% | 43 | 180,0 kW | 35,0% |
| SITE003 (datacenter) | 0,70% | 67 | 65,3 kW | 9,2% |
| SITE004 (retail) | 0,51% | 49 | 90,4 kW | 43,8% |
| SITE005 (hospital) | 0,39% | 37 | 34,8 kW | 7,4% |
| SITE006 (office) | 0,44% | 42 | 23,5 kW | 26,1% |
| SITE007 (factory) | 0,38% | 36 | 207,6 kW | 40,5% |

**Deux réserves importantes sur ces chiffres :**
1. **n minuscule** (36 à 67 points sur ~13 mois) — la couverture réelle de `consumption_kwh` est
   de 0,4 à 0,7%. Ce ne sont pas des MAPE fiables statistiquement, juste des indications.
   Attention: cette couverture de consommation observée sur le gold `hourly` (0,4-0,7%) est bien
   plus basse que ce que les compteurs `records_count`/`missing_consumption_count` du gold `daily`
   suggèrent par ailleurs — à vérifier avec l'équipe ETL, l'écart entre les deux granularités
   mériterait d'être compris avant de conclure quoi que ce soit sur le taux réel de panne capteur.
2. **La comparaison est elle-même biaisée par le skew** : pour tous les champions (`tow_temp` ou
   `lightgbm`), la température réelle est indisponible à cette granularité gold — on a dû l'imputer
   (constante 20°C, ou 0 pour LightGBM) pour pouvoir seulement lancer la prédiction. Le MAPE mesuré
   mélange donc "qualité du modèle" et "bruit de l'imputation forcée" — impossible de les séparer
   avec ces données.

**Conclusion honnête** : pour les sites schedule-driven où la température pèse peu (SITE001,
SITE005... mais aussi SITE003 étonnamment bon), le transfert CSV → réel reste dans un ordre de
grandeur défendable (7-11% de MAPE). Pour factory/retail (SITE002, SITE004, SITE007), l'écart est
important (35-44%) — la ressemblance ne suffit pas. **Le risque de train/serve skew signalé
initialement est réel et se voit concrètement ici**, pas juste théorique : un modèle qui a appris
une vraie dépendance à des features indisponibles en prod (température pour `tow_temp`,
température/humidité/solaire à 0 pour `lightgbm`) produit des prédictions dégradées de façon très
inégale selon le site.

## 4. Recommandation

1. **Ne pas déployer ces modèles CSV tels quels en prod.** Ils sont utiles pour calibrer *quelle
   forme* de modèle utiliser par type de site (cf. §1), pas comme modèle final — le fossé temporel
   (2023-24 vs 2025-26) et le fossé de complétude (2-3% vs 95-99% de trous) sont trop grands.
2. **Différencier le modèle par `site_type`** dans le code de production (`models/towt.py`) :
   TOW+temp pour datacenter/hospital, TOW seul (ou LightGBM sur les features temporelles
   disponibles) pour office/factory/retail — plutôt qu'une formule unique pour les 7 sites.
3. **Attendre le refiltering ETL** (interpolation + flag qualité) avant de rouvrir la question
   température/humidité pour de vrai — et à ce moment-là, réévaluer en filtrant `y_true` sur les
   points *réellement mesurés* uniquement (jamais sur de l'interpolé), pour ne pas fausser le MASE.
4. **Vérifier l'écart de couverture hourly vs daily** avec l'ETL (point 1 de la réserve ci-dessus)
   avant de tirer des conclusions définitives sur le taux de panne capteur réel.

---
Généré par `ml/train_csv_experiment.py` — voir `ml/report_data.json` pour les données brutes.
