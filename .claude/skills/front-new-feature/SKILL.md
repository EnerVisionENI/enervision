---
name: front-new-feature
description: Use when creating, adding, or scaffolding a new page/view/screen or front-end feature in the Vue app under front/src (e.g. "ajoute une page X", "crée une nouvelle vue", "new page for Y", "add a feature to the front"). Scaffolds the component following existing conventions, wires the route/nav and API call, and always adds a colocated Vitest test — a feature is not done until it has one.
---

# Nouvelle page / feature front (Vue 3 + Vitest)

Ce skill s'applique à toute nouvelle page ou fonctionnalité ajoutée dans `front/src`. Le principe non négociable : **pas de nouvelle page/feature sans test colocalisé qui passe**.

## 1. Composant

- Une page complète va dans `front/src/views/NomView.vue`. Un élément réutilisable va dans `front/src/components/` (le dossier n'existe pas encore — le créer à la première occasion).
- Utiliser `<script setup>` (Composition API) — c'est la convention actuelle (voir [LoginView.vue](front/src/views/LoginView.vue)). Ne pas suivre [DashboardView.vue](front/src/views/DashboardView.vue), qui est un reliquat en Options API (`<script>` / `export default`) à ne pas reproduire.
- Noms de fichiers/composants en anglais (`LoginView.vue`), mais variables, fonctions et libellés UI en français, cohérent avec l'existant (`chargement`, `erreur`, textes affichés en français).
- Gérer explicitement les états d'une donnée chargée/soumise via l'API : chargement (`ref` booléen), erreur (`ref` string, message utilisateur en français), et le cas "liste/résultat vide" quand applicable.
- Style en `<style scoped>` dans le composant, pas de framework CSS externe.

## 2. Appels API

- Toujours passer par l'instance unique `front/src/api/client.js` (`import api from "../api/client"`), qui gère déjà le token, la déconnexion auto sur 401, et le `baseURL` (`/api/v1` en relatif, proxifié par Vite en dev et par Nginx en prod — ne jamais coder une URL absolue en dur dans un composant).
- Ajouter les appels directement dans le composant via `api.get/post/put/delete(...)`, sauf si plusieurs vues doivent partager la même logique — dans ce cas seulement, envisager un module dédié dans `api/`.

## 3. Routing et navigation

- Enregistrer la page dans `front/src/router/index.js` : importer le composant en haut du fichier, ajouter une entrée dans `routes` avec `meta: { requiresAuth: true }` sauf si la page doit être publique (comme `/login`).
- **La navigation visible est générée automatiquement** par [App.vue](front/src/App.vue) à partir de `router.getRoutes().filter(r => r.meta?.nav)` — il n'y a pas de composant `AppSidebar`/`AppHeader` à éditer. Pour qu'une page apparaisse dans le menu, ajouter `nav: "Libellé"` dans son `meta` (ex. `meta: { requiresAuth: true, nav: "Dashboard" }`). Ne pas mettre `nav` sur les routes qui ne doivent pas apparaître dans le menu (ex. sous-pages, détail).

## 4. Test (obligatoire)

- Créer `NomView.spec.js` **à côté** du composant (colocalisé, pas dans un dossier `tests/` séparé).
- Stack : Vitest + `@vue/test-utils` (déjà installés et configurés dans `front/vite.config.js` — bloc `test`, pool `threads`).
- Mocker `../api/client` (et `../auth/auth` si le composant s'en sert) avec `vi.mock` pour ne jamais appeler le vrai backend ni le vrai localStorage. Utiliser `flushPromises()` de `@vue/test-utils` après `mount()`/un submit pour laisser les appels async se résoudre.
- Si le composant utilise `useRouter`/`router-link`, monter avec une vraie instance de routeur en mémoire (`createRouter({ history: createMemoryHistory(), routes: [...] })`) plutôt que de mocker `vue-router` — voir [LoginView.spec.js](front/src/views/LoginView.spec.js) qui redirige vers `/` après connexion.
- Couvrir au minimum :
  1. Le rendu correct suite à une action/un appel API réussi (ex. redirection, affichage des données).
  2. Le message d'erreur affiché si l'appel API échoue (`mockRejectedValueOnce`), y compris les cas d'erreur différenciés (ex. 401 vs erreur générique).
  3. L'état de chargement (bouton désactivé, libellé "...", etc.) pendant que la promesse est en attente.
  4. Les interactions clés de la page (formulaire, filtres, boutons).
- S'inspirer de [LoginView.spec.js](front/src/views/LoginView.spec.js) comme gabarit exact à suivre pour la structure d'un test de vue.

## 5. Documentation à synchroniser

- Nouvelle variable `VITE_*` utilisée par le composant → l'ajouter dans [front/.env.example](front/.env.example) avec un commentaire expliquant son usage, même chose que les entrées existantes (`VITE_API_URL`, `VITE_TOKEN_KEY`).
- Si la feature ferme un point listé dans la section "Pistes connues (non traitées)" du [README.md](README.md) racine (ex. "Tests front : aucun"), mettre à jour ou retirer cette ligne.
- Si la feature ajoute une page significative côté produit, vérifier si la description du service **front** dans le tableau "Architecture" du README doit être complétée.

## 6. Vérification finale

Avant de considérer la feature terminée, lancer les tests front :

```bash
cd front && npm test
```

Tous les tests doivent passer (aucun test skip, aucun `.only` oublié) avant de proposer le travail comme terminé.
