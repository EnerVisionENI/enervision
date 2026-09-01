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

const navItems = [
  { path: "/", label: "Dashboard" },
  { path: "/consumption", label: "Consommation" },
  { path: "/alerts", label: "Alertes" },
  { path: "/predictions", label: "Prédictions" },
  { path: "/sensors", label: "Capteurs" },
];

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

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  min-height: 100vh;
}

#app {
  width: 100%;
  min-height: 100vh;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}

.header {
  text-align: center;
  color: white;
  margin-bottom: 40px;
  padding-top: 20px;
}

.header h1 {
  font-size: 3em;
  margin-bottom: 10px;
  text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
}

.header p {
  font-size: 1.2em;
  opacity: 0.9;
}

.navbar {
  display: flex;
  gap: 10px;
  justify-content: center;
  margin-bottom: 40px;
  flex-wrap: wrap;
}

.nav-button {
  padding: 12px 24px;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.2);
  color: white;
  font-size: 1em;
  cursor: pointer;
  transition: all 0.3s ease;
  font-weight: 500;
  text-decoration: none;
  display: inline-block;
}

.nav-button:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
}

.nav-button.active {
  background: white;
  color: #667eea;
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
}

.nav-button.logout {
  background: rgba(255, 0, 0, 0.25);
}

.nav-button.logout:hover {
  background: rgba(255, 0, 0, 0.4);
}

.main-content {
  background: white;
  border-radius: 12px;
  padding: 40px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

@media (max-width: 768px) {
  .container {
    padding: 10px;
  }

  .header h1 {
    font-size: 2em;
  }

  .main-content {
    padding: 20px;
  }
}
</style>