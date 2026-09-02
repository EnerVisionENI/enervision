import { createRouter, createWebHistory } from "vue-router";
import { isAuthenticated } from "../auth/auth";

import LoginView from "../views/LoginView.vue";
import DashboardView from "../views/DashboardView.vue";
import NotFoundView from "../views/error/NotFoundView.vue";

const routes = [
  { path: "/login", name: "login", component: LoginView },
  {
    path: "/",
    name: "dashboard",
    component: DashboardView,
    meta: { requiresAuth: true, nav: "Dashboard" },
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

router.beforeEach((to, from, next) => {
  const authenticated = isAuthenticated();

  if (to.meta.requiresAuth && !authenticated) {
    next("/login");
  } else if (to.name === "login" && authenticated) {
    next("/");
  } else {
    next();
  }
});

export default router;
