"""Tests de l'algorithme seul (api/recommendations.py) : aucune base, aucun HTTP.
Les règles métier se vérifient ici ; le router n'est testé que pour son câblage."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from api.recommendations import NIVEAU_DEPASSEMENT, NIVEAU_RISQUE, analyser_depassements


@dataclass
class LignePrevision:
    target_ts: datetime
    predicted_kwh: float
    upper_90: float | None = None
    step_minutes: int = 60


BASE = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def serie(valeurs, bornes=None, depart=BASE, pas_h=1):
    """Une prévision par heure à partir de `depart`."""
    bornes = bornes or [None] * len(valeurs)
    return [
        LignePrevision(depart + timedelta(hours=i * pas_h), v, b)
        for i, (v, b) in enumerate(zip(valeurs, bornes, strict=True))
    ]


def test_rien_a_signaler_sous_le_seuil():
    assert analyser_depassements(serie([700, 710], [750, 760]), 800.0) == []


def test_sans_puissance_souscrite_aucune_recommandation():
    """capacity_kw est nullable en base : sans seuil contractuel, il n'y a rien à dépasser."""
    assert analyser_depassements(serie([9999]), None) == []
    assert analyser_depassements(serie([9999]), 0.0) == []


def test_sans_prevision_aucune_recommandation():
    assert analyser_depassements([], 800.0) == []


def test_depassement_prevu_quand_la_valeur_centrale_franchit_le_seuil():
    reco = analyser_depassements(serie([850], [900]), 800.0)

    assert len(reco) == 1
    assert reco[0].niveau == NIVEAU_DEPASSEMENT
    assert reco[0].depassement_max_kw == 50.0
    assert reco[0].heures_concernees == 1
    assert "Dépassement prévu" in reco[0].message


def test_risque_quand_seule_la_borne_haute_franchit_le_seuil():
    """La valeur centrale tient sous le seuil, l'intervalle à 90 % non : ce n'est pas un
    dépassement attendu, et l'annoncer comme tel décrédibiliserait les vraies alertes."""
    reco = analyser_depassements(serie([780], [860]), 800.0)

    assert len(reco) == 1
    assert reco[0].niveau == NIVEAU_RISQUE
    assert reco[0].depassement_max_kw == 60.0  # mesuré sur la borne haute, pas sur le centre
    assert "Risque de dépassement" in reco[0].message


def test_borne_haute_absente_ne_declenche_pas_de_risque():
    """upper_90 est NULL quand le run MLflow ne porte pas de marge conforme. Absence
    d'intervalle n'est pas absence de risque, mais on ne peut rien en conclure."""
    assert analyser_depassements(serie([780], [None]), 800.0) == []


def test_heures_consecutives_regroupees_en_une_seule_recommandation():
    """Sept lignes « dépassement à 14h, à 15h… » ne se lisent pas ; une fenêtre se décide."""
    reco = analyser_depassements(serie([850, 870, 860], [900, 920, 910]), 800.0)

    assert len(reco) == 1
    assert reco[0].heures_concernees == 3
    assert reco[0].debut == BASE
    # Borne exclusive : fin du dernier créneau concerné, pas son début.
    assert reco[0].fin == BASE + timedelta(hours=3)
    assert reco[0].depassement_max_kw == 70.0  # le pic de la fenêtre
    assert reco[0].pic_kwh == 870


def test_une_heure_sous_le_seuil_coupe_la_fenetre():
    reco = analyser_depassements(serie([850, 700, 860], [900, 750, 910]), 800.0)

    assert [r.heures_concernees for r in reco] == [1, 1]
    assert [r.debut for r in reco] == [BASE, BASE + timedelta(hours=2)]


def test_niveaux_differents_ne_sont_pas_fusionnes():
    """Un dépassement prévu et un simple risque n'appellent pas la même décision."""
    reco = analyser_depassements(serie([850, 780], [900, 860]), 800.0)

    assert [r.niveau for r in reco] == [NIVEAU_DEPASSEMENT, NIVEAU_RISQUE]


def test_un_trou_de_prevision_rompt_la_fenetre():
    """On ne comble pas une heure manquante en supposant qu'elle dépassait aussi."""
    lignes = [
        LignePrevision(BASE, 850, 900),
        LignePrevision(BASE + timedelta(hours=3), 860, 910),  # 2 heures manquent
    ]
    reco = analyser_depassements(lignes, 800.0)

    assert len(reco) == 2
    assert [r.heures_concernees for r in reco] == [1, 1]


def test_seuil_strict_une_valeur_egale_ne_depasse_pas():
    assert analyser_depassements(serie([800], [800]), 800.0) == []
