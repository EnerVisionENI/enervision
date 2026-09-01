<template>
  <div class="login-container">
    <form @submit.prevent="handleLogin">
      <h1>EnerVision</h1>

      <label for="email">Email</label>
      <input id="email" v-model="email" type="email" required />

      <label for="password">Mot de passe</label>
      <input id="password" v-model="password" type="password" required />

      <p v-if="error" class="error">{{ error }}</p>

      <button type="submit" :disabled="loading">
        {{ loading ? "Connexion..." : "Se connecter" }}
      </button>
    </form>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { login as saveToken } from "../auth/auth";
import api from "../api/client";

const email = ref("");
const password = ref("");
const error = ref("");
const loading = ref(false);
const router = useRouter();

async function handleLogin() {
  error.value = "";
  loading.value = true;
  try {
    const form = new URLSearchParams();
    form.append("username", email.value);
    form.append("password", password.value);

    const { data } = await api.post("/auth/login", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });

    saveToken(data.access_token);
    router.push("/");
  } catch (e) {
    error.value = e.response?.status === 401
      ? "Email ou mot de passe incorrect."
      : "Erreur de connexion, réessayez.";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
}
form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 300px;
}
.error {
  color: red;
  font-size: 0.9rem;
}
</style>