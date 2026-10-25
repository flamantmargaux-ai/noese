"""Les quatre espaces de Noèse : Vue d'ensemble, Explorer la mémoire, Enrichir la mémoire, Lacunes et qualité."""
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from . import analyse, db, demandes, enrichir, memoire, taches, verifications
from . import trous as trous_
from .fournisseur import ErreurFournisseur, fournisseur
from .models import Document, Fiche, Groupe, Import, Question, Service
from .web import page

routeur = APIRouter()


def _agent(request: Request) -> str:
    u = getattr(request.state, "utilisateur", None) or {}
    return u.get("nom") or u.get("email") or ""


def _service_id(valeur: str) -> int | None:
    return int(valeur) if valeur and valeur.isdigit() else None


def _commun(s) -> dict:
    return {"services": memoire.services_tries(s), "collectivite": memoire.parametres(s).collectivite,
            "demo": memoire.est_demo(s)}


# ------------------------------------------------------------------ Vue d'ensemble


@routeur.get("/", response_class=HTMLResponse)
def vue_ensemble(request: Request, service: str = "", periode: str = "tout"):
    periode = periode if periode in memoire.PERIODES else "tout"
    with db.session() as s:
        if not s.scalar(select(Question.id).limit(1)) and not s.scalar(select(Document.id).limit(1)):
            return page(request, "vue_ensemble.html", vide=True, **_commun(s))
        sv = s.get(Service, int(service)) if service.isdigit() else None
        contexte = {
            "vide": False, "filtres": {"service": service, "periode": periode}, "service_choisi": sv,
            "i": memoire.indicateurs(s, service, periode),
            "par_service": memoire.couverture_par_service(s, periode) if not service else [],
            "a_surveiller": memoire.sujets_a_surveiller(s, service, periode),
            "activite": memoire.activite_recente(s, service),
            "PERIODES": memoire.PERIODES, "PRIORITES": memoire.PRIORITES, "LIBELLES_STATUT": memoire.LIBELLES_STATUT,
            "analyse_en_cours": s.scalar(select(Import).where(Import.statut.in_(("en_attente", "en_cours"))).limit(1)),
        }
        for l in contexte["a_surveiller"]:
            l.groupe.service
        return page(request, "vue_ensemble.html", **contexte, **_commun(s))


# ------------------------------------------------------------------ Explorer la mémoire


@routeur.get("/memoire", response_class=HTMLResponse)
def explorer(request: Request, q: str = "", service: str = "", sujet: str = "", statut: str = "", vue: str = "connaissances"):
    vue = vue if vue in ("connaissances", "sources") else "connaissances"
    statut = statut if statut in memoire.STATUTS_CONNAISSANCE else ""
    with db.session() as s:
        ids_fiches = ids_docs = None
        if q.strip():
            ids_fiches, ids_docs = memoire.recherche(s, q)
        connaissances = memoire.lister_connaissances(s, q, service, sujet, statut, ids_fiches)
        sources = memoire.lister_sources(s, q, service, sujet, ids_docs)
        contexte = {
            "filtres": {"q": q.strip(), "service": service, "sujet": sujet, "statut": statut, "vue": vue},
            "connaissances": connaissances, "sources": sources,
            "sujets": memoire.sujets(s, _service_id(service)),
            "STATUTS": memoire.STATUTS_CONNAISSANCE,
        }
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultats":
            return page(request, "_memoire_resultats.html", **contexte, **_commun(s))
        return page(request, "memoire.html", **contexte, **_commun(s))


def _fiche(s, fiche_id: int) -> Fiche:
    f = s.get(Fiche, fiche_id)
    if f is None:
        raise HTTPException(404, "Cette connaissance n'existe pas.")
    return f


@routeur.get("/memoire/connaissances/{fiche_id}", response_class=HTMLResponse)
def connaissance(request: Request, fiche_id: int, message: str = "", edition: int = 0, retour: str = ""):
    from . import fiches

    with db.session() as s:
        f = _fiche(s, fiche_id)
        f.service
        v = memoire.version_affichee(s, f)
        doc = s.get(Document, f.document_id) if f.document_id else None
        g = s.get(Groupe, f.groupe_id) if f.groupe_id else None
        reponse_service = demandes.reponse_d_origine(s, s.get(demandes.Demande, f.demande_id)) if f.demande_id else ""
        return page(request, "connaissance.html", f=f, v=v, etat=memoire.etat_connaissance(s, f), doc=doc, g=g,
                    provenance=memoire.provenance(s, f, v), reponse_service=reponse_service,
                    ouverte=verifications.verification_ouverte(s, f), message=message, edition=edition,
                    retour=retour if retour.startswith("/enrichir/sources/") else "",
                    sujets=memoire.sujets(s), SIGNALEMENTS=memoire.SIGNALEMENTS, agent=_agent(request),
                    jours_normal=demandes.duree_peremption(s, False), jours_saison=demandes.duree_peremption(s, True),
                    **fiches.historique(s, f), **_commun(s))


def _vers_connaissance(fiche_id: int, message: str) -> RedirectResponse:
    return RedirectResponse(f"/memoire/connaissances/{fiche_id}?message={message}", status_code=303)


@routeur.post("/memoire/connaissances/{fiche_id}/valider", response_class=HTMLResponse)
def connaissance_valider(request: Request, fiche_id: int, valide_par: str = Form(""), titre: str = Form(""),
                         reponse: str = Form(""), conditions: str = Form(""), pieces: str = Form(""),
                         lien_demarche: str = Form(""), saisonniere: str = Form(""), sujet: str = Form(""),
                         service_id: str = Form(""), retour: str = Form("")):
    """Valide une proposition, ou enregistre une correction : dans les deux cas, une nouvelle version signée."""
    with db.session() as s:
        f = _fiche(s, fiche_id)
        etait_proposee = f.statut == "brouillon"
        try:
            enrichir.valider(s, f, valide_par, sujet=sujet, service_id=_service_id(service_id), titre=titre, reponse=reponse,
                             conditions=conditions, pieces=pieces, lien_demarche=lien_demarche, saisonniere=saisonniere == "1")
            if f.signalee_le:  # une correction lève le signalement
                f.signalee_le, f.signalement, f.signalement_type, f.signalee_par = None, "", "", ""
        except demandes.ValidationRefusee as e:
            s.rollback()
            cible = retour if retour.startswith("/enrichir/sources/") else f"/memoire/connaissances/{fiche_id}"
            separateur = "&" if "?" in cible else "?"
            return RedirectResponse(f"{cible}{separateur}{urlencode({'erreur': str(e), 'fiche': fiche_id, 'edition': 1})}", status_code=303)
    if retour.startswith("/enrichir/sources/"):
        return RedirectResponse(f"{retour}{'&' if '?' in retour else '?'}message=validee", status_code=303)
    return _vers_connaissance(fiche_id, "validee" if etait_proposee else "corrigee")


@routeur.post("/memoire/connaissances/{fiche_id}/ecarter")
def connaissance_ecarter(request: Request, fiche_id: int, retour: str = Form("")):
    with db.session() as s:
        enrichir.ecarter(s, _fiche(s, fiche_id), _agent(request))
    if retour.startswith("/enrichir/sources/"):
        return RedirectResponse(f"{retour}{'&' if '?' in retour else '?'}message=ecartee", status_code=303)
    return _vers_connaissance(fiche_id, "ecartee")


@routeur.post("/memoire/connaissances/{fiche_id}/signaler")
def connaissance_signaler(request: Request, fiche_id: int, type_: str = Form("autre"), description: str = Form("")):
    from datetime import datetime

    from .models import Evenement

    with db.session() as s:
        f = _fiche(s, fiche_id)
        f.signalement_type = type_ if type_ in memoire.SIGNALEMENTS else "autre"
        f.signalement, f.signalee_par, f.signalee_le = description.strip(), _agent(request), datetime.now()
        s.add(Evenement(type="fiche_signalee", objet="fiche", objet_id=f.id, acteur=_agent(request),
                        details={"type": f.signalement_type}))
    return _vers_connaissance(fiche_id, "signalee")


@routeur.post("/memoire/connaissances/{fiche_id}/lever")
def connaissance_lever(request: Request, fiche_id: int):
    from .models import Evenement

    with db.session() as s:
        f = _fiche(s, fiche_id)
        f.signalee_le, f.signalement, f.signalement_type, f.signalee_par = None, "", "", ""
        s.add(Evenement(type="signalement_leve", objet="fiche", objet_id=f.id, acteur=_agent(request)))
    return _vers_connaissance(fiche_id, "levee")


@routeur.post("/memoire/connaissances/{fiche_id}/classer")
def connaissance_classer(request: Request, fiche_id: int, service_id: str = Form(""), sujet: str = Form("")):
    from . import fiches

    with db.session() as s:
        f = _fiche(s, fiche_id)
        if (_service_id(service_id) or None) != f.service_id:
            fiches.changer_proprietaire(s, f, _service_id(service_id))
        f.sujet = memoire.normaliser_sujet(sujet)
    return _vers_connaissance(fiche_id, "classee")


@routeur.post("/memoire/connaissances/{fiche_id}/verifier")
def connaissance_verifier(fiche_id: int):
    with db.session() as s:
        f = _fiche(s, fiche_id)
        v = verifications.demander_verification(s, f)
        message = "verification" if v else ("deja" if verifications.verification_ouverte(s, f) else "sans_destinataire")
    return _vers_connaissance(fiche_id, message)


# ------------------------------------------------------------------ Sources


def _document(s, document_id: int) -> Document:
    d = s.get(Document, document_id)
    if d is None:
        raise HTTPException(404, "Cette source n'existe pas, ou elle a été retirée.")
    return d


def _contexte_source(s, d: Document, request: Request) -> dict:
    d.service
    return {"d": d, "nature": memoire.NATURES.get(d.type, "Source"),
            "etat": memoire.TRAITEMENTS.get(d.traitement, memoire.TRAITEMENTS["consultable"]),
            "propositions": [(f, memoire.version_affichee(s, f), memoire.etat_connaissance(s, f)) for f in enrichir.propositions(s, d)],
            "besoins": memoire.besoins_citant(s, d.id), "sujets": memoire.sujets(s), "agent": _agent(request),
            "jours_normal": demandes.duree_peremption(s, False), "jours_saison": demandes.duree_peremption(s, True),
            **_commun(s)}


@routeur.get("/memoire/sources/{document_id}", response_class=HTMLResponse)
def source(request: Request, document_id: int, message: str = "", erreur: str = ""):
    with db.session() as s:
        return page(request, "source.html", message=message, erreur=erreur, parcours=False,
                    **_contexte_source(s, _document(s, document_id), request))


@routeur.get("/enrichir/sources/{document_id}", response_class=HTMLResponse)
def source_relecture(request: Request, document_id: int, message: str = "", erreur: str = "", lacune: str = ""):
    with db.session() as s:
        g = s.get(Groupe, int(lacune)) if lacune.isdigit() else None
        return page(request, "source.html", message=message, erreur=erreur, parcours=True, lacune=g,
                    **_contexte_source(s, _document(s, document_id), request))


@routeur.post("/memoire/sources/{document_id}/proposer")
def source_proposer(document_id: int, lacune: str = Form(""), retour: str = Form("")):
    cible = retour if retour.startswith(("/enrichir/sources/", "/memoire/sources/")) else f"/memoire/sources/{document_id}"
    with db.session() as s:
        d = _document(s, document_id)
        g = s.get(Groupe, int(lacune)) if lacune.isdigit() else None
        try:
            fiches = enrichir.proposer(s, d, g)
        except ErreurFournisseur:
            return RedirectResponse(f"{cible}?{urlencode({'erreur': 'proposition', 'lacune': lacune})}", status_code=303)
    return RedirectResponse(f"{cible}?{urlencode({'message': 'propose' if fiches else 'aucune', 'lacune': lacune})}", status_code=303)


@routeur.post("/memoire/sources/{document_id}/classer")
def source_classer(document_id: int, service_id: str = Form(""), sujet: str = Form(""), retour: str = Form("")):
    with db.session() as s:
        d = _document(s, document_id)
        d.service_id, d.sujet = _service_id(service_id), memoire.normaliser_sujet(sujet)
        for f in enrichir.propositions(s, d):
            if f.statut == "brouillon":
                f.service_id, f.sujet = d.service_id, d.sujet
    cible = retour if retour.startswith(("/enrichir/sources/", "/memoire/sources/")) else f"/memoire/sources/{document_id}"
    return RedirectResponse(f"{cible}?message=classee", status_code=303)


@routeur.post("/memoire/sources/{document_id}/retirer")
def source_retirer(request: Request, document_id: int):
    from .models import Evenement

    with db.session() as s:
        d = _document(s, document_id)
        titre = d.titre
        for f in enrichir.propositions(s, d):
            if f.statut == "brouillon":
                f.statut = "archivee"
        s.add(Evenement(type="source_retiree", objet="document", objet_id=d.id, acteur=_agent(request), details={"titre": titre}))
        s.delete(d)
    return RedirectResponse(f"/memoire?vue=sources&{urlencode({'retiree': titre})}", status_code=303)


# ------------------------------------------------------------------ Enrichir la mémoire

MODES = {
    "document": "Importer un document",
    "page": "Ajouter une page web",
    "oral": "Transmettre un savoir oral",
    "saisie": "Ajouter une connaissance",
}


def _lacune(s, valeur: str) -> Groupe | None:
    g = s.get(Groupe, int(valeur)) if valeur and valeur.isdigit() else None
    if g:
        g.service
    return g


@routeur.get("/enrichir", response_class=HTMLResponse)
def enrichir_accueil(request: Request, lacune: str = ""):
    with db.session() as s:
        recentes = memoire.lister_sources(s)[:6]
        return page(request, "enrichir.html", lacune=_lacune(s, lacune), recentes=recentes, MODES=MODES, **_commun(s))


@routeur.get("/enrichir/bilan", response_class=HTMLResponse)
def enrichir_bilan(request: Request, ids: str = "", erreurs: str = "", lacune: str = ""):
    with db.session() as s:
        liste = [int(i) for i in ids.split(",") if i.isdigit()]
        return page(request, "enrichir_bilan.html", sources=memoire.lister_sources(s, ids=liste), erreurs=erreurs,
                    lacune=_lacune(s, lacune), **_commun(s))


@routeur.get("/enrichir/{mode}", response_class=HTMLResponse)
def enrichir_mode(request: Request, mode: str, lacune: str = ""):
    if mode == "questions":
        return enrichir_questions(request)
    if mode not in MODES:
        raise HTTPException(404, "Cette façon d'enrichir la mémoire n'existe pas.")
    with db.session() as s:
        g = _lacune(s, lacune)
        return page(request, "enrichir_mode.html", mode=mode, titre_mode=MODES[mode], lacune=g, erreur="", saisie={},
                    sujets=memoire.sujets(s), MODES=MODES, **_commun(s))


def _erreur_mode(request, s, mode: str, erreur: str, lacune: str, saisie: dict, statut: int = 422, apercu: dict | None = None):
    return page(request, "enrichir_mode.html", statut=statut, mode=mode, titre_mode=MODES[mode], lacune=_lacune(s, lacune),
                erreur=erreur, saisie=saisie, apercu=apercu, sujets=memoire.sujets(s), MODES=MODES, **_commun(s))


def _apres_source(s, doc: Document, lacune: str, proposer: bool = True) -> RedirectResponse:
    """Étapes 3 à 5 : la source est enregistrée, on propose des connaissances puis on les fait relire."""
    message = "enregistree"
    if proposer:
        try:
            fiches = enrichir.proposer(s, doc, _lacune(s, lacune))
            message = "propose" if fiches else "aucune"
        except ErreurFournisseur:
            message = "sans_proposition"
    return RedirectResponse(f"/enrichir/sources/{doc.id}?{urlencode({'message': message, 'lacune': lacune})}", status_code=303)


@routeur.post("/enrichir/document", response_class=HTMLResponse)
async def enrichir_document(request: Request, fichiers: list[UploadFile] | None = None, service_id: str = Form(""),
                            sujet: str = Form(""), lacune: str = Form("")):
    lus, erreurs = [], []
    for f in fichiers or []:
        if not f.filename:
            continue
        try:
            lus.append((f.filename, *enrichir.lire_fichier(f.filename, await f.read())))
        except enrichir.ErreurSource as e:
            erreurs.append(str(e))
    saisie = {"service_id": service_id, "sujet": sujet}
    with db.session() as s:
        if not lus:
            return _erreur_mode(request, s, "document", " ".join(erreurs) or "Choisissez au moins un fichier.", lacune, saisie)
        docs = [enrichir.enregistrer_source(s, type_, titre, texte, url=f"fichier:{nom}", service_id=_service_id(service_id),
                                            sujet=sujet, ajoute_par=_agent(request)) for nom, type_, titre, texte in lus]
        if len(docs) == 1 and not erreurs:
            return _apres_source(s, docs[0], lacune)
        # Plusieurs documents : ils sont enregistrés et consultables ; les propositions se font source par source.
        ids = ",".join(str(d.id) for d in docs)
    return RedirectResponse(f"/enrichir/bilan?{urlencode({'ids': ids, 'erreurs': ' '.join(erreurs), 'lacune': lacune})}", status_code=303)


@routeur.post("/enrichir/page", response_class=HTMLResponse)
def enrichir_page(request: Request, url: str = Form(""), service_id: str = Form(""), sujet: str = Form(""),
                  lacune: str = Form(""), confirmer: str = Form(""), avec_site: str = Form("")):
    saisie = {"url": url, "service_id": service_id, "sujet": sujet}
    with db.session() as s:
        try:
            apercu = enrichir.recuperer_page(url)
        except enrichir.ErreurSource as e:
            return _erreur_mode(request, s, "page", str(e), lacune, saisie)
        if not confirmer:  # étape « afficher l'adresse avant validation »
            return page(request, "enrichir_mode.html", mode="page", titre_mode=MODES["page"], lacune=_lacune(s, lacune),
                        erreur="", saisie=saisie, apercu=apercu, sujets=memoire.sujets(s), MODES=MODES, **_commun(s))
        doc = enrichir.enregistrer_source(s, "url", apercu["titre"], apercu["texte"], url=apercu["url"],
                                          service_id=_service_id(service_id), sujet=sujet, ajoute_par=_agent(request))
        if avec_site:
            imp = Import(type="documents", libelle=f"Pages liées à {apercu['url']}")
            s.add(imp)
            s.flush()
            taches.planifier(s, "crawler", import_id=imp.id, url=apercu["url"])
        return _apres_source(s, doc, lacune)


@routeur.post("/enrichir/oral/transcrire")
async def enrichir_transcrire(audio: UploadFile):
    contenu = await audio.read()
    if len(contenu) > enrichir.TAILLE_MAX:
        return JSONResponse({"erreur": "L'enregistrement est trop long. Transmettez-le en plusieurs fois."}, status_code=413)
    try:
        texte = fournisseur().transcrire(contenu, audio.filename or "enregistrement.wav", audio.content_type or "audio/wav")
    except ErreurFournisseur:
        return JSONResponse({"erreur": "La transcription n'a pas fonctionné. Réessayez, ou écrivez vos notes."}, status_code=502)
    return JSONResponse({"texte": texte})


@routeur.post("/enrichir/oral", response_class=HTMLResponse)
async def enrichir_oral(request: Request, titre: str = Form(""), texte: str = Form(""), notes: str = Form(""),
                        audio: UploadFile | None = None, service_id: str = Form(""), sujet: str = Form(""),
                        lacune: str = Form("")):
    saisie = {"titre": titre, "texte": texte, "notes": notes, "service_id": service_id, "sujet": sujet}
    transcription = texte.strip()
    with db.session() as s:
        if audio is not None and audio.filename:
            if not audio.filename.lower().endswith(enrichir.FORMATS_AUDIO):
                return _erreur_mode(request, s, "oral", "Ce fichier audio n'est pas d'un format pris en charge "
                                    "(WAV, MP3, M4A, OGG, WebM ou FLAC).", lacune, saisie)
            try:
                transcription = (transcription + "\n\n" if transcription else "") + fournisseur().transcrire(
                    await audio.read(), audio.filename, audio.content_type or "audio/mpeg")
            except ErreurFournisseur:
                return _erreur_mode(request, s, "oral", "La transcription du fichier audio n'a pas fonctionné. Réessayez, "
                                    "ou écrivez ce qui a été dit dans le champ prévu.", lacune, saisie, statut=502)
        contenu = "\n\n".join(x for x in (transcription, notes.strip()) if x)
        if len(contenu) < 10:
            return _erreur_mode(request, s, "oral", "Enregistrez, importez un fichier audio, ou écrivez ce qui a été dit.", lacune, saisie)
        doc = enrichir.enregistrer_source(s, "oral", titre.strip() or f"Savoir oral transmis par {_agent(request) or 'un agent'}",
                                          contenu, service_id=_service_id(service_id), sujet=sujet, ajoute_par=_agent(request),
                                          note=notes)
        return _apres_source(s, doc, lacune)


@routeur.post("/enrichir/saisie", response_class=HTMLResponse)
def enrichir_saisie(request: Request, nature: str = Form("regle"), titre: str = Form(""), texte: str = Form(""),
                    service_id: str = Form(""), sujet: str = Form(""), lacune: str = Form("")):
    saisie = {"nature": nature, "titre": titre, "texte": texte, "service_id": service_id, "sujet": sujet}
    natures = {"regle": "Règle ou procédure", "reponse": "Réponse à une question", "changement": "Information manquante ou modifiée"}
    with db.session() as s:
        if len(texte.strip()) < 10:
            return _erreur_mode(request, s, "saisie", "Décrivez la connaissance en quelques mots.", lacune, saisie)
        doc = enrichir.enregistrer_source(s, "saisie", titre.strip() or f"{natures.get(nature, 'Saisie')} : {texte.strip()[:60]}",
                                          texte, service_id=_service_id(service_id), sujet=sujet, ajoute_par=_agent(request),
                                          note=natures.get(nature, ""))
        return _apres_source(s, doc, lacune)


def enrichir_questions(request: Request):
    from .main import _contexte_import

    with db.session() as s:
        return page(request, "enrichir_questions.html", message=request.query_params.get("message", ""),
                    **_contexte_import(s), **_commun(s))


# ------------------------------------------------------------------ Lacunes et qualité


@routeur.get("/lacunes", response_class=HTMLResponse)
def lacunes(request: Request, vue: str = "questions", service: str = "", sujet: str = "", statut: str = "a_traiter",
            priorite: str = "", type_: str = "", q: str = ""):
    vue = vue if vue in ("questions", "qualite") else "questions"
    statut = statut if statut in memoire.STATUTS_LACUNE else "a_traiter"
    with db.session() as s:
        contexte = {
            "filtres": {"vue": vue, "service": service, "sujet": sujet, "statut": statut, "priorite": priorite, "type_": type_, "q": q.strip()},
            "resume": memoire.resume_lacunes(s, service),
            "lignes": memoire.lister_lacunes(s, service, sujet, statut, priorite, type_, q) if vue == "questions" else [],
            "qualite": memoire.qualite_connaissances(s, service) if vue == "qualite" else {},
            "sujets": memoire.sujets(s, _service_id(service)),
            "STATUTS": memoire.STATUTS_LACUNE, "PRIORITES": memoire.PRIORITES, "TYPES": memoire.TYPES_LACUNE,
            "LIBELLES_STATUT": memoire.LIBELLES_STATUT, "REGLE_PRIORITE": memoire.REGLE_PRIORITE,
            "SIGNALEMENTS": memoire.SIGNALEMENTS,
        }
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultats":
            return page(request, "_lacunes_resultats.html", **contexte, **_commun(s))
        return page(request, "lacunes.html", **contexte, **_commun(s))


@routeur.get("/lacunes/{groupe_id}", response_class=HTMLResponse)
def lacune(request: Request, groupe_id: int, retour: str = "", message: str = ""):
    from . import pages_demandes

    with db.session() as s:
        d = trous_.detail(s, groupe_id)
        if d is None:
            raise HTTPException(404, "Ce besoin n'existe plus. Une nouvelle analyse l'a peut-être regroupé avec un autre.")
        g = d.groupe
        g.service
        stats = memoire.stats_besoins(s).get(g.id)
        niveau, calculee = memoire.priorite(g, stats)
        sources_doc = {}
        for src in g.sources or []:
            e = s.get(analyse.Extrait, src.get("extrait_id")) if src.get("extrait_id") else None
            if e:
                sources_doc[src["n"]] = (f"/memoire/connaissances/{e.fiche_id}" if e.fiche_id else
                                         f"/memoire/sources/{e.document_id}" if e.document_id else "")
        fiches = memoire.lister_connaissances(s, ids=[f.id for f in s.scalars(select(Fiche).where(Fiche.groupe_id == g.id))])
        return page(request, "lacune.html", d=d, g=g, priorite=niveau, priorite_calculee=calculee, liens_sources=sources_doc,
                    connaissances=fiches, retour=retour if retour.startswith("/lacunes") else "/lacunes", message=message,
                    candidats=trous_.candidats_rassemblement(s, g), suivi=pages_demandes.suivi_demandes(s, groupe_id),
                    PRIORITES=memoire.PRIORITES, REGLE_PRIORITE=memoire.REGLE_PRIORITE, LIBELLES_STATUT=memoire.LIBELLES_STATUT,
                    TYPES=memoire.TYPES_LACUNE, sujets=memoire.sujets(s), **_commun(s))


@routeur.post("/lacunes/{groupe_id}/priorite")
def lacune_priorite(request: Request, groupe_id: int, priorite: str = Form(""), retour: str = Form("")):
    from .models import Evenement

    with db.session() as s:
        g = s.get(Groupe, groupe_id)
        if g is None:
            raise HTTPException(404, "Ce besoin n'existe plus.")
        g.priorite_manuelle = priorite if priorite in memoire.PRIORITES else None
        s.add(Evenement(type="lacune_priorite", objet="groupe", objet_id=g.id, acteur=_agent(request), details={"priorite": priorite}))
    return RedirectResponse(f"/lacunes/{groupe_id}?{urlencode({'retour': retour, 'message': 'priorite'})}", status_code=303)


@routeur.post("/lacunes/{groupe_id}/sujet")
def lacune_sujet(request: Request, groupe_id: int, sujet: str = Form(""), retour: str = Form("")):
    from .models import Evenement

    with db.session() as s:
        g = s.get(Groupe, groupe_id)
        if g is None:
            raise HTTPException(404, "Ce besoin n'existe plus.")
        avant = g.sujet
        g.sujet, g.sujet_fixe = memoire.normaliser_sujet(sujet), bool(sujet.strip())
        if not g.sujet_fixe:
            memoire.calculer_sujets(s)
        s.add(Evenement(type="lacune_sujet", objet="groupe", objet_id=g.id, acteur=_agent(request), details={"avant": avant, "apres": g.sujet}))
    return RedirectResponse(f"/lacunes/{groupe_id}?{urlencode({'retour': retour, 'message': 'sujet'})}", status_code=303)


@routeur.post("/lacunes/{groupe_id}/cloturer")
def lacune_cloturer(request: Request, groupe_id: int, retour: str = Form("")):
    """« Vérifier et clore » : la lacune n'est résolue que si la mémoire couvre désormais le besoin."""
    from .models import Evenement

    with db.session() as s:
        g = s.get(Groupe, groupe_id)
        if g is None:
            raise HTTPException(404, "Ce besoin n'existe plus.")
        analyse.juger_groupe(s, g)
        if g.verdict == "couvert":
            g.statut = "resolu"
            s.add(Evenement(type="lacune_resolue", objet="groupe", objet_id=g.id, acteur=_agent(request)))
            message = "resolue"
        else:
            message = "pas_encore"
    return RedirectResponse(f"/lacunes/{groupe_id}?{urlencode({'retour': retour, 'message': message})}", status_code=303)


# ------------------------------------------------------------------ anciennes adresses


@routeur.get("/trous")
def ancien_trous(request: Request):
    return RedirectResponse("/lacunes" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)


@routeur.get("/trous/{groupe_id}")
def ancien_trou(groupe_id: int):
    return RedirectResponse(f"/lacunes/{groupe_id}", status_code=301)


@routeur.get("/fiches")
def ancien_fiches():
    return RedirectResponse("/memoire", status_code=301)


@routeur.get("/fiches/{fiche_id}")
def ancienne_fiche(fiche_id: int):
    return RedirectResponse(f"/memoire/connaissances/{fiche_id}", status_code=301)


@routeur.get("/import")
def ancien_import():
    return RedirectResponse("/enrichir/questions", status_code=301)
