"""Rendu des pages : gabarits Jinja, filtres français."""
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import RACINE
from .demandes import date_fr

ICI = RACINE / "app"
gabarits = Jinja2Templates(directory=ICI / "templates")


def nombre_fr(n) -> str:
    return f"{n:,}".replace(",", " ")


gabarits.env.filters["nombre"] = nombre_fr
gabarits.env.filters["date_fr"] = date_fr
VERDICTS = {"couvert": "Couvert", "partiel": "Partiel", "trou": "Trou", None: "À vérifier"}
gabarits.env.globals["VERDICTS"] = VERDICTS
# Version des fichiers statiques dans l'URL : les navigateurs rechargent le CSS après une mise à jour.
gabarits.env.globals["version_statique"] = str(int((ICI / "static/css/noese.css").stat().st_mtime))


def page(request: Request, gabarit: str, /, statut: int = 200, **contexte) -> HTMLResponse:
    courant = "/" + request.url.path.strip("/").split("/")[0] if request.url.path != "/" else "/"
    utilisateur = getattr(request.state, "utilisateur", None)
    return gabarits.TemplateResponse(request, gabarit, {"courant": courant, "utilisateur": utilisateur, **contexte},
                                     status_code=statut)
