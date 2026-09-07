import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import App from "./App.vue";
import { logout } from "./auth/auth";

const etat = vi.hoisted(() => ({ token: null }));

vi.mock("./auth/auth", async () => {
  const { ref: reference } = await import("vue");
  const profil = reference(null);
  return {
    currentUser: profil,
    isAuthenticated: () => !!etat.token,
    getRole: () => profil.value?.role || null,
    logout: vi.fn(() => {
      etat.token = null;
      profil.value = null;
    }),
    __profil: profil,
  };
});

const auth = await import("./auth/auth");

const vide = { template: "<div />" };

function creerRouteur() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: vide, meta: { layout: "bare" } },
      { path: "/", name: "dashboard", component: vide, meta: { requiresAuth: true, nav: "Dashboard" } },
      { path: "/alertes", name: "alerts", component: vide, meta: { requiresAuth: true, nav: "Alertes" } },
      {
        path: "/utilisateurs",
        name: "users",
        component: vide,
        meta: { requiresAuth: true, nav: "Utilisateurs", role: "admin" },
      },
      {
        path: "/mot-de-passe",
        name: "change-password",
        component: vide,
        meta: { requiresAuth: true, layout: "bare" },
      },
    ],
  });
}

async function monter(chemin) {
  const router = creerRouteur();
  router.push(chemin);
  await router.isReady();
  const wrapper = mount(App, { global: { plugins: [router] } });
  await flushPromises();
  return { wrapper, router };
}

function connecter(profil) {
  etat.token = "jeton";
  auth.__profil.value = profil;
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    etat.token = null;
    auth.__profil.value = null;
  });

  it("affiche les écrans d'authentification sans en-tête ni menu", async () => {
    const { wrapper } = await monter("/login");

    expect(wrapper.find(".header").exists()).toBe(false);
    expect(wrapper.find(".navbar").exists()).toBe(false);
  });

  it("masque le menu tant que personne n'est connecté", async () => {
    const { wrapper } = await monter("/");

    expect(wrapper.find(".header").exists()).toBe(true);
    expect(wrapper.find(".navbar").exists()).toBe(false);
  });

  it("réserve l'entrée Utilisateurs aux admins", async () => {
    connecter({ email: "viewer@enervision.fr", role: "viewer" });
    const { wrapper } = await monter("/");

    const libelles = wrapper.findAll(".navbar .nav-button").map((l) => l.text());
    expect(libelles).toContain("Dashboard");
    expect(libelles).toContain("Alertes");
    expect(libelles).not.toContain("Utilisateurs");
    // La barre ne porte plus que les vraies destinations : mot de passe et
    // déconnexion sont passés dans le menu du compte.
    expect(libelles).toEqual(["Dashboard", "Alertes"]);
  });

  it("affiche l'entrée Utilisateurs pour un admin", async () => {
    connecter({ email: "admin@enervision.fr", role: "admin" });
    const { wrapper } = await monter("/");

    const libelles = wrapper.findAll(".navbar .nav-button").map((l) => l.text());
    expect(libelles).toEqual(["Dashboard", "Alertes", "Utilisateurs"]);
  });

  it("regroupe mot de passe et déconnexion dans le menu du compte", async () => {
    connecter({ email: "viewer@enervision.fr", role: "viewer" });
    const { wrapper } = await monter("/");

    // Fermé au départ.
    expect(wrapper.find(".user-menu-panel").exists()).toBe(false);

    await wrapper.find(".user-menu-trigger").trigger("click");

    const panneau = wrapper.find(".user-menu-panel");
    expect(panneau.exists()).toBe(true);
    expect(panneau.text()).toContain("viewer@enervision.fr");
    expect(panneau.text()).toContain("Changer le mot de passe");
    expect(panneau.find(".user-menu-item.logout").exists()).toBe(true);
  });

  it("déconnecte et renvoie vers la page de connexion", async () => {
    connecter({ email: "admin@enervision.fr", role: "admin" });
    const { wrapper, router } = await monter("/");

    await wrapper.find(".user-menu-trigger").trigger("click");
    await wrapper.find(".user-menu-item.logout").trigger("click");
    await flushPromises();

    expect(logout).toHaveBeenCalled();
    expect(router.currentRoute.value.name).toBe("login");
    expect(wrapper.find(".navbar").exists()).toBe(false);
  });
});
