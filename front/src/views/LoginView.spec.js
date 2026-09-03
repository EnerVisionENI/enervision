import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import LoginView from "./LoginView.vue";
import api from "../api/client";
import { login as saveToken, setCurrentUser } from "../auth/auth";

vi.mock("../api/client", () => ({
  default: { post: vi.fn(), get: vi.fn() },
}));

vi.mock("../auth/auth", () => ({
  login: vi.fn(),
  setCurrentUser: vi.fn(),
}));

const PROFIL = {
  user_id: "11111111-1111-1111-1111-111111111111",
  email: "viewer@enervision.fr",
  role: "viewer",
  must_change_password: false,
  created_at: "2026-09-01T10:00:00",
};

function creerRouteur() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div>dashboard</div>" } },
      { path: "/login", component: LoginView },
      { path: "/mot-de-passe", component: { template: "<div>mot de passe</div>" } },
    ],
  });
}

async function monter() {
  const router = creerRouteur();
  router.push("/login");
  await router.isReady();
  const wrapper = mount(LoginView, { global: { plugins: [router] } });
  return { wrapper, router };
}

async function seConnecter(wrapper) {
  await wrapper.find("#email").setValue("viewer@enervision.fr");
  await wrapper.find("#password").setValue("password123");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
}

describe("LoginView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("connecte l'utilisateur puis redirige vers le dashboard", async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: "jeton" } });
    api.get.mockResolvedValueOnce({ data: PROFIL });

    const { wrapper, router } = await monter();
    await seConnecter(wrapper);

    const [url, form] = api.post.mock.calls[0];
    expect(url).toBe("/auth/login");
    expect(form.get("username")).toBe("viewer@enervision.fr");
    expect(form.get("password")).toBe("password123");
    expect(saveToken).toHaveBeenCalledWith("jeton");
    expect(api.get).toHaveBeenCalledWith("/auth/me");
    expect(setCurrentUser).toHaveBeenCalledWith(PROFIL);
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("redirige vers le changement de mot de passe si le compte est neuf", async () => {
    api.post.mockResolvedValueOnce({ data: { access_token: "jeton" } });
    api.get.mockResolvedValueOnce({ data: { ...PROFIL, must_change_password: true } });

    const { wrapper, router } = await monter();
    await seConnecter(wrapper);

    expect(router.currentRoute.value.path).toBe("/mot-de-passe");
  });

  it("affiche un message dédié si les identifiants sont refusés", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 401 } });

    const { wrapper, router } = await monter();
    await seConnecter(wrapper);

    expect(wrapper.find(".auth-error").text()).toBe("Email ou mot de passe incorrect.");
    expect(saveToken).not.toHaveBeenCalled();
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("affiche un message générique sur les autres erreurs", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 500 } });

    const { wrapper } = await monter();
    await seConnecter(wrapper);

    expect(wrapper.find(".auth-error").text()).toBe("Erreur de connexion, réessayez.");
  });

  it("désactive le bouton pendant la connexion", async () => {
    let resoudre;
    api.post.mockReturnValueOnce(new Promise((r) => (resoudre = r)));

    const { wrapper } = await monter();
    await wrapper.find("#email").setValue("viewer@enervision.fr");
    await wrapper.find("#password").setValue("password123");
    await wrapper.find("form").trigger("submit");
    await wrapper.vm.$nextTick();

    const bouton = wrapper.find("button[type=submit]");
    expect(bouton.text()).toBe("connexion…");
    expect(bouton.attributes("disabled")).toBeDefined();

    api.get.mockResolvedValueOnce({ data: PROFIL });
    resoudre({ data: { access_token: "jeton" } });
    await flushPromises();
    expect(wrapper.find("button[type=submit]").text()).toBe("se connecter");
  });

  it("bascule l'affichage du mot de passe", async () => {
    const { wrapper } = await monter();

    expect(wrapper.find("#password").attributes("type")).toBe("password");
    await wrapper.find(".auth-toggle").trigger("click");
    expect(wrapper.find("#password").attributes("type")).toBe("text");
  });
});
