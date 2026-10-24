"""Application web : pages rendues côté serveur + HTMX."""
import html
import os
import re
from urllib.parse import urlencode
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, select

from . import analyse, connexion, db, demo, echeances, export, ingestion, tableau_de_bord, taches
from . import fiches as fiches_
from . import pages_demandes, pages_fiches
from . import trous as trous_
from .config import RACINE, config
from .web import page
from .models import Document, Fiche, Groupe, Import, Question, Service

ICI = RACINE / "app"


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    db.initialiser()
    with db.session() as s:
        demo.semer_services(s)
        connexion.semer_premier_referent(s)
    arret = None
    if os.environ.get("NOESE_WORKER_INTEGRE", "1" if not config.database_url else "0") == "1":
        arret = taches.demarrer_en_arriere_plan()
    yield
    if arret:
        arret.set()


app = FastAPI(title="Noèse", lifespan=cycle_de_vie, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ICI / "static"), name="static")
app.include_router(pages_demandes.routeur)
app.include_router(pages_fiches.routeur)


@app.middleware("http")
async def exiger_connexion(request: Request, call_next):
    """Les pages du référent base demandent une connexion ; celles des services ont leur propre lien."""
    if connexion.est_public(request.url.path):
        request.state.utilisateur = None
        return await call_next(request)
    with db.session() as s:
        u = connexion.utilisateur_de_session(s, request.session)
        request.state.utilisateur = {"id": u.id, "email": u.email, "nom": u.nom} if u else None
    if u is None:
        request.session.pop("utilisateur_id", None)
        suite = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        cible = f"/connexion?{urlencode({'suite': suite})}"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": cible})
        return RedirectResponse(cible, status_code=303)
    return await call_next(request)


# La session doit envelopper la vérification de connexion : ajoutée après, elle s'exécute avant.
app.add_middleware(SessionMiddleware, secret_key=config.secret, session_cookie="noese_session",
                   max_age=connexion.DUREE_SESSION, same_site="lax", https_only=config.base_url.startswith("https://"))


@app.middleware("http")
async def head_comme_get(request: Request, call_next):
    """Répond aux requêtes HEAD (sondes de disponibilité) comme à un GET, sans corps."""
    if request.method != "HEAD":
        return await call_next(request)
    request.scope["method"] = "GET"
    reponse = await call_next(request)
    en_tetes = {k: v for k, v in reponse.headers.items() if k.lower() != "content-length"}
    return Response(status_code=reponse.status_code, headers=en_tetes)


@app.exception_handler(404)
async def introuvable(request: Request, exc):
    detail = getattr(exc, "detail", "") or "L'adresse est peut-être incomplète."
    if detail == "Not Found":
        detail = "L'adresse est peut-être incomplète."
    return page(request, "erreur.html", statut=404, detail=detail)


# ------------------------------------------------------------------ connexion


@app.get("/connexion", response_class=HTMLResponse)
def connexion_page(request: Request, suite: str = "/", message: str = ""):
    with db.session() as s:
        if connexion.utilisateur_de_session(s, request.session):
            return RedirectResponse(connexion.suite_sure(suite), status_code=303)
        aucun = not connexion.referents(s)
    request.session["suite"] = connexion.suite_sure(suite)
    return page(request, "connexion.html", etat="formulaire", message=message, aucun_referent=aucun,
                mode_journal=connexion.mode_journal())


@app.post("/connexion", response_class=HTMLResponse)
def connexion_demander(request: Request, email: str = Form("")):
    with db.session() as s:
        lien_dev = connexion.demander_lien(s, email)
    return page(request, "connexion.html", etat="envoye", email=email.strip(), lien_dev=lien_dev,
                mode_journal=connexion.mode_journal())


@app.get("/connexion/{jeton}")
def connexion_lien(request: Request, jeton: str):
    with db.session() as s:
        u = connexion.consommer(s, jeton)
        uid = u.id if u else None
    if uid is None:
        return page(request, "connexion.html", statut=400, etat="lien_invalide", mode_journal=connexion.mode_journal())
    suite = connexion.suite_sure(request.session.pop("suite", "/"))
    request.session.clear()
    request.session["utilisateur_id"] = uid
    return RedirectResponse(suite, status_code=303)


@app.post("/deconnexion")
def deconnexion(request: Request):
    request.session.clear()
    return RedirectResponse("/connexion?message=deconnecte", status_code=303)


# ------------------------------------------------------------------ accueil


@app.get("/", response_class=HTMLResponse)
def accueil(request: Request):
    with db.session() as s:
        bilan = analyse.bilan(s)
        trous = s.scalars(
            select(Groupe).where(Groupe.verdict.in_(("trou", "partiel")), Groupe.statut == "ouvert")
            .order_by(Groupe.nb.desc()).limit(5)
        ).all()
        for g in trous:
            g.service  # chargement avant fermeture de la session
        en_cours = s.scalar(select(Import).where(Import.statut.in_(("en_attente", "en_cours"))).limit(1))
        suivre = echeances.a_suivre(s)
        signales = [{"titre": d.question, "lien": f"/trous/{d.groupe_id}", "qui": d.destinataire_nom or d.destinataire_email,
                     "depuis": d.envoyee_le, "nature": "Demande sans réponse"} for d in suivre["demandes"]]
        for v in suivre["verifications"]:
            version = fiches_.version_validee(s, s.get(Fiche, v.fiche_id))
            signales.append({"titre": version.titre if version else "Fiche", "lien": f"/fiches/{v.fiche_id}",
                             "qui": v.destinataire_nom or v.destinataire_email, "depuis": v.envoyee_le,
                             "nature": "Vérification sans réponse"})
        c = tableau_de_bord.chiffres(s)
        services = tableau_de_bord.par_service(s)
        for l in services:
            l.service.nom  # chargé avant la fermeture de la session
    return page(request, "accueil.html", bilan=bilan, trous=trous, en_cours=en_cours, signales=signales, c=c,
                delai=tableau_de_bord.chiffre_delai(c["delai_median"]), services=services,
                formuler_delai=tableau_de_bord.formuler_delai)


# ------------------------------------------------------------------ import


def _contexte_import(s) -> dict:
    return {
        "imports": s.scalars(select(Import).order_by(Import.id.desc()).limit(8)).all(),
        "nb_questions": s.scalar(select(func.count(Question.id))),
        "nb_documents": s.scalar(select(func.count(Document.id))),
    }


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, message: str = ""):
    with db.session() as s:
        return page(request, "import.html", message=message, **_contexte_import(s))


@app.post("/import/questions", response_class=HTMLResponse)
async def import_questions(request: Request, fichiers: list[UploadFile] | None = None, collage: str = Form(""),
                           csv_texte: str = Form(""), colonne_question: str = Form(""),
                           colonne_date: str = Form(""), colonne_reponse: str = Form(""),
                           colonne_transfert: str = Form("")):
    questions: list[ingestion.QuestionLue] = []
    noms: list[str] = []
    csv_a_associer = None

    def lire_csv(contenu: bytes, nom: str, colonnes=None):
        nonlocal csv_a_associer
        entetes, lignes = ingestion.lire_csv(contenu)
        colonnes = colonnes or ingestion.deviner_colonnes(entetes)
        if not colonnes.get("question"):
            csv_a_associer = {"nom": nom, "entetes": entetes, "texte": contenu.decode("utf-8-sig", "replace"),
                              "devine": colonnes}
            return
        questions.extend(ingestion.questions_depuis_csv(lignes, colonnes))
        noms.append(nom)

    if csv_texte:
        lire_csv(csv_texte.encode(), "CSV", {
            "question": colonne_question or None, "date": colonne_date or None,
            "reponse_donnee": colonne_reponse or None, "transfere": colonne_transfert or None, "canal": None,
        })
    for f in fichiers or []:
        if not f.filename:
            continue
        contenu = await f.read()
        nom = f.filename.lower()
        if nom.endswith((".csv", ".tsv")):
            lire_csv(contenu, f.filename)
        elif nom.endswith(".eml"):
            questions += ingestion.questions_depuis_eml(contenu)
            noms.append(f.filename)
        elif nom.endswith(".mbox"):
            questions += ingestion.questions_depuis_mbox(contenu)
            noms.append(f.filename)
        elif nom.endswith(".txt"):
            questions += ingestion.questions_depuis_collage(contenu.decode("utf-8", "replace"))
            noms.append(f.filename)
    if collage.strip():
        questions += ingestion.questions_depuis_collage(collage)
        noms.append("copier-coller")

    with db.session() as s:
        if csv_a_associer:
            return page(request, "import_colonnes.html", csv=csv_a_associer)
        if not questions:
            return RedirectResponse("/import?message=aucune", status_code=303)
        libelle = ", ".join(n for n in noms if not n.endswith(".eml"))
        nb_mails = sum(n.endswith(".eml") for n in noms)
        if nb_mails:
            libelle = ", ".join(x for x in (libelle, f"{nb_mails} mail{'s' if nb_mails > 1 else ''}") if x)
        imp = Import(type="questions", libelle=f"Questions : {libelle}"[:300])
        s.add(imp)
        s.flush()
        n = analyse.enregistrer_questions(s, imp.id, questions)
        imp.journal = f"{n} questions nouvelles sur {len(questions)} lues."
        taches.planifier(s, "analyser", import_id=imp.id)
    return RedirectResponse("/import", status_code=303)


@app.post("/import/documents", response_class=HTMLResponse)
async def import_documents(request: Request, fichiers: list[UploadFile] | None = None, url: str = Form("")):
    with db.session() as s:
        imp = Import(type="documents", libelle="Base existante")
        s.add(imp)
        s.flush()
        noms, journal = [], []
        for f in fichiers or []:
            if not f.filename:
                continue
            contenu = await f.read()
            nom = f.filename
            fiches_exportees = export.lire(nom, contenu)
            if fiches_exportees is not None:
                r = export.importer(s, fiches_exportees, nom)
                journal.append(f"{nom} : {r['importees']} fiches importées, {r['deja_presentes']} déjà présentes"
                               + (f", {r['refusees']} refusées faute de validation" if r["refusees"] else "") + ".")
            elif nom.lower().endswith(".pdf"):
                texte = ingestion.texte_depuis_pdf(contenu)
                analyse.enregistrer_document(s, imp.id, "pdf", nom.rsplit(".", 1)[0], f"fichier:{nom}", texte)
            elif nom.lower().endswith((".md", ".markdown", ".txt")):
                texte = contenu.decode("utf-8", "replace")
                type_ = "md" if not nom.lower().endswith(".txt") else "txt"
                analyse.enregistrer_document(s, imp.id, type_, ingestion.titre_markdown(texte, nom.rsplit(".", 1)[0]),
                                             f"fichier:{nom}", texte)
            else:
                continue
            noms.append(nom)
        url = url.strip()
        if url:
            if not re.match(r"^https?://", url):
                url = "https://" + url
            noms.append(url)
            taches.planifier(s, "crawler", import_id=imp.id, url=url)
        elif noms:
            taches.planifier(s, "analyser", import_id=imp.id)
        if not noms:
            s.rollback()
            return RedirectResponse("/import?message=aucune", status_code=303)
        imp.libelle = f"Base : {', '.join(noms)}"[:300]
        imp.journal = " ".join(journal)
    return RedirectResponse("/import", status_code=303)


@app.post("/import/demo")
def import_demo():
    with db.session() as s:
        imp = demo.charger_tout(s)
        taches.planifier(s, "analyser", import_id=imp.id)
    return RedirectResponse("/import", status_code=303)


@app.get("/import/suivi", response_class=HTMLResponse)
def import_suivi(request: Request):
    with db.session() as s:
        return page(request, "_suivi.html", **_contexte_import(s))


# ------------------------------------------------------------------ trous


def _filtres(service: str, statut: str, q: str) -> dict:
    return {"service": service, "statut": statut if statut in trous_.STATUTS else "a_traiter", "q": q.strip()}


@app.get("/trous", response_class=HTMLResponse)
def trous(request: Request, service: str = "", statut: str = "a_traiter", q: str = ""):
    filtres = _filtres(service, statut, q)
    with db.session() as s:
        contexte = {
            "lignes": trous_.lister(s, filtres["service"], filtres["statut"], filtres["q"]),
            "comptes": trous_.compter_par_statut(s, filtres["service"], filtres["q"]),
            "services": trous_.services_avec_compte(s),
            "sans_service": trous_.sans_service_a_traiter(s),
            "filtres": filtres,
            "STATUTS": trous_.STATUTS,
            "bilan": analyse.bilan(s),
        }
        # Requête HTMX : on ne renvoie que la liste.
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultats":
            return page(request, "_trous_liste.html", **contexte)
        return page(request, "trous.html", **contexte)


def _groupe_ou_404(s, groupe_id: int) -> Groupe:
    g = s.get(Groupe, groupe_id)
    if g is None:
        raise HTTPException(404, "Ce groupe n'existe plus. Une nouvelle analyse l'a peut-être fusionné avec un autre.")
    return g


@app.get("/trous/{groupe_id}", response_class=HTMLResponse)
def trou_detail(request: Request, groupe_id: int, retour: str = "", message: str = ""):
    with db.session() as s:
        d = trous_.detail(s, groupe_id)
        if d is None:
            raise HTTPException(404, "Ce groupe n'existe plus. Une nouvelle analyse l'a peut-être fusionné avec un autre.")
        d.groupe.service
        services = sorted(s.scalars(select(Service)).all(), key=lambda sv: trous_.cle_tri(sv.nom))
        retour = retour if retour.startswith("/trous") else "/trous"
        candidats = trous_.candidats_rassemblement(s, d.groupe)
        return page(request, "trou.html", d=d, g=d.groupe, services=services, retour=retour, message=message,
                    candidats=candidats, suivi=pages_demandes.suivi_demandes(s, groupe_id))


def _revenir(groupe_id: int, retour: str, message: str) -> RedirectResponse:
    from urllib.parse import urlencode

    retour = retour if retour.startswith("/trous") else "/trous"
    return RedirectResponse(f"/trous/{groupe_id}?{urlencode({'retour': retour, 'message': message})}", status_code=303)


@app.post("/trous/{groupe_id}/service")
def trou_service(groupe_id: int, service_id: str = Form(""), retour: str = Form("")):
    with db.session() as s:
        g = _groupe_ou_404(s, groupe_id)
        trous_.changer_service(s, g, int(service_id) if service_id.isdigit() else None)
    return _revenir(groupe_id, retour, "service")


@app.post("/trous/{groupe_id}/libelle")
def trou_libelle(groupe_id: int, libelle: str = Form(...), retour: str = Form("")):
    with db.session() as s:
        trous_.renommer(s, _groupe_ou_404(s, groupe_id), libelle)
    return _revenir(groupe_id, retour, "libelle")


@app.post("/trous/{groupe_id}/ecarter")
def trou_ecarter(groupe_id: int, retour: str = Form("")):
    with db.session() as s:
        trous_.ecarter(s, _groupe_ou_404(s, groupe_id))
    return _revenir(groupe_id, retour, "ecarte")


@app.post("/trous/{groupe_id}/rouvrir")
def trou_rouvrir(groupe_id: int, retour: str = Form("")):
    with db.session() as s:
        trous_.rouvrir(s, _groupe_ou_404(s, groupe_id))
    return _revenir(groupe_id, retour, "rouvert")


@app.post("/trous/{groupe_id}/rassembler")
def trou_rassembler(groupe_id: int, cible_id: int = Form(...), retour: str = Form("")):
    with db.session() as s:
        source, cible = _groupe_ou_404(s, groupe_id), _groupe_ou_404(s, cible_id)
        if source.id != cible.id:
            trous_.rassembler(s, source, cible)
    return _revenir(cible_id, retour, "rassemble")


@app.post("/trous/{groupe_id}/reverifier")
def trou_reverifier(groupe_id: int, retour: str = Form("")):
    with db.session() as s:
        trous_.reverifier(s, _groupe_ou_404(s, groupe_id))
    return _revenir(groupe_id, retour, "reverifie")





# ------------------------------------------------------------------ site de démo (pour tester le crawl)


def _markdown_simple(texte: str) -> str:
    morceaux = []
    for bloc in texte.strip().split("\n"):
        bloc = bloc.strip()
        if not bloc:
            continue
        if bloc.startswith("# "):
            morceaux.append(f"<h1>{html.escape(bloc[2:])}</h1>")
        else:
            morceaux.append(f"<p>{html.escape(bloc)}</p>")
    return "\n".join(morceaux)


@app.get("/demo-site/", response_class=HTMLResponse)
@app.get("/demo-site/{slug}", response_class=HTMLResponse)
def demo_site(slug: str = ""):
    pages = sorted((demo.DEMO / "site").glob("*.md"))
    nav = "".join(f'<li><a href="/demo-site/{p.stem}">{html.escape(ingestion.titre_markdown(p.read_text(), p.stem))}</a></li>' for p in pages)
    if slug:
        chemin = demo.DEMO / "site" / f"{slug}.md"
        if not re.fullmatch(r"[a-z0-9-]+", slug) or not chemin.exists():
            return HTMLResponse("<h1>Page introuvable</h1>", status_code=404)
        corps = _markdown_simple(chemin.read_text(encoding="utf-8"))
    else:
        corps = "<h1>Ville-Exemple</h1><p>Site fictif de démonstration.</p>"
    return HTMLResponse(
        f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Ville-Exemple</title></head>'
        f'<body><nav><ul>{nav}</ul></nav><main>{corps}</main><footer>Ville-Exemple, collectivité fictive</footer></body></html>'
    )
