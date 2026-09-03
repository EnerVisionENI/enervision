<template>
  <div class="dashboard">
    <p v-if="chargementSites" class="chargement">Chargement des sites...</p>
    <p v-else-if="erreurSites" class="erreur">{{ erreurSites }}</p>

    <template v-else>
      <div class="selecteur-sites">
        <button
          v-for="s in sites"
          :key="s.site_id"
          type="button"
          class="site-pill"
          :class="{ actif: s.site_id === siteSelectionne, alerte: estEnAlerteMock(s.site_id) }"
          @click="selectionnerSite(s.site_id)"
        >
          <span class="point"></span>{{ s.site_id }}
        </button>
      </div>

      <template v-if="siteActuel">
        <div class="entete-site">
          <div>
            <h1>{{ siteActuel.site_id }} — {{ siteActuel.site_name || "Site" }}</h1>
            <p class="sous-titre">
              {{ siteActuel.site_type || "site" }}
              <span v-if="derniereLecture"> · dernière lecture il y a {{ ecouleDepuisDerniereLecture }}</span>
            </p>
          </div>
          <div class="puissance-instantanee">
            <div class="valeur">{{ formatEntier(siteActuel.capacity_kw) }} kW</div>
            <div class="libelle">puissance souscrite</div>
          </div>
        </div>

        <p v-if="erreurLecture" class="erreur">{{ erreurLecture }}</p>

        <div class="grille-principale">
          <div class="panneau">
            <div class="panneau-entete">
              <div class="panneau-titre-groupe">
                <p class="panneau-titre">graphique — puissance appelée</p>
                <span v-if="tauxDisponibilite !== null" class="disponibilite">
                  {{ tauxDisponibilite }}% de lectures disponibles
                </span>
              </div>
              <select class="select-fenetre" :value="fenetreMs" @change="changerFenetre($event.target.value)">
                <option v-for="option in OPTIONS_FENETRE" :key="option.label" :value="option.ms">
                  {{ option.label }}
                </option>
              </select>
            </div>
            <div v-if="dernierePuissance !== null" class="lecture-actuelle">
              {{ formatEntier(dernierePuissance) }}<span class="unite">kW instantané</span>
            </div>
            <p v-else class="pas-de-donnee">Pas de donnée instantanée</p>

            <div class="chart-wrapper">
              <canvas ref="canvasRef"></canvas>
            </div>

            <div class="legende-graphe">
              <div class="legende-item"><span class="legende-trait teal"></span>mesure réelle</div>
              <div class="legende-item"><span class="legende-trait orange"></span>puissance souscrite</div>
            </div>
          </div>

          <div>
            <div class="panneau confiance-panneau">
              <p class="panneau-titre">chaîne de confiance</p>
              <ul class="confiance-liste">
                <li class="confiance-item">
                  <span class="confiance-label">valeur</span>
                  <span class="confiance-valeur">{{ formatEntier(dernierePuissance) }} kW</span>
                </li>
                <li class="confiance-item">
                  <span class="confiance-label">qualité</span>
                  <span class="confiance-valeur qualite" :class="{ fiable: qualiteFiable }" :title="raisonsQualite">
                    {{ qualiteFiable ? "fiable" : "dégradée" }}
                  </span>
                </li>
                <li class="confiance-item">
                  <span class="confiance-label">horizon</span>
                  <span class="confiance-valeur">{{ ecouleDepuisDerniereLecture || "—" }}</span>
                </li>
                <li class="confiance-item">
                  <span class="confiance-label">incertitude</span>
                  <span class="confiance-valeur">± 24 kW</span>
                </li>
              </ul>
            </div>

            <div class="panneau recommandation">
              <p class="recommandation-titre">recommandation active</p>
              <p class="recommandation-texte">{{ RECOMMANDATION_MOCK.texte }}</p>
              <div class="recommandation-gain">{{ RECOMMANDATION_MOCK.gain }}</div>
            </div>
          </div>
        </div>
      </template>

      <p class="section-titre">parc — {{ PARC_MOCK.length }} sites</p>
      <div class="parc-grille">
        <div
          v-for="carte in PARC_MOCK"
          :key="carte.site_id"
          class="parc-carte"
          :class="{ actif: carte.site_id === siteSelectionne }"
          @click="selectionnerSite(carte.site_id)"
        >
          <div class="id">{{ carte.site_id }}</div>
          <div class="valeur" :class="{ alerte: carte.alerte }">{{ carte.valeur }}</div>
          <svg viewBox="0 0 60 20" width="100%" height="20">
            <polyline
              :points="carte.points"
              fill="none"
              :stroke="carte.alerte ? '#f59e0b' : '#2dd4bf'"
              stroke-width="1.5"
            />
          </svg>
        </div>
      </div>

      <div class="kpi-grille">
        <div v-for="kpi in KPI_MOCK" :key="kpi.label" class="kpi-carte">
          <div class="kpi-label">{{ kpi.label }}</div>
          <div class="kpi-valeur">{{ kpi.valeur }}</div>
        </div>
      </div>

      <div class="grille-bas">
        <div class="panneau">
          <p class="panneau-titre">santé du modèle</p>
          <ul class="sante-liste">
            <li v-for="s in SANTE_MOCK" :key="s.label" class="sante-item">
              <span class="confiance-label">{{ s.label }}</span>
              <span class="sante-valeur" :class="{ ok: s.ok }">{{ s.valeur }}</span>
            </li>
          </ul>
        </div>

        <div class="panneau">
          <p class="panneau-titre">alertes — 24h</p>
          <ul class="alertes-liste">
            <li v-for="(a, i) in ALERTES_MOCK" :key="i" class="alerte-item" :class="{ critique: a.critique }">
              <span class="alerte-heure">{{ a.heure }}</span>
              <span class="alerte-texte">{{ a.texte }}</span>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { Chart } from "chart.js/auto";
import api from "../api/client";

const POLL_INTERVAL_MS = 5000;

const OPTIONS_FENETRE = [
  { label: "2 min", ms: 2 * 60 * 1000 },
  { label: "15 min", ms: 15 * 60 * 1000 },
  { label: "1 h", ms: 60 * 60 * 1000 },
  { label: "6 h", ms: 6 * 60 * 60 * 1000 },
  { label: "1 jour", ms: 24 * 60 * 60 * 1000 }, // borne max de GET /sites/{id}/measurements (1440 min)
];

const LIBELLES_RAISON = {
  humidity_sensor_failure: "capteur humidité en panne",
  temperature_sensor_failure: "capteur température en panne",
  voltage_sensor_failure: "capteur tension en panne",
};

// Contenu de démonstration : aucun moteur de recommandation, de scoring de
// site ni de suivi de modèle n'existe côté backend. Seuls le sélecteur de
// site, l'en-tête et le graphique de puissance ci-dessus sont réellement
// alimentés par l'API (GET /sites, GET /sites/{id}/current).
const PARC_MOCK = [
  { site_id: "SITE001", valeur: 78, alerte: false, points: "0,14 15,10 30,12 45,6 60,8" },
  { site_id: "SITE002", valeur: 474, alerte: false, points: "0,10 15,12 30,6 45,8 60,4" },
  { site_id: "SITE003", valeur: 512, alerte: false, points: "0,8 15,10 30,9 45,11 60,10" },
  { site_id: "SITE004", valeur: 861, alerte: true, points: "0,16 15,12 30,8 45,4 60,2" },
  { site_id: "SITE005", valeur: 203, alerte: false, points: "0,9 15,8 30,11 45,9 60,10" },
  { site_id: "SITE006", valeur: 112, alerte: false, points: "0,12 15,10 30,10 45,8 60,9" },
  { site_id: "SITE007", valeur: 598, alerte: true, points: "0,6 15,9 30,7 45,13 60,15" },
];

const KPI_MOCK = [
  { label: "kWh évités — 7j", valeur: "1 240" },
  { label: "kg CO2 évités — 7j", valeur: "187" },
  { label: "€ évités — 7j", valeur: "2 140" },
  { label: "taux de confiance", valeur: "98,4 %" },
  { label: "ratio d'écart", valeur: "0,004" },
];

const SANTE_MOCK = [
  { label: "fiabilité", valeur: "0,71", ok: true },
  { label: "couverture — cible 96 %", valeur: "91,2 %", ok: false },
  { label: "fusible", valeur: "actif", ok: true },
];

const ALERTES_MOCK = [
  { heure: "08:12", texte: "réenrôlement lumière — challenger non promu", critique: false },
  { heure: "09:30", texte: "SITE004 — qualité ratio inférieur à 20 % pendant 15 min", critique: true },
  { heure: "14:05", texte: "journal d'intégrité du 26/08 scellé", critique: false },
];

const RECOMMANDATION_MOCK = {
  texte: "Décaler 80 kW de 14h à 15h pour éviter le pic. Suggestion envoyée à un humain, aucune commande automatique.",
  gain: "340 € évités",
};

function estEnAlerteMock(siteId) {
  return PARC_MOCK.find((carte) => carte.site_id === siteId)?.alerte ?? false;
}

function libelleRaison(raison) {
  return LIBELLES_RAISON[raison] || raison.replaceAll("_", " ");
}

function formatEntier(nombre) {
  return nombre === null || nombre === undefined ? "—" : String(Math.round(nombre));
}

const sites = ref([]);
const siteSelectionne = ref("");
const fenetreMs = ref(OPTIONS_FENETRE[0].ms);
const chargementSites = ref(false);
const erreurSites = ref("");
const erreurLecture = ref("");
const derniereLecture = ref(null);
const dernierePuissance = ref(null);
const derniereQualite = ref("");
const derniereRaisons = ref([]);
const tauxDisponibilite = ref(null); // % de points avec une valeur, sur la fenêtre affichée
const maintenant = ref(new Date());

const siteActuel = computed(() => sites.value.find((s) => s.site_id === siteSelectionne.value) || null);
const qualiteFiable = computed(() => !derniereQualite.value || derniereQualite.value === "good");
const raisonsQualite = computed(() => derniereRaisons.value.map(libelleRaison).join(", "));

const ecouleDepuisDerniereLecture = computed(() => {
  if (!derniereLecture.value) return "";
  const secondes = Math.max(0, Math.round((maintenant.value - derniereLecture.value) / 1000));
  if (secondes < 60) return `${secondes}s`;
  return `${Math.floor(secondes / 60)}min ${secondes % 60}s`;
});

const canvasRef = ref(null);
let chart = null;
// Historique + dernière lecture par site (rempli en continu pour tous les
// sites à chaque cycle) : changer de site ne fait qu'afficher un buffer déjà
// alimenté, sans appel réseau ni retour à zéro — donc pas de flash "0 donnée".
const historiques = {};
const dernieresLectures = {};
let intervalSondage = null;
let intervalHorloge = null;

function creerGraphique() {
  chart = new Chart(canvasRef.value, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Puissance réelle (kW)",
          data: [],
          borderColor: "#2dd4bf",
          backgroundColor: "rgba(45, 212, 191, 0.08)",
          fill: true,
          tension: 0.3,
          // Un point visible par lecture valide : avec ~30-50 % de lectures
          // sans valeur (capteur peu fiable côté mock), un segment isolé d'un
          // ou deux points resterait quasi invisible avec pointRadius: 0.
          pointRadius: 2,
          pointHoverRadius: 4,
        },
        {
          label: "Puissance souscrite (kW)",
          data: [],
          borderColor: "#f59e0b",
          borderDash: [4, 3],
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      // mode "index" + intersect:false : survoler n'importe quel point de
      // l'axe X déclenche le tooltip, pas besoin de viser un point au pixel
      // près (les lignes n'ont pas de marqueur visible, pointRadius: 0).
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: "#6b7a99" }, grid: { color: "#1f2b42" } },
        y: {
          beginAtZero: true,
          ticks: { color: "#6b7a99" },
          grid: { color: "#1f2b42" },
          title: { display: true, text: "kW", color: "#6b7a99" },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: "index",
          intersect: false,
          backgroundColor: "#0e1728",
          borderColor: "#1f2b42",
          borderWidth: 1,
          titleColor: "#e5e9f0",
          bodyColor: "#e5e9f0",
          padding: 10,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatEntier(ctx.parsed.y)} kW`,
          },
        },
      },
    },
  });
}

function bufferPour(siteId) {
  if (!historiques[siteId]) {
    historiques[siteId] = { instants: [], labels: [], valeurs: [], seuils: [] };
  }
  return historiques[siteId];
}

function ajouterPoint(buffer, instant, valeur, seuil) {
  buffer.instants.push(instant);
  buffer.labels.push(instant.toLocaleTimeString("fr-FR"));
  buffer.valeurs.push(valeur);
  buffer.seuils.push(seuil);
}

function purgerAnciens(buffer) {
  // Purge par âge réel, pas par nombre de points : l'historique chargé au
  // démarrage (~1 point/minute) et le direct (1 point/5s) ont des cadences
  // différentes, un simple compteur de points ne représenterait plus la fenêtre.
  const limite = Date.now() - fenetreMs.value;
  while (buffer.instants.length && buffer.instants[0].getTime() < limite) {
    buffer.instants.shift();
    buffer.labels.shift();
    buffer.valeurs.shift();
    buffer.seuils.shift();
  }
}

function enregistrerLecture(site, lecture) {
  const buffer = bufferPour(site.site_id);
  ajouterPoint(buffer, new Date(lecture.timestamp), lecture.consumption_kw, site.capacity_kw ?? null);
  purgerAnciens(buffer);

  dernieresLectures[site.site_id] = {
    puissance: lecture.consumption_kw ?? null,
    qualite: lecture.data_quality || "",
    raisons: lecture.null_reasons || [],
    timestamp: new Date(lecture.timestamp),
  };
}

async function chargerHistorique(site) {
  try {
    const depuisMinutes = Math.ceil(fenetreMs.value / 60000);
    const { data } = await api.get(`/sites/${site.site_id}/measurements`, {
      params: { depuis_minutes: depuisMinutes },
    });
    const buffer = bufferPour(site.site_id);
    for (const mesure of data) {
      ajouterPoint(buffer, new Date(mesure.timestamp), mesure.consumption_kw, site.capacity_kw ?? null);
    }
    purgerAnciens(buffer);
  } catch {
    // Non bloquant : l'historique n'est qu'un préremplissage, le sondage en
    // direct prend le relais de toute façon.
  }
}

function afficherSiteSelectionne() {
  const buffer = bufferPour(siteSelectionne.value);
  if (chart) {
    chart.data.labels = buffer.labels;
    chart.data.datasets[0].data = buffer.valeurs;
    chart.data.datasets[1].data = buffer.seuils;
    chart.update();
  }

  const derniere = dernieresLectures[siteSelectionne.value];
  derniereLecture.value = derniere?.timestamp ?? null;
  dernierePuissance.value = derniere?.puissance ?? null;
  derniereQualite.value = derniere?.qualite ?? "";
  derniereRaisons.value = derniere?.raisons ?? [];

  // Le mock IoT simule un capteur peu fiable (30 à 50 % de lectures sans
  // valeur selon les sites) : plutôt que de combler les trous par une
  // estimation, on affiche le taux réel de disponibilité sur la fenêtre.
  const total = buffer.valeurs.length;
  const valides = buffer.valeurs.filter((v) => v !== null && v !== undefined).length;
  tauxDisponibilite.value = total > 0 ? Math.round((valides / total) * 100) : null;
}

async function sonderTousLesSites() {
  const resultats = await Promise.all(
    sites.value.map((site) =>
      api
        .get(`/sites/${site.site_id}/current`)
        .then((r) => ({ site, data: r.data, ok: true }))
        .catch(() => ({ site, ok: false }))
    )
  );

  for (const resultat of resultats) {
    if (resultat.ok) enregistrerLecture(resultat.site, resultat.data);
  }

  afficherSiteSelectionne();

  const echecSiteActuel = resultats.some((r) => r.site.site_id === siteSelectionne.value && !r.ok);
  erreurLecture.value = echecSiteActuel ? "Lecture indisponible, nouvelle tentative au prochain cycle." : "";
}

function arreterSondage() {
  if (intervalSondage) {
    clearInterval(intervalSondage);
    intervalSondage = null;
  }
}

function demarrerSondage() {
  arreterSondage();
  sonderTousLesSites();
  intervalSondage = setInterval(sonderTousLesSites, POLL_INTERVAL_MS);
}

function selectionnerSite(siteId) {
  if (siteId === siteSelectionne.value) return;
  siteSelectionne.value = siteId;
  afficherSiteSelectionne();
}

async function changerFenetre(ms) {
  fenetreMs.value = Number(ms);
  // Les buffers déjà en mémoire correspondent à l'ancienne fenêtre (trop
  // courts si on agrandit, à purger si on réduit) : on les recharge à neuf
  // plutôt que de les corriger au cas par cas.
  for (const siteId of Object.keys(historiques)) {
    delete historiques[siteId];
  }
  await Promise.all(sites.value.map((site) => chargerHistorique(site)));
  afficherSiteSelectionne();
}

async function chargerSites() {
  chargementSites.value = true;
  erreurSites.value = "";
  try {
    const { data } = await api.get("/sites");
    sites.value = data;
    // Basculer chargementSites avant nextTick : tant qu'il reste vrai, le
    // template affiche "Chargement des sites..." (v-if) et jamais la branche
    // contenant le <canvas> (v-else) — nextTick attendrait alors le mauvais
    // rendu et canvasRef.value resterait null.
    chargementSites.value = false;
    if (data.length > 0) {
      siteSelectionne.value = data[0].site_id;
      // Préremplir le buffer de chaque site avec son historique réel récent
      // (measurements_silver, ~1 point/minute) avant même le premier sondage
      // en direct : le graphique n'est jamais vide au chargement.
      await Promise.all(data.map((site) => chargerHistorique(site)));
      await nextTick();
      creerGraphique();
      afficherSiteSelectionne();
      demarrerSondage();
    }
  } catch {
    erreurSites.value = "Impossible de charger les sites, réessayez.";
    chargementSites.value = false;
  }
}

onMounted(() => {
  chargerSites();
  intervalHorloge = setInterval(() => {
    maintenant.value = new Date();
  }, 1000);
});

onBeforeUnmount(() => {
  arreterSondage();
  clearInterval(intervalHorloge);
  chart?.destroy();
});
</script>

<style scoped>
.dashboard {
  --bg: #0b1220;
  --panel: #121a2b;
  --panel-2: #0e1728;
  --border: #1f2b42;
  --text: #e5e9f0;
  --text-muted: #6b7a99;
  --ok: #2dd4bf;
  --alerte: #f59e0b;
  --danger: #ef4444;
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;

  background: var(--bg);
  color: var(--text);
  padding: 24px;
  min-height: calc(100vh - 220px);
}

.chargement,
.erreur {
  padding: 14px;
  border-radius: 6px;
  background: var(--panel);
}

.erreur {
  color: var(--danger);
}

.selecteur-sites {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 16px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

.site-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: var(--panel);
  font-family: var(--mono);
  font-size: 0.78em;
  color: var(--text-muted);
  white-space: nowrap;
  cursor: pointer;
}

.site-pill .point {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.site-pill.alerte .point {
  background: var(--alerte);
}

.site-pill.actif {
  background: var(--panel-2);
  border-color: var(--text);
  color: var(--text);
}

.entete-site {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 24px;
}

.entete-site h1 {
  font-size: 1.4em;
  font-weight: 600;
  margin: 0 0 4px 0;
}

.entete-site .sous-titre {
  font-family: var(--mono);
  font-size: 0.8em;
  color: var(--text-muted);
  margin: 0;
}

.puissance-instantanee {
  text-align: right;
}

.puissance-instantanee .valeur {
  font-family: var(--mono);
  font-size: 2.2em;
  font-weight: 700;
}

.puissance-instantanee .libelle {
  font-size: 0.78em;
  color: var(--text-muted);
}

.grille-principale {
  display: grid;
  grid-template-columns: 1fr 300px;
  gap: 16px;
  margin-bottom: 32px;
}

.panneau {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 20px;
}

.confiance-panneau {
  margin-bottom: 16px;
}

.panneau-titre {
  font-size: 0.78em;
  color: var(--text-muted);
  margin: 0 0 12px 0;
}

.panneau-entete {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.panneau-entete .panneau-titre {
  margin: 0;
}

.panneau-titre-groupe {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.disponibilite {
  font-size: 0.72em;
  color: var(--text-muted);
}

.select-fenetre {
  background: var(--panel-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 0.76em;
  padding: 4px 8px;
  cursor: pointer;
}

.select-fenetre:hover,
.select-fenetre:focus {
  border-color: var(--text-muted);
  outline: none;
}

.lecture-actuelle {
  font-family: var(--mono);
  font-size: 2.2em;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 8px;
}

.lecture-actuelle .unite {
  font-size: 0.4em;
  color: var(--text-muted);
  font-weight: 400;
  margin-left: 6px;
}

.pas-de-donnee {
  font-size: 1.3em;
  font-style: italic;
  color: var(--text-muted);
  line-height: 1;
  margin: 0 0 8px 0;
  min-height: 1.7em;
  display: flex;
  align-items: center;
}

.chart-wrapper {
  position: relative;
  height: 320px;
}

.legende-graphe {
  display: flex;
  gap: 18px;
  margin-top: 12px;
  font-size: 0.76em;
  color: var(--text-muted);
}

.legende-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legende-trait {
  width: 16px;
  height: 0;
  border-top: 2px solid;
}

.legende-trait.teal {
  border-color: #2dd4bf;
}

.legende-trait.orange {
  border-color: #f59e0b;
  border-top-style: dashed;
}

.confiance-liste {
  list-style: none;
  padding: 0;
  margin: 0;
}

.confiance-item {
  display: flex;
  justify-content: space-between;
  padding: 9px 0;
  border-top: 1px solid var(--border);
  font-size: 0.85em;
}

.confiance-item:first-child {
  border-top: none;
}

.confiance-label {
  color: var(--text-muted);
}

.confiance-valeur {
  font-family: var(--mono);
}

.confiance-valeur.qualite {
  color: var(--alerte);
}

.confiance-valeur.qualite.fiable {
  color: var(--ok);
}

.recommandation {
  border-left: 3px solid var(--ok);
  background: var(--panel-2);
}

.recommandation-titre {
  font-size: 0.76em;
  color: var(--ok);
  margin: 0 0 6px 0;
}

.recommandation-texte {
  font-size: 0.82em;
  color: var(--text-muted);
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.recommandation-gain {
  font-family: var(--mono);
  font-size: 1.2em;
  font-weight: 700;
  color: var(--ok);
}

.section-titre {
  font-size: 0.78em;
  color: var(--text-muted);
  margin: 0 0 12px 0;
}

.parc-grille {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
  margin-bottom: 32px;
}

.parc-carte {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 10px;
  cursor: pointer;
}

.parc-carte.actif {
  border-color: var(--text);
  background: var(--panel-2);
}

.parc-carte .id {
  font-family: var(--mono);
  font-size: 0.7em;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.parc-carte .valeur {
  font-family: var(--mono);
  font-size: 1.15em;
  font-weight: 700;
  margin-bottom: 6px;
}

.parc-carte .valeur.alerte {
  color: var(--alerte);
}

.kpi-grille {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}

.kpi-carte {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 14px;
}

.kpi-label {
  font-size: 0.72em;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.kpi-valeur {
  font-family: var(--mono);
  font-size: 1.3em;
  font-weight: 700;
}

.grille-bas {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.sante-liste,
.alertes-liste {
  list-style: none;
  padding: 0;
  margin: 0;
}

.sante-item {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-top: 1px solid var(--border);
  font-size: 0.85em;
}

.sante-item:first-child {
  border-top: none;
}

.sante-valeur {
  font-family: var(--mono);
}

.sante-valeur.ok {
  color: var(--ok);
}

.alerte-item {
  display: flex;
  gap: 10px;
  padding: 9px 0;
  border-top: 1px solid var(--border);
  font-size: 0.82em;
}

.alerte-item:first-child {
  border-top: none;
}

.alerte-heure {
  font-family: var(--mono);
  color: var(--text-muted);
  flex-shrink: 0;
}

.alerte-item.critique .alerte-texte {
  color: var(--danger);
}

@media (max-width: 800px) {
  .grille-principale,
  .grille-bas {
    grid-template-columns: 1fr;
  }

  .parc-grille {
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  }

  .kpi-grille {
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  }

  .entete-site {
    flex-direction: column;
    align-items: flex-start;
  }

  .puissance-instantanee {
    text-align: left;
  }
}
</style>
