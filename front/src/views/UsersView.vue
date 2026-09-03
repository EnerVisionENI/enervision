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
.users-header {
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

.creation {
  padding: 24px;
  margin-bottom: 24px;
  border: 1px solid #e6e6f0;
  border-radius: 12px;
  background: #fafafe;
}

.creation-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 18px;
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

td {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  color: #333;
  vertical-align: middle;
}

tbody tr:hover {
  background: #f7f8fd;
}

td.email {
  font-weight: 500;
}

.moi {
  margin-left: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef0fd;
  color: #4c5fd5;
  font-size: 0.72em;
  font-weight: 600;
  text-transform: uppercase;
}

.role-select {
  padding: 7px 10px;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: white;
  color: #333;
  font-family: inherit;
  font-size: 0.9em;
  cursor: pointer;
}

.role-select:disabled {
  background: #f5f5f8;
  color: #999;
  cursor: not-allowed;
}

.badge {
  display: inline-block;
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: 600;
}

.badge-actif {
  background: #e8f5e9;
  color: #2e7d32;
}

.badge-attente {
  background: #fff4e5;
  color: #b26a00;
}

td.date {
  color: #666;
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

.overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(30, 30, 50, 0.45);
  z-index: 10;
}

.modale {
  width: 100%;
  max-width: 440px;
  padding: 28px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.modale h3 {
  color: #333;
  margin-bottom: 10px;
}

.modale-texte {
  color: #666;
  font-size: 0.92em;
  line-height: 1.5;
  margin-bottom: 20px;
}

@media (max-width: 700px) {
  .users-header {
    align-items: stretch;
  }

  .actions-col {
    text-align: left;
  }
}
</style>
