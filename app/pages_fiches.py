"""Fraîcheur : vérification par le service (/v/{jeton}), base de fiches (/fiches), paramètres."""
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from . import db, demandes, echeances, fiches, verifications
from .models import Fiche, Service
from .pages_demandes import EN_TETES_LIEN, _page_metier
from .web import page

routeur = APIRouter()


# ------------------------------------------------------------------ vérification par le service (lien magique)


def _verif(s, jeton: str):
    v = verifications.verification_par_jeton(s, jeton)
    if v is None:
        raise HTTPException(404, "Ce lien ne fonctionne pas. Il est peut-être incomplet : copiez-le en entier depuis le mail.")
    return v, s.get(Fiche, v.fiche_id)


def _contexte_verif(s, v, fiche, jeton):
    return {"v": v, "fiche": fiche, "version": demandes.version_courante(s, fiche), "jeton": jeton,
            "service": s.get(Service, fiche.service_id) if fiche.service_id else None,
            "jours_normal": demandes.duree_peremption(s, False), "jours_saison": demandes.duree_peremption(s, True)}


@routeur.get("/v/{jeton}", response_class=HTMLResponse)
def verifier_page(request: Request, jeton: str):
    with db.session() as s:
        v, fiche = _verif(s, jeton)
        if v.statut in ("repondue", "close"):
            return RedirectResponse(f"/v/{jeton}/merci", status_code=303)
        return _page_metier(request, "v_verifier.html", erreur="", **_contexte_verif(s, v, fiche, jeton))


@routeur.post("/v/{jeton}/toujours-juste")
def verifier_toujours_juste(jeton: str):
    with db.session() as s:
        v, _ = _verif(s, jeton)
        if v.statut in ("envoyee", "relancee"):
            verifications.toujours_juste(s, v)
    return RedirectResponse(f"/v/{jeton}/merci", status_code=303)


@routeur.post("/v/{jeton}/modifier")
def verifier_modifier(request: Request, jeton: str, valide_par: str = Form(""), titre: str = Form(""),
                      reponse: str = Form(""), conditions: str = Form(""), pieces: str = Form(""),
                      lien_demarche: str = Form(""), saisonniere: str = Form("")):
    with db.session() as s:
        v, fiche = _verif(s, jeton)
        if v.statut not in ("envoyee", "relancee"):
            return RedirectResponse(f"/v/{jeton}/merci", status_code=303)
        try:
            verifications.modifier(s, v, valide_par, titre=titre, reponse=reponse, conditions=conditions,
                                   pieces=pieces, lien_demarche=lien_demarche, saisonniere=saisonniere == "1")
        except demandes.ValidationRefusee as e:
            s.rollback()
            v, fiche = _verif(s, jeton)
            contexte = _contexte_verif(s, v, fiche, jeton)
            saisie = {"titre": titre, "reponse": reponse, "conditions": conditions, "pieces": pieces,
                      "lien_demarche": lien_demarche, "valide_par": valide_par, "saisonniere": saisonniere == "1"}
            return _page_metier(request, "v_verifier.html", statut=422, erreur=str(e), saisie=saisie, ouvrir="modifier", **contexte)
    return RedirectResponse(f"/v/{jeton}/merci", status_code=303)


@routeur.post("/v/{jeton}/retirer")
def verifier_retirer(request: Request, jeton: str, par: str = Form(""), raison: str = Form("")):
    with db.session() as s:
        v, fiche = _verif(s, jeton)
        if v.statut not in ("envoyee", "relancee"):
            return RedirectResponse(f"/v/{jeton}/merci", status_code=303)
        try:
            verifications.retirer(s, v, par, raison)
        except demandes.ValidationRefusee as e:
            return _page_metier(request, "v_verifier.html", statut=422, erreur=str(e), ouvrir="retirer",
                                **_contexte_verif(s, v, fiche, jeton))
    return RedirectResponse(f"/v/{jeton}/merci", status_code=303)


@routeur.get("/v/{jeton}/merci", response_class=HTMLResponse)
def verifier_merci(request: Request, jeton: str):
    with db.session() as s:
        v, fiche = _verif(s, jeton)
        return _page_metier(request, "v_merci.html", **_contexte_verif(s, v, fiche, jeton))


# ------------------------------------------------------------------ base de fiches (référent base)


def _tri_services(s):
    from .trous import cle_tri

    return sorted(s.scalars(select(Service)).all(), key=lambda sv: cle_tri(sv.nom))


@routeur.get("/fiches", response_class=HTMLResponse)
def fiches_liste(request: Request, service: str = "", fraicheur: str = "toutes", q: str = ""):
    fraicheur = fraicheur if fraicheur in fiches.FRAICHEURS else "toutes"
    with db.session() as s:
        contexte = {"lignes": fiches.lister(s, service, fraicheur, q), "comptes": fiches.compter(s, service, q),
                    "services": _tri_services(s), "filtres": {"service": service, "fraicheur": fraicheur, "q": q.strip()},
                    "FRAICHEURS": fiches.FRAICHEURS}
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultats":
            return page(request, "_fiches_liste.html", **contexte)
        return page(request, "fiches.html", **contexte)


@routeur.get("/fiches/{fiche_id}", response_class=HTMLResponse)
def fiche_detail(request: Request, fiche_id: int, message: str = ""):
    with db.session() as s:
        fiche = s.get(Fiche, fiche_id)
        if fiche is None or fiche.statut == "brouillon":
            raise HTTPException(404, "Cette fiche n'existe pas ou n'a pas encore été validée.")
        fiche.service
        return page(request, "fiche.html", fiche=fiche, version=fiches.version_validee(s, fiche),
                    fraicheur=verifications.fraicheur(s, fiche), ouverte=verifications.verification_ouverte(s, fiche),
                    services=_tri_services(s), message=message, **fiches.historique(s, fiche))


@routeur.post("/fiches/{fiche_id}/proprietaire")
def fiche_proprietaire(fiche_id: int, service_id: str = Form("")):
    with db.session() as s:
        fiche = s.get(Fiche, fiche_id)
        if fiche is None:
            raise HTTPException(404, "Cette fiche n'existe pas.")
        fiches.changer_proprietaire(s, fiche, int(service_id) if service_id.isdigit() else None)
    return RedirectResponse(f"/fiches/{fiche_id}?message=proprietaire", status_code=303)


@routeur.post("/fiches/{fiche_id}/verifier")
def fiche_demander_verification(fiche_id: int):
    with db.session() as s:
        fiche = s.get(Fiche, fiche_id)
        if fiche is None:
            raise HTTPException(404, "Cette fiche n'existe pas.")
        v = verifications.demander_verification(s, fiche)
        message = "verification" if v else ("deja" if verifications.verification_ouverte(s, fiche) else "sans_destinataire")
    return RedirectResponse(f"/fiches/{fiche_id}?message={message}", status_code=303)


# ------------------------------------------------------------------ paramètres


@routeur.get("/parametres", response_class=HTMLResponse)
def parametres_page(request: Request, message: str = "", relances: int | None = None, signalements: int | None = None):
    verifs = request.query_params.get("verifications")
    bilan = echeances.Bilan(int(verifs), relances or 0, signalements or 0) if verifs and verifs.isdigit() else None
    with db.session() as s:
        return page(request, "parametres.html", p=verifications.parametres(s), services=_tri_services(s),
                    message=message, bilan=bilan, mails=_derniers_mails(s))


@routeur.post("/parametres/fraicheur")
def parametres_fraicheur(peremption_mois: int = Form(6), peremption_saison_jours: int = Form(30),
                         preavis_jours: int = Form(14), preavis_saison_jours: int = Form(7),
                         relance_jours_ouvres: int = Form(5), referent_base_nom: str = Form(""),
                         referent_base_email: str = Form("")):
    with db.session() as s:
        p = verifications.parametres(s)
        s.add(p)
        p.peremption_jours = max(1, min(peremption_mois, 24)) * 365 // 12
        p.peremption_saison_jours = max(7, min(peremption_saison_jours, 365))
        p.preavis_jours = max(1, min(preavis_jours, 60))
        p.preavis_saison_jours = max(1, min(preavis_saison_jours, 30))
        p.relance_jours_ouvres = max(1, min(relance_jours_ouvres, 20))
        p.referent_base_nom, p.referent_base_email = referent_base_nom.strip(), referent_base_email.strip().lower()
    return RedirectResponse("/parametres?message=fraicheur#fraicheur", status_code=303)


@routeur.post("/parametres/services")
async def parametres_services(request: Request):
    formulaire = await request.form()
    with db.session() as s:
        for sv in s.scalars(select(Service)):
            if f"nom_{sv.id}" in formulaire:
                sv.nom = str(formulaire[f"nom_{sv.id}"]).strip() or sv.nom
                sv.referent_nom = str(formulaire.get(f"referent_nom_{sv.id}", "")).strip()
                sv.referent_email = str(formulaire.get(f"referent_email_{sv.id}", "")).strip().lower()
        nouveau = str(formulaire.get("nouveau_nom", "")).strip()
        if nouveau and not s.scalar(select(Service).where(Service.nom == nouveau)):
            s.add(Service(nom=nouveau, referent_nom=str(formulaire.get("nouveau_referent_nom", "")).strip(),
                          referent_email=str(formulaire.get("nouveau_referent_email", "")).strip().lower()))
    return RedirectResponse("/parametres?message=services#services", status_code=303)


@routeur.post("/parametres/echeances")
def parametres_echeances():
    """Lance l'échéancier tout de suite, sans attendre le worker ni les heures ouvrées."""
    with db.session() as s:
        b = echeances.traiter(s, forcer=True)
    resultat = urlencode({"verifications": b.verifications, "relances": b.relances, "signalements": b.signalements})
    return RedirectResponse(f"/parametres?{resultat}#echeances", status_code=303)


def _derniers_mails(s, n: int = 15):
    from .models import MailEnvoye

    return s.scalars(select(MailEnvoye).order_by(MailEnvoye.id.desc()).limit(n)).all()


# ------------------------------------------------------------------ export


@routeur.get("/export/base.{format_}")
def exporter(format_: str, perimees: int = 1):
    from fastapi.responses import Response

    from . import export

    if format_ not in ("md", "json", "csv"):
        raise HTTPException(404, "Formats disponibles : Markdown (.md), JSON (.json) et CSV (.csv).")
    with db.session() as s:
        collectivite = verifications.parametres(s).collectivite or "la collectivité"
        fiches_ = export.collecter(s, avec_perimees=bool(perimees))
    contenu, type_ = {
        "md": (lambda: export.vers_markdown(fiches_, collectivite), "text/markdown; charset=utf-8"),
        "json": (lambda: export.vers_json(fiches_, collectivite), "application/json; charset=utf-8"),
        "csv": (lambda: export.vers_csv(fiches_), "text/csv; charset=utf-8"),
    }[format_]
    from .trous import cle_tri

    nom = "base-" + "-".join(cle_tri(collectivite).split()) + f"-{date.today().isoformat()}.{format_}"
    return Response(contenu(), media_type=type_, headers={"Content-Disposition": f'attachment; filename="{nom}"'})
