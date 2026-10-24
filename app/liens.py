"""Liens magiques.

Le jeton n'est jamais stocké : la base garde son empreinte (pour le retrouver) et un sel.
Le jeton se recalcule à partir du secret de l'instance et du sel, ce qui permet de renvoyer
le même lien dans une relance. Changer NOESE_SECRET invalide tous les liens en cours."""
import base64
import hashlib
import hmac
import secrets

from .config import config


def nouveau_sel() -> str:
    return secrets.token_hex(16)


def jeton(nature: str, identifiant: int, sel: str) -> str:
    empreinte = hmac.new(config.secret.encode(), f"{nature}:{identifiant}:{sel}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(empreinte).decode().rstrip("=")[:32]


def hacher(valeur: str) -> str:
    return hashlib.sha256(valeur.encode()).hexdigest()


def url(chemin: str, valeur: str) -> str:
    return f"{config.base_url.rstrip('/')}/{chemin}/{valeur}"
