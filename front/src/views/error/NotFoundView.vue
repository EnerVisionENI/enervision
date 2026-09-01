<template>
  <div class="not-found">
    <h1>404</h1>
    <p>Cette page n'existe pas.</p>
    <router-link to="/">Retour à l'accueil</router-link>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  gap: 1rem;
  text-align: center;
}
h1 {
  font-size: 4rem;
  margin: 0;
}
</style>