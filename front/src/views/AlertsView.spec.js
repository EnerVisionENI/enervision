import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import AlertsView from "./AlertsView.vue";
import api from "../api/client";

vi.mock("../api/client", () => ({
  default: { get: vi.fn() },
}));

const ALERTES = [
  {
    alert_id: "ALR-SITE001-1788340900",
    timestamp: "2026-09-02T08:52:40.053",
    site_id: "SITE001",
    severity: "medium",
    type: "anomaly",
    message: "Comportement anormal détecté sur Bureau Paris La Défense",
    value: 120.12,
    threshold: 135.0,
  },
  {
    alert_id: "ALR-SITE002-1788340901",
    timestamp: "2026-09-02T09:10:00.000",
    site_id: "SITE002",
    severity: "critical",
    type: "threshold",
    message: "Seuil dépassé sur Usine Lyon",
    value: 310.5,
    threshold: 300.0,
  },
];

describe("AlertsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("affiche les alertes renvoyées par l'API", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    expect(api.get).toHaveBeenCalledWith("/alerts");
    expect(wrapper.findAll("tbody tr")).toHaveLength(2);
    expect(wrapper.text()).toContain("SITE001");
    expect(wrapper.text()).toContain(
      "Comportement anormal détecté sur Bureau Paris La Défense"
    );
    expect(wrapper.text()).toContain("120.12 / 135");
    expect(wrapper.find(".badge-medium").text()).toBe("Moyenne");
    expect(wrapper.find(".badge-critical").text()).toBe("Critique");
  });

  it("affiche un message d'erreur si l'appel échoue", async () => {
    api.get.mockRejectedValueOnce(new Error("boom"));

    const wrapper = mount(AlertsView);
    await flushPromises();

    expect(wrapper.find(".error").text()).toBe(
      "Impossible de charger les alertes, réessayez."
    );
    expect(wrapper.find("table").exists()).toBe(false);
  });

  it("affiche l'état de chargement pendant la requête", async () => {
    let resoudre;
    api.get.mockReturnValueOnce(new Promise((r) => (resoudre = r)));

    const wrapper = mount(AlertsView);
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Chargement des alertes...");

    resoudre({ data: ALERTES });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Chargement des alertes...");
  });

  it("filtre les alertes par sévérité", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    await wrapper.find("#severite").setValue("critical");

    const lignes = wrapper.findAll("tbody tr");
    expect(lignes).toHaveLength(1);
    expect(lignes[0].text()).toContain("SITE002");
  });

  it("filtre les alertes par site", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    await wrapper.find("#site").setValue("SITE001");

    const lignes = wrapper.findAll("tbody tr");
    expect(lignes).toHaveLength(1);
    expect(lignes[0].text()).toContain("Comportement anormal");
  });

  it("filtre les alertes par type", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    await wrapper.find("#type").setValue("threshold");

    const lignes = wrapper.findAll("tbody tr");
    expect(lignes).toHaveLength(1);
    expect(lignes[0].text()).toContain("SITE002");
  });

  it("trie les alertes par sévérité au clic sur l'en-tête", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    const enTeteSeverite = wrapper
      .findAll("th.sortable")
      .find((th) => th.text().includes("Sévérité"));
    await enTeteSeverite.trigger("click");

    let lignes = wrapper.findAll("tbody tr");
    expect(lignes[0].text()).toContain("SITE001");

    await enTeteSeverite.trigger("click");
    lignes = wrapper.findAll("tbody tr");
    expect(lignes[0].text()).toContain("SITE002");
  });

  it("trie les alertes par site au clic sur l'en-tête", async () => {
    api.get.mockResolvedValueOnce({ data: ALERTES });

    const wrapper = mount(AlertsView);
    await flushPromises();

    const enTeteSite = wrapper
      .findAll("th.sortable")
      .find((th) => th.text().includes("Site"));
    await enTeteSite.trigger("click");

    const lignes = wrapper.findAll("tbody tr");
    expect(lignes[0].text()).toContain("SITE001");
    expect(lignes[1].text()).toContain("SITE002");
  });

  it("affiche un message quand aucune alerte ne correspond", async () => {
    api.get.mockResolvedValueOnce({ data: [] });

    const wrapper = mount(AlertsView);
    await flushPromises();

    expect(wrapper.find(".empty").text()).toBe("Aucune alerte à afficher.");
  });
});
