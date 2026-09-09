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

      <button
        type="button"
        class="bouton-refresh"
        :disabled="actualisation"
        :class="{ 'bouton-refresh--actif': actualisation }"
        @click="rafraichirManuellement"
      >
        <span class="refresh-icone" aria-hidden="true">↻</span>
        {{ actualisation ? 'Actualisation…' : 'Actualiser' }}
      </button>

      <span v-if="derniereActualisation" class="derniere-actualisation">
        maj {{ formatHeure(derniereActualisation) }}
      </span>
    </div>

    <div class="panneau panneau-risque" v-if="courbeRisque || risqueCapteurIndisponible">
      <div class="panneau-risque-entete">
        <p class="panneau-risque-titre">risque de panne capteur — prochaines 24h</p>
        <span class="risque-sous-titre">tous sites confondus</span>
      </div>

      <svg
        v-if="courbeRisque"
        :viewBox="courbeRisque.viewBox"
        class="risque-svg"
        role="img"
        aria-label="Courbe du risque de panne capteur heure par heure pour les prochaines 24 heures, avec échelle en pourcentage"
      >
        <line
          v-for="g in courbeRisque.gridlines"
          :key="'g' + g.pct"
          :x1="courbeRisque.padL"
          :y1="g.y"
          :x2="courbeRisque.largeurTracee + courbeRisque.padL"
          :y2="g.y"
          class="risque-grille"
        />
        <text
          v-for="g in courbeRisque.gridlines"
          :key="'gl' + g.pct"
          :x="courbeRisque.padL - 8"
          :y="g.y + 3"
          class="risque-axe-y"
        >{{ g.pct }}%</text>

        <text
          v-for="c in courbeRisque.heuresAxe"
          :key="'h' + c.heure"
          :x="c.x"
          :y="courbeRisque.bas + 16"
          class="risque-axe-x"
        >{{ c.heure }}h</text>

        <path :d="courbeRisque.aire" class="risque-aire" />
        <path :d="courbeRisque.ligne" class="risque-ligne" />
        <circle
          v-for="(c, i) in courbeRisque.coords"
          :key="i"
          :cx="c.x"
          :cy="c.y"
          :r="c === courbeRisque.pic || c === courbeRisque.creux ? 4.5 : 2.5"
          :class="c === courbeRisque.pic || c === courbeRisque.creux ? 'risque-point-marquant' : 'risque-point'"
        >
          <title>{{ c.heure }}h : {{ Math.round(c.risk * 100) }}%</title>
        </circle>
      </svg>
      <p v-else class="etat-message erreur">Risque non actualisé, nouvelle tentative au prochain cycle.</p>

      <div v-if="courbeRisque" class="risque-stats">
        <div class="risque-stat">
          <span class="risque-stat-label">minimum</span>
          <span class="risque-stat-valeur">{{ Math.round(courbeRisque.creux.risk * 100) }}% <span class="risque-stat-heure">à {{ courbeRisque.creux.heure }}h</span></span>
        </div>
        <div class="risque-stat">
          <span class="risque-stat-label">maximum</span>
          <span class="risque-stat-valeur risque-stat-max">{{ Math.round(courbeRisque.pic.risk * 100) }}% <span class="risque-stat-heure">à {{ courbeRisque.pic.heure }}h</span></span>
        </div>
        <div class="risque-stat">
          <span class="risque-stat-label">modèle</span>
          <span class="risque-stat-valeur risque-stat-meta">AUC {{ courbeRisque.aucTest }} · {{ courbeRisque.nJoursEntrainement }} j d'historique</span>
        </div>
      </div>
      <ul v-if="courbeRisque" class="risque-notes">
        <li>
          Basé sur les null / champs manquants de la lecture courante (<code>data_quality</code> ≠
          « good » — capteur température, humidité ou tension en panne, perte réseau...).
        </li>
        <li>
          Un seul modèle pour les 7 sites : le taux de panne horaire est corrélé à 0,93-0,95 entre
          chaque paire de sites sur l'historique, un motif temporel partagé par toute la flotte
          simulée, pas des pannes matérielles indépendantes.
        </li>
        <li>
          Expérimental, entraîné sur peu de jours d'historique : une tendance de qualité de
          données, pas une certitude de panne.
        </li>
      </ul>
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
      actualisation: false,
      derniereActualisation: null,
      risqueCapteur: [],
      risqueCapteurIndisponible: false,
    };
  },
  computed: {
    totalSites() {
      return Object.keys(this.sites).length;
    },
    sitesEnAlerte() {
      return Object.values(this.sites).filter((s) => s.capteurs_en_panne.length > 0).length;
    },
    // Graphique détaillé, avec échelle : contrepartie de la sparkline compacte du Dashboard,
    // ici pour consulter le risque heure par heure plutôt qu'une simple tendance. Mêmes
    // bornes verticales (50-85 %) que la sparkline, pour que la forme reste identique entre
    // les deux vues.
    courbeRisque() {
      const points = this.risqueCapteur;
      if (points.length === 0) return null;

      const vmin = 0.5;
      const vmax = 0.85;
      const largeur = 640;
      const hauteur = 200;
      const padL = 40;
      const padR = 12;
      const padT = 12;
      const padB = 26;
      const largeurTracee = largeur - padL - padR;
      const hauteurTracee = hauteur - padT - padB;
      const bas = padT + hauteurTracee;

      const xy = (i, risk) => {
        const x = padL + (i / (points.length - 1)) * largeurTracee;
        const clamped = Math.max(vmin, Math.min(vmax, risk));
        const y = padT + (1 - (clamped - vmin) / (vmax - vmin)) * hauteurTracee;
        return [x, y];
      };

      const coords = points.map((p, i) => {
        const [x, y] = xy(i, Number(p.risk));
        return { x, y, heure: new Date(p.target_hour).getHours(), risk: Number(p.risk) };
      });

      const ligne = coords.map((c, i) => `${i === 0 ? "M" : "L"} ${c.x} ${c.y}`).join(" ");
      const aire = `M ${padL} ${bas} ` + coords.map((c) => `L ${c.x} ${c.y}`).join(" ") + ` L ${coords[coords.length - 1].x} ${bas} Z`;

      const pic = coords.reduce((a, b) => (b.risk > a.risk ? b : a));
      const creux = coords.reduce((a, b) => (b.risk < a.risk ? b : a));

      const gridlines = [0.5, 0.65, 0.8].map((v) => ({
        pct: Math.round(v * 100),
        y: padT + (1 - (v - vmin) / (vmax - vmin)) * hauteurTracee,
      }));

      // Une graduation toutes les 4 heures : au-delà, les libellés se chevauchent sur 24 points.
      const heuresAxe = coords.filter((_, i) => i % 4 === 0 || i === coords.length - 1);

      const premierPoint = points[0];

      return {
        coords,
        aire,
        ligne,
        pic,
        creux,
        gridlines,
        heuresAxe,
        padL,
        bas,
        largeurTracee,
        viewBox: `0 0 ${largeur} ${hauteur}`,
        aucTest: premierPoint.auc_test != null ? Number(premierPoint.auc_test).toFixed(2) : "—",
        nJoursEntrainement: premierPoint.n_train_days ?? "—",
      };
    },
  },
  mounted() {
    this.chargerCapteurs();
    this.chargerRisqueCapteur();
    this.intervalleId = setInterval(() => {
      this.chargerCapteurs({ silencieux: true });
      this.chargerRisqueCapteur();
    }, 60000);
  },
  beforeUnmount() {
    clearInterval(this.intervalleId);
  },
  methods: {
    // `manuel` distingue le clic sur le bouton du polling silencieux de fond :
    // même requête, mais un clic doit remonter une erreur et piloter le spinner
    // du bouton — un poll raté toutes les 60s ne doit pas, lui, faire clignoter
    // un bandeau d'erreur pour un aléa réseau transitoire.
    async chargerCapteurs({ silencieux = false, manuel = false } = {}) {
      if (!silencieux) {
        this.chargement = true;
        this.erreur = null;
      }
      if (manuel) {
        this.actualisation = true;
        this.erreur = null;
      }
      try {
        const reponse = await api.get("/sensors/failing");
        this.sites = reponse.data;
        this.derniereActualisation = new Date();
        if (silencieux) this.erreur = null;
      } catch {
        if (!silencieux || manuel) {
          this.erreur = "Lecture des capteurs impossible pour le moment.";
        }
      } finally {
        if (!silencieux) this.chargement = false;
        if (manuel) this.actualisation = false;
      }
    },
    rafraichirManuellement() {
      // silencieux: true pour garder les panneaux affichés pendant la requête
      // (pas de retour à l'écran "Lecture des capteurs en cours").
      this.chargerCapteurs({ silencieux: true, manuel: true });
    },
    formatHeure(date) {
      return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    },
    async chargerRisqueCapteur() {
      try {
        const reponse = await api.get("/sensors/failure-forecast");
        this.risqueCapteur = reponse.data;
        this.risqueCapteurIndisponible = false;
      } catch {
        // Courbe conservée telle quelle en cas d'échec ponctuel : un cycle raté ne doit
        // pas vider un graphique valide.
        this.risqueCapteurIndisponible = true;
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
  --bg: var(--shell-bg);
  --panel: var(--shell-panel);
  --panel-border: var(--shell-border);
  --text: var(--shell-text);
  --text-muted: var(--shell-muted);
  --ok: var(--shell-accent);
  --alerte: var(--shell-warning);
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;

  background: var(--bg);
  color: var(--text);
  padding: 24px;
  min-height: calc(100vh - 120px);
}

@media (max-width: 768px) {
  .sensor-view {
    padding: 14px;
  }
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--panel-border);
}

.status-count,
.status-label {
  align-self: baseline;
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

.bouton-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  padding: 7px 14px;
  background: transparent;
  color: var(--text);
  border: 1px solid var(--panel-border);
  border-radius: 2px;
  font-family: var(--mono);
  font-size: 0.82em;
  cursor: pointer;
}

.bouton-refresh:hover:not(:disabled) {
  border-color: var(--ok);
  color: var(--ok);
}

.bouton-refresh:disabled {
  cursor: default;
  opacity: 0.7;
}

.refresh-icone {
  display: inline-block;
  font-size: 1.05em;
}

.bouton-refresh--actif .refresh-icone {
  animation: spin 0.8s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .bouton-refresh--actif .refresh-icone {
    animation: none;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.derniere-actualisation {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.76em;
  white-space: nowrap;
}

.etat-message {
  color: var(--text-muted);
  font-family: var(--mono);
}

.etat-message.erreur {
  color: var(--alerte);
}

.panneau-risque {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 2px;
  padding: 18px 20px;
  margin-bottom: 24px;
}

.panneau-risque-entete {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.panneau-risque-titre {
  margin: 0;
  font-weight: 600;
  font-size: 1em;
}

.risque-sous-titre {
  font-family: var(--mono);
  font-size: 0.76em;
  color: var(--text-muted);
}

.risque-svg {
  width: 100%;
  max-width: 640px;
  height: auto;
  display: block;
}

.risque-grille {
  stroke: var(--panel-border);
  stroke-width: 1;
}

.risque-axe-y,
.risque-axe-x {
  font-family: var(--mono);
  font-size: 9px;
  fill: var(--text-muted);
}

.risque-axe-y {
  text-anchor: end;
}

.risque-axe-x {
  text-anchor: middle;
}

.risque-aire {
  fill: #a78bfa;
  fill-opacity: 0.14;
}

.risque-ligne {
  fill: none;
  stroke: #a78bfa;
  stroke-width: 2;
}

.risque-point {
  fill: #a78bfa;
}

.risque-point-marquant {
  fill: var(--text);
}

.risque-stats {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--panel-border);
}

.risque-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.risque-stat-label {
  font-size: 0.72em;
  color: var(--text-muted);
}

.risque-stat-valeur {
  font-family: var(--mono);
  font-size: 0.95em;
  font-weight: 600;
}

.risque-stat-max {
  color: var(--alerte);
}

.risque-stat-heure {
  font-weight: 400;
  color: var(--text-muted);
}

.risque-stat-meta {
  font-weight: 400;
  font-size: 0.85em;
}

.risque-notes {
  margin: 14px 0 0;
  padding: 10px 0 0 16px;
  border-top: 1px solid var(--panel-border);
  font-size: 0.74em;
  color: var(--text-muted);
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.risque-notes code {
  font-family: var(--mono);
  font-size: 0.95em;
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
