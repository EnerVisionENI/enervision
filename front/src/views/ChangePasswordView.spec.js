import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import ChangePasswordView from "./ChangePasswordView.vue";
import api from "../api/client";
import { mustChangePassword, setCurrentUser } from "../auth/auth";

vi.mock("../api/client", () => ({
  default: { post: vi.fn() },
}));

vi.mock("../auth/auth", () => ({
  currentUser: { value: { email: "viewer@enervision.fr", role: "viewer" } },
  mustChangePassword: vi.fn(() => false),
  setCurrentUser: vi.fn(),
}));

const PROFIL_A_JOUR = {
  user_id: "11111111-1111-1111-1111-111111111111",
  email: "viewer@enervision.fr",
  role: "viewer",
  must_change_password: false,
  created_at: "2026-09-01T10:00:00",
};

async function monter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div>dashboard</div>" } },
      { path: "/mot-de-passe", component: ChangePasswordView },
    ],
  });
  router.push("/mot-de-passe");
  await router.isReady();
  const wrapper = mount(ChangePasswordView, { global: { plugins: [router] } });
  return { wrapper, router };
}

async function remplir(wrapper, { actuel = "password123", nouveau = "nouveau123", confirmation = "nouveau123" } = {}) {
  await wrapper.find("#current-password").setValue(actuel);
  await wrapper.find("#new-password").setValue(nouveau);
  await wrapper.find("#confirm-password").setValue(confirmation);
  await wrapper.find("form").trigger("submit");
  await flushPromises();
}

describe("ChangePasswordView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mustChangePassword.mockReturnValue(false);
  });

  it("change le mot de passe puis redirige vers le dashboard", async () => {
    api.post.mockResolvedValueOnce({ data: PROFIL_A_JOUR });

    const { wrapper, router } = await monter();
    await remplir(wrapper);

    expect(api.post).toHaveBeenCalledWith("/auth/password", {
      current_password: "password123",
      new_password: "nouveau123",
    });
    expect(setCurrentUser).toHaveBeenCalledWith(PROFIL_A_JOUR);
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("annonce la première connexion quand le changement est imposé", async () => {
    mustChangePassword.mockReturnValue(true);

    const { wrapper } = await monter();

    expect(wrapper.text()).toContain("Choisissez un mot de passe");
    expect(wrapper.find(".auth-notice").exists()).toBe(true);
    // Pas d'échappatoire : le compte n'a accès à rien d'autre tant que c'est fait.
    expect(wrapper.text()).not.toContain("annuler");
  });

  it("propose d'annuler quand le changement est volontaire", async () => {
    const { wrapper, router } = await monter();

    expect(wrapper.text()).toContain("Changer de mot de passe");
    expect(wrapper.find(".auth-notice").exists()).toBe(false);

    await wrapper.findAll("button").find((b) => b.text() === "annuler").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("refuse une confirmation qui ne correspond pas, sans appeler l'API", async () => {
    const { wrapper } = await monter();
    await remplir(wrapper, { confirmation: "autre12345" });

    expect(wrapper.find(".auth-error").text()).toBe("Les deux mots de passe ne correspondent pas.");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("refuse un mot de passe trop court, sans appeler l'API", async () => {
    const { wrapper } = await monter();
    await remplir(wrapper, { nouveau: "court", confirmation: "court" });

    expect(wrapper.find(".auth-error").text()).toBe(
      "Le mot de passe doit contenir au moins 8 caractères."
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  it("affiche le message de l'API quand le mot de passe actuel est faux", async () => {
    api.post.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Mot de passe actuel incorrect" } },
    });

    const { wrapper, router } = await monter();
    await remplir(wrapper);

    expect(wrapper.find(".auth-error").text()).toBe("Mot de passe actuel incorrect");
    expect(router.currentRoute.value.path).toBe("/mot-de-passe");
  });

  it("affiche un message générique sur les autres erreurs", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 500 } });

    const { wrapper } = await monter();
    await remplir(wrapper);

    expect(wrapper.find(".auth-error").text()).toBe(
      "Impossible de changer le mot de passe, réessayez."
    );
  });

  it("désactive le bouton pendant l'enregistrement", async () => {
    let resoudre;
    api.post.mockReturnValueOnce(new Promise((r) => (resoudre = r)));

    const { wrapper } = await monter();
    await wrapper.find("#current-password").setValue("password123");
    await wrapper.find("#new-password").setValue("nouveau123");
    await wrapper.find("#confirm-password").setValue("nouveau123");
    await wrapper.find("form").trigger("submit");
    await wrapper.vm.$nextTick();

    const bouton = wrapper.find("button[type=submit]");
    expect(bouton.text()).toBe("enregistrement…");
    expect(bouton.attributes("disabled")).toBeDefined();

    resoudre({ data: PROFIL_A_JOUR });
    await flushPromises();
  });
});
