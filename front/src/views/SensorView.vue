<template>
  <div class="sensor-view">
    <div class="status-bar">
      <div class="status-count">
        <span class="status-number" :class="{ 'has-alerts': sitesEnAlerte > 0 }">{{ sitesEnAlerte }}</span>
        <span class="status-total">/ {{ totalSites }}</span>
      </div>
      <p class="status-label">
        {{ sitesEnAlerte === 0 ? 'sites opérationnels' : 'sites en alerte capteur' }}
      </p>
    </div>

    <p v-if="chargement" class="etat-message">Lecture des capteurs en cours</p>
    <p v-else-if="erreur" class="etat-message erreur">{{ erreur }}</p>

    <div v-else class="panneaux">
      <article
        v-for="(site, siteId) in sites"
        :key="siteId"
        class="panneau"
        :class="site.capteurs_en_panne.length ? 'panneau--alerte' : 'panneau--ok'"
      >
        <div class="panneau-bande" aria-hidden="true"></div>
        <div class="panneau-corps">
          <div class="panneau-tete">
            <span class="site-nom">{{ site.site_name }}</span>
            <span class="site-id">{{ siteId }}</span>
          </div>

          <p v-if="!site.capteurs_en_panne.length" class="panneau-ok-texte">
            Tous les capteurs répondent
          </p>

          <ul v-else class="capteurs">
            <li v-for="panne in site.capteurs_en_panne" :key="panne.capteur" class="capteur">
              <span class="capteur-point" aria-hidden="true"></span>
              <span class="capteur-nom">{{ panne.capteur }}</span>
              <span class="capteur-retablissement">en panne jusqu'au {{ formatDate(panne.failing_until) }}</span>
            </li>
          </ul>
        </div>
      </article>
    </div>
  </div>
</template>

<script>
import api from "../api/client";

export default {
  name: "SensorView",
  data() {
    return {
      sites: {},
      chargement: true,
      erreur: null,
      intervalleId: null,
    };
  },
  computed: {
    totalSites() {
      return Object.keys(this.sites).length;
    },
    sitesEnAlerte() {
      return Object.values(this.sites).filter((s) => s.capteurs_en_panne.length > 0).length;
    },
  },
  mounted() {
    this.chargerCapteurs();
    this.intervalleId = setInterval(() => this.chargerCapteurs({ silencieux: true }), 60000);
  },
  beforeUnmount() {
    clearInterval(this.intervalleId);
  },
  methods: {
    async chargerCapteurs({ silencieux = false } = {}) {
      if (!silencieux) {
        this.chargement = true;
        this.erreur = null;
      }
      try {
        const reponse = await api.get("/sensors/failing");
        this.sites = reponse.data;
        if (silencieux) this.erreur = null;
      } catch (err) {
        if (!silencieux) {
          this.erreur = "Lecture des capteurs impossible pour le moment.";
        }
      } finally {
        if (!silencieux) this.chargement = false;
      }
    },
    formatDate(valeur) {
      if (!valeur) return "";
      // L'API Mock renvoie des timestamps UTC sans suffixe de fuseau (ex. "2026-09-02T13:56:29") :
      // sans "Z", new Date() les interpréterait comme heure locale au lieu d'UTC.
      const estDejaTimezonee = /[zZ]|[+-]\d{2}:\d{2}$/.test(valeur);
      const date = new Date(estDejaTimezonee ? valeur : `${valeur}Z`);
      return date.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    },
  },
};
</script>

<style scoped>
.sensor-view {
  --bg: #0b1220;
  --panel: #121a2b;
  --panel-border: #1f2b42;
  --text: #e5e9f0;
  --text-muted: #6b7a99;
  --ok: #2dd4bf;
  --alerte: #f59e0b;
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;

  background: var(--bg);
  color: var(--text);
  padding: 28px;
  border-radius: 4px;
}

.status-bar {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--panel-border);
}

.status-count {
  display: flex;
  align-items: baseline;
  font-family: var(--mono);
  line-height: 1;
}

.status-number {
  font-size: 3.2em;
  font-weight: 700;
  color: var(--ok);
}

.status-number.has-alerts {
  color: var(--alerte);
}

.status-total {
  font-size: 1.4em;
  color: var(--text-muted);
  margin-left: 4px;
}

.status-label {
  margin: 0;
  color: var(--text-muted);
  font-size: 0.95em;
}

.etat-message {
  color: var(--text-muted);
  font-family: var(--mono);
}

.etat-message.erreur {
  color: var(--alerte);
}

.panneaux {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.panneau {
  display: flex;
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 2px;
  overflow: hidden;
}

.panneau-bande {
  width: 4px;
  flex-shrink: 0;
  background: var(--ok);
}

.panneau--alerte .panneau-bande {
  background: var(--alerte);
}

.panneau-corps {
  padding: 16px 18px;
  flex: 1;
  min-width: 0;
}

.panneau-tete {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.site-nom {
  font-weight: 600;
  font-size: 1em;
}

.site-id {
  font-family: var(--mono);
  font-size: 0.78em;
  color: var(--text-muted);
  white-space: nowrap;
}

.panneau-ok-texte {
  margin: 0;
  color: var(--ok);
  font-size: 0.9em;
}

.capteurs {
  list-style: none;
  padding: 0;
  margin: 0;
}

.capteur {
  display: grid;
  grid-template-columns: 10px 1fr;
  column-gap: 10px;
  row-gap: 2px;
  padding: 7px 0;
  border-top: 1px solid var(--panel-border);
}

.capteur:first-child {
  border-top: none;
}

.capteur-point {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--alerte);
  margin-top: 5px;
  animation: pulse 2.4s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .capteur-point {
    animation: none;
  }
}

.capteur-nom {
  text-transform: capitalize;
  font-size: 0.92em;
}

.capteur-retablissement {
  grid-column: 2;
  font-family: var(--mono);
  font-size: 0.76em;
  color: var(--text-muted);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>