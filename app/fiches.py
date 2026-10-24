"""Base de fiches : recherche, propriétaire, fraîcheur, historique."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from . import verifications
from .models import Evenement, Fiche, FicheVersion, Service, Verification

FRAICHEURS = {
    "toutes": "Toutes",
    "a_jour": "À jour",
    "bientot": "À vérifier bientôt",
    "perimee": "Périmées",
    "archivee": "Retirées",
}


@dataclass
class LigneFiche:
    fiche: Fiche
    version: FicheVersion
    service: str
    fraicheur: str
    verification: Verification | None


def lister(s, service: str = "", fraicheur: str = "toutes", recherche: str = "") -> list[LigneFiche]:
    requete = select(Fiche).where(Fiche.statut.in_(("validee", "archivee")))
    if service == "aucun":
        requete = requete.where(Fiche.service_id.is_(None))
    elif service.isdigit():
        requete = requete.where(Fiche.service_id == int(service))
    noms = {sv.id: sv.nom for sv in s.scalars(select(Service))}
    motif = recherche.strip().lower()
    lignes = []
    for f in s.scalars(requete):
        v = s.scalar(select(FicheVersion).where(FicheVersion.fiche_id == f.id, FicheVersion.validee.is_(True))
                     .order_by(FicheVersion.numero.desc()).limit(1))
        if v is None:
            continue
        if motif and motif not in f"{v.titre} {v.reponse} {v.conditions} {v.pieces}".lower():
            continue
        etat = verifications.fraicheur(s, f)
        if fraicheur == "toutes" and etat == "archivee":
            continue
        if fraicheur not in ("toutes", etat):
            continue
        lignes.append(LigneFiche(f, v, noms.get(f.service_id, ""), etat, verifications.verification_ouverte(s, f)))
    ordre = {"perimee": 0, "bientot": 1, "a_jour": 2, "archivee": 3}
    return sorted(lignes, key=lambda l: (ordre.get(l.fraicheur, 9), l.fiche.perime_le or date.max, l.version.titre))


def compter(s, service: str = "", recherche: str = "") -> dict[str, int]:
    return {cle: len(lister(s, service, cle, recherche)) for cle in FRAICHEURS}


def historique(s, fiche: Fiche) -> dict:
    return {
        "versions": s.scalars(select(FicheVersion).where(FicheVersion.fiche_id == fiche.id)
                              .order_by(FicheVersion.numero.desc())).all(),
        "verifications": s.scalars(select(Verification).where(Verification.fiche_id == fiche.id)
                                   .order_by(Verification.id.desc())).all(),
        "evenements": s.scalars(select(Evenement).where(Evenement.objet == "fiche", Evenement.objet_id == fiche.id)
                                .order_by(Evenement.id.desc())).all(),
    }


def changer_proprietaire(s, fiche: Fiche, service_id: int | None) -> None:
    s.add(Evenement(type="fiche_proprietaire", objet="fiche", objet_id=fiche.id, acteur="referent_base",
                    details={"avant": fiche.service_id, "apres": service_id}))
    fiche.service_id = service_id
    for v in s.scalars(select(Verification).where(Verification.fiche_id == fiche.id,
                                                  Verification.statut.in_(("envoyee", "relancee")))):
        v.statut = "close"  # la vérification en cours visait l'ancien propriétaire


def version_validee(s, fiche: Fiche) -> FicheVersion | None:
    return s.scalar(select(FicheVersion).where(FicheVersion.fiche_id == fiche.id, FicheVersion.validee.is_(True))
                    .order_by(FicheVersion.numero.desc()).limit(1))
