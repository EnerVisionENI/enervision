import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import UsersView from "./UsersView.vue";
import api from "../api/client";

vi.mock("../api/client", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const ADMIN_ID = "11111111-1111-1111-1111-111111111111";

vi.mock("../auth/auth", () => ({
  currentUser: { value: { user_id: "11111111-1111-1111-1111-111111111111", role: "admin" } },
}));

const UTILISATEURS = [
  {
    user_id: ADMIN_ID,
    email: "admin@enervision.fr",
    role: "admin",
    must_change_password: false,
    created_at: "2026-08-01T10:00:00",
  },
  {
    user_id: "22222222-2222-2222-2222-222222222222",
    email: "viewer@enervision.fr",
    role: "viewer",
    must_change_password: true,
    created_at: "2026-09-01T10:00:00",
  },
];

async function monter(donnees = UTILISATEURS) {
  api.get.mockResolvedValueOnce({ data: donnees });
  const wrapper = mount(UsersView);
  await flushPromises();
  return wrapper;
}

function boutonNomme(wrapper, libelle) {
  return wrapper.findAll("button").find((b) => b.text() === libelle);
}

describe("UsersView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("affiche les comptes renvoyés par l'API", async () => {
    const wrapper = await monter();

    expect(api.get).toHaveBeenCalledWith("/users");
    const lignes = wrapper.findAll("tbody tr");
    expect(lignes).toHaveLength(2);
    expect(lignes[0].text()).toContain("admin@enervision.fr");
    expect(wrapper.text()).toContain("2 comptes");
    expect(wrapper.find(".badge-attente").text()).toBe("Mot de passe à changer");
    expect(wrapper.find(".badge-actif").text()).toBe("Actif");
  });

  it("affiche un message d'erreur si le chargement échoue", async () => {
    api.get.mockRejectedValueOnce(new Error("boom"));

    const wrapper = mount(UsersView);
    await flushPromises();

    expect(wrapper.find(".error").text()).toBe("Impossible de charger les utilisateurs, réessayez.");
    expect(wrapper.find("table").exists()).toBe(false);
  });

  it("affiche l'état de chargement pendant la requête", async () => {
    let resoudre;
    api.get.mockReturnValueOnce(new Promise((r) => (resoudre = r)));

    const wrapper = mount(UsersView);
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Chargement des utilisateurs...");

    resoudre({ data: UTILISATEURS });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Chargement des utilisateurs...");
  });

  it("affiche un message quand aucun compte n'existe", async () => {
    const wrapper = await monter([]);

    expect(wrapper.find(".empty").text()).toBe("Aucun utilisateur à afficher.");
  });

  it("crée un compte avec un mot de passe temporaire pré-généré", async () => {
    const wrapper = await monter();
    api.post.mockResolvedValueOnce({ data: {} });
    api.get.mockResolvedValueOnce({ data: UTILISATEURS });

    await boutonNomme(wrapper, "＋ Nouvel utilisateur").trigger("click");
    expect(wrapper.find("#nouveau-mot-de-passe").element.value).toHaveLength(12);

    await wrapper.find("#nouvel-email").setValue("nouveau@enervision.fr");
    await wrapper.find("#nouveau-role").setValue("operator");
    await wrapper.find("form.creation").trigger("submit");
    await flushPromises();

    const [url, payload] = api.post.mock.calls[0];
    expect(url).toBe("/users");
    expect(payload.email).toBe("nouveau@enervision.fr");
    expect(payload.role).toBe("operator");
    expect(payload.password).toHaveLength(12);
    expect(wrapper.text()).toContain("Compte créé pour nouveau@enervision.fr");
    // La liste est rechargée pour refléter la création.
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("signale un email déjà utilisé", async () => {
    const wrapper = await monter();
    api.post.mockRejectedValueOnce({ response: { status: 409 } });

    await boutonNomme(wrapper, "＋ Nouvel utilisateur").trigger("click");
    await wrapper.find("#nouvel-email").setValue("admin@enervision.fr");
    await wrapper.find("form.creation").trigger("submit");
    await flushPromises();

    expect(wrapper.find(".creation .alert-error").text()).toBe("Cet email est déjà utilisé.");
    expect(wrapper.find("form.creation").exists()).toBe(true);
  });

  it("change le rôle d'un compte", async () => {
    const wrapper = await monter();
    api.patch.mockResolvedValueOnce({ data: { ...UTILISATEURS[1], role: "operator" } });

    await wrapper.findAll(".role-select")[1].setValue("operator");
    await flushPromises();

    expect(api.patch).toHaveBeenCalledWith(
      "/users/22222222-2222-2222-2222-222222222222",
      { role: "operator" }
    );
    expect(wrapper.text()).toContain("Rôle de viewer@enervision.fr mis à jour.");
  });

  it("réinitialise le mot de passe d'un autre compte", async () => {
    const wrapper = await monter();
    api.post.mockResolvedValueOnce({ data: { ...UTILISATEURS[1], must_change_password: true } });

    await wrapper.findAll("tbody tr")[1].findAll("button")[0].trigger("click");
    const champ = wrapper.find("#mot-de-passe-temporaire");
    expect(champ.element.value).toHaveLength(12);

    await champ.setValue("temporaire1");
    await wrapper.find(".modale").trigger("submit");
    await flushPromises();

    expect(api.post).toHaveBeenCalledWith(
      "/users/22222222-2222-2222-2222-222222222222/password",
      { password: "temporaire1" }
    );
    expect(wrapper.find(".overlay").exists()).toBe(false);
    expect(wrapper.text()).toContain("Mot de passe temporaire défini pour viewer@enervision.fr.");
  });

  it("supprime un compte après confirmation", async () => {
    const wrapper = await monter();
    api.delete.mockResolvedValueOnce({});
    api.get.mockResolvedValueOnce({ data: [UTILISATEURS[0]] });

    await wrapper.findAll("tbody tr")[1].findAll("button")[1].trigger("click");
    expect(wrapper.find(".overlay").text()).toContain("viewer@enervision.fr");

    await boutonNomme(wrapper, "Confirmer la suppression").trigger("click");
    await flushPromises();

    expect(api.delete).toHaveBeenCalledWith("/users/22222222-2222-2222-2222-222222222222");
    expect(wrapper.findAll("tbody tr")).toHaveLength(1);
    expect(wrapper.text()).toContain("Compte viewer@enervision.fr supprimé.");
  });

  it("n'appelle pas l'API si la suppression est annulée", async () => {
    const wrapper = await monter();

    await wrapper.findAll("tbody tr")[1].findAll("button")[1].trigger("click");
    await boutonNomme(wrapper, "Annuler").trigger("click");

    expect(wrapper.find(".overlay").exists()).toBe(false);
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("verrouille les actions de l'admin sur son propre compte", async () => {
    const wrapper = await monter();

    const ligneAdmin = wrapper.findAll("tbody tr")[0];
    expect(ligneAdmin.text()).toContain("vous");
    expect(ligneAdmin.find(".role-select").attributes("disabled")).toBeDefined();
    // Réinitialiser et Supprimer : l'admin n'agit pas sur son propre compte ici.
    expect(ligneAdmin.findAll("button")[0].attributes("disabled")).toBeDefined();
    expect(ligneAdmin.findAll("button")[1].attributes("disabled")).toBeDefined();
  });

  it("affiche l'erreur renvoyée par l'API sur un changement de rôle refusé", async () => {
    const wrapper = await monter();
    api.patch.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Vous ne pouvez pas modifier votre propre rôle" } },
    });
    api.get.mockResolvedValueOnce({ data: UTILISATEURS });

    await wrapper.findAll(".role-select")[1].setValue("admin");
    await flushPromises();

    expect(wrapper.find(".alert-error").text()).toBe(
      "Vous ne pouvez pas modifier votre propre rôle"
    );
  });
});
