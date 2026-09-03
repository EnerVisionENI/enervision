<template>
  <div id="app">
    <div class="container">
      <header class="header">
        <h1>EnerVision</h1>
        <p>Plateforme de suivi énergétique</p>
      </header>

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

        <button class="nav-button logout" @click="handleLogout">
          Déconnexion
        </button>
      </nav>

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
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { logout, isAuthenticated } from "./auth/auth";

const router = useRouter();
const route = useRoute();
const authenticated = ref(false);

// Le nav est dérivé du routeur : une route visible porte un meta.nav (son libellé).
const navItems = router.getRoutes()
  .filter((route) => route.meta?.nav)
  .map((route) => ({ path: route.path, label: route.meta.nav }));

function refreshAuth() {
  authenticated.value = isAuthenticated();
}

function handleLogout() {
  logout();
  authenticated.value = false;
  router.push("/login");
}

onMounted(refreshAuth);
router.afterEach(refreshAuth); // met à jour le nav après chaque navigation (ex: login)
</script>
