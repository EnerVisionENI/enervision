import { ref } from "vue";

// Mode jour/nuit de l'application (hors écrans d'authentification, volontairement
// toujours sombres — voir le commentaire dans main.css). Sans choix explicite de
// l'utilisateur, on suit la préférence système et on continue de la suivre en direct
// si elle change ; un choix explicite (bouton bascule) prend le dessus et est retenu
// d'une visite à l'autre dans localStorage.
const STORAGE_KEY = "ev-theme";
// matchMedia est absent de certains environnements de test (jsdom minimal) : on
// dégrade alors sur "jour" plutôt que de faire planter tout composant qui importe
// ce module.
const mediaSombre =
  typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

function themeStocke() {
  const valeur = localStorage.getItem(STORAGE_KEY);
  return valeur === "light" || valeur === "dark" ? valeur : null;
}

function themeSysteme() {
  return mediaSombre?.matches ? "dark" : "light";
}

export const theme = ref(themeStocke() ?? themeSysteme());

function appliquer(valeur) {
  document.documentElement.dataset.theme = valeur;
}

appliquer(theme.value);

mediaSombre?.addEventListener("change", (evenement) => {
  // Un choix explicite prime : on n'écrase pas la préférence de l'utilisateur
  // parce que son système a changé d'apparence.
  if (themeStocke()) return;
  theme.value = evenement.matches ? "dark" : "light";
  appliquer(theme.value);
});

export function basculerTheme() {
  theme.value = theme.value === "dark" ? "light" : "dark";
  localStorage.setItem(STORAGE_KEY, theme.value);
  appliquer(theme.value);
}
