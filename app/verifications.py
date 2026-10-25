"""Garder la base fraîche : chaque fiche validée a un propriétaire (son service) et une date de
péremption. Avant l'échéance, le service confirme d'un clic (« Toujours juste »), corrige ou retire."""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import analyse, demandes, liens, mails
from .demandes import date_fr
from .models import Evenement, Extrait, Fiche, FicheVersion, Groupe, Parametres, Service, Verification


def parametres(s) -> Parametres:
    return s.get(Parametres, 1) or Parametres(id=1)


def fraicheur(s, fiche: Fiche, aujourd_hui: date | None = None) -> str:
    """a_jour | bientot | perimee | archivee | brouillon"""
    if fiche.statut != "validee":
        return fiche.statut
    aujourd_hui = aujourd_hui or date.today()
    if fiche.perime_le is None:
        return "a_jour"
    if fiche.perime_le <= aujourd_hui:
        return "perimee"
    if fiche.perime_le - timedelta(days=preavis(s, fiche)) <= aujourd_hui:
        return "bientot"
    return "a_jour"


def preavis(s, fiche: Fiche) -> int:
    p = parametres(s)
    return (p.preavis_saison_jours or 7) if fiche.saisonniere else (p.preavis_jours or 14)


def destinataire(s, fiche: Fiche) -> tuple[str, str]:
    """Le référent du service propriétaire, ou à défaut le référent de la base."""
    sv = s.get(Service, fiche.service_id) if fiche.service_id else None
    if sv and sv.referent_email:
        return sv.referent_nom, sv.referent_email
    p = parametres(s)
    return p.referent_base_nom, p.referent_base_email


def verification_ouverte(s, fiche: Fiche) -> Verification | None:
    return s.scalar(select(Verification).where(Verification.fiche_id == fiche.id,
                                               Verification.statut.in_(("envoyee", "relancee"))).limit(1))


def jeton_de(v: Verification) -> str:
    return liens.jeton("verification", v.id, v.sel)


def verification_par_jeton(s, jeton: str) -> Verification | None:
    if not jeton or len(jeton) > 64:
        return None
    return s.scalar(select(Verification).where(Verification.jeton_hash == liens.hacher(jeton)))


def _corps(s, v: Verification, fiche: Fiche, version: FicheVersion, relance: bool = False) -> tuple[str, str]:
    objet = "Cette fiche est-elle toujours juste ? (30 secondes)"
    rappel = ""
    if relance:
        objet = "Rappel : cette fiche est-elle toujours juste ? (30 secondes)"
        rappel = f"Nous vous avons écrit le {date_fr(v.envoyee_le)}. "
    echeance = f"le {date_fr(fiche.perime_le)}" if fiche.perime_le else "bientôt"
    corps = f"""Bonjour{(' ' + v.destinataire_nom) if v.destinataire_nom else ''},

{rappel}Votre service est propriétaire de cette fiche de la base de connaissances. Elle arrive à échéance {echeance} :

« {version.titre} »
{version.reponse}

Si rien n'a changé, un clic suffit : « Toujours juste ». Sinon, corrigez-la ou retirez-la.

Vérifier la fiche (sans compte, sans mot de passe) :
{liens.url('v', jeton_de(v))}

Noèse, la mémoire de la collectivité
"""
    return objet, corps


def demander_verification(s, fiche: Fiche, maintenant: datetime | None = None) -> Verification | None:
    """Crée la vérification et envoie le mail. Ne fait rien si une vérification est déjà en cours."""
    if fiche.statut != "validee" or verification_ouverte(s, fiche):
        return None
    nom, email = destinataire(s, fiche)
    if not email:
        return None
    sel = liens.nouveau_sel()
    v = Verification(fiche_id=fiche.id, destinataire_nom=nom, destinataire_email=email, sel=sel,
                     jeton_hash=liens.hacher(liens.nouveau_sel()), envoyee_le=maintenant or datetime.now())
    s.add(v)
    s.flush()
    v.jeton_hash = liens.hacher(jeton_de(v))
    version = demandes.version_courante(s, fiche)
    objet, corps = _corps(s, v, fiche, version)
    m = mails.envoyer(s, email, objet, corps, lien=liens.url("v", jeton_de(v)), nom_destinataire=nom)
    m.verification_id, m.type = v.id, "verification"
    s.add(Evenement(type="verification_envoyee", objet="fiche", objet_id=fiche.id, details={"verification": v.id}))
    return v


def relancer(s, v: Verification, maintenant: datetime) -> None:
    fiche = s.get(Fiche, v.fiche_id)
    objet, corps = _corps(s, v, fiche, demandes.version_courante(s, fiche), relance=True)
    m = mails.envoyer(s, v.destinataire_email, objet, corps, lien=liens.url("v", jeton_de(v)), nom_destinataire=v.destinataire_nom)
    m.verification_id, m.type = v.id, "relance"
    v.statut, v.relancee_le = "relancee", maintenant


def _clore(s, v: Verification, reponse: str, par: str) -> None:
    v.statut, v.reponse, v.repondue_le = "repondue", reponse, datetime.now()
    s.add(Evenement(type=f"verification_{reponse}", objet="fiche", objet_id=v.fiche_id, acteur=par,
                    details={"verification": v.id}))


def toujours_juste(s, v: Verification, par: str = "") -> Fiche:
    """Un clic : la fiche est confirmée et sa date de péremption repoussée d'une durée complète."""
    fiche = s.get(Fiche, v.fiche_id)
    par = par.strip() or v.destinataire_nom or v.destinataire_email
    fiche.verifiee_le, fiche.verifiee_par = date.today(), par
    fiche.perime_le = date.today() + timedelta(days=demandes.duree_peremption(s, fiche.saisonniere))
    _clore(s, v, "toujours_juste", par)
    return fiche


def modifier(s, v: Verification, par: str, **champs) -> FicheVersion:
    fiche = s.get(Fiche, v.fiche_id)
    version = demandes.valider_fiche(s, fiche, par, **champs)
    fiche.verifiee_le, fiche.verifiee_par = date.today(), par.strip()
    _clore(s, v, "modifiee", par)
    return version


def retirer(s, v: Verification, par: str, raison: str = "") -> Fiche:
    """La fiche n'est plus valable : elle sort de la base, et le besoin redevient un trou à traiter."""
    if not par.strip():
        raise demandes.ValidationRefusee("Indiquez votre nom pour retirer la fiche.")
    fiche = s.get(Fiche, v.fiche_id)
    fiche.statut = "archivee"
    for e in s.scalars(select(Extrait).where(Extrait.fiche_id == fiche.id)):
        s.delete(e)
    s.flush()
    g = s.get(Groupe, fiche.groupe_id) if fiche.groupe_id else None
    if g:
        g.statut = "ouvert"
        analyse.juger_groupe(s, g)
    _clore(s, v, "retiree", par)
    s.add(Evenement(type="fiche_retiree", objet="fiche", objet_id=fiche.id, acteur=par, details={"raison": raison}))
    return fiche
