<template>
  <!-- Les écrans d'authentification (meta.layout === "bare") occupent toute la page,
       sans en-tête ni cadre blanc. -->
  <router-view v-if="pleinePage" />

  <div v-else class="container">
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
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { logout, isAuthenticated, currentUser } from "./auth/auth";

const router = useRouter();
const route = useRoute();
const authenticated = ref(false);

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

function handleLogout() {
  logout();
  authenticated.value = false;
  router.push("/login");
}

onMounted(refreshAuth);
router.afterEach(refreshAuth); // met à jour le nav après chaque navigation (ex: login)
</script>
