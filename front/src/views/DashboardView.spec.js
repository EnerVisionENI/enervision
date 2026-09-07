import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { config, mount, flushPromises } from "@vue/test-utils";
import { Interaction } from "chart.js/auto";
import DashboardView from "./DashboardView.vue";
import api from "../api/client";

// Le panneau d'alertes contient un <router-link> vers /alertes, mais la vue est
// montée seule, sans routeur. Le stub garde le lien inspectable (href, texte).
config.global.stubs = {
  RouterLink: {
    props: ["to"],
    template: '<a :href="to"><slot /></a>',
  },
};

vi.mock("../api/client", () => ({
  default: { get: vi.fn() },
}));

const chartInstances = [];
vi.mock("chart.js/auto", () => {
  class FakeChart {
    constructor(_ctx, config) {
      this.data = config.data;
      this.options = config.options;
      this.plugins = config.plugins || [];
      this.destroyed = false;
      chartInstances.push(this);
    }
    update() {}
    destroy() {
      this.destroyed = true;
    }
  }
  // `Interaction.modes` est le registre des modes de survol de Chart.js : la vue y inscrit le
  // sien au chargement du module, les tests le relisent par l'import ci-dessus pour l'appeler
  // directement, sans canvas ni souris.
  return { Chart: FakeChart, Interaction: { modes: {} } };
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

// Alertes telles que les sert GET /alerts : déjà triées du plus récent au plus
// ancien, limitées côté API. Horodatages du jour pour que le panneau n'affiche
// que l'heure (le jour n'apparaît qu'au-delà de la journée en cours).
function alerteA(heure, reste = {}) {
  const date = new Date();
  date.setHours(heure, 0, 0, 0);
  return {
    alert_id: `A-${heure}`,
    timestamp: date.toISOString(),
    site_id: "SITE001",
    severity: "medium",
    type: "consumption_spike",
    message: `pic de consommation à ${heure}h`,
    value: 180,
    threshold: 150,
    ...reste,
  };
}

function recommandation({ niveau = "depassement_prevu", heures = 2, ...reste } = {}) {
  const debut = new Date(Date.now() + 3_600_000);
  return {
    niveau,
    debut: debut.toISOString(),
    fin: new Date(debut.getTime() + heures * 3_600_000).toISOString(),
    heures_concernees: heures,
    capacity_kw: 800,
    pic_kwh: 847,
    depassement_max_kw: 47,
    message: "Dépassement prévu pendant 2 h : jusqu'à 47 kW au-dessus des 800 kW souscrits.",
    ...reste,
  };
}

function mockApi({ sites = SITES, lectures = {}, previsions = {}, recommandations = {}, alertes = null } = {}) {
  api.get.mockImplementation((url) => {
    if (url === "/sites") return Promise.resolve({ data: sites });
    if (lectures[url]) return lectures[url]();
    // Défaut à liste vide : la plupart des tests ne portent pas sur le panneau
    // d'alertes et n'ont pas à le basculer en erreur faute de mock.
    if (url === "/alerts") return alertes ? alertes() : Promise.resolve({ data: [] });
    // Défaut à liste vide : la plupart des tests ne portent ni sur la prévision ni sur les
    // recommandations, et ne doivent pas basculer l'écran en « service injoignable » faute
    // de mock.
    if (url.endsWith("/predictions")) {
      return previsions[url] ? previsions[url]() : Promise.resolve({ data: [] });
    }
    if (url.endsWith("/recommendations")) {
      return recommandations[url] ? recommandations[url]() : Promise.resolve({ data: [] });
    }
    return Promise.reject(new Error(`URL non mockée: ${url}`));
  });
}

const MESURES_SIMPLES = {
  "/sites/SITE001/measurements": () => Promise.resolve({ data: [] }),
  "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
};

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
    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 2, pas_minutes: 1 } });
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

    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Puissance appelée (kW)");
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

    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Tension (V)");
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

    await choisirPeriode(wrapper, "1 h");
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 60, pas_minutes: 1 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 60, pas_minutes: 1 } });
  });

  // La barre de période : cinq pilules pour les durées les plus demandées, et un formulaire
  // pour toutes les autres. Les helpers ci-dessous en font le tour comme le ferait la souris.
  function pilulePeriode(wrapper, libelle) {
    return wrapper.findAll(".pilule-periode").find((p) => p.text().startsWith(libelle));
  }

  async function choisirPeriode(wrapper, libelle) {
    await pilulePeriode(wrapper, libelle).trigger("click");
    await flushPromises();
  }

  async function ouvrirSaisiePeriode(wrapper) {
    await pilulePeriode(wrapper, "personnalisée").trigger("click");
    await flushPromises();
  }

  async function saisirPeriode(wrapper, valeur, unite = null) {
    if (!wrapper.find(".saisie-periode").exists()) await ouvrirSaisiePeriode(wrapper);
    await wrapper.find(".saisie-periode-duree").setValue(String(valeur));
    if (unite) await wrapper.find(".saisie-periode-unite").setValue(unite);
    // La période s'applique à la validation du formulaire (Entrée ou « appliquer ») et pas à
    // la frappe : sinon « 45 » passerait par une fenêtre de 4 min, tous graphiques rechargés.
    await wrapper.find(".saisie-periode").trigger("submit");
    await flushPromises();
  }

  async function monterAvecMesures() {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });
    const wrapper = mount(DashboardView);
    await flushPromises();
    return wrapper;
  }

  it("recharge les mesures sur la période saisie, hors préréglages", async () => {
    const wrapper = await monterAvecMesures();

    await saisirPeriode(wrapper, 45);

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 45, pas_minutes: 1 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 45, pas_minutes: 1 } });
    // Aucun préréglage ne vaut 45 min : plus aucune pilule de durée ne peut rester allumée,
    // sinon deux périodes différentes seraient lisibles pour la seule courbe tracée.
    expect(pilulePeriode(wrapper, "2 min").classes()).not.toContain("actif");
    expect(pilulePeriode(wrapper, "personnalisée").classes()).toContain("actif");
    // Et le résumé dit la période réellement affichée, elle qu'aucune pilule ne nomme plus.
    expect(wrapper.find(".resume-periode").text()).toContain("45 min");
  });

  it("compte la période dans l'unité choisie à côté du champ", async () => {
    const wrapper = await monterAvecMesures();

    await saisirPeriode(wrapper, 3, "h");

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 180, pas_minutes: 1 } });
    expect(wrapper.find(".resume-periode").text()).toContain("3 h");
  });

  it("n'applique rien avant la validation du formulaire", async () => {
    const wrapper = await monterAvecMesures();
    await ouvrirSaisiePeriode(wrapper);
    const appelsAvant = api.get.mock.calls.length;

    // Frappe et changement d'unité, sans valider : la période affichée ne bouge pas.
    await wrapper.find(".saisie-periode-duree").setValue("45");
    await wrapper.find(".saisie-periode-unite").setValue("h");
    await flushPromises();

    expect(api.get.mock.calls).toHaveLength(appelsAvant);
    expect(wrapper.find(".resume-periode").text()).toContain("2 min");
  });

  it("dépasse les 24 h par le formulaire, en éclaircissant la réponse", async () => {
    const wrapper = await monterAvecMesures();

    // C'est tout l'intérêt du formulaire : les préréglages s'arrêtent à la semaine, mais
    // aucune période intermédiaire n'était atteignable avant lui.
    await saisirPeriode(wrapper, 3, "j");

    // 3 jours à la minute feraient 4320 lectures par site, rechargées pour les sept sites à
    // chaque cycle : l'API n'en renvoie qu'une toutes les 3 minutes.
    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", {
      params: { depuis_minutes: 3 * 24 * 60, pas_minutes: 3 },
    });
    // Et le résumé le dit : la courbe ne porte plus une lecture par minute.
    expect(wrapper.find(".resume-periode").text()).toContain("3 j");
    expect(wrapper.find(".resume-periode").text()).toContain("1 point / 3 min");
  });

  it("ramène une période hors bornes dans celles de l'API, et le dit", async () => {
    const wrapper = await monterAvecMesures();

    // 30 jours dépassent `depuis_minutes` (le=10080) : envoyés tels quels, l'API répondrait
    // 422 et les sept graphiques se videraient sans que rien ne l'explique.
    await saisirPeriode(wrapper, 30, "j");

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", {
      params: { depuis_minutes: 7 * 24 * 60, pas_minutes: 7 },
    });
    expect(wrapper.find(".message-periode").text()).toContain("7 j");
    // Le champ est réaligné sur la période retenue, dans l'unité où elle se lit.
    expect(wrapper.find(".saisie-periode-duree").element.value).toBe("7");
    expect(wrapper.find(".saisie-periode-unite").element.value).toBe("j");
  });

  it("refuse une durée illisible sans toucher à la période affichée", async () => {
    const wrapper = await monterAvecMesures();
    const appelsAvant = api.get.mock.calls.length;

    await saisirPeriode(wrapper, "");

    expect(api.get.mock.calls).toHaveLength(appelsAvant);
    expect(wrapper.find(".resume-periode").text()).toContain("2 min");
    expect(wrapper.find(".message-periode").classes()).toContain("invalide");
    // Le champ est signalé aux lecteurs d'écran, pas seulement encadré de rouge.
    expect(wrapper.find(".saisie-periode-duree").attributes("aria-invalid")).toBe("true");
  });

  it("ouvre le formulaire sur la période déjà affichée", async () => {
    const wrapper = await monterAvecMesures();

    await choisirPeriode(wrapper, "6 h");
    await ouvrirSaisiePeriode(wrapper);

    // Le formulaire est le point de départ de tout ajustement : il part de la période tracée,
    // et non d'un chiffre resté d'une saisie précédente.
    expect(wrapper.find(".saisie-periode-duree").element.value).toBe("6");
    expect(wrapper.find(".saisie-periode-unite").element.value).toBe("h");
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

    expect(api.get).toHaveBeenCalledWith("/sites/SITE001/measurements", { params: { depuis_minutes: 2, pas_minutes: 1 } });
    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/measurements", { params: { depuis_minutes: 2, pas_minutes: 1 } });
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
    await choisirPeriode(wrapper, "1 h");
    await flushPromises();
    return wrapper;
  }

  // Les greffons dessinent au canvas : on leur passe un contexte qui note les
  // ordres reçus plutôt que de peindre, seul moyen de vérifier OÙ ils tracent.
  function ctxEspion() {
    return {
      appels: [],
      save() {},
      restore() {},
      beginPath() {},
      setLineDash() {},
      lineTo() {},
      measureText: () => ({ width: 60 }),
      fillRect(...a) {
        this.appels.push(["fillRect", ...a]);
      },
      moveTo(...a) {
        this.appels.push(["moveTo", ...a]);
      },
      stroke() {
        this.appels.push(["stroke"]);
      },
      fillText(...a) {
        this.appels.push(["fillText", ...a]);
      },
    };
  }

  // Cadre de 100x50 px, la dernière mesure retombant sur l'abscisse demandée.
  function chartFictif(ctx, x) {
    return {
      ctx,
      chartArea: { left: 0, right: 100, top: 0, bottom: 50 },
      scales: { x: { getPixelForValue: () => x } },
    };
  }

  function greffon(id) {
    return graphiqueGrand().plugins.find((g) => g.id === id);
  }

  function petitsGraphiques() {
    return chartInstances.filter((c) => !c.destroyed && c.options.scales.x.display === false);
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
    await choisirPeriode(wrapper, "1 h");
    await flushPromises();

    expect(graphiqueGrand().data.datasets[2].data.length).toBeGreaterThan(0);
    expect(wrapper.text()).not.toContain("Prévision masquée sur cette fenêtre");
  });

  it("n'apparie jamais les séries par index, seulement par position temporelle", async () => {
    // Régression vécue : en mode "index", Chart.js lit le MÊME numéro d'index dans toutes les
    // séries. Les mesures en portent ~360 (à la minute) et les prévisions ~9 (à l'heure), si
    // bien que survoler la mesure d'index 5 affichait la prévision d'index 5 — une tout autre
    // heure. L'infobulle inventait des prévisions à la minute que la base ne contient pas.
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
    const grand = graphiqueGrand();

    expect(grand.options.interaction.mode).not.toBe("index");
    expect(grand.options.plugins.tooltip.mode).not.toBe("index");
    // Mode maison, apparieur par horodatage : l'infobulle et le survol lisent le même.
    expect(grand.options.plugins.tooltip.mode).toBe(grand.options.interaction.mode);
    expect(Interaction.modes[grand.options.interaction.mode]).toBeTypeOf("function");

    // Le nombre de points diffère entre les séries : c'est précisément ce que le mode
    // "index" ne sait pas gérer, et ce qui doit rester vrai (on ne comble pas les minutes
    // sans prévision pour faire coïncider les longueurs).
    const [mesures, , prediction] = grand.data.datasets.map((d) => d.data);
    expect(mesures.length).not.toBe(prediction.length);
  });

  it("survole une série par le curseur sans avoir à viser le point, chacune à son pas", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();
    const mode = Interaction.modes[graphiqueGrand().options.interaction.mode];

    // Trois séries au même graphique, à trois pas différents : mesures tous les 10 px
    // (la minute), prévision tous les 300 px (l'heure), seuil réduit aux deux bords de
    // l'axe. Les abscisses sont des pixels, comme celles que Chart.js pose sur les points.
    const chart = {
      data: { datasets: [{}, { constante: true }, {}, { decoratif: true }] },
      getSortedVisibleDatasetMetas: () => [
        { index: 0, data: [{ x: 0 }, { x: 10 }, { x: 20 }, { x: 30 }, { x: 40 }] },
        { index: 1, data: [{ x: 0 }, { x: 600 }] },
        { index: 2, data: [{ x: 0 }, { x: 300 }, { x: 600 }] },
        { index: 3, data: [{ x: 0 }, { x: 300 }, { x: 600 }] },
      ],
    };
    const survol = (x) =>
      mode(chart, { native: {}, x, y: 40 }).map(({ datasetIndex, index }) => [datasetIndex, index]);

    // Curseur à 23 px, donc entre deux mesures et à 23 px de la première prévision : les
    // deux séries répondent quand même, avec leur point le plus proche. C'est tout l'intérêt
    // du mode — il n'y a plus à poser la souris sur le point.
    expect(survol(23)).toEqual([
      [0, 2],
      [1, 0],
      [2, 0],
    ]);

    // Passé la dernière mesure (40 px), la partie prédite n'a plus de mesure à montrer : la
    // série des mesures se retire au lieu de reporter sa dernière valeur à une heure où elle
    // n'existe pas. La prévision, elle, couvre l'axe par tranches de 150 px de part et
    // d'autre de chaque point horaire.
    expect(survol(200)).toEqual([
      [1, 0],
      [2, 0],
    ]);
    expect(survol(320)).toEqual([
      [1, 1],
      [2, 1],
    ]);

    // Le dataset 3 (borne de l'incertitude) n'apparaît dans aucun relevé : il ne sert qu'à
    // remplir la bande, et l'activer ferait apparaître un point parasite sur sa lisière.
    expect(survol(23).map(([datasetIndex]) => datasetIndex)).not.toContain(3);
  });

  it("lit la mesure, le seuil et la prévision dans une seule infobulle", async () => {
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
    const { filter, callbacks } = graphiqueGrand().options.plugins.tooltip;

    const instantMesure = new Date("2026-09-07T09:23:00").getTime();
    const instantPrevision = new Date("2026-09-07T09:00:00").getTime();
    const items = [
      { label: "07/09 09:23", parsed: { x: instantMesure, y: 182.4 }, dataset: { label: "Puissance appelée" } },
      { label: "07/09 06:00", parsed: { x: instantMesure - 3 * 3600_000, y: 200 }, dataset: { label: "Puissance souscrite (kW)", constante: true } },
      { label: "07/09 09:00", parsed: { x: instantPrevision, y: 178.2 }, dataset: { label: "Prévision" } },
    ];

    // Chart.js construit le titre avant le corps : l'ordre des appels ci-dessous est celui de
    // l'infobulle réelle, dont les lignes se comparent à l'instant du titre.
    expect(callbacks.title(items)).toBe("07/09 09:23");
    expect(items.map(callbacks.label)).toEqual([
      "Puissance appelée: 182 kW",
      // Le seuil vaut autant à toute heure : l'horodatage de ses deux points, posés aux bords
      // de l'axe, ne doit jamais s'afficher ni servir de titre.
      "Puissance souscrite (kW): 200 kW",
      // Le modèle est horaire : la ligne rappelle l'heure qu'elle prédit vraiment, plutôt que
      // de se laisser lire comme une prévision à 09:23.
      `Prévision (${new Date(instantPrevision).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}): 178 kW`,
    ]);

    expect(filter({ dataset: { decoratif: true } })).toBe(false);
    expect(filter(items[2])).toBe(true);
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
    await choisirPeriode(wrapper, "1 h");
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
    await choisirPeriode(wrapper, "6 h");
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
    await choisirPeriode(wrapper, "2 min");
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

  it("affiche la recommandation de dépassement servie par l'API", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      recommandations: {
        "/sites/SITE001/recommendations": () => Promise.resolve({ data: [recommandation()] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.text()).toContain("Dépassement prévu pendant 2 h");
    expect(wrapper.text()).toContain("+47 kW");
    // Le principe posé par la maquette d'origine, à ne pas perdre : on suggère, on ne pilote pas.
    expect(wrapper.text()).toContain("aucune commande automatique");
    expect(wrapper.find(".recommandation").classes()).toContain("critique");
  });

  it("distingue un risque d'un dépassement prévu, y compris à la couleur", async () => {
    // Les deux n'appellent pas la même réaction : agir dans un cas, surveiller dans l'autre.
    // Les présenter pareil décrédibiliserait les vraies alertes.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      recommandations: {
        "/sites/SITE001/recommendations": () =>
          Promise.resolve({ data: [recommandation({ niveau: "risque_depassement" })] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const panneau = wrapper.find(".recommandation");
    expect(panneau.classes()).toContain("avertissement");
    expect(panneau.classes()).not.toContain("critique");
  });

  it("annonce explicitement l'absence de dépassement prévu", async () => {
    // Un panneau vide se lit comme une panne. « Rien à signaler » est une information.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.text()).toContain("Aucun dépassement prévu");
    expect(wrapper.find(".recommandation").classes()).not.toContain("critique");
  });

  it("ne présente pas une absence de prévision comme un feu vert", async () => {
    // SITE002/004/007 n'ont aucun modèle inférable : ne rien pouvoir dire n'est pas la même
    // chose que n'avoir rien à signaler.
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.text()).toContain("Aucun modèle de prévision pour ce site");
    expect(wrapper.text()).not.toContain("Aucun dépassement prévu");
  });

  it("distingue un service de recommandation en panne d'une absence de dépassement", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      previsions: {
        "/sites/SITE001/predictions": () => Promise.resolve({ data: previsionsAVenir() }),
      },
      recommandations: {
        "/sites/SITE001/recommendations": () => Promise.reject(new Error("503")),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.text()).toContain("Recommandations indisponibles");
    expect(wrapper.text()).not.toContain("Aucun dépassement prévu");
  });

  it("recharge les recommandations du nouveau site au changement de site", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
      },
      recommandations: {
        "/sites/SITE001/recommendations": () => Promise.resolve({ data: [] }),
        "/sites/SITE002/recommendations": () => Promise.resolve({ data: [recommandation()] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    const carte = wrapper.findAll(".parc-carte").find((c) => c.text().includes("SITE002"));
    await carte.trigger("click");
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/sites/SITE002/recommendations", expect.anything());
    expect(wrapper.text()).toContain("Dépassement prévu pendant 2 h");
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

  it("affiche les dernières alertes du parc telles que l'API les sert", async () => {
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () =>
        Promise.resolve({
          data: [
            alerteA(14, { site_id: "SITE002", message: "tension hors plage" }),
            alerteA(9),
            alerteA(8),
          ],
        }),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    // La limite est demandée à l'API, pas appliquée après coup : le panneau ne
    // doit pas rapatrier les 100 alertes par défaut pour n'en montrer que 5.
    expect(api.get).toHaveBeenCalledWith("/alerts", { params: { limit: 5 } });
    expect(wrapper.text()).toContain("alertes — 5 dernières");

    const items = wrapper.findAll(".alerte-item");
    expect(items).toHaveLength(3);
    // Ordre de l'API conservé (plus récente en tête), aucun retri côté front.
    expect(items[0].find(".alerte-heure").text()).toBe("14:00");
    expect(items[0].find(".alerte-site").text()).toBe("SITE002");
    expect(items[0].find(".alerte-texte").text()).toContain("tension hors plage");
    expect(items[2].find(".alerte-heure").text()).toBe("08:00");
  });

  it("n'affiche jamais plus d'alertes que ce que l'API en renvoie", async () => {
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () => Promise.resolve({ data: [alerteA(10), alerteA(9)] }),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.findAll(".alerte-item")).toHaveLength(2);
    expect(wrapper.find(".alertes-etat").exists()).toBe(false);
  });

  it("distingue les sévérités et ne met en rouge que celles qui appellent une action", async () => {
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () =>
        Promise.resolve({
          data: [
            alerteA(12, { alert_id: "A-crit", severity: "critical" }),
            alerteA(11, { alert_id: "A-high", severity: "high" }),
            alerteA(10, { alert_id: "A-med", severity: "medium" }),
            alerteA(9, { alert_id: "A-low", severity: "low" }),
            alerteA(8, { alert_id: "A-nulle", severity: null }),
          ],
        }),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const items = wrapper.findAll(".alerte-item");
    expect(items.map((i) => i.classes().includes("critique"))).toEqual([true, true, false, false, false]);
    expect(items[0].classes()).toContain("severite-critical");
    expect(items[3].classes()).toContain("severite-low");
    // Une sévérité absente ne doit pas produire de classe "severite-null" muette.
    expect(items[4].classes()).toContain("severite-inconnue");
    expect(items[0].find(".alerte-point").attributes("title")).toBe("sévérité critique");
    expect(items[4].find(".alerte-point").attributes("title")).toBe("sévérité inconnue");
  });

  it("ajoute le jour aux alertes qui ne sont pas de la journée en cours", async () => {
    const avantHier = new Date();
    avantHier.setDate(avantHier.getDate() - 2);
    avantHier.setHours(8, 12, 0, 0);

    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () =>
        Promise.resolve({
          data: [alerteA(14), { ...alerteA(8), alert_id: "A-ancienne", timestamp: avantHier.toISOString() }],
        }),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const heures = wrapper.findAll(".alerte-heure").map((h) => h.text());
    const jourMois = avantHier.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
    // Sans le jour, une alerte d'avant-hier passerait pour une alerte de ce matin.
    expect(heures[0]).toBe("14:00");
    expect(heures[1]).toBe(`${jourMois} 08:12`);
  });

  it("affiche le texte d'une alerte sans message plutôt qu'une ligne vide", async () => {
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () =>
        Promise.resolve({
          data: [alerteA(10, { message: null, type: "sensor_failure", site_id: null })],
        }),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const item = wrapper.find(".alerte-item");
    expect(item.find(".alerte-texte").text()).toContain("sensor_failure");
    // Une alerte sans site porte sur le parc, ce n'est pas une donnée manquante.
    expect(item.find(".alerte-site").text()).toBe("parc");
  });

  it("dit qu'il n'y a aucune alerte plutôt que d'afficher un panneau vide", async () => {
    mockApi({ lectures: MESURES_SIMPLES, alertes: () => Promise.resolve({ data: [] }) });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.findAll(".alerte-item")).toHaveLength(0);
    expect(wrapper.find(".alertes-etat").text()).toBe("Aucune alerte sur le parc.");
    expect(wrapper.find(".alertes-erreur").exists()).toBe(false);
  });

  it("signale un échec de chargement des alertes sans faire échouer le reste de l'écran", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
      alertes: () => Promise.reject(new Error("boom")),
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".alertes-erreur").text()).toBe(
      "Alertes non actualisées, nouvelle tentative au prochain cycle."
    );
    // Le reste de l'écran est intact : les alertes sont un panneau, pas la page.
    expect(wrapper.find(".lecture-actuelle").text()).toContain("180");
  });

  it("garde les alertes affichées quand un cycle de sondage échoue", async () => {
    let cycle = 0;
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () => {
        cycle += 1;
        return cycle === 1 ? Promise.resolve({ data: [alerteA(9)] }) : Promise.reject(new Error("boom"));
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(wrapper.findAll(".alerte-item")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(30_000);

    // Vider le panneau sur un échec ponctuel perdrait une information valide :
    // la liste reste, l'échec est signalé à côté.
    expect(wrapper.findAll(".alerte-item")).toHaveLength(1);
    expect(wrapper.find(".alertes-erreur").exists()).toBe(true);
  });

  it("recharge les alertes à chaque cycle de sondage", async () => {
    let cycle = 0;
    mockApi({
      lectures: MESURES_SIMPLES,
      alertes: () => {
        cycle += 1;
        return Promise.resolve({ data: cycle === 1 ? [alerteA(9)] : [alerteA(10), alerteA(9)] });
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();
    expect(wrapper.findAll(".alerte-item")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(30_000);

    expect(wrapper.findAll(".alerte-item")).toHaveLength(2);
    expect(wrapper.find(".alertes-erreur").exists()).toBe(false);
  });

  it("ne redemande pas les alertes au changement de fenêtre, elles n'en dépendent pas", async () => {
    mockApi({ lectures: MESURES_SIMPLES, alertes: () => Promise.resolve({ data: [alerteA(9)] }) });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(api.get.mock.calls.filter(([url]) => url === "/alerts")).toHaveLength(1);

    await choisirPeriode(wrapper, "1 h");
    await flushPromises();

    expect(api.get.mock.calls.filter(([url]) => url === "/alerts")).toHaveLength(1);
  });

  it("renvoie vers la liste complète des alertes", async () => {
    mockApi({ lectures: MESURES_SIMPLES, alertes: () => Promise.resolve({ data: [alerteA(9)] }) });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const lien = wrapper.find(".lien-alertes");
    expect(lien.text()).toBe("tout voir");
    expect(lien.attributes("href")).toBe("/alertes");
  });

  it("relie les points par des segments droits, sans lissage qui inventerait un sommet", async () => {
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

    // Une tension non nulle dessine des Béziers qui dépassent les valeurs mesurées :
    // le sommet tracé passerait au-dessus de la mesure la plus haute, sur un écran
    // dont la question est justement « le pic a-t-il dépassé la puissance souscrite ? ».
    expect(graphiqueGrand().data.datasets.some((d) => d.tension)).toBe(false);
    expect(petitsGraphiques().some((c) => c.data.datasets.some((d) => d.tension))).toBe(false);
  });

  it("ne pose un marqueur que sur les points qu'aucun segment ne relie", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    const rayon = graphiqueGrand().data.datasets[0].pointRadius;
    // Un rayon fixe donnait un chapelet de 1440 marqueurs sur la fenêtre "1 jour".
    expect(typeof rayon).toBe("function");

    const data = [10, null, 20, null, null, 30, 40];
    const r = (dataIndex) => rayon({ dataset: { data }, dataIndex });
    // Seuls les points qu'aucun voisin ne prolonge : sans marqueur ils ne dessinent
    // aucun segment et disparaîtraient complètement du graphique.
    expect(r(0)).toBeGreaterThan(0);
    expect(r(2)).toBeGreaterThan(0);
    // Trous : rien à marquer.
    expect(r(1)).toBe(0);
    // Points reliés à un voisin : le trait suffit à les montrer.
    expect(r(5)).toBe(0);
    expect(r(6)).toBe(0);
  });

  it("donne à chaque métrique sa propre couleur", async () => {
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

    const [mesure, seuil, prevision] = graphiqueGrand().data.datasets;
    expect(mesure.borderColor).toBe("#2dd4bf");
    expect(seuil.borderColor).toBe("#f59e0b");
    expect(prevision.borderColor).toBe("#a78bfa");

    // Sept teintes distinctes, une par carte.
    const couleurs = petitsGraphiques().map((c) => c.data.datasets[0].borderColor);
    expect(couleurs).toHaveLength(7);
    expect(new Set(couleurs).size).toBe(7);
  });

  it("fait suivre le trait de la légende à la couleur de la métrique affichée", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    // Le trait était teal en dur : sur "Tension" la courbe est bleue et la légende
    // annonçait pourtant du teal pour "mesure réelle".
    const trait = () => wrapper.find(".legende-item .legende-trait");
    expect(trait().attributes("style")).toContain("rgb(45, 212, 191)");

    const carteTension = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Tension"));
    await carteTension.trigger("click");
    await flushPromises();

    expect(trait().attributes("style")).toContain("rgb(96, 165, 250)");
  });

  it("marque sur l'axe la frontière entre ce qui est mesuré et ce qui est prédit", async () => {
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

    const ctx = ctxEspion();
    greffon("separateurPrevision").beforeDatasetsDraw(chartFictif(ctx, 60));

    // Teinte à droite du trait seulement : à gauche tout est mesuré, et sans repère
    // la seule marque du passage au prédit était le pointillé de la courbe.
    expect(ctx.appels).toContainEqual(["fillRect", 60, 0, 40, 50]);
    expect(ctx.appels).toContainEqual(["moveTo", 60, 0]);
    expect(ctx.appels.some(([nom, texte]) => nom === "fillText" && texte === "maintenant")).toBe(true);
  });

  it("ne trace aucune frontière quand il n'y a pas de prévision à séparer", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    await monterAvecPrevisionVisible();

    const ctx = ctxEspion();
    greffon("separateurPrevision").beforeDatasetsDraw(chartFictif(ctx, 60));

    // Site sans modèle : la courbe s'arrête au dernier point mesuré, il n'y a pas
    // de futur à distinguer et un trait "maintenant" au bord droit n'apprendrait rien.
    expect(ctx.appels).toEqual([]);
  });

  it("ne trace pas la frontière hors du cadre quand la dernière mesure précède la fenêtre", async () => {
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

    const avant = ctxEspion();
    greffon("separateurPrevision").beforeDatasetsDraw(chartFictif(avant, -20));
    expect(avant.appels).toEqual([]);

    const apres = ctxEspion();
    greffon("separateurPrevision").beforeDatasetsDraw(chartFictif(apres, 140));
    expect(apres.appels).toEqual([]);
  });

  it("trace un croisillon vertical sur le point survolé, et rien sans survol", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: mesuresSurUneHeure() }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    const cadre = { chartArea: { left: 0, right: 100, top: 0, bottom: 50 } };

    const survole = ctxEspion();
    greffon("croisillon").afterDatasetsDraw({
      ...cadre,
      ctx: survole,
      tooltip: { getActiveElements: () => [{ element: { x: 42 } }] },
    });
    expect(survole.appels).toContainEqual(["moveTo", 42, 0]);

    const repos = ctxEspion();
    greffon("croisillon").afterDatasetsDraw({
      ...cadre,
      ctx: repos,
      tooltip: { getActiveElements: () => [] },
    });
    expect(repos.appels).toEqual([]);
  });

  it("garde les graduations discrètes : des horizontales pour lire, pas de quadrillage", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    mount(DashboardView);
    await flushPromises();

    const scales = graphiqueGrand().options.scales;
    // Les verticales n'aident à lire aucune valeur sur une série à la minute.
    expect(scales.x.grid.display).toBe(false);
    // Les horizontales, si : elles reportent un point sur l'axe des ordonnées.
    expect(scales.y.grid.display).not.toBe(false);
  });

  it("porte l'unité dans le titre, sauf quand ce n'est pas une unité", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Puissance appelée (kW)");

    const carteQualite = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Score qualité"));
    await carteQualite.trigger("click");
    await flushPromises();

    // "/100" est une échelle, pas une unité : « Score qualité (/100) » se lirait mal.
    expect(wrapper.find(".graphique-grand-titre").text()).toBe("Score qualité");
  });

  it("affiche la dernière valeur de chaque métrique sur sa carte, sans avoir à cliquer", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () => Promise.resolve({ data: [MESURE_OK] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const valeurs = wrapper.findAll(".graphique-carte-valeur").map((v) => v.text());
    expect(valeurs).toEqual(["180 kW", "405 V", "270 A", "0.93", "9.5 °C", "41 %", "100 /100"]);
  });

  it("écrit un tiret sur la carte d'une métrique sans valeur, pas un zéro", async () => {
    mockApi({
      lectures: {
        "/sites/SITE001/measurements": () =>
          Promise.resolve({ data: [{ ...MESURE_OK, humidity_percent: null }] }),
        "/sites/SITE002/measurements": () => Promise.resolve({ data: [] }),
      },
    });

    const wrapper = mount(DashboardView);
    await flushPromises();

    const carte = wrapper.findAll(".graphique-carte").find((c) => c.text().includes("Humidité"));
    // Un capteur en panne n'est pas une humidité de 0 %.
    expect(carte.find(".graphique-carte-valeur").text()).toBe("—");
  });
});
