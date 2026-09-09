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
          :class="{ actif: s.site_id === siteSelectionne, alerte: resumeParSite[s.site_id]?.alerte }"
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
                <p class="panneau-titre">graphiques — mesures du site</p>
                <span v-if="tauxDisponibilite !== null" class="disponibilite">
                  {{ tauxDisponibilite }}% de lectures disponibles
                </span>
              </div>
            </div>

            <!-- Barre de période : les cinq durées les plus demandées en un clic, et un
                 formulaire pour toutes les autres. Le résumé dit toujours ce qui est
                 réellement tracé — c'est la seule ligne qui reste juste quand la période
                 ne correspond à aucun préréglage. -->
            <div class="barre-periode">
              <div class="pilules-periode" role="group" aria-label="période affichée">
                <button
                  v-for="option in OPTIONS_FENETRE"
                  :key="option.label"
                  type="button"
                  class="pilule-periode"
                  :class="{ actif: option.ms === fenetreMs }"
                  :aria-pressed="option.ms === fenetreMs"
                  @click="choisirPrereglage(option.ms)"
                >
                  {{ option.label }}
                </button>
                <button
                  type="button"
                  class="pilule-periode"
                  :class="{ actif: !prereglageActif }"
                  aria-controls="saisie-periode"
                  :aria-expanded="saisieOuverte"
                  @click="basculerSaisie"
                >
                  personnalisée…
                </button>
              </div>

              <p class="resume-periode">{{ resumePeriode }}</p>

              <form
                v-if="saisieOuverte"
                id="saisie-periode"
                class="saisie-periode"
                @submit.prevent="appliquerSaisie"
              >
                <label class="saisie-periode-libelle" for="periode-duree">durée affichée</label>
                <input
                  id="periode-duree"
                  ref="champDureeRef"
                  v-model="dureeBrouillon"
                  class="saisie-periode-duree"
                  type="number"
                  inputmode="numeric"
                  min="1"
                  :max="maxBrouillon"
                  step="1"
                  :aria-invalid="saisieInvalide"
                  aria-describedby="periode-bornes"
                />
                <select v-model="uniteBrouillon" class="saisie-periode-unite" aria-label="unité de la durée">
                  <option v-for="u in UNITES_FENETRE" :key="u.cle" :value="u.cle">{{ u.label }}</option>
                </select>
                <button type="submit" class="bouton-periode">appliquer</button>
                <span id="periode-bornes" class="saisie-periode-bornes">
                  de {{ libelleDuree(FENETRE_MIN_MS) }} à {{ libelleDuree(FENETRE_MAX_MS) }}
                </span>
              </form>

              <!-- role="status" : la correction est annoncée aux lecteurs d'écran, qui ne
                   verraient rien du champ réaligné sur la période retenue. -->
              <p
                v-if="messagePeriode"
                class="message-periode"
                :class="{ invalide: saisieInvalide }"
                role="status"
              >
                {{ messagePeriode }}
              </p>
            </div>

            <div v-if="derniereValeurFocus !== null" class="lecture-actuelle">
              {{ formatValeur(derniereValeurFocus, metriqueFocusInfo.decimales)
              }}<span class="unite">{{ suffixeUnite }}</span>
            </div>
            <p v-else class="pas-de-donnee">Pas de mesure récente</p>

            <div class="graphique-grand">
              <div class="graphique-grand-entete">
                <p class="graphique-grand-titre">{{ titreMetriqueFocus }}</p>
                <button
                  v-if="grandGraphiqueEstZoome"
                  type="button"
                  class="bouton-reinitialiser-zoom"
                  @click="reinitialiserZoomGraphiqueGrand"
                >
                  réinitialiser le zoom
                </button>
              </div>
              <div class="chart-wrapper-grand">
                <canvas ref="canvasGrandRef"></canvas>
              </div>
              <div class="legende-graphe">
                <div class="legende-item">
                  <span class="legende-trait" :style="{ borderColor: metriqueFocusInfo.couleur }"></span>
                  mesure réelle
                </div>
                <template v-if="metriqueFocusInfo.avecPrediction && previsions.length > 0 && !previsionTropCourtePourFenetre">
                  <div class="legende-item">
                    <span class="legende-trait violet"></span>prévision ({{ infoPrevision.champion }} v{{
                      infoPrevision.model_version
                    }})
                  </div>
                  <div class="legende-item"><span class="legende-bande"></span>intervalle 90 %</div>
                </template>
                <div v-if="metriqueFocusInfo.avecSeuil" class="legende-item">
                  <span class="legende-trait orange"></span>puissance souscrite
                </div>
              </div>

              <!-- Distinguer les trois cas plutôt qu'afficher un graphe muet : un site sans
                   modèle inférable est un état normal du système, une API en échec ne l'est
                   pas, et une prévision issue de CSV synthétique ne doit jamais passer pour
                   une prévision validée sur donnée réelle. -->
              <p v-if="metriqueFocusInfo.avecPrediction && previsionIndisponible" class="note-prevision alerte">
                Prévision indisponible : service de prédiction injoignable.
              </p>
              <p
                v-else-if="metriqueFocusInfo.avecPrediction && previsions.length === 0"
                class="note-prevision"
              >
                Aucune prévision pour ce site : son modèle exige une variable
                (<code>solar_irradiance_wm2</code>) que la collecte ne fournit pas encore.
              </p>
              <p
                v-else-if="metriqueFocusInfo.avecPrediction && previsionTropCourtePourFenetre"
                class="note-prevision"
              >
                Prévision masquée sur cette fenêtre : le modèle est horaire. Choisissez 1 h ou
                plus pour l'afficher.
              </p>
              <p
                v-else-if="metriqueFocusInfo.avecPrediction && previsionSurDonneeSynthetique"
                class="note-prevision alerte"
              >
                Modèle entraîné sur données synthétiques, non validé sur données réelles — à
                lire comme un ordre de grandeur.
              </p>
            </div>

            <!-- Ordre fixe, toujours les 7 visibles : cliquer sur une carte ne
                 fait qu'en afficher un aperçu agrandi ci-dessus, elle reste
                 aussi ici à sa place habituelle. -->
            <div class="grille-graphiques">
              <div
                v-for="m in METRIQUES"
                :key="m.cle"
                class="graphique-carte"
                :class="{ actif: m.cle === metriqueFocus }"
                @click="metriqueFocus = m.cle"
              >
                <div class="graphique-carte-entete">
                  <p class="graphique-carte-titre">{{ m.label }}</p>
                  <span class="graphique-carte-valeur">{{ valeurCarte(m) }}</span>
                </div>
                <div class="chart-wrapper-carte">
                  <canvas :ref="(el) => (canvasEls[m.cle] = el)"></canvas>
                </div>
              </div>
            </div>
          </div>

          <div>
            <div class="panneau confiance-panneau">
              <p class="panneau-titre">chaîne de confiance</p>
              <ul class="confiance-liste">
                <li class="confiance-item">
                  <span class="confiance-label">valeur</span>
                  <span class="confiance-valeur">{{ formatValeurAvecUnite(derniereValeurFocus) }}</span>
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
                  <span class="confiance-valeur">
                    {{ incertitudeFocus === null ? "—" : `± ${formatValeur(incertitudeFocus, metriqueFocusInfo.decimales)} ${metriqueFocusInfo.unite}` }}
                  </span>
                </li>
              </ul>
            </div>

            <!-- Quatre états distincts, parce qu'ils appellent des réactions différentes :
                 un ajustement à faire, une surveillance, un « rien à signaler » qui est une
                 information en soi, et une absence de prévision qui n'est pas un feu vert. -->
            <div class="panneau recommandation" :class="classeRecommandation">
              <p class="recommandation-titre">recommandation</p>

              <template v-if="recommandations.length > 0">
                <div v-for="(reco, i) in recommandations" :key="i" class="reco-bloc">
                  <p class="reco-creneau">{{ formatCreneau(reco) }}</p>
                  <p class="recommandation-texte">{{ reco.message }}</p>
                  <div class="recommandation-gain" :class="reco.niveau">
                    +{{ formatValeur(reco.depassement_max_kw, 0) }} kW
                  </div>
                </div>
                <p class="reco-mention">Suggestion, aucune commande automatique n'est émise.</p>
              </template>

              <!-- previsionIndisponible compte autant que recommandationIndisponible : sans
                   prévision joignable il n'y a rien à analyser, et annoncer « aucun modèle pour
                   ce site » serait un diagnostic faux — le site en a peut-être un. -->
              <p
                v-else-if="recommandationIndisponible || previsionIndisponible"
                class="recommandation-texte"
              >
                Recommandations indisponibles : service injoignable.
              </p>
              <p v-else-if="previsions.length === 0" class="recommandation-texte">
                Aucun modèle de prévision pour ce site : impossible d'anticiper un dépassement.
              </p>
              <p v-else-if="siteActuel && siteActuel.capacity_kw === null" class="recommandation-texte">
                Puissance souscrite inconnue pour ce site : aucun seuil à comparer.
              </p>
              <p v-else class="recommandation-texte">
                Aucun dépassement prévu sur les prochaines heures.
              </p>
            </div>
          </div>
        </div>
      </template>

      <p class="section-titre">parc — {{ sites.length }} sites</p>
      <div class="parc-grille">
        <div
          v-for="s in sites"
          :key="s.site_id"
          class="parc-carte"
          :class="{ actif: s.site_id === siteSelectionne }"
          @click="selectionnerSite(s.site_id)"
        >
          <div class="id">{{ s.site_id }}</div>
          <div class="valeur" :class="{ alerte: resumeParSite[s.site_id]?.alerte }">
            {{ formatEntier(resumeParSite[s.site_id]?.valeur) }}
          </div>
          <svg viewBox="0 0 60 20" width="100%" height="20">
            <polyline
              v-for="(segment, i) in resumeParSite[s.site_id]?.segments || []"
              :key="i"
              :points="segment"
              fill="none"
              :stroke="resumeParSite[s.site_id]?.alerte ? '#f59e0b' : '#2dd4bf'"
              stroke-width="1.5"
            />
          </svg>
        </div>
      </div>

      <div class="kpi-grille">
        <template v-if="kpiJour">
          <div v-for="kpi in kpiJour" :key="kpi.label" class="kpi-carte">
            <div class="kpi-label">{{ kpi.label }}</div>
            <div class="kpi-valeur">{{ kpi.valeur }}<span class="kpi-unite">{{ kpi.unite }}</span></div>
          </div>
        </template>
        <p v-else class="pas-de-donnee-kpi">Résumé du jour pas encore calculé pour ce site.</p>

        <div
          v-if="risqueResume"
          class="kpi-carte risque-mini"
          title="Risque qu'une lecture signale une panne dans l'heure : capteur température, humidité ou tension en panne, perte réseau, ou autre cause remontée par l'API (null_reasons / data_quality). Tous sites confondus."
        >
          <div class="risque-mini-entete">
            <div class="kpi-label">
              Risque panne capteur
              <span class="risque-mini-badge">nuls, 24h</span>
            </div>
            <router-link to="/capteurs" class="risque-mini-lien">détail →</router-link>
          </div>
          <div class="risque-mini-corps">
            <div class="kpi-valeur">
              {{ Math.round(risqueResume.actuel.risk * 100) }}<span class="kpi-unite">%</span>
            </div>
            <svg
              :viewBox="risqueResume.viewBox"
              class="risque-mini-svg"
              preserveAspectRatio="none"
              role="img"
              aria-label="Tendance du risque de panne capteur sur les prochaines 24 heures"
            >
              <path :d="risqueResume.aire" class="risque-mini-aire" />
              <path :d="risqueResume.ligne" class="risque-mini-ligne" />
            </svg>
          </div>
          <p class="risque-mini-pic">pic {{ Math.round(risqueResume.pic.risk * 100) }}% à {{ risqueResume.pic.heure }}h</p>
        </div>
        <p v-else-if="risqueCapteurIndisponible" class="kpi-carte risque-mini-erreur">
          Risque panne capteur non actualisé.
        </p>
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
          <div class="panneau-entete">
            <p class="panneau-titre">alertes — {{ NB_ALERTES_DASHBOARD }} dernières</p>
            <router-link to="/alertes" class="lien-alertes">tout voir</router-link>
          </div>

          <p v-if="chargementAlertes" class="alertes-etat">Chargement des alertes...</p>
          <template v-else>
            <ul v-if="alertesRecentes.length > 0" class="alertes-liste">
              <li
                v-for="a in alertesRecentes"
                :key="a.alert_id"
                class="alerte-item"
                :class="[`severite-${a.severity || 'inconnue'}`, { critique: estCritique(a) }]"
              >
                <span class="alerte-point" :title="libelleSeverite(a.severity)"></span>
                <span class="alerte-heure">{{ formatInstant(a.timestamp) }}</span>
                <span class="alerte-texte">
                  <span class="alerte-site">{{ a.site_id || "parc" }}</span>
                  {{ texteAlerte(a) }}
                </span>
              </li>
            </ul>
            <p v-else class="alertes-etat">Aucune alerte sur le parc.</p>
            <p v-if="erreurAlertes" class="alertes-etat alertes-erreur">{{ erreurAlertes }}</p>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Chart, Interaction } from "chart.js/auto";
import zoomPlugin from "chartjs-plugin-zoom";
import { getRelativePosition } from "chart.js/helpers";
// Enregistre l'adaptateur de dates auprès de Chart.js (effet de bord, pas d'export) : sans lui
// l'échelle de type "time" du grand graphique lève au premier rendu.
import "chartjs-adapter-date-fns";
// Le bundle "auto" enregistre déjà tous les contrôleurs/éléments/échelles, mais pas ce plugin :
// c'est le seul qui reste à déclarer explicitement pour que `plugins.zoom` du grand graphique
// soit reconnu.
Chart.register(zoomPlugin);
import api from "../api/client";
import { theme } from "../theme";

// Lit une variable CSS du thème courant (posée sur <html> par src/theme.js) au moment
// de l'appel : le canevas Chart.js ne prend pas les couleurs des règles CSS comme le
// reste de la page, donc les graphiques doivent les relire eux-mêmes à chaque bascule
// jour/nuit plutôt que d'en garder une valeur figée.
function couleurTheme(nom) {
  return getComputedStyle(document.documentElement).getPropertyValue(nom).trim();
}

// etl-collect écrit measurements_silver environ une fois par minute — mais
// sonder à cette même cadence crée un effet de battement : si le cycle du
// front tombe juste avant l'écriture, il faut alors attendre presque 2
// minutes avant de voir la nouvelle ligne. Sonder 2x plus vite (30s) donne
// une bien meilleure chance de la capter peu après son écriture, sans pour
// autant appeler l'API Mock IoT en direct (/current, résolution factice de
// 5s sans rapport avec ce qui est vraiment persisté). Comme chaque cycle
// recharge tout le buffer (chargerHistorique), un cycle "à vide" (pas encore
// de nouvelle ligne) est inoffensif : mêmes données, même affichage.
const POLL_INTERVAL_MS = 30 * 1000;

const OPTIONS_FENETRE = [
  { label: "2 min", ms: 2 * 60 * 1000 },
  { label: "15 min", ms: 15 * 60 * 1000 },
  { label: "1 h", ms: 60 * 60 * 1000 },
  { label: "6 h", ms: 6 * 60 * 60 * 1000 },
  { label: "1 j", ms: 24 * 60 * 60 * 1000 },
  { label: "7 j", ms: 7 * 24 * 60 * 60 * 1000 }, // borne max de GET /sites/{id}/measurements
];

// Unités de la saisie libre, de la plus petite à la plus grande — l'ordre sert à choisir
// celle qui tombe juste pour afficher une période (« 7 j » plutôt que « 168 h »).
const UNITES_FENETRE = [
  { cle: "min", label: "min", ms: 60 * 1000 },
  { cle: "h", label: "h", ms: 60 * 60 * 1000 },
  { cle: "j", label: "j", ms: 24 * 60 * 60 * 1000 },
];

// Bornes de `depuis_minutes` sur GET /sites/{id}/measurements (api/routers/sites.py : ge=1,
// le=10080). Une saisie hors bornes ferait répondre 422 à l'API, donc un écran vide sur les
// sept métriques à la fois : elle est ramenée dans les bornes, et l'écran dit laquelle a été
// retenue plutôt que de laisser croire à une panne.
const FENETRE_MIN_MS = 60 * 1000;
const FENETRE_MAX_MS = 7 * 24 * 60 * 60 * 1000;

// Plafond de points demandés à l'API, quelle que soit la période : c'est ce que la fenêtre
// « 1 jour » traçait déjà à la minute sans que le rendu ne bronche. Au-delà, la réponse est
// éclaircie côté API (une lecture par tranche) — sans quoi 7 jours feraient ~10 000 lectures
// par site, pour les sept sites, à chaque cycle de sondage.
const POINTS_MAX = 1440;

function pasMinutesPour(fenetreMs) {
  return Math.max(1, Math.ceil(fenetreMs / 60000 / POINTS_MAX));
}

function msUnite(cle) {
  return UNITES_FENETRE.find((u) => u.cle === cle).ms;
}

// La plus grande unité qui tombe juste : « 7 j » plutôt que « 168 h », mais « 90 min » plutôt
// qu'une heure et demie arrondie — une période affichée arrondie serait une période fausse.
function uniteJuste(ms) {
  return [...UNITES_FENETRE].reverse().find((u) => ms % u.ms === 0) ?? UNITES_FENETRE[0];
}

function libelleDuree(ms) {
  const unite = uniteJuste(ms);
  return `${Math.round(ms / unite.ms)} ${unite.label}`;
}

// Une carte par métrique, dans measurements_silver. On expose volontairement
// pas consumption_kwh (toujours identique à consumption_kw dans ce mock),
// has_anomaly (jamais vrai sur les données collectées) ni
// consumption_change_pct (quasi toujours NULL) : voir api/models.py.
const METRIQUES = [
  {
    cle: "consumption_kw",
    couleur: "#2dd4bf",
    fond: "rgba(45, 212, 191, 0.08)",
    label: "Puissance appelée",
    unite: "kW",
    decimales: 0,
    debuteAZero: true,
    avecSeuil: true,
    avecPrediction: true,
  },
  {
    cle: "voltage_v",
    couleur: "#60a5fa",
    fond: "rgba(96, 165, 250, 0.08)",
    label: "Tension",
    unite: "V",
    decimales: 0,
    debuteAZero: false,
  },
  {
    cle: "current_a",
    couleur: "#f472b6",
    fond: "rgba(244, 114, 182, 0.08)",
    label: "Courant",
    unite: "A",
    decimales: 0,
    debuteAZero: true,
  },
  {
    cle: "power_factor",
    couleur: "#facc15",
    fond: "rgba(250, 204, 21, 0.08)",
    label: "Facteur de puissance",
    unite: "",
    decimales: 2,
    debuteAZero: false,
  },
  {
    cle: "temperature_celsius",
    couleur: "#fb923c",
    fond: "rgba(251, 146, 60, 0.08)",
    label: "Température",
    unite: "°C",
    decimales: 1,
    debuteAZero: false,
  },
  {
    cle: "humidity_percent",
    couleur: "#38bdf8",
    fond: "rgba(56, 189, 248, 0.08)",
    label: "Humidité",
    unite: "%",
    decimales: 0,
    debuteAZero: true,
  },
  {
    cle: "quality_score",
    couleur: "#a78bfa",
    fond: "rgba(167, 139, 250, 0.08)",
    label: "Score qualité",
    unite: "/100",
    decimales: 0,
    debuteAZero: true,
  },
];

const LIBELLES_RAISON = {
  humidity_sensor_failure: "capteur humidité en panne",
  temperature_sensor_failure: "capteur température en panne",
  voltage_sensor_failure: "capteur tension en panne",
};

// Contenu de démonstration : aucun suivi de modèle (santé/scoring) n'existe côté backend. Tout
// le reste de l'écran est réellement alimenté par l'API : sélecteur de site (dont la pastille
// d'alerte), en-tête, graphiques de mesures, grille "parc", KPI du jour, prévisions,
// recommandations et panneau d'alertes (GET /sites, GET /sites/{id}/measurements,
// GET /sites/{id}/daily-summary, GET /sites/{id}/predictions, GET /sites/{id}/recommendations,
// GET /alerts).
const SANTE_MOCK = [
  { label: "fiabilité", valeur: "0,71", ok: true },
  { label: "couverture — cible 96 %", valeur: "91,2 %", ok: false },
  { label: "fusible", valeur: "actif", ok: true },
];

// Aperçu du parc entier, pas du site affiché : une alerte critique ailleurs
// doit rester visible sans changer de site. La liste complète, avec ses
// filtres, est sur /alertes — ce panneau n'en montre que la tête.
const NB_ALERTES_DASHBOARD = 5;

const LIBELLES_SEVERITE = {
  low: "faible",
  medium: "moyenne",
  high: "élevée",
  critical: "critique",
};

// Ces deux niveaux passent le texte en rouge : ils appellent une action, là
// où "faible" et "moyenne" ne sont qu'à lire.
const SEVERITES_CRITIQUES = ["high", "critical"];

// Site proche de sa limite contractuelle : signal réel (dernière puissance
// mesurée / puissance souscrite), pas une couleur mise au hasard.
const SEUIL_ALERTE_PARC = 0.9;

// Chaque métrique porte sa couleur (déclarée dans METRIQUES) ; l'ambre reste
// réservé à la puissance souscrite et le violet à la prévision.
const COULEUR_SEUIL = "#f59e0b";

// Prévision de consommation servie par GET /sites/{id}/predictions (table
// predictions_forecast, réécrite à chaque cycle de ml/predict.py). Plus aucun mock ici : ce
// que trace la courbe violette vient du registry MLflow, modèle et intervalle compris.
//
// Le pas est horaire et non plus continu — les modèles sont calendaires (168 créneaux par
// semaine), structurellement incapables de descendre sous l'heure. L'unité change donc en
// apparence (predicted_kwh, pas des kW), mais les deux courbes restent comparables sur le même
// axe : l'énergie livrée en une heure vaut numériquement la puissance moyenne de cette heure.
// Cette équivalence ne tient QUE parce que step_minutes vaut 60 ; un futur modèle au quart
// d'heure imposerait une conversion explicite.
const COULEUR_PREDICTION = "#a78bfa";
const COULEUR_BANDE_PREDICTION = "rgba(167, 139, 250, 0.15)";

// La prévision vise un tiers de la largeur du graphique : sur une fenêtre W, prolonger de W/2
// donne W/(W + W/2) = 2/3 d'historique pour 1/3 de prévision. Prolonger de W — ce que faisait
// la version précédente — donnait moitié-moitié, où le futur pèse autant que le mesuré.
//
// Le plancher d'une heure reste : les modèles sont horaires, on ne sait pas prédire plus fin.
// Il rend le tiers inatteignable sur la fenêtre 1 h (0,5 h arrondi à 1 h, soit la moitié) —
// écart assumé, l'alternative étant de ne rien afficher sur cette fenêtre.
const HEURES_PREVISION_MAX = 24;
const PART_PREVISION = 0.5;

// En deçà d'une heure d'historique, la prévision n'est pas tracée. Le pas des modèles est
// l'heure : sur la fenêtre 2 min (celle par défaut), un seul point de prévision étendrait
// l'axe temporel à 62 min et réduirait les mesures à 3 % de la largeur. Le problème est
// symétrique de celui de l'échelle catégorielle — une durée y écrasait l'autre, ici c'est
// l'inverse — et se règle en ne montrant la prévision qu'aux fenêtres où elle a une place.
const FENETRE_MIN_PREVISION_MS = 60 * 60 * 1000;

function heuresPrevisionPour(fenetreMs) {
  const heures = (fenetreMs / (60 * 60 * 1000)) * PART_PREVISION;
  return Math.min(HEURES_PREVISION_MAX, Math.max(1, Math.round(heures)));
}

// Profondeur de prévisions passées demandée à l'API : toute la fenêtre affichée, pour que la
// courbe prédite couvre exactement la même période que les mesures et qu'on puisse lire l'écart
// entre prévu et réalisé sur toute la largeur. Arrondi au-dessus, une fenêtre partiellement
// couverte laissant un blanc au bord gauche.
function heuresHistoriquePrevisionPour(fenetreMs) {
  return Math.min(168, Math.max(1, Math.ceil(fenetreMs / (60 * 60 * 1000))));
}

function libelleRaison(raison) {
  return LIBELLES_RAISON[raison] || raison.replaceAll("_", " ");
}

function formatEntier(nombre) {
  return nombre === null || nombre === undefined ? "—" : String(Math.round(nombre));
}

function formatValeur(nombre, decimales = 0) {
  return nombre === null || nombre === undefined ? "—" : Number(nombre).toFixed(decimales);
}

// Heure rendue dans le fuseau du navigateur : l'API livre des horodatages bruts, et rien dans
// ses messages ne fige l'heure en UTC.
function formatHeure(instant) {
  return new Date(instant).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function formatMillier(nombre) {
  return nombre === null || nombre === undefined ? "—" : Math.round(nombre).toLocaleString("fr-FR");
}

const sites = ref([]);
const siteSelectionne = ref("");
const metriqueFocus = ref(METRIQUES[0].cle);
const fenetreMs = ref(OPTIONS_FENETRE[0].ms);
// Brouillon du formulaire de période : ce que l'utilisateur a tapé, pas encore appliqué.
// `fenetreMs` reste la seule période tracée, et le résumé à l'écran ne parle que d'elle —
// un champ en cours de saisie ne doit jamais laisser croire que le graphique a suivi.
const saisieOuverte = ref(false);
// Vides jusqu'à l'ouverture du formulaire : synchroniserBrouillon() les remplit alors avec la
// période affichée, et le champ n'existe pas avant (v-if).
const dureeBrouillon = ref("");
const uniteBrouillon = ref("min");
const messagePeriode = ref("");
const saisieInvalide = ref(false);
const champDureeRef = ref(null);
const chargementSites = ref(false);
const erreurSites = ref("");
const erreurLecture = ref("");
const derniereLecture = ref(null);
const derniereMesure = ref(null); // dernière ligne measurements_silver reçue pour le site affiché
const derniereQualite = ref("");
const derniereRaisons = ref([]);
const tauxDisponibilite = ref(null); // % de points avec une valeur, sur la métrique et la fenêtre affichées
const resumeJour = ref(null); // aggregates_gold_daily du jour pour le site affiché (null si pas encore calculé)
const previsions = ref([]); // predictions_forecast à venir pour le site affiché ([] si aucun modèle)
const previsionIndisponible = ref(false); // l'appel a échoué, à distinguer d'un site sans modèle
const recommandations = ref([]); // fenêtres de dépassement à venir pour le site affiché
const recommandationIndisponible = ref(false); // l'appel a échoué, à distinguer d'un « rien à signaler »
const resumeParSite = ref({}); // { [site_id]: { valeur, alerte, segments } } pour la grille "parc" et le sélecteur
const alertesRecentes = ref([]); // les NB_ALERTES_DASHBOARD dernières lignes de la table alerts, tous sites confondus
// Vrai dès la déclaration, faux définitivement à la première réponse : le
// relever à chaque cycle de sondage remplacerait la liste par "Chargement..."
// toutes les 30 secondes.
const chargementAlertes = ref(true);
const erreurAlertes = ref("");
const maintenant = ref(new Date());

// Risque de panne capteur heure par heure (table sensor_failure_forecast, réécrite à
// chaque cycle de ml/scripts/predict_sensor_failure.py). Global, pas par site : le motif
// horaire et la tendance quotidienne sont confirmés identiques sur les 7 sites, voir le
// tag "caveat" du run MLflow enervision-sensor-failure-forecast.
const risqueCapteur = ref([]);
const risqueCapteurIndisponible = ref(false);

const siteActuel = computed(() => sites.value.find((s) => s.site_id === siteSelectionne.value) || null);
const metriqueFocusInfo = computed(() => METRIQUES.find((m) => m.cle === metriqueFocus.value));
const derniereValeurFocus = computed(() => derniereMesure.value?.[metriqueFocus.value] ?? null);
const qualiteFiable = computed(() => !derniereQualite.value || derniereQualite.value === "good");
const raisonsQualite = computed(() => derniereRaisons.value.map(libelleRaison).join(", "));

// Sparkline du risque de panne capteur : juste la forme, sans graduations — le détail
// heure par heure avec échelle vit sur la page Capteurs (/capteurs). Mêmes bornes
// verticales (50 % à 85 %) que ce graphique détaillé, pour que la forme reste identique
// entre les deux vues.
const RISQUE_MINI_VMIN = 0.5;
const RISQUE_MINI_VMAX = 0.85;
const RISQUE_MINI_W = 160;
const RISQUE_MINI_H = 40;

const risqueResume = computed(() => {
  const points = risqueCapteur.value;
  if (points.length === 0) return null;

  const xy = (i, risk) => {
    const x = (i / (points.length - 1)) * RISQUE_MINI_W;
    const clamped = Math.max(RISQUE_MINI_VMIN, Math.min(RISQUE_MINI_VMAX, risk));
    const y = RISQUE_MINI_H - ((clamped - RISQUE_MINI_VMIN) / (RISQUE_MINI_VMAX - RISQUE_MINI_VMIN)) * RISQUE_MINI_H;
    return [x, y];
  };

  const coords = points.map((p, i) => {
    const [x, y] = xy(i, Number(p.risk));
    return { x, y, heure: new Date(p.target_hour).getHours(), risk: Number(p.risk) };
  });

  const ligne = coords.map((c, i) => `${i === 0 ? "M" : "L"} ${c.x} ${c.y}`).join(" ");
  const aire = `M 0 ${RISQUE_MINI_H} ` + coords.map((c) => `L ${c.x} ${c.y}`).join(" ") + ` L ${RISQUE_MINI_W} ${RISQUE_MINI_H} Z`;

  // coords[0] est l'heure la plus proche (première ligne renvoyée par l'API, filtrée sur
  // "maintenant"), pas forcément l'heure pleine en cours.
  const actuel = coords[0];
  const pic = coords.reduce((a, b) => (b.risk > a.risk ? b : a));

  return { coords, ligne, aire, actuel, pic, viewBox: `0 0 ${RISQUE_MINI_W} ${RISQUE_MINI_H}` };
});

// Provenance du modèle, reprise de la première ligne : toutes les lignes d'un même site
// partagent le même run MLflow. Affichée à l'écran parce que les modèles V1 sont entraînés sur
// CSV synthétique — sans ce rappel, ces courbes passeraient pour des prévisions validées.
const infoPrevision = computed(() => previsions.value[0] ?? null);
const previsionSurDonneeSynthetique = computed(() => infoPrevision.value?.data_source === "csv_synthetic");
const previsionTropCourtePourFenetre = computed(() => fenetreMs.value < FENETRE_MIN_PREVISION_MS);

const maxBrouillon = computed(() => FENETRE_MAX_MS / msUnite(uniteBrouillon.value));
// Aucune pilule ne peut rester allumée sur une période saisie à la main : c'est alors
// « personnalisée… » qui l'est, sans quoi deux périodes différentes seraient lisibles à
// l'écran pour la seule courbe tracée.
const prereglageActif = computed(() => OPTIONS_FENETRE.some((o) => o.ms === fenetreMs.value));

// Ce que le graphique montre, en une ligne : la durée, et l'instant où elle commence. La
// fenêtre glisse avec le temps, donc le début est recalculé à chaque battement d'horloge.
const resumePeriode = computed(() => {
  const debut = new Date(maintenant.value.getTime() - fenetreMs.value);
  // Le pas est écrit dès qu'il dépasse la minute : passé 24 h, la courbe ne porte plus une
  // lecture par minute, et rien d'autre à l'écran ne le dirait.
  const pas = pasMinutesPour(fenetreMs.value);
  const echantillon = pas > 1 ? ` · 1 point / ${pas} min` : "";
  return `${libelleDuree(fenetreMs.value)} depuis ${formatInstant(debut)}${echantillon}`;
});

// L'unité va dans le titre plutôt que sur chaque graduation : l'axe reste une
// colonne de nombres nus, et l'information n'est écrite qu'une fois.
const titreMetriqueFocus = computed(() => {
  const m = metriqueFocusInfo.value;
  // "/100" est une échelle, pas une unité : "Score qualité (/100)" se lirait mal,
  // et l'axe 0-100 la donne déjà.
  return m.unite && !m.unite.startsWith("/") ? `${m.label} (${m.unite})` : m.label;
});

// Demi-largeur de la bande upper_90/lower_90 sur la prévision la plus proche : même null que
// dans le graphique quand le run MLflow ne porte pas de marge conforme, plutôt qu'une valeur
// inventée à 0.
const incertitudeFocus = computed(() => {
  if (!metriqueFocusInfo.value.avecPrediction) return null;
  const { upper_90: haut, lower_90: bas } = infoPrevision.value ?? {};
  if (haut == null || bas == null) return null;
  return (haut - bas) / 2;
});

// La bordure du panneau reprend le niveau le plus grave : un dépassement prévu et un simple
// risque n'appellent pas la même réaction, et la couleur doit le dire avant la lecture.
const classeRecommandation = computed(() => {
  if (recommandations.value.some((r) => r.niveau === "depassement_prevu")) return "critique";
  if (recommandations.value.length > 0) return "avertissement";
  return "";
});

// `fin` est une borne exclusive — la fin du dernier créneau, pas son début.
function formatCreneau(reco) {
  return `${formatHeure(reco.debut)} — ${formatHeure(reco.fin)}`;
}

const suffixeUnite = computed(() =>
  metriqueFocusInfo.value.unite ? `${metriqueFocusInfo.value.unite} · dernière mesure` : "dernière mesure"
);

// Les 7 cartes n'affichaient qu'un titre et une sparkline : lire une valeur
// imposait de cliquer, une métrique à la fois. La dernière mesure de chacune rend
// la grille lisible d'un coup d'œil, sans rien changer à la sélection.
function valeurCarte(m) {
  const valeur = derniereMesure.value?.[m.cle] ?? null;
  if (valeur === null) return "—";
  const texte = formatValeur(valeur, m.decimales);
  return m.unite ? `${texte} ${m.unite}` : texte;
}

function formatValeurAvecUnite(valeur) {
  const texte = formatValeur(valeur, metriqueFocusInfo.value.decimales);
  return metriqueFocusInfo.value.unite ? `${texte} ${metriqueFocusInfo.value.unite}` : texte;
}

// Résumé du jour (aggregates_gold_daily, recalculé par etl/quality.py) : seul
// panneau "KPI" de la page à être réel plutôt que du contenu de démonstration.
const kpiJour = computed(() => {
  const r = resumeJour.value;
  if (!r) return null;
  const tauxFiable = r.records_count > 0 ? Math.round((r.good_count / r.records_count) * 100) : null;
  return [
    { label: "Consommation du jour", valeur: formatMillier(r.total_consumption_kwh), unite: " kWh" },
    { label: "Puissance moyenne", valeur: formatValeur(r.avg_consumption_kw, 0), unite: " kW" },
    { label: "Pic de puissance", valeur: formatValeur(r.max_consumption_kw, 0), unite: " kW" },
    { label: "Score qualité moyen", valeur: formatValeur(r.avg_quality_score, 0), unite: "/100" },
    { label: "Lectures fiables", valeur: tauxFiable === null ? "—" : String(tauxFiable), unite: " %" },
  ];
});

const ecouleDepuisDerniereLecture = computed(() => {
  if (!derniereLecture.value) return "";
  const secondes = Math.max(0, Math.round((maintenant.value - derniereLecture.value) / 1000));
  if (secondes < 60) return `${secondes}s`;
  return `${Math.floor(secondes / 60)}min ${secondes % 60}s`;
});

function libelleSeverite(severite) {
  return `sévérité ${LIBELLES_SEVERITE[severite] || "inconnue"}`;
}

function estCritique(alerte) {
  return SEVERITES_CRITIQUES.includes(alerte.severity);
}

function texteAlerte(alerte) {
  return alerte.message || alerte.type || "alerte sans description";
}

function formatInstant(timestamp) {
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";

  const heure = date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  // Le jour n'est ajouté que hors de la journée en cours : la colonne des alertes est
  // étroite, mais "08:12" seul ferait passer une alerte d'avant-hier pour une alerte de ce
  // matin — et une période de 24 h pour une période commencée ce matin. maintenant.value
  // rend la bascule de minuit réactive.
  if (date.toDateString() === maintenant.value.toDateString()) return heure;
  return `${date.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} ${heure}`;
}

const canvasGrandRef = ref(null);
let chartGrand = null;
// Reflète l'état du plugin zoom (lui-même hors de la réactivité Vue) : sert uniquement à
// afficher ou non le bouton de réinitialisation, pas de logique de zoom ici.
const grandGraphiqueEstZoome = ref(false);

function reinitialiserZoomGraphiqueGrand() {
  chartGrand?.resetZoom();
  grandGraphiqueEstZoome.value = false;
}
// Instant porté par le titre de l'infobulle, retenu par le callback `title` pour les callbacks
// `label` qui le suivent — Chart.js construit toujours le titre avant le corps. Sans lui, une
// ligne ne saurait pas si elle tombe sur l'instant lu ou sur un autre.
let instantTitreInfobulle = null;
const canvasEls = {};
const charts = {};
// Historique + dernière lecture par site (rempli en continu pour tous les
// sites à chaque cycle) : changer de site ne fait qu'afficher un buffer déjà
// alimenté, sans appel réseau ni retour à zéro — donc pas de flash "0 donnée".
const historiques = {};
const dernieresLectures = {};
let intervalSondage = null;
let intervalHorloge = null;

// Un marqueur sur chaque point donnerait un chapelet illisible : la fenêtre
// "1 jour" contient jusqu'à 1440 mesures pour ~900 px de large. Mais un point
// isolé — une valeur seule entre deux trous, cas courant avec ~30-50 % de lectures
// sans valeur — ne dessine aucun segment et disparaîtrait purement et simplement
// sans marqueur. On n'en affiche donc que là où il porte l'information : sur les
// points que la ligne ne relie à rien.
function rayonPointIsole(ctx) {
  const data = ctx.dataset.data;
  const i = ctx.dataIndex;
  const present = (j) => data[j] !== null && data[j] !== undefined;
  if (!present(i)) return 0;
  return present(i - 1) || present(i + 1) ? 0 : 2.5;
}

// Chart.js n'offre aucun mode d'interaction qui apparie les séries par horodatage. "index" les
// apparie par numéro de point : les mesures en portent ~360 (à la minute) contre ~9 pour la
// prévision (à l'heure), si bien que survoler 09:05 affichait la prévision d'index 5 — une tout
// autre heure. "nearest" ne renvoie qu'un seul point, celui de la série la plus dense : la
// mesure, à 30 s du curseur, gagnait toujours contre une prévision à 30 min, et on ne voyait
// jamais qu'une des trois séries.
//
// Ce mode renvoie, pour chaque série, le point dont la « cellule » contient le curseur — la
// cellule allant jusqu'à mi-chemin des voisins. Chaque série est donc lue à son propre pas : la
// prévision horaire couvre l'axe par tranches de 30 min (plus besoin de viser le point), la
// mesure à la minute par tranches de 30 s. Passé la dernière mesure, plus aucune cellule de
// mesure ne couvre le curseur : la partie prédite n'affiche pas de mesure, plutôt que de
// reporter la dernière connue à une heure où elle n'existe pas.
const MODE_MEME_INSTANT = "memeInstant";

function pointsAuMemeInstant(chart, evenement) {
  const position = getRelativePosition(evenement, chart);
  const trouves = [];

  for (const meta of chart.getSortedVisibleDatasetMetas()) {
    // Les bornes de l'incertitude ne sont pas survolables : elles ne servent qu'à remplir la
    // bande, et les activer ferait apparaître deux points parasites sur ses lisières.
    if (chart.data.datasets[meta.index]?.decoratif) continue;

    let choisi = -1;
    let ecartChoisi = Number.POSITIVE_INFINITY;
    for (let i = 0; i < meta.data.length; i += 1) {
      // Un point sans valeur (NULL en base, ~30-50 % des lectures) n'est pas candidat, mais
      // reste dans le tableau : il compte donc comme voisin ci-dessous, et un trou de mesures
      // reste un trou au lieu d'élargir la cellule des points qui l'encadrent.
      if (meta.data[i].skip) continue;
      const ecart = Math.abs(meta.data[i].x - position.x);
      if (ecart < ecartChoisi) {
        ecartChoisi = ecart;
        choisi = i;
      }
    }

    if (choisi !== -1 && ecartChoisi <= demiEcartAuVoisin(meta.data, choisi)) {
      trouves.push({ element: meta.data[choisi], datasetIndex: meta.index, index: choisi });
    }
  }
  return trouves;
}

// Demi-distance en pixels au voisin le plus proche, soit la demi-largeur de la cellule du
// point. Sans voisin — série d'un seul point, ou abscisses inattendues (NaN) — la cellule
// couvre tout l'axe : mieux vaut une série toujours lisible qu'une série jamais affichée.
function demiEcartAuVoisin(points, i) {
  const gauche = i > 0 ? points[i].x - points[i - 1].x : Number.POSITIVE_INFINITY;
  const droite = i + 1 < points.length ? points[i + 1].x - points[i].x : Number.POSITIVE_INFINITY;
  const ecart = Math.min(gauche, droite);
  return Number.isFinite(ecart) ? ecart / 2 : Number.POSITIVE_INFINITY;
}

Interaction.modes[MODE_MEME_INSTANT] = pointsAuMemeInstant;

function construireDatasets(m, { avecPrediction = false } = {}) {
  const datasets = [
    {
      label: m.label,
      data: [],
      borderColor: m.couleur,
      backgroundColor: m.fond,
      fill: true,
      borderWidth: 2,
      // Segments droits, sans lissage : `tension` dessine des Béziers qui dépassent
      // les valeurs mesurées, donc un sommet plus haut que la mesure la plus haute.
      // Sur un écran dont la question est « le pic a-t-il dépassé la puissance
      // souscrite ? », ce dépassement est une réponse inventée.
      tension: 0,
      pointRadius: rayonPointIsole,
      pointHoverRadius: 4,
    },
  ];
  if (m.avecSeuil) {
    datasets.push({
      label: "Puissance souscrite (kW)",
      data: [],
      borderColor: COULEUR_SEUIL,
      borderDash: [4, 3],
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 3,
      fill: false,
      // Horizontale tracée entre les deux bords de l'axe : elle vaut autant à toute heure, et
      // l'horodatage de ses deux points ne désigne rien. L'infobulle ne l'affiche donc jamais
      // comme instant de référence, et ne rappelle pas son heure.
      constante: true,
    });
  }
  // Uniquement sur le grand graphique, et seulement pour la puissance appelée :
  // les petites cartes restent des sparklines de mesures réelles.
  if (avecPrediction && m.avecPrediction) {
    datasets.push(
      {
        label: "Prévision",
        data: [],
        borderColor: COULEUR_PREDICTION,
        borderDash: [5, 4],
        borderWidth: 2,
        // Même raison que pour les mesures : le modèle donne une valeur par heure,
        // courber entre deux heures inventerait un relief qu'il ne prédit pas.
        tension: 0,
        // Marqueurs visibles, contrairement aux bornes : un point par heure prédite rend le
        // pas du modèle lisible à l'œil. Sans eux, la ligne se confond avec la courbe des
        // mesures à la minute et laisse croire à une prévision beaucoup plus fine.
        pointRadius: 2,
        pointHoverRadius: 4,
        fill: false,
      },
      {
        // Borne haute de l'incertitude, remplie jusqu'au dataset suivant (la
        // borne basse) : c'est ce qui dessine la bande autour de la prédiction.
        label: "Borne haute",
        data: [],
        borderColor: "transparent",
        backgroundColor: COULEUR_BANDE_PREDICTION,
        fill: "+1",
        tension: 0,
        pointRadius: 0,
        // Série de rendu, pas de lecture : ni survolable, ni affichée dans l'infobulle.
        decoratif: true,
      },
      {
        label: "Borne basse",
        data: [],
        borderColor: "transparent",
        fill: false,
        tension: 0,
        pointRadius: 0,
        decoratif: true,
      }
    );
  }
  return datasets;
}

function creerGraphiques() {
  for (const m of METRIQUES) {
    const canvas = canvasEls[m.cle];
    if (!canvas) continue;

    charts[m.cle] = new Chart(canvas, {
      type: "line",
      data: { labels: [], datasets: construireDatasets(m) },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        // Rendu sparkline épuré, sans axes : la carte agrandie ci-dessus est
        // le seul endroit où l'échelle est affichée. La position et l'ordre
        // de ces 7 petites cartes ne changent jamais, cliquer sur l'une
        // d'elles l'affiche juste en grand ci-dessus, en plus.
        scales: {
          x: { display: false },
          y: { display: false, beginAtZero: m.debuteAZero },
        },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }
}

const POLICE_GRAPHE = '10px Consolas, "Liberation Mono", monospace';
const ETIQUETTE_MAINTENANT = "maintenant";

// L'axe du grand graphique porte du mesuré ET du prédit, bout à bout. Rien ne
// disait où l'un s'arrête : la seule marque était le pointillé de la courbe, qu'on
// ne remarque pas au premier coup d'œil — et sur une métrique sans prévision la
// question ne se pose pas. Le séparateur matérialise l'instant de la dernière
// mesure et teinte tout ce qui est à sa droite : au-delà, aucune valeur n'est mesurée.
const separateurPrevision = {
  id: "separateurPrevision",
  beforeDatasetsDraw(chart) {
    if (!derniereLecture.value) return;
    if (previsions.value.length === 0 || previsionTropCourtePourFenetre.value) return;

    const { ctx, chartArea, scales } = chart;
    const x = scales.x.getPixelForValue(derniereLecture.value.getTime());
    // Hors du cadre : la dernière mesure est plus vieille que la fenêtre affichée,
    // il n'y a alors pas de frontière à tracer dedans.
    if (!Number.isFinite(x) || x < chartArea.left || x >= chartArea.right) return;

    ctx.save();
    ctx.fillStyle = "rgba(167, 139, 250, 0.05)";
    ctx.fillRect(x, chartArea.top, chartArea.right - x, chartArea.bottom - chartArea.top);

    ctx.strokeStyle = couleurTheme("--shell-line");
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = couleurTheme("--shell-muted");
    ctx.font = POLICE_GRAPHE;
    ctx.textBaseline = "top";
    // Étiquette basculée à gauche du trait quand la place manque à droite, plutôt
    // que tronquée au bord du cadre.
    const aDroite = x + 6 + ctx.measureText(ETIQUETTE_MAINTENANT).width <= chartArea.right;
    ctx.textAlign = aDroite ? "left" : "right";
    ctx.fillText(ETIQUETTE_MAINTENANT, aDroite ? x + 6 : x - 6, chartArea.top + 4);
    ctx.restore();
  },
};

// Un graphique à l'écran est interactif : le croisillon donne le repère vertical
// que l'infobulle seule ne donne pas, celui qui permet de reporter un point sur
// l'axe des temps sans le perdre de vue.
const croisillon = {
  id: "croisillon",
  afterDatasetsDraw(chart) {
    const actifs = chart.tooltip?.getActiveElements?.() ?? [];
    if (actifs.length === 0) return;

    const { ctx, chartArea } = chart;
    const x = actifs[0].element?.x;
    if (!Number.isFinite(x)) return;

    ctx.save();
    ctx.strokeStyle = couleurTheme("--shell-line");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

function creerGraphiqueGrand() {
  if (!canvasGrandRef.value) return;
  const m = metriqueFocusInfo.value;

  chartGrand?.destroy();
  grandGraphiqueEstZoome.value = false;
  chartGrand = new Chart(canvasGrandRef.value, {
    type: "line",
    data: { labels: [], datasets: construireDatasets(m, { avecPrediction: true }) },
    plugins: [separateurPrevision, croisillon],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      // Les trois séries sont lues ensemble, à l'instant survolé et chacune à son propre pas :
      // voir MODE_MEME_INSTANT. Surtout PAS "index", qui apparie par numéro de point et
      // fabriquait des prévisions à la minute, ni "nearest", qui n'en renvoie qu'une des trois.
      // Aucune n'exige que le curseur touche le point : il suffit d'être dans le graphique.
      interaction: { mode: MODE_MEME_INSTANT },
      scales: {
        // Échelle temporelle et non catégorielle : les mesures arrivent à la minute, la
        // prévision à l'heure. Sur une échelle catégorielle, chaque point occupe la même
        // largeur quelle que soit la durée qu'il représente — 6 h de prévision (6 points)
        // se tassaient sur 1,6 % de l'axe face à 6 h de mesures (360 points), donnant une
        // courbe illisible et un pic qui paraissait instantané alors qu'il s'étale sur
        // une heure. En "time", chaque point est placé à son horodatage réel.
        x: {
          type: "time",
          time: {
            // Pas d'unité imposée : Chart.js l'adapte à l'étendue affichée, de la fenêtre
            // 2 min à la journée. Formats numériques, donc pas de locale à charger.
            displayFormats: { minute: "HH:mm", hour: "HH:mm", day: "dd/MM" },
            tooltipFormat: "dd/MM HH:mm",
          },
          ticks: {
            color: couleurTheme("--shell-muted"),
            maxRotation: 0,
            autoSkip: true,
            font: { size: 10 },
          },
          // Pas de verticales : sur une série à la minute elles n'aident à lire aucune
          // valeur et posent autant de traits que de graduations. L'axe suffit.
          grid: { display: false },
          border: { color: couleurTheme("--shell-border") },
        },
        y: {
          beginAtZero: m.debuteAZero,
          ticks: { color: couleurTheme("--shell-muted"), font: { size: 10 } },
          // Horizontales seules, et plus discrètes que le filet des panneaux : elles
          // servent à reporter une valeur sur l'axe, pas à quadriller l'image.
          grid: { color: couleurTheme("--shell-border"), drawTicks: false },
          border: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: MODE_MEME_INSTANT,
          // Ancrage sur le point le plus proche du curseur, et non sur la moyenne des points
          // affichés (défaut) : le seuil n'ayant que deux points, aux deux bords de l'axe,
          // cette moyenne emportait l'infobulle à des centaines de pixels du curseur.
          position: "nearest",
          backgroundColor: couleurTheme("--shell-panel-2"),
          borderColor: couleurTheme("--shell-border"),
          borderWidth: 1,
          titleColor: couleurTheme("--shell-text"),
          bodyColor: couleurTheme("--shell-text"),
          padding: 10,
          // Les deux bornes de l'incertitude ne sont là que pour dessiner la bande : les lire
          // n'apprendrait rien. Le mode d'interaction les écarte déjà, ce filtre reste le
          // garde-fou si l'infobulle retombait un jour sur un mode standard.
          filter: (item) => !item.dataset.decoratif,
          callbacks: {
            // L'instant lu est celui de la mesure survolée, à défaut celui de la prévision
            // (dans la partie prédite, il n'y a pas encore de mesure). Jamais celui du seuil :
            // ses deux points sont aux bords de l'axe et leur heure ne désigne rien.
            title: (items) => {
              const reference = items.find((item) => !item.dataset.constante) ?? items[0];
              instantTitreInfobulle = reference?.parsed?.x ?? null;
              return reference?.label ?? "";
            },
            // Le pas des séries diffère : la mesure est à la minute, la prévision à l'heure.
            // Une ligne qui ne tombe pas sur l'instant du titre rappelle donc le sien, sans
            // quoi l'infobulle laisserait croire à une prévision à la minute — celle que le
            // correctif du mode "index" avait justement pour but de ne plus inventer.
            label: (ctx) => {
              const valeur = `${formatValeur(ctx.parsed.y, m.decimales)} ${m.unite}`.trimEnd();
              const decale = !ctx.dataset.constante && ctx.parsed.x !== instantTitreInfobulle;
              const serie = decale
                ? `${ctx.dataset.label} (${formatHeure(ctx.parsed.x)})`
                : ctx.dataset.label;
              return `${serie}: ${valeur}`;
            },
          },
        },
        // Sélectionner une zone à la souris (ex. 1 h → 19 h) zoome dessus ; le bouton "réinitialiser
        // le zoom" du template appelle chart.resetZoom(). Ni molette ni pincement : ce sont des
        // gestes qu'on ne veut pas voler à la page (défilement) sans que l'utilisateur l'ait demandé.
        zoom: {
          zoom: {
            drag: {
              enabled: true,
              backgroundColor: "rgba(45, 212, 191, 0.15)",
              borderColor: "rgba(45, 212, 191, 0.6)",
              borderWidth: 1,
            },
            mode: "x",
            onZoomComplete: () => {
              grandGraphiqueEstZoome.value = true;
            },
          },
        },
      },
    },
  });

  rafraichirGraphiqueGrand();
}

function rafraichirGraphiqueGrand() {
  if (!chartGrand) return;
  const buffer = bufferPour(siteSelectionne.value);
  const m = metriqueFocusInfo.value;
  const valeurs = buffer.metriques[m.cle];

  if (!m.avecPrediction) {
    chartGrand.data.labels = buffer.instants;
    chartGrand.data.datasets[0].data = valeurs;
    if (m.avecSeuil) chartGrand.data.datasets[1].data = buffer.seuils;
    chartGrand.update();
    return;
  }

  const capacite = siteActuel.value?.capacity_kw ?? null;
  // Passé compris : l'API renvoie aussi les heures écoulées (historique_heures), qui ne sont
  // pas des prévisions périmées mais celles réellement émises pour ces heures-là. Les tracer
  // en regard des mesures est le seul moyen de voir, à l'œil, si le modèle tombe juste.
  const lignes = previsionTropCourtePourFenetre.value ? [] : previsions.value;

  if (lignes.length === 0) {
    // Site sans modèle inférable, ou API en échec : on n'invente rien, la courbe s'arrête
    // simplement au dernier point mesuré. Le message d'explication est dans le template.
    chartGrand.data.labels = buffer.instants;
    chartGrand.data.datasets[0].data = valeurs;
    chartGrand.data.datasets[1].data = buffer.seuils;
    chartGrand.data.datasets[2].data = [];
    chartGrand.data.datasets[3].data = [];
    chartGrand.data.datasets[4].data = [];
    chartGrand.update();
    return;
  }

  // Points {x, y} et non tableaux alignés sur `labels` : les prévisions sont horaires, les
  // mesures à la minute, et les deux séries ne partagent aucun horodatage. Les faire cohabiter
  // dans un tableau indexé imposerait de combler chaque minute sans prévision — soit inventer
  // une résolution que le modèle n'a pas. Sur une échelle temporelle, chaque série porte ses
  // propres abscisses et Chart.js les place côte à côte.
  //
  // lower_90/upper_90 sont NULL quand le run MLflow ne porte pas de marge conforme. On propage
  // le null plutôt que de replier la borne sur la valeur centrale, ce qui dessinerait une bande
  // d'épaisseur nulle — soit visuellement une prévision certaine.
  const enPoints = (champ) =>
    lignes.map((ligne) => ({ x: new Date(ligne.target_ts), y: ligne[champ] ?? null }));

  // Le seuil ne se dessine plus point par point : deux extrémités suffisent à tracer une
  // horizontale, et elle doit couvrir la partie prédite, que `buffer.seuils` ne connaît pas.
  const debutVisible = buffer.instants[0] ?? new Date(lignes[0].target_ts);
  const finVisible = new Date(lignes[lignes.length - 1].target_ts);

  chartGrand.data.labels = buffer.instants;
  chartGrand.data.datasets[0].data = valeurs;
  chartGrand.data.datasets[1].data =
    capacite === null
      ? []
      : [
          { x: debutVisible, y: capacite },
          { x: finVisible, y: capacite },
        ];
  chartGrand.data.datasets[2].data = enPoints("predicted_kwh");
  chartGrand.data.datasets[3].data = enPoints("upper_90");
  chartGrand.data.datasets[4].data = enPoints("lower_90");
  chartGrand.update();
}

// Les prévisions arrivent par un appel séparé des mesures : sans ce watch, la courbe violette
// n'apparaîtrait qu'au cycle de sondage suivant après un changement de site.
watch(previsions, () => {
  rafraichirGraphiqueGrand();
});

watch(metriqueFocus, () => {
  // Reconstruit plutôt que mettre à jour en place : les datasets diffèrent
  // d'une métrique à l'autre (seuil présent ou non, couleur, formatage).
  creerGraphiqueGrand();

  const buffer = historiques[siteSelectionne.value];
  if (buffer) tauxDisponibilite.value = calculerDisponibilite(buffer);
});

// Les couleurs d'axes et d'infobulle du grand graphique sont lues une fois à sa
// création (Chart.js ne suit pas les variables CSS) : une bascule jour/nuit doit
// donc le reconstruire pour rester lisible dans le nouveau thème.
watch(theme, () => {
  creerGraphiqueGrand();
});

function bufferPour(siteId) {
  if (!historiques[siteId]) {
    const metriques = {};
    for (const m of METRIQUES) metriques[m.cle] = [];
    historiques[siteId] = { instants: [], labels: [], metriques, seuils: [] };
  }
  return historiques[siteId];
}

function ajouterPoint(buffer, mesure, site) {
  const instant = new Date(mesure.timestamp);
  buffer.instants.push(instant);
  buffer.labels.push(instant.toLocaleTimeString("fr-FR"));
  for (const m of METRIQUES) buffer.metriques[m.cle].push(mesure[m.cle] ?? null);
  buffer.seuils.push(site.capacity_kw ?? null);
}

function purgerAnciens(buffer) {
  // Purge par âge réel, pas par nombre de points : au fil des changements de
  // fenêtre, la longueur du buffer n'est pas un proxy fiable de sa durée.
  const limite = Date.now() - fenetreMs.value;
  while (buffer.instants.length && buffer.instants[0].getTime() < limite) {
    buffer.instants.shift();
    buffer.labels.shift();
    for (const m of METRIQUES) buffer.metriques[m.cle].shift();
    buffer.seuils.shift();
  }
}

function calculerDisponibilite(buffer) {
  const valeurs = buffer.metriques[metriqueFocus.value];
  const total = valeurs.length;
  const valides = valeurs.filter((v) => v !== null && v !== undefined).length;
  return total > 0 ? Math.round((valides / total) * 100) : null;
}

function segmentsSparkline(valeurs) {
  // Un <polyline> par plage continue de données réelles, jamais un trait
  // reliant deux points de part et d'autre d'un trou (même principe que les
  // graphiques principaux, spanGaps:false) : un sparkline honnête plutôt
  // qu'une tendance lissée qui masquerait les lectures manquantes.
  const indicesValides = [];
  for (let i = 0; i < valeurs.length; i++) {
    if (valeurs[i] !== null && valeurs[i] !== undefined) indicesValides.push(i);
  }
  if (indicesValides.length === 0) return [];

  const min = Math.min(...indicesValides.map((i) => valeurs[i]));
  const max = Math.max(...indicesValides.map((i) => valeurs[i]));
  const echelle = max > min ? max - min : 1;
  const n = valeurs.length;
  const xPour = (i) => (n > 1 ? (i / (n - 1)) * 60 : 30);
  const yPour = (v) => 18 - ((v - min) / echelle) * 16;

  const segments = [];
  let courant = [];
  for (let i = 0; i < n; i++) {
    if (valeurs[i] === null || valeurs[i] === undefined) {
      if (courant.length > 1) segments.push(courant.join(" "));
      courant = [];
    } else {
      courant.push(`${xPour(i).toFixed(1)},${yPour(valeurs[i]).toFixed(1)}`);
    }
  }
  if (courant.length > 1) segments.push(courant.join(" "));

  return segments;
}

function calculerResumeParc() {
  const resume = {};
  for (const site of sites.value) {
    const buffer = bufferPour(site.site_id);
    const derniereValeur = dernieresLectures[site.site_id]?.mesure?.consumption_kw ?? null;
    resume[site.site_id] = {
      valeur: derniereValeur,
      alerte:
        derniereValeur !== null && site.capacity_kw
          ? derniereValeur >= SEUIL_ALERTE_PARC * site.capacity_kw
          : false,
      segments: segmentsSparkline(buffer.metriques.consumption_kw),
    };
  }
  resumeParSite.value = resume;
}

async function chargerHistorique(site) {
  // Source unique : measurements_silver (alimentée par etl-collect, ~60s).
  // Rappelé à chaque cycle, ce n'est pas un simple préremplissage mais LA
  // façon dont le buffer est tenu à jour — donc on le reconstruit à chaque
  // fois plutôt que d'y ajouter, pour ne jamais dupliquer une ligne déjà vue.
  try {
    const depuisMinutes = Math.ceil(fenetreMs.value / 60000);
    const { data } = await api.get(`/sites/${site.site_id}/measurements`, {
      params: { depuis_minutes: depuisMinutes, pas_minutes: pasMinutesPour(fenetreMs.value) },
    });

    const buffer = bufferPour(site.site_id);
    buffer.instants = [];
    buffer.labels = [];
    for (const m of METRIQUES) buffer.metriques[m.cle] = [];
    buffer.seuils = [];
    for (const mesure of data) ajouterPoint(buffer, mesure, site);
    purgerAnciens(buffer);

    const derniere = data[data.length - 1];
    dernieresLectures[site.site_id] = derniere ? { mesure: derniere, timestamp: new Date(derniere.timestamp) } : null;

    return true;
  } catch {
    return false;
  }
}

function afficherSiteSelectionne() {
  const buffer = bufferPour(siteSelectionne.value);
  for (const m of METRIQUES) {
    const instance = charts[m.cle];
    if (!instance) continue;
    instance.data.labels = buffer.labels;
    instance.data.datasets[0].data = buffer.metriques[m.cle];
    if (m.avecSeuil) instance.data.datasets[1].data = buffer.seuils;
    instance.update();
  }
  rafraichirGraphiqueGrand();

  const derniere = dernieresLectures[siteSelectionne.value];
  derniereLecture.value = derniere?.timestamp ?? null;
  derniereMesure.value = derniere?.mesure ?? null;
  derniereQualite.value = derniere?.mesure?.data_quality ?? "";
  derniereRaisons.value = derniere?.mesure?.null_reasons ?? [];

  // Le mock IoT simule un capteur peu fiable (30 à 50 % de lectures sans
  // valeur selon les sites) : plutôt que de combler les trous par une
  // estimation, on affiche le taux réel de disponibilité sur la fenêtre.
  tauxDisponibilite.value = calculerDisponibilite(buffer);
}

async function chargerPrevisions(siteId) {
  // Même garde que chargerResumeJour : un résultat qui arrive après un changement de site
  // afficherait la courbe d'un site sur les mesures d'un autre.
  try {
    const { data } = await api.get(`/sites/${siteId}/predictions`, {
      params: {
        horizon_heures: heuresPrevisionPour(fenetreMs.value),
        historique_heures: heuresHistoriquePrevisionPour(fenetreMs.value),
      },
    });
    if (siteId !== siteSelectionne.value) return;
    previsions.value = data;
    previsionIndisponible.value = false;
  } catch {
    if (siteId !== siteSelectionne.value) return;
    // Liste vidée et drapeau levé : un site sans modèle et une API en échec donnent tous deux
    // une absence de courbe, mais l'écran ne doit pas raconter la même chose dans les deux cas.
    previsions.value = [];
    previsionIndisponible.value = true;
  }
}

async function chargerRecommandations(siteId) {
  // Horizon fixe et non calé sur la fenêtre du graphique : une recommandation porte sur ce
  // qu'il faut décider, pas sur ce qu'on regarde. Un dépassement prévu dans 8 h doit remonter
  // même si l'écran affiche 15 minutes d'historique.
  try {
    const { data } = await api.get(`/sites/${siteId}/recommendations`, {
      params: { horizon_heures: HEURES_PREVISION_MAX },
    });
    if (siteId !== siteSelectionne.value) return;
    recommandations.value = data;
    recommandationIndisponible.value = false;
  } catch {
    if (siteId !== siteSelectionne.value) return;
    recommandations.value = [];
    recommandationIndisponible.value = true;
  }
}

async function chargerResumeJour(siteId) {
  try {
    const { data } = await api.get(`/sites/${siteId}/daily-summary`);
    // Ignorer si le site affiché a changé pendant l'appel (résultat périmé).
    if (siteId === siteSelectionne.value) resumeJour.value = data;
  } catch {
    if (siteId === siteSelectionne.value) resumeJour.value = null;
  }
}

async function chargerAlertes() {
  try {
    const { data } = await api.get("/alerts", { params: { limit: NB_ALERTES_DASHBOARD } });
    // L'API trie déjà par timestamp décroissant et applique la limite : la plus
    // récente est en tête, rien à retrier ici.
    alertesRecentes.value = data;
    erreurAlertes.value = "";
  } catch {
    // Liste conservée telle quelle : un échec de cycle ne doit pas vider un
    // panneau qui affichait des alertes valides, seulement le signaler.
    erreurAlertes.value = "Alertes non actualisées, nouvelle tentative au prochain cycle.";
  } finally {
    chargementAlertes.value = false;
  }
}

async function chargerRisqueCapteur() {
  try {
    const { data } = await api.get("/sensors/failure-forecast");
    risqueCapteur.value = data;
    risqueCapteurIndisponible.value = false;
  } catch {
    // Courbe conservée telle quelle en cas d'échec ponctuel : même logique que
    // chargerAlertes, un cycle raté ne doit pas vider un graphique valide.
    risqueCapteurIndisponible.value = true;
  }
}

async function actualiserToutesLesMesures() {
  const [resultats] = await Promise.all([
    Promise.all(sites.value.map(async (site) => ({ siteId: site.site_id, ok: await chargerHistorique(site) }))),
    chargerResumeJour(siteSelectionne.value),
    chargerPrevisions(siteSelectionne.value),
    chargerRecommandations(siteSelectionne.value),
  ]);

  afficherSiteSelectionne();
  calculerResumeParc();

  const echecSiteActuel = resultats.some((r) => r.siteId === siteSelectionne.value && !r.ok);
  erreurLecture.value = echecSiteActuel ? "Mesures indisponibles, nouvelle tentative au prochain cycle." : "";
}

function arreterSondage() {
  if (intervalSondage) {
    clearInterval(intervalSondage);
    intervalSondage = null;
  }
}

function demarrerSondage() {
  arreterSondage();
  // Pas d'appel immédiat ici : chargerSites() a déjà fait le premier
  // chargement avant d'appeler demarrerSondage(), l'intervalle ne fait que
  // prendre le relais pour les cycles suivants.
  //
  // Les alertes sont rechargées dans le même cycle mais hors
  // actualiserToutesLesMesures() : elles ne dépendent ni du site affiché ni de
  // la fenêtre de temps, un changement de fenêtre n'a pas à les redemander.
  intervalSondage = setInterval(() => {
    actualiserToutesLesMesures();
    chargerAlertes();
    chargerRisqueCapteur();
  }, POLL_INTERVAL_MS);
}

function selectionnerSite(siteId) {
  if (siteId === siteSelectionne.value) return;
  siteSelectionne.value = siteId;
  // Un zoom manuel n'a de sens que pour les données qui l'ont provoqué : passer à un autre
  // site sans le lever appliquerait la même fenêtre d'axe à une tout autre courbe.
  reinitialiserZoomGraphiqueGrand();
  afficherSiteSelectionne();
  // Résumé du jour propre au site : reset immédiat (pas les chiffres de
  // l'ancien site le temps que la requête réponde), puis rechargement du bon.
  resumeJour.value = null;
  chargerResumeJour(siteId);
  // Même raison pour les prévisions : elles sont propres au site et au modèle qui l'a produit,
  // les garder à l'écran le temps de la requête tracerait la courbe du site précédent.
  previsions.value = [];
  previsionIndisponible.value = false;
  chargerPrevisions(siteId);
  recommandations.value = [];
  recommandationIndisponible.value = false;
  chargerRecommandations(siteId);
}

async function changerFenetre(ms) {
  fenetreMs.value = Number(ms);
  // Même raison que dans selectionnerSite : la période affichée change, un zoom pris sur
  // l'ancienne fenêtre n'aurait plus de sens sur la nouvelle.
  reinitialiserZoomGraphiqueGrand();
  // chargerHistorique() reconstruit chaque buffer à neuf : pas besoin de les
  // vider explicitement avant de relancer le chargement avec la nouvelle fenêtre.
  await actualiserToutesLesMesures();
}

// Le brouillon du formulaire suit le préréglage choisi : ouvrir la saisie juste après part
// de la période affichée, et non d'un chiffre resté d'une saisie précédente.
async function choisirPrereglage(ms) {
  synchroniserBrouillon(ms);
  messagePeriode.value = "";
  saisieInvalide.value = false;
  saisieOuverte.value = false;
  if (ms !== fenetreMs.value) await changerFenetre(ms);
}

function synchroniserBrouillon(ms) {
  const unite = uniteJuste(ms);
  uniteBrouillon.value = unite.cle;
  dureeBrouillon.value = String(Math.round(ms / unite.ms));
}

async function basculerSaisie() {
  // Le message est remis à zéro dans les deux sens : refermer le formulaire ne doit pas
  // laisser sous la barre une correction qui parle d'un champ désormais absent.
  messagePeriode.value = "";
  saisieInvalide.value = false;
  saisieOuverte.value = !saisieOuverte.value;
  if (!saisieOuverte.value) return;

  synchroniserBrouillon(fenetreMs.value);
  // Le champ prend le focus à l'ouverture : le clic sur « personnalisée… » n'a qu'une suite
  // possible, taper une durée, et le clavier n'a pas à retraverser la barre pour y arriver.
  await nextTick();
  champDureeRef.value?.focus();
}

// Rien n'est appliqué à la frappe : la période change à la validation du formulaire (Entrée
// ou « appliquer »). Recharger à chaque touche ferait passer « 45 » par une fenêtre de
// 4 minutes, soit sept graphiques rechargés pour rien sur tous les sites.
async function appliquerSaisie() {
  const nombre = Number(dureeBrouillon.value);
  if (!Number.isInteger(nombre) || nombre <= 0) {
    saisieInvalide.value = true;
    messagePeriode.value = "Durée attendue : un nombre entier, dans l'unité choisie.";
    return;
  }
  saisieInvalide.value = false;

  const demande = nombre * msUnite(uniteBrouillon.value);
  const retenue = Math.min(FENETRE_MAX_MS, Math.max(FENETRE_MIN_MS, demande));
  if (retenue === demande) {
    messagePeriode.value = "";
  } else {
    // Ramenée dans les bornes plutôt qu'envoyée telle quelle : hors bornes, l'API répond 422
    // et les sept graphiques se videraient d'un coup, sans que rien à l'écran l'explique.
    synchroniserBrouillon(retenue);
    messagePeriode.value = `Période ramenée à ${libelleDuree(retenue)} : l'API expose de ${libelleDuree(FENETRE_MIN_MS)} à ${libelleDuree(FENETRE_MAX_MS)} d'historique.`;
  }

  if (retenue !== fenetreMs.value) await changerFenetre(retenue);
}

async function chargerSites() {
  chargementSites.value = true;
  erreurSites.value = "";
  try {
    const { data } = await api.get("/sites");
    sites.value = data;
    // Basculer chargementSites avant nextTick : tant qu'il reste vrai, le
    // template affiche "Chargement des sites..." (v-if) et jamais la branche
    // contenant les <canvas> (v-else) — nextTick attendrait alors le mauvais
    // rendu et canvasEls resterait vide.
    chargementSites.value = false;
    if (data.length > 0) {
      siteSelectionne.value = data[0].site_id;
      await nextTick();
      creerGraphiques();
      creerGraphiqueGrand();
      // Premier chargement de tous les sites depuis measurements_silver,
      // avant de lancer le cycle périodique (30s) qui prend le relais.
      await actualiserToutesLesMesures();
      demarrerSondage();
    }
  } catch {
    erreurSites.value = "Impossible de charger les sites, réessayez.";
    chargementSites.value = false;
  }
}

onMounted(() => {
  chargerSites();
  chargerAlertes();
  chargerRisqueCapteur();
  intervalHorloge = setInterval(() => {
    maintenant.value = new Date();
  }, 1000);
});

onBeforeUnmount(() => {
  arreterSondage();
  clearInterval(intervalHorloge);
  for (const instance of Object.values(charts)) instance?.destroy();
  chartGrand?.destroy();
});
</script>

<style scoped>
.dashboard {
  --bg: var(--shell-bg);
  --panel: var(--shell-panel);
  --panel-2: var(--shell-panel-2);
  --border: var(--shell-border);
  --text: var(--shell-text);
  --text-muted: var(--shell-muted);
  --ok: var(--shell-accent);
  --alerte: var(--shell-warning);
  --danger: var(--shell-danger);
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

/* Réutilise .kpi-carte pour le fond/bordure/padding : cette tuile vit dans la même
   grille que les 5 KPI du jour, à la demande, plutôt que dans un panneau à part. */
.risque-mini-entete {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.risque-mini-entete .kpi-label {
  margin-bottom: 0;
}

.risque-mini-lien {
  font-size: 0.72em;
  color: var(--text-muted);
  text-decoration: none;
  white-space: nowrap;
}

.risque-mini-lien:hover {
  color: var(--text);
}

.risque-mini-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 5px;
  border-radius: 8px;
  background: var(--panel-2);
  font-size: 0.85em;
  white-space: nowrap;
}

.risque-mini-corps {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
}

.risque-mini-svg {
  width: 100%;
  max-width: 80px;
  height: 24px;
  display: block;
  flex-shrink: 0;
}

.risque-mini-aire {
  fill: #a78bfa;
  fill-opacity: 0.16;
}

.risque-mini-ligne {
  fill: none;
  stroke: #a78bfa;
  stroke-width: 2;
}

.risque-mini-pic {
  margin: 10px 0 0;
  font-size: 0.68em;
  color: var(--text-muted);
  white-space: nowrap;
}

.risque-mini-erreur {
  font-size: 0.78em;
  color: var(--text-muted);
  display: flex;
  align-items: center;
}


.disponibilite {
  font-size: 0.72em;
  color: var(--text-muted);
}

.barre-periode {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-bottom: 16px;
}

.pilules-periode {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* Même vocabulaire visuel que le sélecteur de sites en haut de page : deux sélections
   exclusives, qui se manipulent donc de la même façon. */
.pilule-periode {
  padding: 6px 11px;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: var(--panel);
  font-family: var(--mono);
  font-size: 0.76em;
  color: var(--text-muted);
  white-space: nowrap;
  cursor: pointer;
}

.pilule-periode:hover {
  border-color: var(--text-muted);
  color: var(--text);
}

.pilule-periode.actif {
  background: var(--panel-2);
  border-color: var(--text);
  color: var(--text);
}

.resume-periode {
  margin: 0;
  font-family: var(--mono);
  font-size: 0.74em;
  color: var(--text-muted);
}

/* Le formulaire prend toute la ligne suivante : encadré, il se lit comme un panneau ouvert
   par la pilule « personnalisée… » plutôt que comme un sixième préréglage. */
.saisie-periode {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  flex-basis: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--panel-2);
}

.saisie-periode-libelle {
  font-family: var(--mono);
  font-size: 0.74em;
  color: var(--text-muted);
}

.saisie-periode-duree,
.saisie-periode-unite {
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 0.78em;
  padding: 5px 8px;
}

.saisie-periode-unite {
  cursor: pointer;
}

.saisie-periode-duree {
  /* De quoi lire 1440, la plus longue saisie que les bornes de l'API acceptent. */
  width: 74px;
  text-align: right;
}

.saisie-periode-duree[aria-invalid="true"] {
  border-color: var(--danger);
}

.bouton-periode {
  padding: 5px 12px;
  border-radius: 3px;
  border: 1px solid var(--ok);
  background: transparent;
  color: var(--ok);
  font-family: var(--mono);
  font-size: 0.78em;
  cursor: pointer;
}

.bouton-periode:hover {
  background: rgba(45, 212, 191, 0.12);
}

.saisie-periode-bornes {
  margin-left: auto;
  font-family: var(--mono);
  font-size: 0.72em;
  color: var(--text-muted);
}

.message-periode {
  flex-basis: 100%;
  margin: 0;
  font-family: var(--mono);
  font-size: 0.74em;
  color: var(--alerte);
}

.message-periode.invalide {
  color: var(--danger);
}

/* La barre se parcourt entièrement au clavier : un contour visible, sinon la pilule ou le
   bouton qui a le focus ne se distingue pas des autres. */
.pilule-periode:focus-visible,
.bouton-periode:focus-visible,
.saisie-periode-duree:focus-visible,
.saisie-periode-unite:focus-visible {
  outline: 1px solid var(--text);
  outline-offset: 1px;
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

.graphique-grand {
  margin-bottom: 12px;
}

.graphique-grand-entete {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 8px 0;
}

.graphique-grand-titre {
  font-size: 0.72em;
  color: var(--text-muted);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.bouton-reinitialiser-zoom {
  flex-shrink: 0;
  padding: 3px 9px;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.72em;
  cursor: pointer;
}

.bouton-reinitialiser-zoom:hover {
  border-color: var(--text-muted);
  color: var(--text);
}

.bouton-reinitialiser-zoom:focus-visible {
  outline: 1px solid var(--text);
  outline-offset: 1px;
}

.chart-wrapper-grand {
  position: relative;
  height: 280px;
}

/* Ordre fixe, toujours les 7 cartes : cliquer sur l'une d'elles l'affiche en
   plus dans .graphique-grand ci-dessus, elle reste ici à sa place habituelle. */
.grille-graphiques {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}

.graphique-carte {
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 10px 12px;
  background: var(--panel-2);
  transition: border-color 0.15s ease;
}

.graphique-carte:hover {
  border-color: var(--text-muted);
}

/* La teinte ne distingue plus les cartes (une couleur = un sens) : la sélection
   doit donc se voir au cadre et au fond, pas à la couleur de la courbe. */
.graphique-carte.actif {
  border-color: var(--text);
  background: var(--panel);
}

.graphique-carte-entete {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.graphique-carte-titre {
  font-size: 0.7em;
  color: var(--text-muted);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.graphique-carte-valeur {
  font-family: var(--mono);
  font-size: 0.78em;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  white-space: nowrap;
}

.chart-wrapper-carte {
  position: relative;
  height: 70px;
}

.legende-graphe {
  display: flex;
  gap: 18px;
  margin-top: 12px;
  font-size: 0.76em;
  color: var(--text-muted);
}

.note-prevision {
  margin: 8px 0 0;
  font-size: 0.74em;
  line-height: 1.45;
  color: var(--text-muted);
}

/* Réservé aux deux cas qui engagent la lecture du graphe : service injoignable, ou
   prévision issue d'un modèle non validé sur donnée réelle. */
.note-prevision.alerte {
  color: #f59e0b;
}

.note-prevision code {
  font-size: 0.95em;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(148, 163, 184, 0.16);
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

.legende-trait.orange {
  border-color: #f59e0b;
  border-top-style: dashed;
}

.legende-trait.violet {
  border-color: #a78bfa;
  border-top-style: dashed;
}

.legende-bande {
  width: 16px;
  height: 8px;
  background: rgba(167, 139, 250, 0.3);
  border-radius: 2px;
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
  /* Vert par défaut : l'absence de dépassement prévu est une bonne nouvelle, pas un état
     neutre. La bordure passe à l'orange ou au rouge selon le niveau le plus grave. */
  border-left: 3px solid var(--ok);
  background: var(--panel-2);
}

.recommandation.avertissement {
  border-left-color: #f59e0b;
}

.recommandation.critique {
  border-left-color: #ef4444;
}

/* Plusieurs fenêtres peuvent coexister (un dépassement ce soir, un risque demain) : chacune
   est un bloc séparé, un filet les distingue sans alourdir. */
.reco-bloc + .reco-bloc {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border, #1f2b42);
}

.reco-creneau {
  margin: 0 0 4px;
  font-family: var(--mono);
  font-size: 0.8em;
  color: var(--text-muted);
}

.reco-mention {
  margin: 10px 0 0;
  font-size: 0.72em;
  font-style: italic;
  color: var(--text-muted);
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

/* L'amplitude reprend la couleur du niveau : c'est le chiffre que l'œil accroche en premier. */
.recommandation-gain.depassement_prevu {
  color: #ef4444;
}

.recommandation-gain.risque_depassement {
  color: #f59e0b;
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
  grid-template-columns: repeat(6, 1fr);
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

.kpi-unite {
  font-size: 0.6em;
  color: var(--text-muted);
  font-weight: 400;
}

.pas-de-donnee-kpi {
  grid-column: 1 / -1;
  font-size: 0.85em;
  font-style: italic;
  color: var(--text-muted);
  padding: 10px 0;
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

.lien-alertes {
  font-family: var(--mono);
  font-size: 0.72em;
  color: var(--text-muted);
  text-decoration: none;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.lien-alertes:hover {
  color: var(--ok);
  border-bottom-color: var(--ok);
}

.alertes-etat {
  margin: 0;
  padding: 8px 0;
  font-size: 0.82em;
  color: var(--text-muted);
}

.alertes-erreur {
  border-top: 1px solid var(--border);
  color: var(--alerte);
}

.alerte-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 0;
  border-top: 1px solid var(--border);
  font-size: 0.82em;
}

.alerte-item:first-child {
  border-top: none;
}

/* Même échelle de couleurs que les badges de la vue Alertes : une sévérité
   ne doit pas changer de teinte d'un écran à l'autre. */
.severite-low {
  --severite: var(--ok);
}

.severite-medium {
  --severite: var(--alerte);
}

.severite-high {
  --severite: #fb923c;
}

.severite-critical {
  --severite: var(--danger);
}

.alerte-point {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  /* Aligne la pastille sur la première ligne de texte, pas sur le haut du
     bloc : le message peut passer sur deux lignes. */
  margin-top: 0.45em;
  border-radius: 50%;
  background: var(--severite, var(--text-muted));
}

.alerte-heure {
  font-family: var(--mono);
  color: var(--text-muted);
  flex-shrink: 0;
}

.alerte-site {
  font-family: var(--mono);
  color: var(--text-muted);
}

.alerte-item.critique .alerte-texte {
  color: var(--severite, var(--danger));
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
