<template>
  <div class="dashboard">
    <h2>Dashboard</h2>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon">⚡</div>
        <div class="stat-info">
          <p class="stat-label">Consommation aujourd'hui</p>
          <p class="stat-value">245 kWh</p>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon">💰</div>
        <div class="stat-info">
          <p class="stat-label">Coût estimé</p>
          <p class="stat-value">€34.50</p>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon">🌱</div>
        <div class="stat-info">
          <p class="stat-label">CO₂ évité</p>
          <p class="stat-value">125 kg</p>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon">📈</div>
        <div class="stat-info">
          <p class="stat-label">Efficacité</p>
          <p class="stat-value">92%</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'Dashboard'
}
</script>

<style scoped>
.dashboard h2 {
  margin-bottom: 30px;
  color: #333;
  font-size: 1.8em;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 20px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transition: transform 0.3s ease, box-shadow 0.3s ease;
}

.stat-card:hover {
  transform: translateY(-5px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
}

.stat-icon {
  font-size: 2.5em;
  margin-right: 20px;
}

.stat-info {
  flex: 1;
}

.stat-label {
  margin: 0;
  font-size: 0.9em;
  opacity: 0.9;
}

.stat-value {
  margin: 5px 0 0 0;
  font-size: 1.8em;
  font-weight: bold;
}
</style>
