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
      this.options = config.options;
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

const MESURE_OK = {
  // measurements_silver représente un instant passé : date dynamique, pas
  // fixe, sinon la purge par âge réel du buffer (fenêtre glissante) la
  // rejette aussitôt comme trop ancienne.
  timestamp: new Date().toISOString(),
  consumption_kw: 179.61,
  voltage_v: 404.6,
  current_a: 269.8,
  power_factor: 0.931,
  temperature_celsius: 9.5,
  humidity_percent: 41.2,
  quality_score: 100,
  data_quality: "good",
  null_reasons: [],
};

const MESURE_DEGRADEE = {
  ...MESURE_OK,
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

  it("charge les sites, sélectionne le premier et affiche sa dernière mesure", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 500 }] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites");
    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 2 } });
    expect(wrapper.text()).toContain("SITE001");
    expect(wrapper.text()).toContain("Bureau Paris La Défense");
    expect(wrapper.text()).toContain("200 kW");
    expect(wrapper.find(".lecture-actuelle").text()).toContain("180");
    expect(wrapper.findAll(".site-pill")).toHaveLength(2);
    // Index 0 = "consumption_kw", la première métrique déclarée (focus par défaut).
    expect(chartInstances[0].data.datasets[0].data).toEqual([179.61]);
    expect(chartInstances[0].data.datasets[1].data).toEqual([200]);
  });

  it("crée un graphique par métrique disponible dans measurements_silver", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    // 7 petites cartes (toujours visibles, ordre fixe) + 1 graphique "grand"
    // (aperçu de la métrique sélectionnée, recréé à chaque changement de focus).
    expect(chartInstances).toHaveLength(8);
  });

  it("affiche un aperçu agrandi de la carte cliquée, sans jamais la retirer de sa place fixe", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Puissance appelée");
    expect(wrapper.find(".lecture-actuelle").text()).toContain("kW");
    // Les 7 petites cartes, ordre fixe, "Puissance appelée" toujours présente.
    const titresAvant = wrapper.findAll(".graphique-carte-titre").map((t) => t.text());
    expect(titresAvant).toEqual([
      "Puissance appelée",
      "Tension",
      "Courant",
      "Facteur de puissance",
      "Température",
      "Humidité",
      "Score qualité",
    ]);

    const carteTension = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Tension"));
    await carteTension.trigger("click");
    await flushPromises();

    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Tension");
    // 404.6 arrondi à l'entier (0 décimale pour la tension).
    expect(wrapper.find(".lecture-actuelle").text()).toContain("405");
    expect(wrapper.find(".lecture-actuelle").text()).toContain("V");
    // Même 7 cartes, même ordre : "Tension" n'a pas disparu de sa place.
    const titresApres = wrapper.findAll(".graphique-carte-titre").map((t) => t.text());
    expect(titresApres).toEqual(titresAvant);
    expect(wrapper.find(".graphique-carte.actif .graphique-carte-titre").text()).toBe("Tension");

    // Le graphique agrandi a de vrais axes ; la petite carte "Tension" reste
    // en rendu sparkline (axes masqués), c'est un aperçu distinct.
    const grand = chartInstances.filter((c) => !c.destroyed).find((c) => c.options.scales.x.display !== false);
    expect(grand.data.datasets[0].label).toBe("Tension");
    const petiteTension = chartInstances[1]; // 2e métrique déclarée = voltage_v
    expect(petiteTension.options.scales.x.display).toBe(false);
  });

  it("affiche toutes les mesures récentes reçues, dans l'ordre chronologique", async () => {
    const ilYA90s = new Date(Date.now() - 90_000).toISOString();
    const ilYA30s = new Date(Date.now() - 30_000).toISOString();
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({
            data: [
              { ...MESURE_OK, timestamp: ilYA90s, consumption_kw: 40 },
              { ...MESURE_OK, timestamp: ilYA30s, consumption_kw: 55 },
            ],
          }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    expect(chartInstances[0].data.datasets[0].data).toEqual([40, 55]);
  });

  it("affiche le taux réel de disponibilité des lectures sur la fenêtre", async () => {
    const ilYA90s = new Date(Date.now() - 90_000).toISOString();
    const ilYA60s = new Date(Date.now() - 60_000).toISOString();
    const ilYA30s = new Date(Date.now() - 30_000).toISOString();
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({
            data: [
              { ...MESURE_OK, timestamp: ilYA90s, consumption_kw: 40 },
              {
                ...MESURE_OK,
                timestamp: ilYA60s,
                consumption_kw: null,
                data_quality: "critical",
                null_reasons: ["network_loss"],
              },
              { ...MESURE_OK, timestamp: ilYA30s, consumption_kw: 55 },
            ],
          }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    // 2 lectures valides sur 3 -> 67 %.
    expect(wrapper.find(".disponibilite").text()).toContain("67% de lectures disponibles");
  });

  it("affiche un message explicite quand la dernière mesure n'a pas de valeur", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: null, data_quality: "critical" }] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".lecture-actuelle").exists()).toBe(false);
    expect(wrapper.find(".pas-de-donnee").text()).toBe("Pas de mesure récente");
  });

  it("recharge les mesures de tous les sites avec la nouvelle fenêtre choisie", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000)); // 1 h
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 60 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 60 } });
  });

  it("interroge les mesures de tous les sites à chaque cycle, pas seulement celui affiché", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 500 }] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 2 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 2 } });
  });

  // Le grand graphique de la puissance appelée est prolongé par une prédiction
  // mockée (aucun modèle ML côté backend) : trait pointillé + bande
  // d'incertitude, à la suite des mesures réelles.
  function graphiqueGrand() {
    return chartInstances.filter((c) => !c.destroyed).find((c) => c.options.scales.x.display !== false);
  }

  function mesuresSurUneHeure({ n = 20, base = 100 } = {}) {
    const mesures = [];
    for (let i = n; i > 0; i--) {
      mesures.push({ ...MESURE_OK, timestamp: new Date(Date.now() - i * 60_000).toISOString(), consumption_kw: base + (i % 3) });
    }
    return mesures;
  }

  it("prolonge la puissance appelée par une prédiction, à la suite des mesures réelles", async () => {
    const reelles = mesuresSurUneHeure({ n: 20, base: 100 });
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: reelles }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000)); // 1 h
    await flushPromises();

    const grand = graphiqueGrand();
    const [reel, , prediction, bornesHautes, bornesBasses] = grand.data.datasets.map((d) => d.data);

    // 20 points réels + 60 points de prédiction sur l'axe des abscisses.
    expect(grand.data.labels).toHaveLength(80);
    expect(reel).toHaveLength(20);
    expect(prediction).toHaveLength(80);
    // Vide sur la portion réelle sauf le dernier point, repris tel quel pour que
    // le pointillé démarre exactement là où le trait plein s'arrête.
    expect(prediction.slice(0, 19).every((v) => v === null)).toBe(true);
    expect(prediction[19]).toBe(reelles[reelles.length - 1].consumption_kw);
    expect(prediction.slice(20).every((v) => typeof v === "number")).toBe(true);

    // La bande d'incertitude encadre la prédiction sur tout l'horizon.
    for (let i = 20; i < 80; i++) {
      expect(bornesBasses[i]).toBeLessThanOrEqual(prediction[i]);
      expect(bornesHautes[i]).toBeGreaterThanOrEqual(prediction[i]);
    }
  });

  it("borne la prédiction par la puissance souscrite du site", async () => {
    // SITE001 : capacity_kw 200, mesures à 198-200 kW — même bruitée, aucune
    // valeur prédite ne doit dépasser le plafond contractuel ni passer sous 0.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure({ base: 198 }) }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000)); // 1 h
    await flushPromises();

    const prediction = graphiqueGrand().data.datasets[2].data.slice(20);
    expect(prediction).toHaveLength(60);
    for (const v of prediction) {
      expect(v).toBeLessThanOrEqual(200);
      expect(v).toBeGreaterThanOrEqual(0);
    }
  });

  it("ne prédit que la puissance appelée, jamais les autres métriques", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(graphiqueGrand().data.datasets.some((d) => d.label === "Prédiction ML")).toBe(true);
    expect(wrapper.text()).toContain("prédiction ML");

    const carteTension = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Tension"));
    await carteTension.trigger("click");
    await flushPromises();

    // La tension n'a ni prédiction ni seuil : un seul dataset, celui des mesures.
    expect(graphiqueGrand().data.datasets.some((d) => d.label === "Prédiction ML")).toBe(false);
    expect(wrapper.text()).not.toContain("prédiction ML");
  });

  it("laisse les petites cartes en sparkline, sans prédiction", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    // chartInstances[0] = petite carte "Puissance appelée" : mesure + seuil, rien d'autre.
    expect(chartInstances[0].data.datasets.map((d) => d.label)).toEqual([
      "Puissance appelée",
      "Puissance souscrite (kW)",
    ]);
  });

  it("n'affiche aucune prédiction tant qu'aucune mesure n'a de valeur", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: null, data_quality: "critical" }] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data).toEqual([]);
  });

  it("affiche un message d'erreur si le chargement des sites échoue", async () => {
    api.get.mockRejectedValueOnce(new Error("boom"));

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".erreur").text()).toBe("Impossible de charger les sites, réessayez.");
  });

  it("affiche la qualité dégradée dans la chaîne de confiance", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_DEGRADEE] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const qualite = wrapper.find(".confiance-valeur.qualite");
    expect(qualite.text()).toBe("dégradée");
    expect(qualite.classes()).not.toContain("fiable");
    expect(qualite.attributes("title")).toBe("capteur humidité en panne");
  });

  it("affiche un message d'erreur si une actualisation échoue, sans planter la page", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.reject(new Error("boom")),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".erreur").text()).toBe(
      "Mesures indisponibles, nouvelle tentative au prochain cycle."
    );
  });

  it("sonde à nouveau toutes les 30 secondes et actualise la courbe", async () => {
    let compteur = 0;
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => {
          compteur += 1;
          const mesures = [];
          for (let i = 0; i < compteur; i++) {
            mesures.push({
              ...MESURE_OK,
              timestamp: new Date(Date.now() - (compteur - i) * 1000).toISOString(),
              consumption_kw: 100 + i,
            });
          }
          return Promise.resolve({ data: mesures });
        },
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();
    expect(chartInstances[0].data.datasets[0].data).toEqual([100]);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(chartInstances[0].data.datasets[0].data).toEqual([100, 101]);
  });

  it("un cycle de sondage sans donnée nouvelle est inoffensif ; la mise à jour arrive au cycle suivant", async () => {
    let compteur = 0;
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => {
          compteur += 1;
          // etl-collect n'écrit qu'une fois par minute : la même dernière
          // ligne est renvoyée à 0s et 30s, une nouvelle apparaît à 60s.
          const premierPoint = { ...MESURE_OK, timestamp: new Date(Date.now() - 60_000).toISOString(), consumption_kw: 100 };
          const deuxiemePoint = { ...MESURE_OK, timestamp: new Date().toISOString(), consumption_kw: 101 };
          const data = compteur <= 2 ? [premierPoint] : [premierPoint, deuxiemePoint];
          return Promise.resolve({ data });
        },
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();
    expect(chartInstances[0].data.datasets[0].data).toEqual([100]);

    // Cycle à 30s : pas encore de nouvelle ligne côté silver, rien ne change.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(chartInstances[0].data.datasets[0].data).toEqual([100]);

    // Cycle à 60s : la nouvelle mesure est là, le graphique se met à jour.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(chartInstances[0].data.datasets[0].data).toEqual([100, 101]);
  });

  it("change de site instantanément au clic, sans nouvel appel de mesures", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 500 }] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(chartInstances[0].data.datasets[0].data).toEqual([179.61]);

    const appelsMesuresAvant = api.get.mock.calls.filter(([url]) => url.includes("/measurements")).length;
    await wrapper.findAll(".site-pill")[1].trigger("click");
    await flushPromises();

    // Le buffer de SITE002 a déjà été rempli pendant le cycle initial (tous
    // les sites sont sondés à chaque tick) : basculer dessus ne déclenche
    // aucun appel de mesures supplémentaire et n'affiche jamais 0 donnée. Le
    // résumé du jour, lui, n'est chargé que pour le site affiché : un nouvel
    // appel pour SITE002 est normal et attendu.
    const appelsMesuresApres = api.get.mock.calls.filter(([url]) => url.includes("/measurements")).length;
    expect(appelsMesuresApres).toBe(appelsMesuresAvant);
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/daily-summary");
    expect(wrapper.text()).toContain("Usine Rennes");
    expect(chartInstances[0].data.datasets[0].data).toEqual([500]);
    expect(chartInstances[0].data.datasets[1].data).toEqual([1000]);
  });

  it("change de site au clic sur une carte du parc", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 500 }] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const carteSite002 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    await carteSite002.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Usine Rennes");
  });

  it("affiche la vraie dernière puissance dans la grille parc et signale un site proche de sa limite", async () => {
    mockApi({
      lectures: {
        // SITE001 : capacity_kw 200 -> 190 kW = 95 %, au-dessus du seuil d'alerte.
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 190 }] }),
        // SITE002 : capacity_kw 1000 -> 300 kW = 30 %, sous le seuil.
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: 300 }] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const pilleSite001 = wrapper.findAll(".site-pill").find((p) => p.text().includes("SITE001"));
    const pilleSite002 = wrapper.findAll(".site-pill").find((p) => p.text().includes("SITE002"));
    expect(pilleSite001.classes()).toContain("alerte");
    expect(pilleSite002.classes()).not.toContain("alerte");

    const carteSite001 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE001"));
    const carteSite002 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    expect(carteSite001.find(".valeur").text()).toBe("190");
    expect(carteSite001.find(".valeur").classes()).toContain("alerte");
    expect(carteSite002.find(".valeur").text()).toBe("300");
    expect(carteSite002.find(".valeur").classes()).not.toContain("alerte");
  });

  it("affiche une vraie sparkline pour un site avec plusieurs mesures, aucune sans donnée", async () => {
    const ilYA60s = new Date(Date.now() - 60_000).toISOString();
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({
            data: [
              { ...MESURE_OK, timestamp: ilYA60s, consumption_kw: 50 },
              { ...MESURE_OK, consumption_kw: 60 },
            ],
          }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const carteSite001 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE001"));
    const carteSite002 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    expect(carteSite001.findAll("polyline").length).toBeGreaterThan(0);
    expect(carteSite002.findAll("polyline").length).toBe(0);
  });

  it("affiche les KPI réels du jour (aggregates_gold_daily) quand ils existent", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
        "/sites/SITE001/daily-summary": () =>
          Promise.resolve({
            data: {
              record_date: "2026-09-03",
              records_count: 200,
              good_count: 40,
              avg_consumption_kw: 111.29,
              max_consumption_kw: 186.63,
              total_consumption_kwh: 11685.34,
              avg_quality_score: 68.5,
            },
          }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/daily-summary");
    const cartes = wrapper.findAll(".kpi-carte").map((c) => c.text());
    // toLocaleString("fr-FR") sépare les milliers par une espace insécable
    // (pas une espace normale) : on vérifie les groupes de chiffres séparément.
    expect(cartes.some((t) => t.includes("Consommation du jour") && t.includes("11") && t.includes("685"))).toBe(
      true
    );
    expect(cartes.some((t) => t.includes("Puissance moyenne") && t.includes("111"))).toBe(true);
    expect(cartes.some((t) => t.includes("Pic de puissance") && t.includes("187"))).toBe(true);
    expect(cartes.some((t) => t.includes("Score qualité moyen") && t.includes("69"))).toBe(true);
    // 40 bonnes lectures sur 200 -> 20 %.
    expect(cartes.some((t) => t.includes("Lectures fiables") && t.includes("20"))).toBe(true);
  });

  it("affiche un message quand le résumé du jour n'est pas encore calculé", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
        "/sites/SITE001/daily-summary": () => Promise.resolve({ data: null }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".pas-de-donnee-kpi").text()).toBe("Résumé du jour pas encore calculé pour ce site.");
    expect(wrapper.findAll(".kpi-carte")).toHaveLength(0);
  });

  it("recharge le résumé du jour du nouveau site au changement de site", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
        "/sites/SITE001/daily-summary": () =>
          Promise.resolve({
            data: { record_date: "2026-09-03", records_count: 10, good_count: 10, avg_consumption_kw: 100 },
          }),
        "/sites/SITE002/daily-summary": () =>
          Promise.resolve({
            data: { record_date: "2026-09-03", records_count: 10, good_count: 5, avg_consumption_kw: 900 },
          }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    function puissanceMoyenneAffichee() {
      const carte = wrapper.findAll(".kpi-carte").find((c) => c.text().includes("Puissance moyenne"));
      return carte.find(".kpi-valeur").text();
    }

    expect(puissanceMoyenneAffichee()).toContain("100");

    await wrapper.findAll(".site-pill")[1].trigger("click");
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/daily-summary");
    expect(puissanceMoyenneAffichee()).toContain("900");
  });
});
