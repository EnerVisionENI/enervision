<template>
  <!-- Les écrans d'authentification (meta.layout === "bare") occupent toute la page,
       sans en-tête ni cadre blanc. -->
  <router-view v-if="pleinePage" />

  <div v-else>
    <!-- Barre d'application pleine largeur, langage graphique « console » commun au
         Dashboard, aux Capteurs et aux écrans d'auth (fond sombre, accent teal,
         monospace pour les libellés, angles à 3px). Contenu recentré sur 1200px. -->
    <header class="header">
      <div class="header-inner">
        <router-link to="/" class="brand">
          <span class="brand-bar" aria-hidden="true"></span>
          <span class="brand-text">
            <span class="brand-name">EnerVision</span>
            <span class="brand-baseline">plateforme de suivi énergétique</span>
          </span>
        </router-link>

        <nav v-if="authenticated" class="navbar">
          <router-link
            v-for="item in navItems"
            :key="item.path"
            :to="item.path"
            class="nav-button"
            active-class="active"
          >
            {{ item.label }}
          </router-link>

          <!-- Bascule jour/nuit : au premier chargement le thème suit le système
               (voir src/theme.js), ce bouton permet de le forcer explicitement
               et retient le choix pour les prochaines visites. -->
          <button
            type="button"
            class="theme-toggle"
            :aria-label="theme === 'dark' ? 'Passer au thème jour' : 'Passer au thème nuit'"
            :title="theme === 'dark' ? 'Thème jour' : 'Thème nuit'"
            @click="basculerTheme"
          >
            <svg
              v-if="theme === 'dark'"
              class="theme-glyph"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="4.2" stroke="currentColor" stroke-width="1.6" />
              <path
                d="M12 2.5v2.4M12 19.1v2.4M21.5 12h-2.4M4.9 12H2.5M18.4 5.6l-1.7 1.7M7.3 16.7l-1.7 1.7M18.4 18.4l-1.7-1.7M7.3 7.3 5.6 5.6"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
              />
            </svg>
            <svg v-else class="theme-glyph" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M20.5 14.7A8.5 8.5 0 1 1 9.3 3.5a6.7 6.7 0 0 0 11.2 11.2Z"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linejoin="round"
              />
            </svg>
          </button>

          <!-- Compte : un seul point d'entrée (icône) qui déplie le mot de passe
               et la déconnexion, plutôt que deux boutons dans la barre. -->
          <div ref="userMenuRef" class="user-menu">
            <button
              type="button"
              class="user-menu-trigger"
              :class="{ open: menuOuvert }"
              aria-haspopup="menu"
              :aria-expanded="menuOuvert"
              aria-label="Menu du compte"
              @click="menuOuvert = !menuOuvert"
            >
              <svg class="user-glyph" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="8" r="3.4" stroke="currentColor" stroke-width="1.6" />
                <path
                  d="M5.5 19c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                />
              </svg>
              <span class="user-caret" aria-hidden="true"></span>
            </button>

            <div v-if="menuOuvert" class="user-menu-panel" role="menu">
              <div class="user-menu-head">
                <span class="user-menu-email">{{ currentUser?.email }}</span>
                <span v-if="currentUser?.role" class="user-menu-role">{{ currentUser.role }}</span>
              </div>
              <router-link
                to="/mot-de-passe"
                class="user-menu-item"
                role="menuitem"
                @click="fermerMenu"
              >
                Changer le mot de passe
              </router-link>
              <button
                type="button"
                class="user-menu-item logout"
                role="menuitem"
                @click="handleLogout"
              >
                Déconnexion
              </button>
            </div>
          </div>
        </nav>
      </div>
    </header>

    <div class="container">
      <!-- Routes normales : inchangé, imbriqué dans .container (max-width 1200px). -->
      <main v-if="!route.meta?.pleinePage" class="main-content">
        <router-view />
      </main>
    </div>

    <!-- Routes "pleine page" (ex. Dashboard) : hors de .container, donc sans
         sa contrainte max-width/padding — occupe toute la largeur de #app. -->
    <main v-if="route.meta?.pleinePage" class="main-content main-content--pleine-page">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import { logout, isAuthenticated, currentUser } from "./auth/auth";
import { theme, basculerTheme } from "./theme";

const router = useRouter();
const route = useRoute();
const authenticated = ref(false);
const menuOuvert = ref(false);
const userMenuRef = ref(null);

const pleinePage = computed(() => route.meta?.layout === "bare");

// Le nav est dérivé du routeur : une route visible porte un meta.nav (son libellé),
// et un meta.role éventuel la réserve à ce rôle (ex. la gestion des utilisateurs).
const navItems = computed(() =>
  router
    .getRoutes()
    .filter((r) => r.meta?.nav && (!r.meta.role || r.meta.role === currentUser.value?.role))
    .map((r) => ({ path: r.path, label: r.meta.nav }))
);

function refreshAuth() {
  authenticated.value = isAuthenticated();
}

function fermerMenu() {
  menuOuvert.value = false;
}

function handleLogout() {
  fermerMenu();
  logout();
  authenticated.value = false;
  router.push("/login");
}

// Le menu du compte se ferme sur un clic en dehors ou sur Échap.
function surClicExterieur(evenement) {
  if (menuOuvert.value && userMenuRef.value && !userMenuRef.value.contains(evenement.target)) {
    fermerMenu();
  }
}

function surTouche(evenement) {
  if (evenement.key === "Escape") fermerMenu();
}

onMounted(() => {
  refreshAuth();
  document.addEventListener("click", surClicExterieur);
  document.addEventListener("keydown", surTouche);
});

onBeforeUnmount(() => {
  document.removeEventListener("click", surClicExterieur);
  document.removeEventListener("keydown", surTouche);
});

router.afterEach(() => {
  refreshAuth(); // met à jour le nav après chaque navigation (ex: login)
  fermerMenu();
});
</script>
