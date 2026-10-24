"""Pages de la boucle demande → réponse → validation.

/trous/{id}/demande : le référent base rédige et envoie la demande.
/r/{jeton}          : le référent métier répond, sans compte (lien magique).
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from . import db, demandes
from .fournisseur import ErreurFournisseur, fournisseur
from .models import Demande, Fiche, Groupe, MailEnvoye, Service
from .web import page

routeur = APIRouter()
EN_TETES_LIEN = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}


def _groupe(s, groupe_id: int) -> Groupe:
    g = s.get(Groupe, groupe_id)
    if g is None:
        raise HTTPException(404, "Ce besoin n'existe plus. Une nouvelle analyse l'a peut-être fusionné avec un autre.")
    return g


# ------------------------------------------------------------------ côté référent base


@routeur.get("/trous/{groupe_id}/demande", response_class=HTMLResponse)
def demande_formulaire(request: Request, groupe_id: int, retour: str = ""):
    with db.session() as s:
        g = _groupe(s, groupe_id)
        g.service
        return page(request, "demande.html", g=g, exemples=demandes.exemples(s, g.id),
                    nb_30j=demandes.nb_recent(s, g.id), retour=retour if retour.startswith("/trous") else "/trous",
                    erreur="")


@routeur.get("/trous/{groupe_id}/demande/brouillon", response_class=HTMLResponse)
def demande_brouillon(groupe_id: int):
    """Chargé après l'affichage de la page : l'agent n'attend pas le modèle pour voir l'écran."""
    with db.session() as s:
        g = _groupe(s, groupe_id)
        g.service
        try:
            question = demandes.rediger_question(s, g)
        except ErreurFournisseur:
            question = ""
    from html import escape

    return HTMLResponse(escape(question))


@routeur.post("/trous/{groupe_id}/demande")
def demande_envoyer(request: Request, groupe_id: int, question: str = Form(""), destinataire_email: str = Form(""),
                    destinataire_nom: str = Form(""), retour: str = Form("")):
    with db.session() as s:
        g = _groupe(s, groupe_id)
        g.service
        erreur = ""
        if len(question.strip()) < 10:
            erreur = "Écrivez la question à poser au service."
        elif "@" not in destinataire_email or "." not in destinataire_email.split("@")[-1]:
            erreur = "Indiquez une adresse mail valide pour le destinataire."
        if erreur:
            return page(request, "demande.html", statut=422, g=g, exemples=demandes.exemples(s, g.id),
                        nb_30j=demandes.nb_recent(s, g.id), retour=retour, erreur=erreur, question=question,
                        destinataire_email=destinataire_email, destinataire_nom=destinataire_nom)
        d, _ = demandes.creer_demande(s, g, question, destinataire_email, destinataire_nom)
        demande_id = d.id
    retour = retour if retour.startswith("/trous") else "/trous"
    return RedirectResponse(f"/trous/{groupe_id}?{urlencode({'retour': retour, 'message': 'demande', 'demande': demande_id})}",
                            status_code=303)


def suivi_demandes(s, groupe_id: int) -> list[dict]:
    """Pour la page de détail d'un besoin : demandes, mails et fiche éventuelle."""
    resultat = []
    for d in demandes.demandes_du_groupe(s, groupe_id):
        mail = s.scalar(select(MailEnvoye).where(MailEnvoye.demande_id == d.id).order_by(MailEnvoye.id.desc()).limit(1))
        fiche = demandes.brouillon_de(s, d)
        version = demandes.version_courante(s, fiche) if fiche else None
        resultat.append({"d": d, "mail": mail, "fiche": fiche, "version": version})
    return resultat


# ------------------------------------------------------------------ côté référent métier (lien magique)


def _demande(s, jeton: str) -> Demande:
    d = demandes.demande_par_jeton(s, jeton)
    if d is None:
        raise HTTPException(404, "Ce lien ne fonctionne pas. Il est peut-être incomplet : copiez-le en entier depuis le mail.")
    return d


def _contexte(s, d: Demande, jeton: str) -> dict:
    g = s.get(Groupe, d.groupe_id)
    return {
        "d": d, "g": g, "jeton": jeton,
        "service": s.get(Service, d.service_id) if d.service_id else None,
        "exemples": demandes.exemples(s, d.groupe_id, 4),
        "nb_30j": demandes.nb_recent(s, d.groupe_id),
        "nb_total": g.nb if g else 0,
    }


def _page_metier(request, gabarit, /, statut=200, **contexte):
    reponse = page(request, gabarit, statut=statut, **contexte)
    reponse.headers.update(EN_TETES_LIEN)
    return reponse


@routeur.get("/r/{jeton}", response_class=HTMLResponse)
def reponse_page(request: Request, jeton: str):
    with db.session() as s:
        d = _demande(s, jeton)
        contexte = _contexte(s, d, jeton)
        fiche = demandes.brouillon_de(s, d)
        if d.statut == "transferee":
            suivante = s.scalar(select(Demande).where(Demande.transferee_de == d.id))
            return _page_metier(request, "r_transferee.html", suivante=suivante, **contexte)
        if fiche and fiche.statut == "validee":
            return RedirectResponse(f"/r/{jeton}/merci", status_code=303)
        return _page_metier(request, "r_reponse.html", brouillon=fiche, **contexte)


@routeur.post("/r/{jeton}/dictee")
async def reponse_dictee(jeton: str, audio: UploadFile):
    with db.session() as s:
        _demande(s, jeton)
    contenu = await audio.read()
    if len(contenu) > 15 * 1024 * 1024:
        return JSONResponse({"erreur": "L'enregistrement est trop long. Dictez en plusieurs fois."}, status_code=413)
    try:
        texte = fournisseur().transcrire(contenu, "dictee.wav", "audio/wav")
    except ErreurFournisseur:
        return JSONResponse({"erreur": "La transcription n'a pas fonctionné. Réessayez, ou écrivez votre réponse."},
                            status_code=502)
    return JSONResponse({"texte": texte}, headers=EN_TETES_LIEN)


@routeur.post("/r/{jeton}/reponse")
def reponse_envoyer(request: Request, jeton: str, texte: str = Form(""), dictee: str = Form("")):
    with db.session() as s:
        d = _demande(s, jeton)
        if d.statut == "transferee":
            return RedirectResponse(f"/r/{jeton}", status_code=303)
        if len(texte.strip()) < 3:
            return _page_metier(request, "r_reponse.html", statut=422, brouillon=None, erreur="Écrivez ou dictez votre réponse.",
                                **_contexte(s, d, jeton))
        try:
            demandes.proposer_fiche(s, d, texte, dictee=dictee == "1")
        except ErreurFournisseur:
            return _page_metier(request, "r_reponse.html", statut=503, brouillon=None, texte=texte,
                                erreur="La mise en forme n'a pas fonctionné. Votre réponse est conservée ci-dessous : réessayez dans un instant.",
                                **_contexte(s, d, jeton))
    return RedirectResponse(f"/r/{jeton}/fiche", status_code=303)


@routeur.get("/r/{jeton}/fiche", response_class=HTMLResponse)
def fiche_relecture(request: Request, jeton: str):
    with db.session() as s:
        d = _demande(s, jeton)
        fiche = demandes.brouillon_de(s, d)
        if fiche is None:
            return RedirectResponse(f"/r/{jeton}", status_code=303)
        if fiche.statut == "validee":
            return RedirectResponse(f"/r/{jeton}/merci", status_code=303)
        v = demandes.version_courante(s, fiche)
        return _page_metier(request, "r_fiche.html", fiche=fiche, v=v, erreur="", origine=demandes.reponse_d_origine(s, d),
                            **_contexte(s, d, jeton),
                            jours_normal=demandes.duree_peremption(s, False), jours_saison=demandes.duree_peremption(s, True))


@routeur.post("/r/{jeton}/valider")
def fiche_valider(request: Request, jeton: str, valide_par: str = Form(""), titre: str = Form(""),
                  reponse: str = Form(""), conditions: str = Form(""), pieces: str = Form(""),
                  lien_demarche: str = Form(""), saisonniere: str = Form("")):
    with db.session() as s:
        d = _demande(s, jeton)
        fiche = demandes.brouillon_de(s, d)
        if fiche is None or fiche.statut == "validee":
            return RedirectResponse(f"/r/{jeton}", status_code=303)
        try:
            demandes.valider_fiche(s, fiche, valide_par, titre, reponse, conditions, pieces, lien_demarche,
                                   saisonniere == "1")
        except demandes.ValidationRefusee as e:
            v = demandes.version_courante(s, fiche)
            # On réaffiche ce que la personne a saisi, pas la proposition d'origine.
            v.titre, v.reponse, v.conditions, v.pieces, v.lien_demarche = titre, reponse, conditions, pieces, lien_demarche
            s.expunge(v)
            fiche.saisonniere = saisonniere == "1"
            return _page_metier(request, "r_fiche.html", statut=422, fiche=fiche, v=v, erreur=str(e), valide_par=valide_par,
                                origine=demandes.reponse_d_origine(s, d),
                                **_contexte(s, d, jeton), jours_normal=demandes.duree_peremption(s, False),
                                jours_saison=demandes.duree_peremption(s, True))
    return RedirectResponse(f"/r/{jeton}/merci", status_code=303)


@routeur.get("/r/{jeton}/merci", response_class=HTMLResponse)
def fiche_merci(request: Request, jeton: str):
    with db.session() as s:
        d = _demande(s, jeton)
        fiche = demandes.brouillon_de(s, d)
        if fiche is None or fiche.statut != "validee":
            return RedirectResponse(f"/r/{jeton}", status_code=303)
        return _page_metier(request, "r_merci.html", fiche=fiche, v=demandes.version_courante(s, fiche),
                            **_contexte(s, d, jeton))


@routeur.get("/r/{jeton}/transferer", response_class=HTMLResponse)
def transferer_page(request: Request, jeton: str):
    with db.session() as s:
        d = _demande(s, jeton)
        return _page_metier(request, "r_transferer.html", erreur="", **_contexte(s, d, jeton))


@routeur.post("/r/{jeton}/transferer")
def transferer(request: Request, jeton: str, email: str = Form(""), nom: str = Form(""), par: str = Form("")):
    with db.session() as s:
        d = _demande(s, jeton)
        if d.statut == "transferee":
            return RedirectResponse(f"/r/{jeton}", status_code=303)
        if "@" not in email or "." not in email.split("@")[-1]:
            return _page_metier(request, "r_transferer.html", statut=422, erreur="Indiquez l'adresse mail de votre collègue.",
                                email=email, nom=nom, par=par, **_contexte(s, d, jeton))
        if email.strip().lower() == d.destinataire_email:
            return _page_metier(request, "r_transferer.html", statut=422, erreur="C'est votre propre adresse. Indiquez celle d'un collègue.",
                                email=email, nom=nom, par=par, **_contexte(s, d, jeton))
        demandes.transferer(s, d, email, nom, par.strip() or d.destinataire_nom or d.destinataire_email)
    return RedirectResponse(f"/r/{jeton}", status_code=303)
