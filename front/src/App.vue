<template>
  <div id="app" class="container">
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

    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { logout, isAuthenticated } from "./auth/auth";

const router = useRouter();
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
