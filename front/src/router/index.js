import { createRouter, createWebHistory } from "vue-router";
import {
  currentUser,
  getRole,
  isAuthenticated,
  mustChangePassword,
  setCurrentUser,
} from "../auth/auth";
import api from "../api/client";

import LoginView from "../views/LoginView.vue";
import DashboardView from "../views/DashboardView.vue";
import AlertsView from "../views/AlertsView.vue";
import UsersView from "../views/UsersView.vue";
import ChangePasswordView from "../views/ChangePasswordView.vue";
import NotFoundView from "../views/error/NotFoundView.vue";
import SensorView from "../views/SensorView.vue";

const routes = [
  // layout "bare" : la vue occupe toute la page, sans l'en-tête ni le cadre blanc
  // de App.vue (écrans d'authentification).
  { path: "/login", name: "login", component: LoginView, meta: { layout: "bare" } },
  {
    path: "/",
    name: "dashboard",
    component: DashboardView,
    meta: { requiresAuth: true, nav: "Dashboard" },
  },
  {
    path: "/alertes",
    name: "alerts",
    component: AlertsView,
    meta: { requiresAuth: true, nav: "Alertes" },
  },
  {
    path: "/capteurs",
    name: "sensors",
    component: SensorView,
    meta: { requiresAuth: true, nav: "Capteurs" },
  },
  {
    path: "/utilisateurs",
    name: "users",
    component: UsersView,
    meta: { requiresAuth: true, nav: "Utilisateurs", role: "admin" },
  },
  {
    path: "/mot-de-passe",
    name: "change-password",
    component: ChangePasswordView,
    meta: { requiresAuth: true, layout: "bare", nav: "Mot de passe" },
  },
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: NotFoundView,
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// Le profil n'est pas dans le token : on le récupère une fois par session (ou après
// un rechargement qui aurait vidé le cache) pour connaître rôle et mot de passe à changer.
async function chargerProfilSiBesoin() {
  if (!isAuthenticated() || currentUser.value) return;
  try {
    const { data } = await api.get("/auth/me");
    setCurrentUser(data);
  } catch {
    // Un 401 est déjà traité par l'intercepteur du client API (déconnexion).
  }
}

router.beforeEach(async (to) => {
  const authenticated = isAuthenticated();

  if (to.meta.requiresAuth && !authenticated) return { name: "login" };
  if (!authenticated) return true;

  await chargerProfilSiBesoin();

  // Mot de passe temporaire : l'API refuse tout le reste, on ne propose que cet écran.
  if (mustChangePassword() && to.name !== "change-password") return { name: "change-password" };
  if (to.name === "login") return { name: "dashboard" };
  if (to.meta.role && getRole() !== to.meta.role) return { name: "dashboard" };

  return true;
});

export default router;
