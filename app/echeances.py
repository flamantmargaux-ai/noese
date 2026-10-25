"""Échéances, lancées par le worker : vérifications avant péremption, relance douce au bout de
5 jours ouvrés sans réponse, puis signalement au référent de la base 5 jours ouvrés plus tard.

Les mails ne partent que les jours ouvrés, en journée. Chaque étape est horodatée sur l'objet
concerné : relancer l'échéancier deux fois n'envoie rien deux fois."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select, text

from . import demandes, liens, mails, verifications
from .demandes import date_fr
from .models import Demande, Evenement, Fiche, Groupe, Service, Verification

HEURE_DEBUT, HEURE_FIN = 8, 18
VERROU = 7_384_221  # verrou consultatif PostgreSQL : un seul échéancier à la fois


# ------------------------------------------------------------------ jours ouvrés


def _paques(annee: int) -> date:
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois, jour = divmod(h + l - 7 * m + 114, 31)
    return date(annee, mois, jour + 1)


@lru_cache(maxsize=32)
def jours_feries(annee: int) -> frozenset[date]:
    """Jours fériés nationaux (métropole)."""
    paques = _paques(annee)
    fixes = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    return frozenset([date(annee, m, j) for m, j in fixes]
                     + [paques + timedelta(days=1), paques + timedelta(days=39), paques + timedelta(days=50)])


def est_ouvre(jour: date) -> bool:
    return jour.weekday() < 5 and jour not in jours_feries(jour.year)


def ajouter_jours_ouvres(depart: date, n: int) -> date:
    jour = depart
    while n > 0:
        jour += timedelta(days=1)
        if est_ouvre(jour):
            n -= 1
    return jour


def echu(depuis: datetime | None, n: int, maintenant: datetime) -> bool:
    return depuis is not None and ajouter_jours_ouvres(depuis.date(), n) <= maintenant.date()


# ------------------------------------------------------------------ échéancier


@dataclass
class Bilan:
    verifications: int = 0
    relances: int = 0
    signalements: int = 0
    ignore: str = ""
    details: list[str] = field(default_factory=list)


def _signaler(s, objet: str, corps: str, maintenant: datetime) -> None:
    p = verifications.parametres(s)
    if p.referent_base_email:
        m = mails.envoyer(s, p.referent_base_email, objet, corps, nom_destinataire=p.referent_base_nom)
        m.type = "signalement"


def _corps_signalement_demande(d: Demande, g: Groupe | None, service: str) -> str:
    return f"""Bonjour,

Une demande envoyée au service {service} reste sans réponse, malgré une relance :

« {d.question} »

Envoyée le {date_fr(d.envoyee_le)} à {d.destinataire_nom or d.destinataire_email}, relancée le {date_fr(d.relancee_le)}.

Un appel ou un mot de vive voix fait souvent avancer les choses. Vous pouvez aussi envoyer la demande à une autre personne :
{liens.url('lacunes', str(d.groupe_id))}

Noèse, la mémoire de la collectivité
"""


def _corps_signalement_verification(v: Verification, titre: str, service: str) -> str:
    return f"""Bonjour,

Une fiche attend sa vérification par le service {service}, malgré une relance :

« {titre} »

Demandée le {date_fr(v.envoyee_le)} à {v.destinataire_nom or v.destinataire_email}, relancée le {date_fr(v.relancee_le)}.
Si personne ne la confirme, elle sera signalée comme périmée à son échéance.

Voir la connaissance : {liens.url('memoire/connaissances', str(v.fiche_id))}

Noèse, la mémoire de la collectivité
"""


def traiter(s, maintenant: datetime | None = None, forcer: bool = False) -> Bilan:
    maintenant = maintenant or datetime.now()
    bilan = Bilan()
    if not forcer and not (est_ouvre(maintenant.date()) and HEURE_DEBUT <= maintenant.hour < HEURE_FIN):
        bilan.ignore = "Hors des heures ouvrées : aucun mail n'est envoyé."
        return bilan
    if not s.scalar(text("select pg_try_advisory_xact_lock(:v)"), {"v": VERROU}):
        bilan.ignore = "Un autre échéancier est déjà en cours."
        return bilan
    p = verifications.parametres(s)
    n = p.relance_jours_ouvres or 5
    aujourd_hui = maintenant.date()

    # 1. Fiches qui arrivent à échéance : on demande au service propriétaire de les vérifier.
    for fiche in s.scalars(select(Fiche).where(Fiche.statut == "validee", Fiche.perime_le.is_not(None))
                           .with_for_update(skip_locked=True)):
        if fiche.perime_le - timedelta(days=verifications.preavis(s, fiche)) <= aujourd_hui:
            if verifications.demander_verification(s, fiche, maintenant):
                bilan.verifications += 1

    # 2. Demandes sans réponse : relance douce, puis signalement.
    for d in s.scalars(select(Demande).where(Demande.statut.in_(("envoyee", "relancee"))).with_for_update(skip_locked=True)):
        g = s.get(Groupe, d.groupe_id)
        service = s.get(Service, d.service_id).nom if d.service_id else "concerné"
        if d.statut == "envoyee" and echu(d.envoyee_le, n, maintenant):
            jeton = demandes.jeton_de(d)
            objet, corps = demandes.corps_mail(s, d, g, jeton, relance=True)
            m = mails.envoyer(s, d.destinataire_email, objet, corps, lien=demandes.lien(jeton), demande_id=d.id,
                              nom_destinataire=d.destinataire_nom)
            m.type = "relance"
            d.statut, d.relancee_le = "relancee", maintenant
            s.add(Evenement(type="demande_relancee", objet="demande", objet_id=d.id))
            bilan.relances += 1
        elif d.statut == "relancee" and d.signalee_le is None and echu(d.relancee_le, n, maintenant):
            _signaler(s, f"Une demande au service {service} reste sans réponse", _corps_signalement_demande(d, g, service), maintenant)
            d.signalee_le = maintenant
            s.add(Evenement(type="demande_signalee", objet="demande", objet_id=d.id))
            bilan.signalements += 1

    # 3. Vérifications sans réponse : même rythme.
    for v in s.scalars(select(Verification).where(Verification.statut.in_(("envoyee", "relancee")))
                       .with_for_update(skip_locked=True)):
        fiche = s.get(Fiche, v.fiche_id)
        if fiche.statut != "validee":
            v.statut = "close"
            continue
        if v.statut == "envoyee" and echu(v.envoyee_le, n, maintenant):
            verifications.relancer(s, v, maintenant)
            bilan.relances += 1
        elif v.statut == "relancee" and v.signalee_le is None and echu(v.relancee_le, n, maintenant):
            service = s.get(Service, fiche.service_id).nom if fiche.service_id else "propriétaire"
            titre = demandes.version_courante(s, fiche).titre
            _signaler(s, f"Une fiche du service {service} attend sa vérification",
                      _corps_signalement_verification(v, titre, service), maintenant)
            v.signalee_le = maintenant
            s.add(Evenement(type="verification_signalee", objet="fiche", objet_id=fiche.id))
            bilan.signalements += 1
    s.flush()
    return bilan


def a_suivre(s) -> dict:
    """Pour l'accueil : ce qui demande l'attention du référent base."""
    demandes_signalees = s.scalars(select(Demande).where(Demande.signalee_le.is_not(None),
                                                         Demande.statut.in_(("envoyee", "relancee")))
                                   .order_by(Demande.envoyee_le)).all()
    verifs_signalees = s.scalars(select(Verification).where(Verification.signalee_le.is_not(None),
                                                            Verification.statut.in_(("envoyee", "relancee")))).all()
    perimees = s.scalars(select(Fiche).where(Fiche.statut == "validee", Fiche.perime_le <= date.today())
                         .order_by(Fiche.perime_le)).all()
    return {"demandes": demandes_signalees, "verifications": verifs_signalees, "perimees": perimees}
