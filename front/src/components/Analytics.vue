<template>
  <div class="analytics">
    <h2>Analyses</h2>
    <div class="analytics-container">
      <div class="chart-placeholder">
        <div class="chart-title">Consommation - Derniers 7 jours</div>
        <div class="chart-bars">
          <div class="bar" v-for="(val, idx) in weekData" :key="idx" :style="{ height: val + '%' }"></div>
        </div>
      </div>

      <div class="chart-placeholder">
        <div class="chart-title">Répartition par source</div>
        <div class="pie-chart">
          <div class="pie-segment segment-1"></div>
          <div class="pie-segment segment-2"></div>
          <div class="pie-segment segment-3"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref } from 'vue'

export default {
  name: 'Analytics',
  setup() {
    const weekData = ref([65, 72, 80, 68, 85, 90, 78])
    
    return {
      weekData
    }
  }
}
</script>

<style scoped>
.analytics h2 {
  margin-bottom: 30px;
  color: #333;
  font-size: 1.8em;
}

.analytics-container {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 30px;
}

.chart-placeholder {
  background: #f8f9fa;
  padding: 20px;
  border-radius: 12px;
  border-left: 4px solid #667eea;
}

.chart-title {
  font-weight: bold;
  margin-bottom: 20px;
  color: #333;
}

.chart-bars {
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  height: 200px;
  gap: 8px;
}

.bar {
  flex: 1;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 4px 4px 0 0;
  min-height: 20px;
  transition: all 0.3s ease;
}

.bar:hover {
  opacity: 0.8;
  transform: scaleY(1.05);
}

.pie-chart {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
  position: relative;
}

.pie-segment {
  position: absolute;
  width: 100px;
  height: 100px;
  border-radius: 50%;
}

.segment-1 {
  background: #667eea;
  clip-path: polygon(50% 50%, 50% 0, 100% 0, 100% 50%);
}

.segment-2 {
  background: #764ba2;
  clip-path: polygon(50% 50%, 100% 50%, 100% 100%, 50% 100%);
}

.segment-3 {
  background: #f093fb;
  clip-path: polygon(50% 50%, 50% 100%, 0 100%, 0 50%);
}
</style>
