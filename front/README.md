# EnerVision Front

Application Vue 3 pour la plateforme de suivi énergétique EnerVision.

## Installation

```bash
npm install
```

## Développement

```bash
npm run dev
```

L'application sera disponible à `http://localhost:3000`

## Build

```bash
npm run build
```

Les fichiers compilés seront dans le dossier `dist/`.

## Aperçu de production

```bash
npm run preview
```

## Structure du projet

```
src/
├── main.js           # Point d'entrée
├── App.vue           # Composant principal
└── components/       # Composants réutilisables
    ├── Dashboard.vue # Page d'accueil
    ├── Analytics.vue # Analyses
    └── Settings.vue  # Paramètres
```

## Fonctionnalités

- 📊 **Dashboard** : Vue d'ensemble de la consommation énergétique
- 📈 **Analyses** : Graphiques et statistiques détaillées
- ⚙️ **Paramètres** : Configuration personnalisée

## Technologies

- Vue 3 (dernière version stable)
- Vite
- CSS3
