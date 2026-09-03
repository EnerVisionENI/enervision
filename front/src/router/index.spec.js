import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";

const etat = vi.hoisted(() => ({ token: null, profil: null }));

vi.mock("../api/client", () => ({
  default: { get: vi.fn() },
}));

vi.mock("../auth/auth", async () => {
  const { ref: reference } = await import("vue");
  const profil = reference(null);
  return {
    currentUser: profil,
    isAuthenticated: () => !!etat.token,
    getRole: () => profil.value?.role || null,
    mustChangePassword: () => profil.value?.must_change_password === true,
    setCurrentUser: (user) => {
      profil.value = user || null;
    },
    __profil: profil,
  };
});

const auth = await import("../auth/auth");
const api = (await import("../api/client")).default;
const router = (await import("./index")).default;

function connecter(profil) {
  etat.token = "jeton";
  auth.setCurrentUser(profil);
}

const VIEWER = { user_id: "1", email: "viewer@enervision.fr", role: "viewer", must_change_password: false };
const ADMIN = { user_id: "2", email: "admin@enervision.fr", role: "admin", must_change_password: false };

async function allerA(chemin) {
  try {
    await router.push(chemin);
  } catch {
    // une redirection depuis une garde rejette la navigation, l'état final suffit
  }
  await router.isReady();
  return router.currentRoute.value;
}

describe("router", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    etat.token = null;
    auth.setCurrentUser(null);
    await allerA("/login");
  });

  it("renvoie vers /login une route protégée sans session", async () => {
    expect((await allerA("/alertes")).name).toBe("login");
  });

  it("laisse passer une route protégée avec une session valide", async () => {
    connecter(VIEWER);
    expect((await allerA("/alertes")).name).toBe("alerts");
  });

  it("renvoie vers le dashboard un utilisateur connecté qui rouvre /login", async () => {
    connecter(VIEWER);
    await allerA("/alertes"); // vue-router ignore un push vers la route courante

    expect((await allerA("/login")).name).toBe("dashboard");
  });

  it("force le changement de mot de passe tant que le compte est neuf", async () => {
    connecter({ ...VIEWER, must_change_password: true });

    expect((await allerA("/alertes")).name).toBe("change-password");
    expect((await allerA("/")).name).toBe("change-password");
    expect((await allerA("/mot-de-passe")).name).toBe("change-password");
  });

  it("réserve la gestion des utilisateurs aux admins", async () => {
    connecter(VIEWER);
    expect((await allerA("/utilisateurs")).name).toBe("dashboard");

    connecter(ADMIN);
    expect((await allerA("/utilisateurs")).name).toBe("users");
  });

  it("récupère le profil depuis /auth/me quand il n'est pas en cache", async () => {
    etat.token = "jeton";
    api.get.mockResolvedValueOnce({ data: ADMIN });

    const route = await allerA("/utilisateurs");

    expect(api.get).toHaveBeenCalledWith("/auth/me");
    expect(route.name).toBe("users");
  });

  it("ne rappelle pas /auth/me quand le profil est déjà connu", async () => {
    connecter(ADMIN);

    await allerA("/utilisateurs");
    await allerA("/alertes");

    expect(api.get).not.toHaveBeenCalled();
  });
});
