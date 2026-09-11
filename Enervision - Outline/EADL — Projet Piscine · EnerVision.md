# EADL — Projet Piscine · EnerVision

## Smart Energy Optimizer — MVP Cloud & IA

* **Diffusion** : Durant la semaine de cours avant le début du projet
* **Durée** : 2 semaines (mode piscine)
* **Modalité** : Travail en équipe (\~5 personnes) + évaluations individuelles

## 1. Contexte métier

Les entreprises européennes font face à une triple pression :

* Des coûts énergétiques volatils et imprévisibles
* Des réglementations environnementales de plus en plus strictes (directive CSRD, taxonomie verte européenne — *source : EUR-Lex, Règlement 2020/852*)
* Une obligation croissante de reporting RSE et de neutralité carbone Dans ce contexte, **EnerVision** est une startup spécialisée dans l’optimisation énergétique par l’IA. Vous intégrez son équipe technique pour construire la première version commercialisable de sa plateforme. Votre mission : livrer **Smart Energy Optimizer**, une plateforme cloud-native capable de :
* Collecter et stocker des données de consommation énergétique
* Anticiper les pics de consommation via un modèle prédictif
* Recommander des actions correctives aux utilisateurs
* Exposer ces fonctionnalités via une API sécurisée et un dashboard web **Cible** : convaincre un client pilote industriel et un fonds d’investissement green-tech lors d’une démo finale.

## 2. Contrainte centrale

*Vous n’avez que 2 semaines pour livrer ce que les grandes équipes font en 4+. Priorisez, automatisez, documentez.* Il est **interdit** d’utiliser des solutions clés en main incompatibles avec l’évaluation des compétences architecture/DevSecOps (pas de plateforme no-code, pas de back-end as a service sans justification d’architecture).

## 3. Fonctionnalités du MVP

#### Composant Description

#### Dashboard web

Interface utilisateur (front-end) pour visualiser la consommation

#### Composant Description

#### API sécurisée

Micro-service back-end exposant les données et prédictions **Pipeline ETL** Collecte, transformation et chargement des données capteurs simulés **Stockage cloud** Data Lake ou base de données hébergée en cloud **Modèle IA/ML** Prédiction de consommation énergétique **Module recommandations** Suggestions d’actions de réduction **Infrastructure Cloud** Déploiement cloud, IaC (Terraform ou équivalent) **CI/CD + Sécurité** Pipelines automatisés, scans, monitoring

## 4. Stack technique

Les choix technologiques sont libres. Vous devez **justifier vos choix** dans le dossier de conception.

## 5. Rôles de l’équipe

Chaque membre occupe un rôle principal (la polyvalence est encouragée) : **Rôle Responsabilités principales** **Product Owner** Backlog, user stories, priorisation, communication client **Tech Lead** Architecture globale, décisions techniques, intégration **Cloud/DevOps** IaC, CI/CD, sécurité, monitoring **Data & IA** Pipeline ETL, modèle ML, MLOps **Fullstack Dev** API back-end + front-end dashboard *Vous travaillez en mode Agile : daily standup, démos intermédiaires, backlog versionné.*

## 6. Livrables attendus

### EPCF_01 — À rendre le premier jour du projet (individuel)

#### EC01 — Dossier de conception d’architecture (\~10 pages max)

Chaque critère d’évaluation de l’Activité 1 du REAC sera examiné :

* **Veille technologique et décisionnelle :** Sources pertinentes, récentes, sources anglaises exploitées
* **Analyse des besoins :** ≥80% des besoins identifiés, objectifs couverts à 100%
* **Etude de faisabilité :** Ressources évaluées, score technique attribué, réalisme noté
* **Conception de l’architecture :** Modèle documenté, exigences F et NF intégrées, schémas présents
* **Pertinence et justification des critères de sélection :** Grille comparaison technologique, choix justifiés par tests/prototypes
* **Qualité des revues de conception :** Retours intégrés, modifications traçables dans le dossier
* **Qualité des modélisations :** Diagrammes UML/C4 pertinents et cohérents avec l’architecture
* **Tests automatisés :** Couverture de tests définie, conformité aux bonnes pratiques
* **Rédaction de documents de projet :** Document conforme, clair, solutions techniques évaluées Ce document est **individuel**. Chaque membre rend sa propre vision architecturale. Les différences entre membres d’une même équipe sont normales et attendues.

### EPCF_02 — À rendre à la fin du projet

#### EC02 — Management de projet (rapport collectif + oral individuel)

Rapport collectif (\~10 pages) incluant :

* **Planification et conduite de projet :** Planning réaliste, méthodologie adaptée, jalons documentés
* **Coordination des équipes de projet :** Outils collaboratifs utilisés, communication tracée, RACI défini
* **Suivi continu des indicateurs de performance :** Indicateurs définis avec baseline, suivi régulier documenté
* **Evaluations de l’avancement du projet :** Compte-rendu d’activité complet et honnête Oral individuel :
* **Mobilisation des connaissances technologiques dans la résolution d’incident :** Capacité démontrée à analyser et résoudre un problème technique
* **Montée compétences des équipes :** Partage de connaissances documenté, impact mesurable sur l’équipe Il faut présenter votre contribution personnelle et le relier au rôle qui vous a été défini au début du projet.

#### EC03 — CI/CD (individuel)

* **Qualité du pipeline :** Pipeline opérationnel end-to-end (build → test → deploy)
* **Qualité des tests :** Tests F et NF présents, exécutés dans le pipeline
* **Sécurité du CI/CD** Scan sécurité intégré (OWASP ZAP, Trivy…), vulnérabilités traitées
* **Revue de code et documentation** Linter configuré, rapport qualité produit, code modulaire
* **Documentation technique et partage des connaissances :** Documentation complète, accessible, à jour en soutenance

#### EC04 — Cloud (individuel)

* **Qualité des services cloud sélectionnés :** Services pertinents sélectionnés, automatisation démontrée
* **Efficacité du déploiement :** Terraform (ou équivalent) fonctionnel, déploiements reproductibles
* **Qualité des outils d’optimisation :** Scripts Bash/Python d’administration cloud opérationnels
* **Qualité du monitoring :** Indicateurs pertinents, outil de monitoring actif, données visibles
* **Stratégie de sécurité :** IAM configuré, secrets gérés, audit de sécurité documenté
* **Sécurité des transactions et des données :** (optionnel) Intégration blockchain/intégrité fonctionnelle

#### EC05 — Data & BI (individuel)

* **Qualité de l’architecture :** Data Lake/DW conçu, évolutif, documenté
* **Qualité des processus ETL :** Pipeline ETL fonctionnel, données transformées selon besoins métier
* **Qualité des informations :** | Technologies Big Data utilisées, insights extraits
* **Qualité des Insights :** Tableaux de bord interactifs, KPI pertinents, données réelles
* **Efficacité des RPA :** Au moins une tâche automatisée, conformité et traçabilité assurées

#### EC06 — IA & Automatisation (individuel)

* **Efficience du Machine Learning :** Modèle entraîné, versionné (MLflow), endpoint déployé et fonctionnel
* **Efficacité des outils de surveillance :** Surveillance du modèle en production (drift, métriques)
* **Qualité de la documentation :** Documentation claire et utilisable de l’API prédictive
* **Amélioration continue des processus d’automatisation :** KPI définis et mesurés, ajustements documentés

## 7. Critère d’évaluation ultime

*"Pourrait-on réellement déployer cette solution auprès d’un client pilote demain ?"* Le jury évalue : la **vision**, la **rigueur technique**, la **cohérence d’ensemble** et votre **capacité à travailler comme** **une équipe d’ingénierie mature**.

## 8. Bonus (optionnels)

* **Observabilité avancée :** stack Prometheus + Grafana avec alertes configurées
* **FinOps :** simulation de coûts cloud et optimisation des ressources
* **Sécurité avancée :** rapport OWASP ZAP intégré au pipeline CI/CD