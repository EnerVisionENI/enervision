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
.alerts-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 24px;
}

.heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.heading h2 {
  color: #333;
  font-size: 1.8em;
}

.count {
  color: #888;
  font-size: 0.9em;
}

.filtres {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.champ {
  display: flex;
  align-items: center;
  gap: 8px;
}

.champ label {
  color: #666;
  font-size: 0.9em;
  font-weight: 500;
  white-space: nowrap;
}

select {
  padding: 8px 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 0.95em;
  background: white;
  color: #333;
  cursor: pointer;
  transition: border-color 0.2s ease;
}

select:hover,
select:focus {
  border-color: #667eea;
  outline: none;
}

.table-wrapper {
  overflow-x: auto;
  border-radius: 12px;
  border: 1px solid #eee;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95em;
}

thead th {
  position: sticky;
  top: 0;
  background: #f6f7fd;
  color: #667eea;
  font-size: 0.78em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  text-align: left;
  padding: 14px 16px;
  border-bottom: 2px solid #eee;
  white-space: nowrap;
}

th.sortable {
  cursor: pointer;
  user-select: none;
}

th.sortable:hover {
  color: #4c5fd5;
}

.fleche {
  display: inline-block;
  width: 10px;
  font-size: 0.9em;
}

td {
  padding: 14px 16px;
  border-bottom: 1px solid #f0f0f0;
  color: #333;
  vertical-align: top;
}

tbody tr:nth-child(even) {
  background: #fafafe;
}

tbody tr:hover {
  background: #f0f1fb;
}

td.date {
  white-space: nowrap;
  color: #666;
}

td.message {
  max-width: 360px;
}

td.numerique,
th.numerique {
  text-align: right;
}

td.numerique {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 0.82em;
  font-weight: 600;
  color: white;
  background: #999;
}

.badge::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.85);
}

.badge-low {
  background: #4caf50;
}

.badge-medium {
  background: #ff9800;
}

.badge-high {
  background: #f4511e;
}

.badge-critical {
  background: #c62828;
}

.loading,
.error,
.empty {
  padding: 40px 20px;
  text-align: center;
  border-radius: 12px;
  background: #fafafa;
}

.error {
  color: #c62828;
  background: #fdecea;
}

.empty {
  color: #888;
}

@media (max-width: 600px) {
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
