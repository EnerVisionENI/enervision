"""
Recommandations d'ajustement de charge déduites de la prévision.

Aucune table dédiée : une recommandation est entièrement déterminée par les prévisions
(predictions_forecast) et la puissance souscrite du site (sites.capacity_kw). La persister
créerait un second état à maintenir cohérent avec le premier — et à réécrire à chaque cycle
d'inférence, puisque le futur change toutes les heures. On la recalcule à la lecture.

Deux niveaux, et non un seul seuil binaire, parce que les modèles produisent un intervalle
conforme à 90 % et que l'ignorer reviendrait à jeter l'information la plus utile à la décision :

  - dépassement prévu : la valeur centrale passe au-dessus de la puissance souscrite. C'est le
    scénario le plus probable, il appelle un ajustement.
  - risque de dépassement : la valeur centrale reste sous le seuil mais la borne haute le
    franchit. Le dépassement n'est pas attendu, il n'est pas exclu — appelle une surveillance,
    pas une action.

Les heures consécutives de même niveau sont regroupées en une seule recommandation. Sept
lignes « dépassement à 14h, à 15h, à 16h… » ne se lisent pas ; « de 14h à 17h, jusqu'à 47 kW
au-dessus » se lit et se décide. Une heure manquante rompt le groupe : on ne comble pas un trou
de prévision en supposant qu'il dépassait lui aussi.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

NIVEAU_DEPASSEMENT = "depassement_prevu"
NIVEAU_RISQUE = "risque_depassement"


@dataclass(frozen=True)
class Recommandation:
    niveau: str
    debut: datetime
    fin: datetime  # borne exclusive : fin du dernier créneau concerné, pas son début
    heures_concernees: int
    capacity_kw: float
    pic_kwh: float
    depassement_max_kw: float
    message: str


def _niveau_pour(predit: float, borne_haute: float | None, capacite: float) -> str | None:
    if predit > capacite:
        return NIVEAU_DEPASSEMENT
    if borne_haute is not None and borne_haute > capacite:
        return NIVEAU_RISQUE
    return None


def _message(niveau: str, heures: int, depassement: float, capacite: float) -> str:
    """Le message ne nomme aucune heure, à dessein : les horodatages sont rendus par le front,
    dans le fuseau de l'utilisateur. Les inscrire ici les figerait en UTC et ferait diverger la
    phrase du graphique affiché juste au-dessus."""
    duree = f"{heures} h" if heures > 1 else "1 h"
    if niveau == NIVEAU_DEPASSEMENT:
        return (
            f"Dépassement prévu pendant {duree} : jusqu'à {depassement:.0f} kW au-dessus des "
            f"{capacite:.0f} kW souscrits. Décaler ou lisser cette charge évite le dépassement."
        )
    return (
        f"Risque de dépassement pendant {duree} : la prévision reste sous les {capacite:.0f} kW "
        f"souscrits, mais son intervalle à 90 % monte jusqu'à {depassement:.0f} kW au-dessus. "
        f"À surveiller."
    )


def analyser_depassements(lignes, capacite_kw: float | None) -> list[Recommandation]:
    """
    `lignes` : prévisions d'un site, triées par target_ts croissant, une par créneau.

    Renvoie une liste vide quand il n'y a rien à dire — pas de prévision, pas de puissance
    souscrite connue, ou aucun créneau au-dessus du seuil. L'absence de recommandation est un
    résultat en soi : le front l'affiche comme tel plutôt que comme une donnée manquante.
    """
    if capacite_kw is None or capacite_kw <= 0 or not lignes:
        return []

    capacite = float(capacite_kw)
    recommandations: list[Recommandation] = []
    courant: list[tuple[datetime, float, float, str]] = []
    niveau_courant: str | None = None
    pas_courant = timedelta(hours=1)

    def cloturer() -> None:
        if not courant or niveau_courant is None:
            return
        debut = courant[0][0]
        fin = courant[-1][0] + pas_courant
        # Le dépassement se mesure sur la grandeur qui a déclenché le niveau : la valeur
        # centrale pour un dépassement prévu, la borne haute pour un risque. Les mélanger
        # afficherait une amplitude que le niveau annoncé ne justifie pas.
        indice = 1 if niveau_courant == NIVEAU_DEPASSEMENT else 2
        depassement = max(creneau[indice] for creneau in courant) - capacite
        recommandations.append(
            Recommandation(
                niveau=niveau_courant,
                debut=debut,
                fin=fin,
                heures_concernees=len(courant),
                capacity_kw=capacite,
                pic_kwh=max(creneau[1] for creneau in courant),
                depassement_max_kw=round(depassement, 1),
                message=_message(niveau_courant, len(courant), depassement, capacite),
            )
        )

    for ligne in lignes:
        predit = float(ligne.predicted_kwh)
        borne_haute = None if ligne.upper_90 is None else float(ligne.upper_90)
        niveau = _niveau_pour(predit, borne_haute, capacite)
        pas = timedelta(minutes=ligne.step_minutes or 60)

        contigu = bool(courant) and ligne.target_ts == courant[-1][0] + pas_courant
        if niveau != niveau_courant or not contigu:
            cloturer()
            courant = []
            niveau_courant = niveau
        pas_courant = pas

        if niveau is not None:
            courant.append((ligne.target_ts, predit, borne_haute if borne_haute is not None else predit, niveau))

    cloturer()
    return recommandations
