"""Connexion du référent base par lien magique, sans mot de passe.

Le lien est valable 15 minutes et une seule fois ; seule son empreinte est stockée.
La session dure 30 jours sur le poste. Retirer un référent dans les Paramètres
coupe sa session à la requête suivante."""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select

from . import liens, mails
from .config import config
from .models import JetonConnexion, Parametres, Utilisateur

DUREE_LIEN = timedelta(minutes=15)
DUREE_SESSION = 30 * 24 * 3600  # secondes
LIENS_PAR_HEURE = 5
# Pages ouvertes sans connexion : celles des services ont leur propre lien magique.
CHEMINS_PUBLICS = ("/connexion", "/r/", "/v/", "/static/", "/demo-site")


def est_public(chemin: str) -> bool:
    return any(chemin == p.rstrip("/") or chemin.startswith(p) for p in CHEMINS_PUBLICS)


def mode_journal() -> bool:
    """Sans serveur de mail, le lien de connexion s'affiche à l'écran (développement uniquement)."""
    return config.smtp_host == "journal"


def referents(s) -> list[Utilisateur]:
    return s.scalars(select(Utilisateur).where(Utilisateur.role == "referent_base").order_by(Utilisateur.email)).all()


def par_email(s, email: str) -> Utilisateur | None:
    return s.scalar(select(Utilisateur).where(Utilisateur.email == email.strip().lower(),
                                              Utilisateur.role == "referent_base"))


def semer_premier_referent(s) -> None:
    email = config.referent_base_email.strip().lower()
    if not email or par_email(s, email):
        return
    s.add(Utilisateur(email=email, role="referent_base", nom=""))
    p = s.get(Parametres, 1)
    if p and not p.referent_base_email:
        p.referent_base_email = email
    s.flush()


def ajouter(s, email: str, nom: str = "") -> Utilisateur | None:
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Indiquez une adresse mail valide.")
    if par_email(s, email):
        return None
    u = Utilisateur(email=email, nom=nom.strip(), role="referent_base")
    s.add(u)
    s.flush()
    return u


def retirer(s, utilisateur_id: int) -> None:
    if len(referents(s)) <= 1:
        raise ValueError("Il faut au moins un référent de la base.")
    u = s.get(Utilisateur, utilisateur_id)
    if u and u.role == "referent_base":
        s.delete(u)
        s.flush()


def demander_lien(s, email: str, maintenant: datetime | None = None) -> str | None:
    """Envoie un lien si l'adresse est celle d'un référent. Ne dit jamais si l'adresse est connue.
    Renvoie le lien seulement en mode journal (développement), pour l'afficher à l'écran."""
    maintenant = maintenant or datetime.now()
    u = par_email(s, email)
    if u is None:
        return None
    recents = s.scalar(select(func.count(JetonConnexion.id)).where(
        JetonConnexion.utilisateur_id == u.id, JetonConnexion.cree_le >= maintenant - timedelta(hours=1)))
    if recents >= LIENS_PAR_HEURE:
        return None
    jeton = secrets.token_urlsafe(32)
    s.add(JetonConnexion(utilisateur_id=u.id, jeton_hash=liens.hacher(jeton), expire_le=maintenant + DUREE_LIEN,
                         cree_le=maintenant))
    adresse = liens.url("connexion", jeton)
    corps = f"""Bonjour{(' ' + u.nom) if u.nom else ''},

Voici votre lien pour vous connecter au Jardinier. Il est valable 15 minutes et ne fonctionne qu'une fois :

{adresse}

Si vous n'avez pas demandé ce lien, ignorez ce message : personne ne pourra se connecter sans lui.

Le Jardinier
"""
    m = mails.envoyer(s, u.email, "Votre lien de connexion au Jardinier", corps, lien=adresse, nom_destinataire=u.nom)
    m.type = "connexion"
    return adresse if mode_journal() else None


def consommer(s, jeton: str, maintenant: datetime | None = None) -> Utilisateur | None:
    maintenant = maintenant or datetime.now()
    if not jeton or len(jeton) > 64:
        return None
    j = s.scalar(select(JetonConnexion).where(JetonConnexion.jeton_hash == liens.hacher(jeton)).with_for_update())
    if j is None or j.utilise_le is not None or j.expire_le < maintenant:
        return None
    j.utilise_le = maintenant
    return s.get(Utilisateur, j.utilisateur_id)


def utilisateur_de_session(s, session: dict) -> Utilisateur | None:
    uid = session.get("utilisateur_id")
    if not isinstance(uid, int):
        return None
    u = s.get(Utilisateur, uid)
    return u if u and u.role == "referent_base" else None


def suite_sure(suite: str) -> str:
    """Évite les redirections vers un autre site après connexion."""
    return suite if suite.startswith("/") and not suite.startswith("//") and not est_public(suite) else "/"
