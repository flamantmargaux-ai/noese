"""Tableau de bord : quatre chiffres, et où un coup de main serait le plus utile.

Le classement des services sert à aider, pas à pointer du doigt : il met côte à côte ce qui
reste à faire et ce que chaque service a déjà apporté, sans couleur ni rang."""
from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import select

from . import analyse, echeances, verifications
from .models import Demande, Fiche, Groupe, Service


def jours_ouvres_entre(debut: date, fin: date) -> int:
    """Nombre de jours ouvrés écoulés après `debut`, jusqu'à `fin` inclus."""
    n, jour = 0, debut
    while jour < fin:
        jour = echeances.ajouter_jours_ouvres(jour, 1)
        if jour <= fin:
            n += 1
    return n


def _premier_envoi(s, d: Demande) -> date:
    """Le délai d'une question transmise à un collègue court depuis le premier envoi."""
    while d.transferee_de:
        d = s.get(Demande, d.transferee_de)
    return d.envoyee_le.date()


def delais(s, service_id: int | None = None) -> list[int]:
    requete = select(Demande).where(Demande.statut == "repondue", Demande.repondue_le.is_not(None))
    if service_id is not None:
        requete = requete.where(Demande.service_id == service_id)
    return [jours_ouvres_entre(_premier_envoi(s, d), d.repondue_le.date()) for d in s.scalars(requete)]


def mediane(valeurs: list[int]) -> float | None:
    return median(valeurs) if valeurs else None


def formuler_delai(valeur: float | None) -> str:
    if valeur is None:
        return "—"
    if valeur < 1:
        return "le jour même"
    jours = f"{valeur:g}".replace(".", ",")
    return f"{jours} jour{'s' if valeur >= 2 else ''} ouvré{'s' if valeur >= 2 else ''}"


def chiffre_delai(valeur: float | None) -> str:
    """Version courte pour le grand chiffre de l'accueil."""
    if valeur is None:
        return "—"
    return "< 1" if valeur < 1 else f"{valeur:g}".replace(".", ",")


@dataclass
class LigneService:
    service: Service
    a_traiter: int
    demandes_en_cours: int
    fiches: int
    delai: float | None


def chiffres(s) -> dict:
    b = analyse.bilan(s)
    perimees = sum(1 for f in s.scalars(select(Fiche).where(Fiche.statut == "validee"))
                   if verifications.fraicheur(s, f) == "perimee")
    d = delais(s)
    return {**b, "trous_ouverts": b["trou"] + b["partiel"], "perimees": perimees,
            "delai_median": mediane(d), "nb_reponses": len(d)}


def par_service(s) -> list[LigneService]:
    lignes = []
    for sv in s.scalars(select(Service)):
        a_traiter = len(s.scalars(select(Groupe.id).where(
            Groupe.service_id == sv.id, Groupe.verdict.in_(("trou", "partiel")), Groupe.statut == "ouvert")).all())
        en_cours = len(s.scalars(select(Demande.id).where(
            Demande.service_id == sv.id, Demande.statut.in_(("envoyee", "relancee")))).all())
        nb_fiches = len(s.scalars(select(Fiche.id).where(Fiche.service_id == sv.id, Fiche.statut == "validee")).all())
        if a_traiter or en_cours or nb_fiches:
            lignes.append(LigneService(sv, a_traiter, en_cours, nb_fiches, mediane(delais(s, sv.id))))
    return sorted(lignes, key=lambda l: (-l.a_traiter, -l.demandes_en_cours, l.service.nom))
