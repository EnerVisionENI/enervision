<template>
  <div id="app" class="container">
    <header class="header">
      <h1>EnerVision</h1>
      <p>Plateforme de suivi énergétique</p>
    </header>

    <nav class="navbar">
      <button 
        v-for="item in navItems" 
        :key="item"
        @click="activeTab = item"
        :class="{ active: activeTab === item }"
        class="nav-button"
      >
        {{ item }}
      </button>
    </nav>

    <main class="main-content">
      <component :is="currentComponent" />
    </main>
  </div>
</template>

<script>
import { ref, computed } from 'vue'
import Dashboard from './components/Dashboard.vue'
import Analytics from './components/Analytics.vue'
import Settings from './components/Settings.vue'

export default {
  name: 'App',
  components: {
    Dashboard,
    Analytics,
    Settings
  },
  setup() {
    const activeTab = ref('Dashboard')
    const navItems = ['Dashboard', 'Analytics', 'Settings']

    const currentComponent = computed(() => {
      return activeTab.value
    })

    return {
      activeTab,
      navItems,
      currentComponent
    }
  }
}
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
