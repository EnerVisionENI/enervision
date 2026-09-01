"""
Tests unitaires pour etl/collect.py, ticket EV-003.
Couvre uniquement la logique testable sans appel réseau,
le nommage des fichiers et l'écriture en JSONL.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect


def test_chemin_fichier_du_jour_cree_un_dossier_par_site(tmp_path):
    """Le chemin doit contenir un sous-dossier au nom du site."""
    chemin = collect.chemin_fichier_du_jour("SITE001", dossier=str(tmp_path))
    assert "SITE001" in chemin
    assert chemin.endswith(".jsonl")
    assert os.path.isdir(os.path.join(str(tmp_path), "SITE001"))


def test_chemin_fichier_du_jour_meme_site_meme_fichier(tmp_path):
    """Deux appels le même jour pour le même site doivent donner le même chemin."""
    chemin_1 = collect.chemin_fichier_du_jour("SITE002", dossier=str(tmp_path))
    chemin_2 = collect.chemin_fichier_du_jour("SITE002", dossier=str(tmp_path))
    assert chemin_1 == chemin_2


def test_ajouter_mesure_ecrit_une_ligne_json_valide(tmp_path, monkeypatch):
    """Une mesure ajoutée doit être une ligne JSON valide et lisible."""
    monkeypatch.setattr(collect, "DOSSIER_BRONZE", str(tmp_path))
    mesure = {"site_id": "SITE003", "consumption_kw": 42.0, "data_quality": "good"}
    collect.ajouter_mesure("SITE003", mesure)

    chemin = collect.chemin_fichier_du_jour("SITE003", dossier=str(tmp_path))
    with open(chemin, encoding="utf-8") as f:
        ligne = f.readline()
    donnee_relue = json.loads(ligne)
    assert donnee_relue == mesure


def test_ajouter_mesure_conserve_les_valeurs_nulles(tmp_path, monkeypatch):
    """Une valeur nulle dans une mesure ne doit jamais être filtrée à l'écriture."""
    monkeypatch.setattr(collect, "DOSSIER_BRONZE", str(tmp_path))
    mesure_degradee = {
        "site_id": "SITE004",
        "consumption_kw": None,
        "null_reasons": ["consumption_sensor_failure"],
        "data_quality": "partial",
    }
    collect.ajouter_mesure("SITE004", mesure_degradee)

    chemin = collect.chemin_fichier_du_jour("SITE004", dossier=str(tmp_path))
    with open(chemin, encoding="utf-8") as f:
        donnee_relue = json.loads(f.readline())
    assert donnee_relue["consumption_kw"] is None
    assert donnee_relue["data_quality"] == "partial"


def test_ajouter_mesure_ajoute_a_la_suite(tmp_path, monkeypatch):
    """Deux mesures successives doivent s'ajouter, pas se remplacer."""
    monkeypatch.setattr(collect, "DOSSIER_BRONZE", str(tmp_path))
    collect.ajouter_mesure("SITE005", {"consumption_kw": 10})
    collect.ajouter_mesure("SITE005", {"consumption_kw": 20})

    chemin = collect.chemin_fichier_du_jour("SITE005", dossier=str(tmp_path))
    with open(chemin, encoding="utf-8") as f:
        lignes = f.readlines()
    assert len(lignes) == 2