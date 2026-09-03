import { computed, ref } from "vue";

const TOKEN_KEY = import.meta.env.VITE_TOKEN_KEY || "enervision_token";
// Profil mis en cache à côté du token : les gardes du routeur ont ainsi le rôle
// et l'état "mot de passe à changer" sans attendre un appel réseau à chaque
// navigation (il est rafraîchi depuis /auth/me à la connexion et au rechargement).
const USER_KEY = `${TOKEN_KEY}_user`;

function lireProfilStocke() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY)) || null;
  } catch {
    return null;
  }
}

const profil = ref(lireProfilStocke());

/** Profil de l'utilisateur connecté (`null` si inconnu), réactif. */
export const currentUser = computed(() => profil.value);

export function login(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function setCurrentUser(user) {
  profil.value = user || null;
  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } else {
    localStorage.removeItem(USER_KEY);
  }
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
  setCurrentUser(null);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function isAuthenticated() {
  return !!getToken();
}

export function getRole() {
  return profil.value?.role || null;
}

/** Vrai tant que le mot de passe temporaire donné par l'admin n'a pas été remplacé. */
export function mustChangePassword() {
  return profil.value?.must_change_password === true;
}
