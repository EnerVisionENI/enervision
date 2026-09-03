<template>
  <AuthLayout
    title="Connexion"
    subtitle="identifiants EnerVision"
    note="accès réservé · les comptes sont créés par un administrateur"
  >
    <form class="formulaire" @submit.prevent="handleLogin">
      <div class="auth-field">
        <label for="email">email</label>
        <input
          id="email"
          v-model.trim="email"
          class="auth-input"
          type="email"
          autocomplete="username"
          placeholder="prenom.nom@enervision.fr"
          required
        />
      </div>

      <div class="auth-field">
        <div class="auth-label-row">
          <label for="password">mot de passe</label>
          <button type="button" class="auth-toggle" @click="motDePasseVisible = !motDePasseVisible">
            {{ motDePasseVisible ? "masquer" : "afficher" }}
          </button>
        </div>
        <input
          id="password"
          v-model="password"
          class="auth-input"
          :type="motDePasseVisible ? 'text' : 'password'"
          autocomplete="current-password"
          required
        />
      </div>

      <p v-if="erreur" class="auth-error">{{ erreur }}</p>

      <button type="submit" class="auth-submit" :disabled="chargement">
        {{ chargement ? "connexion…" : "se connecter" }}
      </button>
    </form>
  </AuthLayout>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import AuthLayout from "../components/AuthLayout.vue";
import { login as saveToken, setCurrentUser } from "../auth/auth";
import api from "../api/client";

const email = ref("");
const password = ref("");
const motDePasseVisible = ref(false);
const erreur = ref("");
const chargement = ref(false);
const router = useRouter();

async function handleLogin() {
  erreur.value = "";
  chargement.value = true;
  try {
    const form = new URLSearchParams();
    form.append("username", email.value);
    form.append("password", password.value);

    const { data } = await api.post("/auth/login", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });

    saveToken(data.access_token);

    // Le token ne porte pas le profil : on le charge tout de suite pour que le
    // routeur connaisse le rôle et l'obligation éventuelle de changer le mot de passe.
    const { data: profil } = await api.get("/auth/me");
    setCurrentUser(profil);

    router.push(profil.must_change_password ? "/mot-de-passe" : "/");
  } catch (e) {
    erreur.value =
      e.response?.status === 401
        ? "Email ou mot de passe incorrect."
        : "Erreur de connexion, réessayez.";
  } finally {
    chargement.value = false;
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
