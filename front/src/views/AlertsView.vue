<template>
  <div class="alerts">
    <div class="alerts-header">
      <div class="heading">
        <h2>Alertes</h2>
        <span v-if="!chargement && !erreur" class="count">
          {{ alertesFiltrees.length }} / {{ alertes.length }}
          alerte{{ alertes.length > 1 ? "s" : "" }}
        </span>
      </div>

      <div class="filtres">
        <div class="champ">
          <label for="severite">Sévérité</label>
          <select id="severite" v-model="severite">
            <option value="">Toutes</option>
            <option v-for="(libelle, cle) in LIBELLES_SEVERITE" :key="cle" :value="cle">
              {{ libelle }}
            </option>
          </select>
        </div>

        <div class="champ">
          <label for="site">Site</label>
          <select id="site" v-model="site">
            <option value="">Tous</option>
            <option v-for="s in sitesDisponibles" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>

        <div class="champ">
          <label for="type">Type</label>
          <select id="type" v-model="type">
            <option value="">Tous</option>
            <option v-for="t in typesDisponibles" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
      </div>
    </div>

    <p v-if="chargement" class="loading">Chargement des alertes...</p>
    <p v-else-if="erreur" class="error">{{ erreur }}</p>
    <p v-else-if="alertesFiltrees.length === 0" class="empty">
      Aucune alerte à afficher.
    </p>

    <div v-else class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th
              v-for="colonne in COLONNES"
              :key="colonne.cle"
              :class="{ sortable: colonne.triable, numerique: colonne.cle === 'value' }"
              @click="colonne.triable && trierPar(colonne.cle)"
            >
              {{ colonne.libelle }}
              <span v-if="colonne.triable" class="fleche">{{ indicateurTri(colonne.cle) }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alerte in alertesFiltrees" :key="alerte.alert_id">
            <td class="date">{{ formatDate(alerte.timestamp) }}</td>
            <td>{{ alerte.site_id || "—" }}</td>
            <td>
              <span class="badge" :class="`badge-${alerte.severity || 'inconnue'}`">
                {{ LIBELLES_SEVERITE[alerte.severity] || alerte.severity || "—" }}
              </span>
            </td>
            <td>{{ alerte.type || "—" }}</td>
            <td class="message">{{ alerte.message || "—" }}</td>
            <td class="numerique">
              {{ formatNombre(alerte.value) }} / {{ formatNombre(alerte.threshold) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from "vue";
import api from "../api/client";

const LIBELLES_SEVERITE = {
  low: "Faible",
  medium: "Moyenne",
  high: "Élevée",
  critical: "Critique",
};

const RANG_SEVERITE = { low: 0, medium: 1, high: 2, critical: 3 };

const COLONNES = [
  { cle: "timestamp", libelle: "Date", triable: true },
  { cle: "site_id", libelle: "Site", triable: true },
  { cle: "severity", libelle: "Sévérité", triable: true },
  { cle: "type", libelle: "Type", triable: true },
  { cle: "message", libelle: "Message", triable: false },
  { cle: "value", libelle: "Valeur / Seuil", triable: true },
];

const alertes = ref([]);
const severite = ref("");
const site = ref("");
const type = ref("");
const chargement = ref(false);
const erreur = ref("");
const triCle = ref("timestamp");
const triSens = ref("desc");

const sitesDisponibles = computed(() =>
  [...new Set(alertes.value.map((a) => a.site_id).filter(Boolean))].sort()
);

const typesDisponibles = computed(() =>
  [...new Set(alertes.value.map((a) => a.type).filter(Boolean))].sort()
);

function comparerAlertes(a, b) {
  const sens = triSens.value === "asc" ? 1 : -1;

  if (triCle.value === "severity") {
    return sens * ((RANG_SEVERITE[a.severity] ?? -1) - (RANG_SEVERITE[b.severity] ?? -1));
  }
  if (triCle.value === "timestamp") {
    return sens * (new Date(a.timestamp) - new Date(b.timestamp));
  }
  if (triCle.value === "value") {
    return sens * ((a.value ?? -Infinity) - (b.value ?? -Infinity));
  }
  return sens * String(a[triCle.value] ?? "").localeCompare(String(b[triCle.value] ?? ""));
}

const alertesFiltrees = computed(() => {
  const base = alertes.value.filter(
    (alerte) =>
      (!severite.value || alerte.severity === severite.value) &&
      (!site.value || alerte.site_id === site.value) &&
      (!type.value || alerte.type === type.value)
  );
  return [...base].sort(comparerAlertes);
});

function trierPar(cle) {
  if (triCle.value === cle) {
    triSens.value = triSens.value === "asc" ? "desc" : "asc";
  } else {
    triCle.value = cle;
    triSens.value = "asc";
  }
}

function indicateurTri(cle) {
  if (triCle.value !== cle) return "";
  return triSens.value === "asc" ? "▲" : "▼";
}

function formatDate(timestamp) {
  return timestamp ? new Date(timestamp).toLocaleString("fr-FR") : "—";
}

function formatNombre(nombre) {
  return nombre === null || nombre === undefined ? "—" : String(nombre);
}

async function chargerAlertes() {
  erreur.value = "";
  chargement.value = true;
  try {
    const { data } = await api.get("/alerts");
    alertes.value = data;
  } catch {
    erreur.value = "Impossible de charger les alertes, réessayez.";
  } finally {
    chargement.value = false;
  }
}

onMounted(chargerAlertes);
</script>

<style scoped>
/* Même langage « console » que le Dashboard et les Capteurs : fond sombre,
   filets, monospace pour les libellés techniques, angles à 3px, accent teal. */
.alerts {
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
  min-height: calc(100vh - 120px);
}

.alerts-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

.heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.heading h2 {
  color: var(--text);
  font-size: 1.4em;
  font-weight: 600;
  margin: 0;
}

.count {
  font-family: var(--mono);
  font-size: 0.78em;
  color: var(--text-muted);
}

.filtres {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.champ {
  display: flex;
  align-items: center;
  gap: 8px;
}

.champ label {
  font-family: var(--mono);
  font-size: 0.72em;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  white-space: nowrap;
}

select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 0.78em;
  background: var(--panel-2);
  color: var(--text);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

select:hover,
select:focus {
  border-color: var(--text-muted);
  outline: none;
}

.table-wrapper {
  overflow-x: auto;
  border-radius: 3px;
  border: 1px solid var(--border);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85em;
}

thead th {
  position: sticky;
  top: 0;
  background: var(--panel);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.72em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  text-align: left;
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

th.sortable {
  cursor: pointer;
  user-select: none;
}

th.sortable:hover {
  color: var(--text);
}

.fleche {
  display: inline-block;
  width: 10px;
  font-size: 0.9em;
  color: var(--ok);
}

td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  vertical-align: top;
}

tbody tr:last-child td {
  border-bottom: none;
}

tbody tr:nth-child(even) {
  background: rgba(255, 255, 255, 0.02);
}

tbody tr:hover {
  background: var(--panel);
}

td.date {
  white-space: nowrap;
  font-family: var(--mono);
  font-size: 0.92em;
  color: var(--text-muted);
}

td.message {
  max-width: 360px;
}

td.numerique,
th.numerique {
  text-align: right;
}

td.numerique {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* Filet + tint plutôt qu'aplat plein — convention d'accent du reste de l'app. */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border: 1px solid currentColor;
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 0.72em;
  font-weight: 500;
  color: var(--text-muted);
}

.badge::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.badge-low {
  color: var(--ok);
}

.badge-medium {
  color: var(--alerte);
}

.badge-high {
  color: #fb923c;
}

.badge-critical {
  color: var(--danger);
}

.loading,
.error,
.empty {
  padding: 32px 16px;
  border-radius: 3px;
  background: var(--panel);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.85em;
}

.error {
  color: #fca5a5;
  background: var(--panel-2);
  border-left: 2px solid var(--danger);
}

@media (max-width: 768px) {
  .alerts {
    padding: 14px;
  }

  .alerts-header {
    flex-direction: column;
    align-items: stretch;
  }

  .filtres {
    flex-direction: column;
    align-items: stretch;
  }

  td.message {
    max-width: 200px;
  }
}
</style>
