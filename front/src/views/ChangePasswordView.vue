<template>
  <AuthLayout :title="titre" :subtitle="sousTitre" :note="note">
    <form class="formulaire" @submit.prevent="handleSubmit">
      <p v-if="obligatoire" class="auth-notice">
        Votre mot de passe a été défini par un administrateur. Choisissez-en un nouveau
        pour accéder à l'application.
      </p>

      <div class="auth-field">
        <label for="current-password">mot de passe actuel</label>
        <input
          id="current-password"
          v-model="motDePasseActuel"
          class="auth-input"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>

      <div class="auth-field">
        <label for="new-password">nouveau mot de passe</label>
        <input
          id="new-password"
          v-model="nouveauMotDePasse"
          class="auth-input"
          type="password"
          autocomplete="new-password"
          required
        />
        <span class="auth-hint">{{ LONGUEUR_MINIMALE }} caractères minimum</span>
      </div>

      <div class="auth-field">
        <label for="confirm-password">confirmation</label>
        <input
          id="confirm-password"
          v-model="confirmation"
          class="auth-input"
          type="password"
          autocomplete="new-password"
          required
        />
      </div>

      <p v-if="erreur" class="auth-error">{{ erreur }}</p>

      <button type="submit" class="auth-submit" :disabled="envoi">
        {{ envoi ? "enregistrement…" : "changer le mot de passe" }}
      </button>

      <button
        v-if="!obligatoire"
        type="button"
        class="auth-secondaire"
        :disabled="envoi"
        @click="router.push('/')"
      >
        annuler
      </button>
    </form>
  </AuthLayout>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import AuthLayout from "../components/AuthLayout.vue";
import { currentUser, mustChangePassword, setCurrentUser } from "../auth/auth";
import api from "../api/client";

const LONGUEUR_MINIMALE = 8;

const router = useRouter();
// Figé au montage : la valeur repasse à false dès l'enregistrement, or le libellé
// ne doit pas changer sous les yeux de l'utilisateur pendant la redirection.
const obligatoire = ref(mustChangePassword());

const motDePasseActuel = ref("");
const nouveauMotDePasse = ref("");
const confirmation = ref("");
const erreur = ref("");
const envoi = ref(false);

const titre = computed(() =>
  obligatoire.value ? "Choisissez un mot de passe" : "Changer de mot de passe"
);
const sousTitre = computed(() =>
  obligatoire.value ? "première connexion" : currentUser.value?.email || ""
);

const note = computed(() =>
  obligatoire.value
    ? "mot de passe temporaire · l'accès à l'application est suspendu tant qu'il n'est pas remplacé"
    : "le mot de passe reste connu de vous seul · un administrateur ne peut que le réinitialiser"
);

function messageErreur(e) {
  if (e.response?.status === 400) {
    return e.response.data?.detail || "Mot de passe actuel incorrect.";
  }
  if (e.response?.status === 422) {
    return `Le mot de passe doit contenir au moins ${LONGUEUR_MINIMALE} caractères.`;
  }
  return "Impossible de changer le mot de passe, réessayez.";
}

async function handleSubmit() {
  erreur.value = "";

  if (nouveauMotDePasse.value.length < LONGUEUR_MINIMALE) {
    erreur.value = `Le mot de passe doit contenir au moins ${LONGUEUR_MINIMALE} caractères.`;
    return;
  }
  if (nouveauMotDePasse.value !== confirmation.value) {
    erreur.value = "Les deux mots de passe ne correspondent pas.";
    return;
  }

  envoi.value = true;
  try {
    const { data } = await api.post("/auth/password", {
      current_password: motDePasseActuel.value,
      new_password: nouveauMotDePasse.value,
    });
    setCurrentUser(data);
    router.push("/");
  } catch (e) {
    erreur.value = messageErreur(e);
  } finally {
    envoi.value = false;
  }
}
</script>

<style scoped>
.formulaire {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
</style>
