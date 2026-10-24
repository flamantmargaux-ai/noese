"""Tableau des trous : filtres, priorisation par volume, détail et actions du référent base."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import case, func, or_, select

from . import analyse
from .models import Evenement, Groupe, Question, Service

# Filtre de statut : clé d'URL -> (libellé, condition)
STATUTS = {
    "a_traiter": "À traiter",
    "trou": "Trous",
    "partiel": "Partiels",
    "couvert": "Couverts",
    "demande": "Demandés",
    "ecarte": "Écartés",
    "tous": "Tous",
}
STATUTS_OUVERTS = ("ouvert",)


def _condition(statut: str):
    if statut == "a_traiter":
        return Groupe.verdict.in_(("trou", "partiel")) & Groupe.statut.in_(STATUTS_OUVERTS)
    if statut in ("trou", "partiel", "couvert"):
        return (Groupe.verdict == statut) & (Groupe.statut != "ecarte")
    if statut == "demande":
        return Groupe.statut.in_(("demande", "repondu"))
    if statut == "ecarte":
        return Groupe.statut == "ecarte"
    return Groupe.id.is_not(None)


@dataclass
class Ligne:
    groupe: Groupe
    service: str
    nb: int
    nb_30j: int
    transferts: int

    @property
    def part_transferts(self) -> int:
        return round(100 * self.transferts / self.nb) if self.nb else 0


def _statistiques():
    """Nombre de questions, dont les 30 derniers jours, et transferts à un agent, par groupe."""
    depuis = date.today() - timedelta(days=30)
    return (
        select(
            Question.groupe_id,
            func.count(Question.id).label("nb"),
            func.count(case((Question.date >= depuis, 1))).label("nb_30j"),
            func.count(case((Question.transfere.is_(True), 1))).label("transferts"),
        )
        .group_by(Question.groupe_id)
        .subquery()
    )


def lister(s, service: str = "", statut: str = "a_traiter", recherche: str = "") -> list[Ligne]:
    stats = _statistiques()
    requete = (
        select(Groupe, Service.nom, stats.c.nb, stats.c.nb_30j, stats.c.transferts)
        .outerjoin(Service, Service.id == Groupe.service_id)
        .outerjoin(stats, stats.c.groupe_id == Groupe.id)
        .where(_condition(statut if statut in STATUTS else "a_traiter"))
        .order_by(func.coalesce(stats.c.nb, 0).desc(), Groupe.libelle)
    )
    if service == "aucun":
        requete = requete.where(Groupe.service_id.is_(None))
    elif service.isdigit():
        requete = requete.where(Groupe.service_id == int(service))
    if recherche.strip():
        motif = f"%{recherche.strip()}%"
        requete = requete.where(or_(
            Groupe.libelle.ilike(motif),
            Groupe.id.in_(select(Question.groupe_id).where(Question.texte.ilike(motif))),
        ))
    return [Ligne(g, nom or "", nb or 0, nb_30j or 0, tr or 0) for g, nom, nb, nb_30j, tr in s.execute(requete)]


def compter_par_statut(s, service: str = "", recherche: str = "") -> dict[str, int]:
    """Compteurs des onglets, avec les mêmes filtres que la liste."""
    return {cle: len(lister(s, service, cle, recherche)) for cle in STATUTS}


def services_avec_compte(s) -> list[tuple[Service, int]]:
    """Services et nombre de groupes à traiter, pour le filtre."""
    a_traiter = _condition("a_traiter")
    lignes = s.execute(
        select(Service, func.count(Groupe.id).filter(a_traiter))
        .outerjoin(Groupe, Groupe.service_id == Service.id)
        .group_by(Service.id)
    ).all()
    return sorted(lignes, key=lambda l: cle_tri(l[0].nom))


def cle_tri(texte: str) -> str:
    """Tri alphabétique français : « État civil » se range avec les E."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", texte.lower()) if not unicodedata.combining(c))


def sans_service_a_traiter(s) -> int:
    return s.scalar(select(func.count(Groupe.id)).where(_condition("a_traiter"), Groupe.service_id.is_(None)))


@dataclass
class Detail:
    groupe: Groupe
    questions: list[Question]
    nb_30j: int
    transferts: int
    canaux: list[tuple[str, int]]
    premiere: date | None
    derniere: date | None


def detail(s, groupe_id: int) -> Detail | None:
    g = s.get(Groupe, groupe_id)
    if not g:
        return None
    questions = s.scalars(
        select(Question).where(Question.groupe_id == g.id).order_by(Question.date.desc().nulls_last(), Question.id)
    ).all()
    depuis = date.today() - timedelta(days=30)
    dates = [q.date for q in questions if q.date]
    return Detail(
        groupe=g,
        questions=questions,
        nb_30j=sum(1 for d in dates if d >= depuis),
        transferts=sum(1 for q in questions if q.transfere),
        canaux=Counter(q.canal or "non précisé" for q in questions).most_common(),
        premiere=min(dates) if dates else None,
        derniere=max(dates) if dates else None,
    )


def _journaliser(s, type_: str, g: Groupe, **details) -> None:
    s.add(Evenement(type=type_, objet="groupe", objet_id=g.id, acteur="referent_base", details=details))


def changer_service(s, g: Groupe, service_id: int | None) -> None:
    ancien = g.service_id
    g.service_id = service_id
    _journaliser(s, "groupe_service", g, avant=ancien, apres=service_id)


def renommer(s, g: Groupe, libelle: str) -> None:
    libelle = libelle.strip()[:300]
    if libelle and libelle != g.libelle:
        _journaliser(s, "groupe_libelle", g, avant=g.libelle, apres=libelle)
        g.libelle = libelle


def ecarter(s, g: Groupe) -> None:
    _journaliser(s, "groupe_ecarte", g, avant=g.statut)
    g.statut = "ecarte"


def rouvrir(s, g: Groupe) -> None:
    _journaliser(s, "groupe_rouvert", g, avant=g.statut)
    g.statut = "ouvert"


def candidats_rassemblement(s, g: Groupe) -> list[Groupe]:
    """Besoins avec lesquels on peut rassembler celui-ci : même service d'abord."""
    autres = s.scalars(select(Groupe).where(Groupe.id != g.id, Groupe.statut != "ecarte")).all()
    return sorted(autres, key=lambda a: (a.service_id != g.service_id, cle_tri(a.libelle)))


def rassembler(s, source: Groupe, cible: Groupe) -> None:
    """Déplace toutes les questions de `source` dans `cible`, puis supprime `source`.
    Les questions sont marquées comme rangées à la main : l'analyse ne les déplacera plus."""
    questions = s.scalars(select(Question).where(Question.groupe_id.in_((source.id, cible.id)))).all()
    for q in questions:
        q.groupe_id, q.groupe_manuel = cible.id, True
    vecteurs = [np.asarray(q.embedding) for q in questions if q.embedding is not None]
    if vecteurs:
        centroide = np.mean(vecteurs, axis=0)
        cible.centroide = centroide / (np.linalg.norm(centroide) or 1.0)
    cible.nb = len(questions)
    if cible.service_id is None:
        cible.service_id = source.service_id
    _journaliser(s, "groupe_rassemble", cible, source=source.id, libelle_source=source.libelle)
    s.flush()
    s.delete(source)
    s.flush()
    analyse.juger_groupe(s, cible)


def reverifier(s, g: Groupe) -> None:
    avant = g.verdict
    analyse.juger_groupe(s, g)
    _journaliser(s, "groupe_reverifie", g, avant=avant, apres=g.verdict)
