# Front — Dashboard

# `front/` — Interface web

Vue 3 (Composition API) + Vite + vue-router, sans framework CSS ni store global. Servi par Nginx en production, qui proxifie `/api` vers le service `api`.

## Démarrage rapide

```bash
npm install
npm run dev        # http://localhost:3000
```

En dev, Vite proxifie `/api` vers `http://localhost:8000` ([`vite.config.js`](vite.config.js)) : il faut donc que l'API tourne, par exemple via `docker compose up -d api postgres`.

| Commande | Rôle |
|----------|------|
| `npm run dev` | Serveur de développement, rechargement à chaud |
| `npm run build` | Bundle de production dans `dist/` |
| `npm run preview` | Sert le bundle construit, pour vérification |
| `npm test` | Tests unitaires (Vitest + jsdom) |
| `npm run lint` | ESLint |

## Structure

```
front/src/
├── main.js                Point d'entrée : app + router + feuille de style globale
├── App.vue                Coquille : en-tête, navigation, menu utilisateur
├── router/index.js        Routes + garde de navigation globale
├── auth/auth.js           Token et profil dans le localStorage, état réactif
├── api/client.js          Instance axios : injection du JWT, déconnexion sur 401
├── components/
│   └── AuthLayout.vue     Cadre commun aux écrans d'authentification
├── views/
│   ├── DashboardView.vue      Graphiques de consommation et prévisions
│   ├── AlertsView.vue         Alertes remontées par l'ETL
│   ├── SensorView.vue         Capteurs en panne, par site
│   ├── UsersView.vue          Administration des comptes (admin uniquement)
│   ├── ChangePasswordView.vue Changement de mot de passe
│   ├── LoginView.vue          Connexion
│   └── error/NotFoundView.vue 404
└── assets/styles/main.css Design system : variables CSS, classes utilitaires
```

Les fichiers `*.spec.js` vivent à côté du composant qu'ils testent.

## Routes

| Chemin | Vue | Accès |
|--------|-----|-------|
| `/login` | `LoginView` | public |
| `/`    | `DashboardView` | connecté |
| `/alertes` | `AlertsView` | connecté |
| `/capteurs` | `SensorView` | connecté |
| `/utilisateurs` | `UsersView` | **admin** |
| `/mot-de-passe` | `ChangePasswordView` | connecté |
| `*`    | `NotFoundView` | —     |

Deux `meta` pilotent l'affichage : `nav` fait apparaître la route dans la barre de navigation, `layout: "bare"` retire l'en-tête et le cadre (écrans d'authentification).

## Authentification côté client

Le JWT est stocké dans le `localStorage` sous `enervision_token`, avec le profil (`rôle`, `must_change_password`) mis en cache à côté sous `enervision_token_user` — sinon chaque navigation devrait attendre un appel à `/auth/me` pour savoir quoi afficher. Le profil est rafraîchi à la connexion et après un rechargement de page.

La garde globale du routeur ([`router/index.js`](src/router/index.js)) applique, dans l'ordre :


1. route protégée sans token → `/login` ;
2. profil inconnu → chargement de `/auth/me` ;
3. mot de passe temporaire non changé → redirection forcée vers `/mot-de-passe` ;
4. déjà connecté sur `/login` → `/` ;
5. rôle insuffisant pour `meta.role` → `/`.

> **Le front ne fait que refléter les règles, il ne les applique pas.** L'autorisation réelle est portée par l'API : un `403` sur `/api/v1/users` reste un `403` même si quelqu'un force la route côté navigateur. Voir [`api/README.md`](../api/README.md).

Un `401` sur n'importe quel appel déclenche `logout()` puis une redirection vers `/login`, via l'intercepteur de réponse d'[`api/client.js`](src/api/client.js).

## Configuration

Variables `VITE_*` uniquement (seul ce préfixe est exposé au navigateur). Copier [`.env.example`](.env.example) en `front/.env.local` pour surcharger en dev.

| Variable | Défaut | Rôle |
|----------|--------|------|
| `VITE_API_URL` | *(vide)* | URL absolue de l'API. À laisser vide en temps normal : le front appelle `/api/v1` en same-origin |
| `VITE_TOKEN_KEY` | `enervision_token` | Clé de stockage du JWT |

## Tests

```bash
npm test
```

Vitest + jsdom + `@vue/test-utils`. Les appels réseau sont mockés : aucun service n'a besoin de tourner.

## Style

Pas de framework CSS. [`assets/styles/main.css`](src/assets/styles/main.css) définit les variables (couleurs, espacements, rayons) et un jeu réduit de classes utilitaires ; le reste vit dans les blocs `<style scoped>` des composants.

## Production

[`Dockerfile`](Dockerfile), en deux étapes : build Node puis image Nginx statique. Deux points à connaître avant d'y toucher :

* le **contexte de build est la racine du dépôt**, pas `front/`, parce que l'image copie aussi `infra/nginx.conf` ;
* le HEALTHCHECK vise `http://127.0.0.1/health` et non `localhost` : Nginx n'écoute qu'en IPv4, alors que le résolveur du conteneur tente `::1` en premier — d'où des échecs de healthcheck alors que le serveur répond parfaitement.

## Captures d'écran

### Connexion

Écran de connexion (route `/login`, publique).

 ![Connexion](http://10.105.200.44:3002uploads/6116ff9d-c0ab-4151-aede-276507313da3/293e6a1b-257f-4a33-bd5f-ac654a81b821/01-login.png)

### Dashboard

Consommation, prévisions et chaîne de confiance par site (route `/`).

 ![Dashboard](http://10.105.200.44:3002uploads/6116ff9d-c0ab-4151-aede-276507313da3/ebb76d2b-8b6f-44eb-a67b-5ec5bece06d3/02-dashboard.png)

### Alertes

Alertes remontées par `etl-alerts` (route `/alertes`).

 ![Alertes](http://10.105.200.44:3002uploads/6116ff9d-c0ab-4151-aede-276507313da3/9e0655f7-3489-427a-8b47-069a50063079/03-alertes.png)

### Capteurs

Capteurs en panne par site (route `/capteurs`).

 ![Capteurs](http://10.105.200.44:3002uploads/6116ff9d-c0ab-4151-aede-276507313da3/a15fafab-1961-4a86-a4d9-e33d666ec154/04-capteurs.png)

### Utilisateurs

Administration des comptes, réservée au rôle admin (route `/utilisateurs`).

 ![Utilisateurs](http://10.105.200.44:3002uploads/6116ff9d-c0ab-4151-aede-276507313da3/0968924a-8176-4590-9c75-a4289b0f3c54/05-utilisateurs.png)