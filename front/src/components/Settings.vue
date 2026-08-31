<template>
  <div class="settings">
    <h2>Paramètres</h2>
    
    <div class="settings-section">
      <h3>Préférences générales</h3>
      
      <div class="setting-item">
        <label for="language">Langue</label>
        <select id="language" v-model="language">
          <option>Français</option>
          <option>English</option>
          <option>Español</option>
        </select>
      </div>

      <div class="setting-item">
        <label for="theme">Thème</label>
        <select id="theme" v-model="theme">
          <option>Clair</option>
          <option>Sombre</option>
          <option>Auto</option>
        </select>
      </div>

      <div class="setting-item checkbox">
        <input type="checkbox" id="notifications" v-model="notifications">
        <label for="notifications">Activer les notifications</label>
      </div>
    </div>

    <div class="settings-section">
      <h3>Paramètres de consommation</h3>
      
      <div class="setting-item">
        <label for="alert-threshold">Seuil d'alerte (kWh)</label>
        <input type="number" id="alert-threshold" v-model.number="alertThreshold">
      </div>

      <div class="setting-item">
        <label for="update-frequency">Fréquence de mise à jour</label>
        <select id="update-frequency" v-model="updateFrequency">
          <option>Chaque minute</option>
          <option>Chaque 5 minutes</option>
          <option>Chaque 30 minutes</option>
        </select>
      </div>
    </div>

    <div class="button-group">
      <button class="btn-save" @click="saveSettings">Enregistrer</button>
      <button class="btn-reset" @click="resetSettings">Réinitialiser</button>
    </div>
  </div>
</template>

<script>
import { ref } from 'vue'

export default {
  name: 'Settings',
  setup() {
    const language = ref('Français')
    const theme = ref('Clair')
    const notifications = ref(true)
    const alertThreshold = ref(300)
    const updateFrequency = ref('Chaque 5 minutes')

    const saveSettings = () => {
      alert('Paramètres enregistrés avec succès!')
    }

    const resetSettings = () => {
      language.value = 'Français'
      theme.value = 'Clair'
      notifications.value = true
      alertThreshold.value = 300
      updateFrequency.value = 'Chaque 5 minutes'
    }

    return {
      language,
      theme,
      notifications,
      alertThreshold,
      updateFrequency,
      saveSettings,
      resetSettings
    }
  }
}
</script>

<style scoped>
.settings h2 {
  margin-bottom: 30px;
  color: #333;
  font-size: 1.8em;
}

.settings-section {
  background: #f8f9fa;
  padding: 20px;
  border-radius: 12px;
  margin-bottom: 25px;
  border-left: 4px solid #667eea;
}

.settings-section h3 {
  margin-top: 0;
  margin-bottom: 20px;
  color: #333;
  font-size: 1.2em;
}

.setting-item {
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
}

.setting-item.checkbox {
  flex-direction: row;
  align-items: center;
}

.setting-item label {
  font-weight: 500;
  color: #555;
  margin-bottom: 8px;
}

.setting-item.checkbox label {
  margin-bottom: 0;
  margin-left: 10px;
}

.setting-item input[type="text"],
.setting-item input[type="number"],
.setting-item select {
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 1em;
  font-family: inherit;
  transition: border-color 0.3s ease;
}

.setting-item input[type="text"]:focus,
.setting-item input[type="number"]:focus,
.setting-item select:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.setting-item input[type="checkbox"] {
  width: 18px;
  height: 18px;
  cursor: pointer;
}

.button-group {
  display: flex;
  gap: 10px;
  margin-top: 30px;
}

.btn-save,
.btn-reset {
  padding: 12px 24px;
  border: none;
  border-radius: 8px;
  font-size: 1em;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
}

.btn-save {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.btn-save:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.btn-reset {
  background: #e9ecef;
  color: #333;
}

.btn-reset:hover {
  background: #dee2e6;
}
</style>
