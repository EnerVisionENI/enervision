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

// Une heure prédite telle que la sert GET /sites/{id}/predictions. Horodatages dynamiques :
// l'API ne renvoie que du futur, et le composant écarte tout point antérieur au dernier
// instant mesuré — des dates fixes seraient rejetées.
function previsionsAVenir({ n = 3, base = 150, dansHeures = 1, ...reste } = {}) {
  const lignes = [];
  for (let i = 0; i < n; i++) {
    lignes.push({
      target_ts: new Date(Date.now() + (dansHeures + i) * 3_600_000).toISOString(),
      step_minutes: 60,
      predicted_kwh: base + i * 10,
      lower_90: base + i * 10 - 20,
      upper_90: base + i * 10 + 20,
      temperature_celsius: 10.6,
      model_version: "1",
      model_stage: "Production",
      champion: "tow_temp",
      data_source: "csv_synthetic",
      predicted_at: new Date().toISOString(),
      ...reste,
    });
  }
  return lignes;
}

function mockApi({ sites = SITES, lectures = {}, previsions = {} } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/sites") return Promise.resolve({ data: sites });
    if (lectures[url]) return lectures[url]();
    // Défaut à liste vide : la plupart des tests ne portent pas sur la prévision et ne
    // doivent pas basculer l'écran en « service injoignable » faute de mock.
    if (url.endsWith("/predictions")) {
      return previsions[url] ? previsions[url]() : Promise.resolve({ data: [] });
    }
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

  // La prévision est volontairement masquée sous une heure d'historique (modèles horaires),
  // et la fenêtre par défaut est de 2 min : tout test qui porte sur la courbe doit d'abord
  // se placer sur une fenêtre où elle s'affiche.
  // Les jeux de prévision portent des points {x, y} (horodatages horaires propres, distincts
  // de ceux des mesures) : les assertions ne lisent que les ordonnées.
  function ordonnees(dataset) {
    return dataset.map((point) => point.y);
  }

  async function monterAvecPrevisionVisible() {
    const wrapper = mount(DashboardView);
    await flushPromises();
    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000));
    await flushPromises();
    return wrapper;
  }

  function mesuresSurUneHeure({ n = 20, base = 100 } = {}) {
    const mesures = [];
    for (let i = n; i > 0; i--) {
      mesures.push({ ...MESURE_OK, timestamp: new Date(Date.now() - i * 60_000).toISOString(), consumption_kw: base + (i % 3) });
    }
    return mesures;
  }

  it("masque la prévision sous une heure d'historique, et le dit", async () => {
    // Fenêtre par défaut : 2 min. Un point de prévision horaire étirerait l'axe à 62 min et
    // réduirait les mesures à 3 % de la largeur — l'inverse exact du défaut qu'on vient de
    // corriger. Tant qu'on n'affiche rien, il faut dire pourquoi.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data).toEqual([]);
    expect(wrapper.text()).toContain("Prévision masquée sur cette fenêtre");
    expect(wrapper.text()).not.toContain("intervalle 90 %");

    // À partir d'une heure, elle réapparaît.
    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000));
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data.length).toBeGreaterThan(0);
    expect(wrapper.text()).not.toContain("Prévision masquée sur cette fenêtre");
  });

  it("place les points sur une échelle temporelle, pas catégorielle", async () => {
    // Les mesures arrivent à la minute, la prévision à l'heure. Sur une échelle catégorielle
    // chaque point occupe la même largeur : 6 h de prévision (6 points) se tassaient sur 1,6 %
    // de l'axe face à 6 h de mesures (360 points). L'axe doit rester en "time" et recevoir des
    // Date, seul moyen que les durées soient représentées à leur proportion réelle.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ n: 3 }) }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    const grand = graphiqueGrand();
    expect(grand.options.scales.x.type).toBe("time");
    // Des Date jusqu'au bout : une seule chaîne formatée suffirait à faire retomber
    // Chart.js sur un placement à index constant pour toute la série.
    expect(grand.data.labels.every((l) => l instanceof Date)).toBe(true);
  });

  it("prolonge la puissance appelée par la prévision servie par l'API", async () => {
    const reelles = mesuresSurUneHeure({ n: 20, base: 100 });
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: reelles }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ n: 3, base: 150 }) }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    // Fenêtre 1 h : sur la fenêtre par défaut (2 min), la purge par âge ne laisserait que
    // deux des vingt mesures et l'assertion ne porterait plus sur le raccord des courbes.
    await wrapper.find(".select-fenetre").setValue(String(60 * 60 * 1000));
    await flushPromises();

    const grand = graphiqueGrand();
    const [reel, , prediction, bornesHautes, bornesBasses] = grand.data.datasets.map((d) => d.data);

    // Les libellés ne couvrent plus que les mesures : la prévision porte ses propres
    // abscisses et n'a plus besoin d'un emplacement dans ce tableau.
    expect(grand.data.labels).toHaveLength(20);
    expect(reel).toHaveLength(20);
    // Un point par heure prédite, horodaté, sans remplissage de la portion mesurée.
    expect(prediction).toHaveLength(3);
    expect(prediction.every((p) => p.x instanceof Date)).toBe(true);
    // Les valeurs tracées sont celles de l'API, pas une simulation.
    expect(ordonnees(prediction)).toEqual([150, 160, 170]);
    expect(ordonnees(bornesBasses)).toEqual([130, 140, 150]);
    expect(ordonnees(bornesHautes)).toEqual([170, 180, 190]);
    await wrapper.unmount();
  });

  it("superpose les prévisions passées aux mesures, pour rendre l'écart lisible", async () => {
    // Une ligne passée n'est pas une prévision périmée : chaque cycle réécrit le futur et
    // laisse le passé intact, donc elle reste ce qui avait été prédit pour cette heure-là.
    // La tracer en regard de la mesure est tout l'intérêt — sans ça, rien ne dit à l'écran
    // si le modèle tombe juste.
    const passees = previsionsAVenir({ n: 2, base: 700, dansHeures: -3 }); // -3 h et -2 h
    const futures = previsionsAVenir({ n: 2, base: 900, dansHeures: 1 });
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: [...passees, ...futures] }),
      },
    });

    await monterAvecPrevisionVisible();

    const prediction = graphiqueGrand().data.datasets[2].data;
    // Les quatre points sont tracés, passé compris.
    expect(ordonnees(prediction)).toEqual([700, 710, 900, 910]);
    const maintenant = Date.now();
    expect(prediction.filter((p) => p.x.getTime() < maintenant)).toHaveLength(2);
    expect(prediction.filter((p) => p.x.getTime() > maintenant)).toHaveLength(2);
  });

  it("étend la puissance souscrite jusqu'au bout de la prévision", async () => {
    // Le seuil est tracé par ses deux extrémités : s'il s'arrêtait à la dernière mesure, la
    // partie prédite — justement celle où un dépassement se lit — n'aurait plus de référence.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ n: 3 }) }),
      },
    });

    await monterAvecPrevisionVisible();

    const seuil = graphiqueGrand().data.datasets[1].data;
    const prediction = graphiqueGrand().data.datasets[2].data;
    expect(ordonnees(seuil)).toEqual([200, 200]); // capacity_kw de SITE001
    expect(seuil[seuil.length - 1].x.getTime()).toBe(prediction[prediction.length - 1].x.getTime());
  });

  it("trace la prévision telle quelle, sans la borner à la puissance souscrite", async () => {
    // SITE001 est souscrit à 200 kW et le modèle prédit 400 : les modèles V1 sont entraînés sur
    // CSV synthétique et débordent réellement (SITE003 dépasse sa capacité en production).
    // Écrêter à l'affichage ferait passer un modèle mal calibré pour un modèle prudent.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure({ base: 198 }) }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ n: 2, base: 400 }) }),
      },
    });

    await monterAvecPrevisionVisible();

    expect(ordonnees(graphiqueGrand().data.datasets[2].data)).toEqual([400, 410]);
  });

  it("propage des bornes absentes en null, sans les replier sur la valeur prédite", async () => {
    // Un run MLflow sans marge conforme donne lower_90/upper_90 à NULL. Les remplacer par la
    // valeur centrale dessinerait une bande d'épaisseur nulle, soit une certitude parfaite.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () =>
          Promise.resolve({ data: previsionsAVenir({ n: 2, base: 150, lower_90: null, upper_90: null }) }),
      },
    });

    await monterAvecPrevisionVisible();

    const [, , prediction, hautes, basses] = graphiqueGrand().data.datasets.map((d) => d.data);
    expect(ordonnees(prediction)).toEqual([150, 160]);
    expect(ordonnees(hautes)).toEqual([null, null]);
    expect(ordonnees(basses)).toEqual([null, null]);
  });

  it("affiche le champion et la version du modèle dans la légende", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = await monterAvecPrevisionVisible();

    expect(wrapper.text()).toContain("tow_temp");
    expect(wrapper.text()).toContain("v1");
    expect(wrapper.text()).toContain("intervalle 90 %");
  });

  it("signale qu'un modèle entraîné sur données synthétiques n'est pas validé", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = await monterAvecPrevisionVisible();

    expect(wrapper.text()).toContain("données synthétiques");
  });

  it("n'invente aucune courbe pour un site sans modèle inférable, et l'explique", async () => {
    // SITE002/004/007 : champion LightGBM réclamant solar_irradiance_wm2, absente du gold.
    // L'API répond 200 avec une liste vide — c'est un état normal, pas une panne.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data).toEqual([]);
    expect(wrapper.text()).toContain("Aucune prévision pour ce site");
    expect(wrapper.text()).toContain("solar_irradiance_wm2");
  });

  it("distingue un service de prédiction en panne d'un site sans modèle", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.reject(new Error("503")),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data).toEqual([]);
    expect(wrapper.text()).toContain("service de prédiction injoignable");
    expect(wrapper.text()).not.toContain("Aucune prévision pour ce site");
  });

  it("affiche la prévision même sans mesure exploitable, le modèle n'en dépendant pas", async () => {
    // Les modèles sont calendaires (heure de la semaine + météo) : aucun retard de consommation
    // n'entre en entrée. Un capteur muet n'empêche donc pas de prévoir.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({ data: [{ ...MESURE_OK, consumption_kw: null, data_quality: "critical" }] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ n: 2, base: 150 }) }),
      },
    });

    await monterAvecPrevisionVisible();

    expect(ordonnees(graphiqueGrand().data.datasets[2].data)).toEqual([150, 160]);
  });

  it("demande un horizon d'un tiers du graphe, et le passé de toute la fenêtre", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    await wrapper.find(".select-fenetre").setValue(String(6 * 60 * 60 * 1000)); // 6 h
    await flushPromises();

    const derniersParams = () => {
      const appels = api.get.mock.calls.filter(([url]) => url === "/sites/SITE001/predictions");
      return appels[appels.length - 1][1].params;
    };

    // 6 h affichées + 3 h prédites = la prévision occupe un tiers de la largeur.
    expect(derniersParams().horizon_heures).toBe(3);
    // Le passé demandé couvre toute la fenêtre, pour que la courbe prédite s'étende sur la
    // même période que les mesures et rende l'écart lisible sur toute la largeur.
    expect(derniersParams().historique_heures).toBe(6);

    // Plancher à 1 h : les modèles sont horaires, une fenêtre plus courte ne peut pas
    // demander un horizon plus fin.
    await wrapper.find(".select-fenetre").setValue(String(2 * 60 * 1000));
    await flushPromises();
    expect(derniersParams().horizon_heures).toBe(1);
  });

  it("recharge la prévision du nouveau site au changement de site", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir({ base: 150 }) }),
        "/sites/SITE002/predictions": () => Promise.resolve({ data: previsionsAVenir({ base: 900 }) }),
      },
    });

    const wrapper = await monterAvecPrevisionVisible();

    const carteSite002 = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    await carteSite002.trigger("click");
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/predictions", expect.anything());
    expect(ordonnees(graphiqueGrand().data.datasets[2].data)).toEqual([900, 910, 920]);
  });

  it("ne prédit que la puissance appelée, jamais les autres métriques", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(graphiqueGrand().data.datasets.some((d) => d.label === "Prévision")).toBe(true);

    const carteTension = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Tension"));
    await carteTension.trigger("click");
    await flushPromises();

    // La tension n'a ni prévision ni seuil : un seul dataset, celui des mesures.
    expect(graphiqueGrand().data.datasets.some((d) => d.label === "Prévision")).toBe(false);
    expect(wrapper.text()).not.toContain("intervalle 90 %");
  });

  it("ne montre aucun avertissement de prévision sur une métrique qui n'en affiche pas", async () => {
    // L'avertissement « données synthétiques » qualifie la courbe de prévision. Sur la tension,
    // qui n'en porte aucune, il désignerait une courbe absente de l'écran.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = await monterAvecPrevisionVisible();
    expect(wrapper.text()).toContain("données synthétiques");

    const carteTension = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Tension"));
    await carteTension.trigger("click");
    await flushPromises();

    expect(wrapper.text()).not.toContain("données synthétiques");
  });

  it("laisse les petites cartes en sparkline, sans prévision", async () => {
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
