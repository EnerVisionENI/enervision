<template>
  <div class="users">
    <div class="users-header">
      <div class="heading">
        <h2>Utilisateurs</h2>
        <span v-if="!chargement && !erreur" class="count">
          {{ utilisateurs.length }} compte{{ utilisateurs.length > 1 ? "s" : "" }}
        </span>
      </div>

      <button
        class="btn"
        :class="formulaireOuvert ? 'btn-secondary' : 'btn-primary'"
        @click="basculerFormulaire"
      >
        {{ formulaireOuvert ? "Fermer" : "＋ Nouvel utilisateur" }}
      </button>
    </div>

    <form v-if="formulaireOuvert" class="creation" @submit.prevent="creerUtilisateur">
      <div class="creation-grid">
        <div class="field">
          <label for="nouvel-email">Email</label>
          <input
            id="nouvel-email"
            v-model.trim="nouvel.email"
            type="email"
            placeholder="prenom.nom@enervision.fr"
            required
          />
        </div>

        <div class="field">
          <label for="nouveau-role">Rôle</label>
          <select id="nouveau-role" v-model="nouvel.role">
            <option v-for="(libelle, cle) in LIBELLES_ROLE" :key="cle" :value="cle">
              {{ libelle }}
            </option>
          </select>
        </div>

        <div class="field">
          <label for="nouveau-mot-de-passe">Mot de passe temporaire</label>
          <div class="avec-bouton">
            <input
              id="nouveau-mot-de-passe"
              v-model="nouvel.password"
              type="text"
              :minlength="LONGUEUR_MINIMALE"
              required
            />
            <button type="button" class="btn btn-secondary btn-small" @click="genererPour(nouvel)">
              Générer
            </button>
          </div>
          <span class="hint">
            {{ LONGUEUR_MINIMALE }} caractères minimum. À transmettre à l'utilisateur : il
            devra le remplacer à sa première connexion.
          </span>
        </div>
      </div>

      <p v-if="creationErreur" class="alert alert-error">{{ creationErreur }}</p>

      <div class="actions-formulaire">
        <button type="submit" class="btn btn-primary" :disabled="creationEnCours">
          {{ creationEnCours ? "Création..." : "Créer le compte" }}
        </button>
        <button type="button" class="btn btn-secondary" :disabled="creationEnCours" @click="basculerFormulaire">
          Annuler
        </button>
      </div>
    </form>

    <p v-if="message" class="alert alert-success bandeau">{{ message }}</p>
    <p v-if="erreurAction" class="alert alert-error bandeau">{{ erreurAction }}</p>

    <p v-if="chargement" class="loading">Chargement des utilisateurs...</p>
    <p v-else-if="erreur" class="error">{{ erreur }}</p>
    <p v-else-if="utilisateurs.length === 0" class="empty">Aucun utilisateur à afficher.</p>

    <div v-else class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Rôle</th>
            <th>Statut</th>
            <th>Créé le</th>
            <th class="actions-col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="utilisateur in utilisateurs" :key="utilisateur.user_id">
            <td class="email">
              {{ utilisateur.email }}
              <span v-if="estMoi(utilisateur)" class="moi">vous</span>
            </td>
            <td>
              <select
                class="role-select"
                :value="utilisateur.role"
                :disabled="estMoi(utilisateur)"
                :title="estMoi(utilisateur) ? 'Vous ne pouvez pas modifier votre propre rôle' : ''"
                @change="changerRole(utilisateur, $event.target.value)"
              >
                <option v-for="(libelle, cle) in LIBELLES_ROLE" :key="cle" :value="cle">
                  {{ libelle }}
                </option>
              </select>
            </td>
            <td>
              <span class="badge" :class="utilisateur.must_change_password ? 'badge-attente' : 'badge-actif'">
                {{ utilisateur.must_change_password ? "Mot de passe à changer" : "Actif" }}
              </span>
            </td>
            <td class="date">{{ formatDate(utilisateur.created_at) }}</td>
            <td class="actions-col">
              <button
                class="btn btn-secondary btn-small"
                :disabled="estMoi(utilisateur)"
                :title="estMoi(utilisateur) ? 'Vous ne pouvez pas réinitialiser votre propre mot de passe' : ''"
                @click="ouvrirReinitialisation(utilisateur)"
              >
                Réinitialiser
              </button>
              <button
                class="btn btn-danger btn-small"
                :disabled="estMoi(utilisateur)"
                :title="estMoi(utilisateur) ? 'Vous ne pouvez pas supprimer votre propre compte' : ''"
                @click="cibleSuppression = utilisateur"
              >
                Supprimer
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="cibleReinitialisation" class="overlay" @click.self="fermerReinitialisation">
      <form class="modale" @submit.prevent="reinitialiserMotDePasse">
        <h3>Réinitialiser le mot de passe</h3>
        <p class="modale-texte">
          Un nouveau mot de passe temporaire sera défini pour
          <strong>{{ cibleReinitialisation.email }}</strong>, à changer à sa prochaine connexion.
        </p>

        <div class="field">
          <label for="mot-de-passe-temporaire">Mot de passe temporaire</label>
          <div class="avec-bouton">
            <input
              id="mot-de-passe-temporaire"
              v-model="motDePasseTemporaire"
              type="text"
              :minlength="LONGUEUR_MINIMALE"
              required
            />
            <button type="button" class="btn btn-secondary btn-small" @click="motDePasseTemporaire = genererMotDePasse()">
              Générer
            </button>
          </div>
        </div>

        <p v-if="reinitialisationErreur" class="alert alert-error">{{ reinitialisationErreur }}</p>

        <div class="actions-formulaire">
          <button type="submit" class="btn btn-primary" :disabled="reinitialisationEnCours">
            {{ reinitialisationEnCours ? "Enregistrement..." : "Réinitialiser" }}
          </button>
          <button type="button" class="btn btn-secondary" @click="fermerReinitialisation">Annuler</button>
        </div>
      </form>
    </div>

    <div v-if="cibleSuppression" class="overlay" @click.self="cibleSuppression = null">
      <div class="modale">
        <h3>Supprimer le compte</h3>
        <p class="modale-texte">
          Supprimer définitivement <strong>{{ cibleSuppression.email }}</strong> ?
          Cette action est irréversible.
        </p>

        <div class="actions-formulaire">
          <button class="btn btn-danger" :disabled="suppressionEnCours" @click="supprimerUtilisateur">
            {{ suppressionEnCours ? "Suppression..." : "Confirmer la suppression" }}
          </button>
          <button class="btn btn-secondary" :disabled="suppressionEnCours" @click="cibleSuppression = null">
            Annuler
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import api from "../api/client";
import { currentUser } from "../auth/auth";

const LIBELLES_ROLE = {
  viewer: "Lecture seule",
  operator: "Opérateur",
  admin: "Administrateur",
};

const LONGUEUR_MINIMALE = 8;
const ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";

const utilisateurs = ref([]);
const chargement = ref(false);
const erreur = ref("");
const message = ref("");
const erreurAction = ref("");

const formulaireOuvert = ref(false);
const nouvel = reactive({ email: "", role: "viewer", password: "" });
const creationErreur = ref("");
const creationEnCours = ref(false);

const cibleReinitialisation = ref(null);
const motDePasseTemporaire = ref("");
const reinitialisationErreur = ref("");
const reinitialisationEnCours = ref(false);

const cibleSuppression = ref(null);
const suppressionEnCours = ref(false);

function estMoi(utilisateur) {
  return utilisateur.user_id === currentUser.value?.user_id;
}

function formatDate(date) {
  return date ? new Date(date).toLocaleDateString("fr-FR") : "—";
}

function genererMotDePasse() {
  const longueur = 12;
  const source = globalThis.crypto?.getRandomValues
    ? globalThis.crypto.getRandomValues(new Uint32Array(longueur))
    : Array.from({ length: longueur }, () => Math.floor(Math.random() * ALPHABET.length));
  return Array.from(source, (n) => ALPHABET[n % ALPHABET.length]).join("");
}

function genererPour(cible) {
  cible.password = genererMotDePasse();
}

function basculerFormulaire() {
  formulaireOuvert.value = !formulaireOuvert.value;
  creationErreur.value = "";
  if (formulaireOuvert.value) {
    nouvel.email = "";
    nouvel.role = "viewer";
    nouvel.password = genererMotDePasse();
  }
}

function messageErreur(e, defaut) {
  if (e.response?.status === 409) return "Cet email est déjà utilisé.";
  if (e.response?.status === 422) {
    return `Vérifiez les champs : email valide et mot de passe d'au moins ${LONGUEUR_MINIMALE} caractères.`;
  }
  if (e.response?.status === 400) return e.response.data?.detail || defaut;
  return defaut;
}

async function chargerUtilisateurs() {
  erreur.value = "";
  chargement.value = true;
  try {
    const { data } = await api.get("/users");
    utilisateurs.value = data;
  } catch {
    erreur.value = "Impossible de charger les utilisateurs, réessayez.";
  } finally {
    chargement.value = false;
  }
}

async function creerUtilisateur() {
  creationErreur.value = "";
  erreurAction.value = "";
  message.value = "";
  creationEnCours.value = true;
  try {
    await api.post("/users", { email: nouvel.email, role: nouvel.role, password: nouvel.password });
    message.value = `Compte créé pour ${nouvel.email}. Communiquez-lui son mot de passe temporaire.`;
    formulaireOuvert.value = false;
    await chargerUtilisateurs();
  } catch (e) {
    creationErreur.value = messageErreur(e, "Impossible de créer le compte, réessayez.");
  } finally {
    creationEnCours.value = false;
  }
}

async function changerRole(utilisateur, role) {
  if (role === utilisateur.role) return;
  erreurAction.value = "";
  message.value = "";
  try {
    const { data } = await api.patch(`/users/${utilisateur.user_id}`, { role });
    Object.assign(utilisateur, data);
    message.value = `Rôle de ${utilisateur.email} mis à jour.`;
  } catch (e) {
    erreurAction.value = messageErreur(e, "Impossible de modifier le rôle, réessayez.");
    // La liste fait foi : on la recharge pour resynchroniser le select.
    await chargerUtilisateurs();
  }
}

function ouvrirReinitialisation(utilisateur) {
  cibleReinitialisation.value = utilisateur;
  motDePasseTemporaire.value = genererMotDePasse();
  reinitialisationErreur.value = "";
}

function fermerReinitialisation() {
  cibleReinitialisation.value = null;
  motDePasseTemporaire.value = "";
}

async function reinitialiserMotDePasse() {
  reinitialisationErreur.value = "";
  erreurAction.value = "";
  message.value = "";
  reinitialisationEnCours.value = true;
  const cible = cibleReinitialisation.value;
  try {
    const { data } = await api.post(`/users/${cible.user_id}/password`, {
      password: motDePasseTemporaire.value,
    });
    Object.assign(cible, data);
    message.value = `Mot de passe temporaire défini pour ${cible.email}.`;
    fermerReinitialisation();
  } catch (e) {
    reinitialisationErreur.value = messageErreur(e, "Impossible de réinitialiser le mot de passe, réessayez.");
  } finally {
    reinitialisationEnCours.value = false;
  }
}

async function supprimerUtilisateur() {
  erreurAction.value = "";
  message.value = "";
  suppressionEnCours.value = true;
  const cible = cibleSuppression.value;
  try {
    await api.delete(`/users/${cible.user_id}`);
    message.value = `Compte ${cible.email} supprimé.`;
    cibleSuppression.value = null;
    await chargerUtilisateurs();
  } catch (e) {
    erreurAction.value = messageErreur(e, "Impossible de supprimer le compte, réessayez.");
    cibleSuppression.value = null;
  } finally {
    suppressionEnCours.value = false;
  }
}

onMounted(chargerUtilisateurs);
</script>

<style scoped>
/* Même langage « console » que le Dashboard et les Capteurs. Les classes
   utilitaires globales (.btn, .field, .alert) sont réhabillées ici, sous la
   portée .users, sans toucher au thème clair du reste des utilitaires. */
.users {
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

.users-header {
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

/* Boutons : filet + tint, jamais d'aplat plein ni de translation au survol. */
.users .btn {
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: none;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.78em;
  font-weight: 500;
  letter-spacing: 0.03em;
}

.users .btn:not(:disabled):hover {
  transform: none;
}

.users .btn:disabled {
  opacity: 0.4;
}

.users .btn-primary {
  border-color: var(--ok);
  background: rgba(45, 212, 191, 0.1);
  color: var(--ok);
}

.users .btn-primary:not(:disabled):hover {
  background: rgba(45, 212, 191, 0.2);
}

.users .btn-secondary {
  border-color: var(--border);
  color: var(--text-muted);
}

.users .btn-secondary:not(:disabled):hover {
  color: var(--text);
  border-color: var(--text-muted);
}

.users .btn-danger {
  border-color: var(--danger);
  background: rgba(239, 68, 68, 0.08);
  color: #fca5a5;
}

.users .btn-danger:not(:disabled):hover {
  background: rgba(239, 68, 68, 0.16);
}

.users .btn-small {
  padding: 5px 10px;
  font-size: 0.72em;
}

.creation {
  padding: 20px;
  margin-bottom: 24px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--panel);
}

.creation-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 18px;
}

.users .field label {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.72em;
  letter-spacing: 0.04em;
  font-weight: 500;
}

.users .field input,
.users .field select {
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--panel-2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 0.85em;
}

.users .field input:focus,
.users .field select:focus {
  border-color: var(--ok);
  box-shadow: none;
}

.users .field input::placeholder {
  color: #3b4a68;
}

.users .field .hint {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 0.72em;
  line-height: 1.6;
}

.avec-bouton {
  display: flex;
  gap: 8px;
}

.avec-bouton input {
  flex: 1;
  min-width: 0;
}

.actions-formulaire {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.bandeau {
  margin-bottom: 20px;
}

.users .alert {
  border-radius: 3px;
  border-left: 2px solid;
  background: var(--panel-2);
  font-family: var(--mono);
  font-size: 0.78em;
  line-height: 1.55;
}

.users .alert-error {
  border-color: var(--danger);
  color: #fca5a5;
}

.users .alert-success {
  border-color: var(--ok);
  color: var(--ok);
}

.users .alert-info {
  border-color: var(--text-muted);
  color: var(--text-muted);
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

td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  vertical-align: middle;
}

tbody tr:last-child td {
  border-bottom: none;
}

tbody tr:hover {
  background: var(--panel);
}

td.email {
  font-weight: 500;
}

.moi {
  margin-left: 8px;
  padding: 2px 7px;
  border-radius: 3px;
  background: rgba(45, 212, 191, 0.12);
  color: var(--ok);
  font-family: var(--mono);
  font-size: 0.66em;
  font-weight: 600;
  text-transform: uppercase;
}

.role-select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--panel-2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 0.8em;
  cursor: pointer;
}

.role-select:disabled {
  opacity: 0.4;
  background: var(--panel);
  cursor: not-allowed;
}

.badge {
  display: inline-block;
  padding: 3px 10px;
  border: 1px solid currentColor;
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 0.72em;
  font-weight: 500;
}

.badge-actif {
  color: var(--ok);
}

.badge-attente {
  color: var(--alerte);
}

td.date {
  font-family: var(--mono);
  font-size: 0.92em;
  color: var(--text-muted);
  white-space: nowrap;
}

.actions-col {
  text-align: right;
  white-space: nowrap;
}

.actions-col .btn + .btn {
  margin-left: 8px;
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

.overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(3, 7, 18, 0.72);
  z-index: 10;
}

.modale {
  width: 100%;
  max-width: 440px;
  padding: 24px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
  color: var(--text);
}

.modale h3 {
  color: var(--text);
  font-size: 1.05em;
  margin-bottom: 10px;
}

.modale-texte {
  color: var(--text-muted);
  font-size: 0.9em;
  line-height: 1.55;
  margin-bottom: 20px;
}

.modale-texte strong {
  color: var(--text);
}

@media (max-width: 768px) {
  .users {
    padding: 14px;
  }

  .users-header {
    align-items: stretch;
  }

  .actions-col {
    text-align: left;
  }
}
</style>
