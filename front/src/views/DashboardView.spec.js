import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import DashboardView from "./DashboardView.vue";
import api from "../api/client";

vi.mock("../api/client", () => ({
  default: { get: vi.fn() },
}));

const chartInstances = [];
vi.mock("chart.js/auto", () => {
  class FakeChart {
    constructor(_ctx, config) {
      this.data = config.data;
      this.destroyed = false;
      chartInstances.push(this);
    }
    update() {}
    destroy() {
      this.destroyed = true;
    }
  }
  return { Chart: FakeChart };
});

const SITES = [
  { site_id: "SITE001", site_type: "office", site_name: "Bureau Paris La Défense", location: "Paris", capacity_kw: 200, status: "active" },
  { site_id: "SITE002", site_type: "industrial", site_name: "Usine Rennes", location: "Rennes", capacity_kw: 1000, status: "active" },
];

const LECTURE_OK = {
  // Une lecture "current" représente l'instant présent : date dynamique, pas
  // fixe, sinon la purge par âge réel du buffer (fenêtre glissante de 2 min)
  // la rejette aussitôt comme trop ancienne.
  timestamp: new Date().toISOString(),
  site_id: "SITE001",
  site_type: "office",
  consumption_kw: 179.61,
  consumption_kwh: 179.61,
  voltage_v: 404.6,
  current_a: 269.8,
  power_factor: 0.931,
  temperature_celsius: 9.5,
  humidity_percent: null,
  null_reasons: [],
  data_quality: "good",
};

const LECTURE_DEGRADEE = {
  ...LECTURE_OK,
  data_quality: "partial",
  null_reasons: ["humidity_sensor_failure"],
};

function mockApi({ sites = SITES, lectures = {} } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/sites") return Promise.resolve({ data: sites });
    if (lectures[url]) return lectures[url]();
    return Promise.reject(new Error(`URL non mockée: ${url}`));
  });
}

describe("DashboardView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chartInstances.length = 0;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("charge les sites, sélectionne le premier et affiche sa première lecture", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () =>
          Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002", consumption_kw: 500 } }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites");
    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/current");
    expect(wrapper.text()).toContain("SITE001");
    expect(wrapper.text()).toContain("Bureau Paris La Défense");
    expect(wrapper.text()).toContain("200 kW");
    expect(wrapper.find(".lecture-actuelle").text()).toContain("180");
    expect(wrapper.findAll(".site-pill")).toHaveLength(2);
    expect(chartInstances[0].data.datasets[0].data).toEqual([179.61]);
    expect(chartInstances[0].data.datasets[1].data).toEqual([200]);
  });

  it("préremplit le graphique avec l'historique réel au chargement", async () => {
    const ilYA1Min = new Date(Date.now() - 60_000).toISOString();
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () => Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002" } }),
        "/sites/SITE001/measurements": () =>
          Promise.resolve({ data: [{ timestamp: ilYA1Min, consumption_kw: 40, data_quality: "good" }] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 2 } });
    // Le point historique (40) précède le point du premier sondage en direct
    // (179.61) : l'historique préremplit le graphique sans être écrasé par
    // le direct qui prend le relais juste après.
    expect(chartInstances[0].data.datasets[0].data).toEqual([40, 179.61]);
  });

  it("affiche le taux réel de disponibilité des lectures sur la fenêtre", async () => {
    const ilYA90s = new Date(Date.now() - 90_000).toISOString();
    const ilYA30s = new Date(Date.now() - 30_000).toISOString();
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }), // valide
        "/sites/SITE002/current": () => Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002" } }),
        "/sites/SITE001/measurements": () =>
          Promise.resolve({
            data: [
              { timestamp: ilYA90s, consumption_kw: 40, data_quality: "good" }, // valide
              { timestamp: ilYA30s, consumption_kw: null, data_quality: "critical" }, // sans valeur
            ],
          }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    // 2 lectures valides (40, 179.61) sur 3 points au total -> 67 %.
    expect(wrapper.find(".disponibilite").text()).toContain("67% de lectures disponibles");
  });

  it("affiche un message explicite quand la lecture n'a pas de valeur", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () =>
          Promise.resolve({ data: { ...LECTURE_OK, consumption_kw: null, data_quality: "critical" } }),
        "/sites/SITE002/current": () => Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002" } }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".lecture-actuelle").exists()).toBe(false);
    expect(wrapper.find(".pas-de-donnee").text()).toBe("Pas de donnée instantanée");
  });

  it("recharge l'historique de tous les sites avec la nouvelle fenêtre choisie", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () => Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002" } }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000)); // 1 h
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 60 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 60 } });
  });

  it("interroge tous les sites à chaque cycle de sondage, pas seulement celui affiché", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () =>
          Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002", consumption_kw: 500 } }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/current");
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/current");
  });

  it("affiche un message d'erreur si le chargement des sites échoue", async () => {
    api.get.mockRejectedValueOnce(new Error("boom"));

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".erreur").text()).toBe("Impossible de charger les sites, réessayez.");
  });

  it("affiche la qualité dégradée dans la chaîne de confiance", async () => {
    mockApi({
      lectures: { "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_DEGRADEE }) },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const qualite = wrapper.find(".confiance-valeur.qualite");
    expect(qualite.text()).toBe("dégradée");
    expect(qualite.classes()).not.toContain("fiable");
    expect(qualite.attributes("title")).toBe("capteur humidité en panne");
  });

  it("affiche un message d'erreur si une lecture échoue, sans planter la page", async () => {
    mockApi({
      lectures: { "/sites/SITE001/current": () => Promise.reject(new Error("boom")) },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".erreur").text()).toBe(
      "Lecture indisponible, nouvelle tentative au prochain cycle."
    );
  });

  it("sonde à nouveau toutes les 5 secondes et fait grandir la courbe", async () => {
    let compteur = 0;
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => {
          compteur += 1;
          return Promise.resolve({ data: { ...LECTURE_OK, consumption_kw: 100 + compteur } });
        },
      },
    });

    mount(DashboardView);
    await flushPromises();
    expect(chartInstances[0].data.datasets[0].data).toEqual([101]);

    await vi.advanceTimersByTimeAsync(5000);
    expect(chartInstances[0].data.datasets[0].data).toEqual([101, 102]);
  });

  it("change de site instantanément au clic, sans nouvel appel ni retour à 0 donnée", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () =>
          Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002", consumption_kw: 500 } }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(chartInstances[0].data.datasets[0].data).toEqual([179.61]);

    const appelsAvant = api.get.mock.calls.length;
    await wrapper.findAll(".site-pill")[1].trigger("click");
    await flushPromises();

    // Le buffer de SITE002 a déjà été rempli pendant le cycle initial (tous
    // les sites sont sondés à chaque tick) : basculer dessus ne déclenche
    // aucun appel réseau supplémentaire et n'affiche jamais 0 donnée.
    expect(api.get.mock.calls.length).toBe(appelsAvant);
    expect(wrapper.text()).toContain("Usine Rennes");
    expect(chartInstances[0].data.datasets[0].data).toEqual([500]);
    expect(chartInstances[0].data.datasets[1].data).toEqual([1000]);
  });

  it("change de site au clic sur une carte du parc (contenu de démonstration)", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/current": () => Promise.resolve({ data: LECTURE_OK }),
        "/sites/SITE002/current": () =>
          Promise.resolve({ data: { ...LECTURE_OK, site_id: "SITE002", consumption_kw: 500 } }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const carteSite002 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    await carteSite002.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Usine Rennes");
  });
});
