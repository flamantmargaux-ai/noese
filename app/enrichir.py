"""Enrichir la mémoire : un parcours unique, quelle que soit la forme de l'information.

1. L'agent fournit la donnée (document, page web, savoir oral, saisie).
2. Il précise s'il le peut le service et le sujet.
3. La source est enregistrée telle quelle, avec sa provenance : elle est aussitôt consultable.
4. Des connaissances sont proposées à partir de son contenu, quand c'est possible.
5. L'agent les corrige, les valide ou les écarte.
6. Seules les connaissances validées par un agent comptent comme connaissances de la mémoire.

Si la proposition automatique échoue, la source reste enregistrée et marquée comme consultable :
rien n'est perdu, et l'agent peut réessayer.
"""
import io
import re
import zipfile
from html import unescape

import httpx
from sqlalchemy import select

from . import analyse, demandes, ingestion
from .fournisseur import ErreurFournisseur, fournisseur
from .models import Document, Evenement, Fiche, FicheVersion, Groupe

TAILLE_MAX = 25 * 1024 * 1024
FORMATS = (".pdf", ".md", ".markdown", ".txt", ".docx")
FORMATS_AUDIO = (".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac")


class ErreurSource(ValueError):
    """Message destiné à l'agent : dit ce qui n'a pas marché et quoi faire."""


# ------------------------------------------------------------------ lecture des sources


def texte_depuis_docx(contenu: bytes) -> str:
    """Texte d'un document Word, paragraphe par paragraphe, sans dépendance supplémentaire."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ErreurSource("Ce fichier Word n'a pas pu être lu. Enregistrez-le de nouveau au format .docx, ou en PDF.") from e
    paragraphes = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        p = re.sub(r"<w:tab/>", "\t", p)
        p = re.sub(r"<w:br/>", "\n", p)
        texte = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
        if texte.strip():
            paragraphes.append(unescape(texte))
    return "\n\n".join(paragraphes)


def lire_fichier(nom: str, contenu: bytes) -> tuple[str, str, str]:
    """(type, titre, texte) d'un fichier déposé, ou une erreur compréhensible."""
    bas = nom.lower()
    titre = nom.rsplit(".", 1)[0].replace("_", " ").strip() or nom
    if len(contenu) > TAILLE_MAX:
        raise ErreurSource(f"« {nom} » dépasse 25 Mo. Découpez-le, ou transmettez seulement les pages utiles.")
    if bas.endswith(".pdf"):
        try:
            texte, type_ = ingestion.texte_depuis_pdf(contenu), "pdf"
        except Exception as e:
            raise ErreurSource(f"« {nom} » n'a pas pu être ouvert. Le fichier est peut-être protégé ou abîmé.") from e
        if len(texte.strip()) < 30:
            raise ErreurSource(f"« {nom} » ne contient pas de texte lisible : c'est sans doute un document scanné. "
                               "Transmettez une version texte, ou copiez le contenu dans « Ajouter une connaissance ».")
    elif bas.endswith(".docx"):
        texte, type_ = texte_depuis_docx(contenu), "docx"
    elif bas.endswith((".md", ".markdown")):
        texte, type_ = contenu.decode("utf-8", "replace"), "md"
        titre = ingestion.titre_markdown(texte, titre)
    elif bas.endswith(".txt"):
        texte, type_ = contenu.decode("utf-8", "replace"), "txt"
    elif bas.endswith(".doc"):
        raise ErreurSource(f"« {nom} » est au format Word ancien (.doc). Enregistrez-le en .docx ou en PDF, puis réessayez.")
    else:
        raise ErreurSource(f"« {nom} » n'est pas d'un format pris en charge. Formats acceptés : PDF, Word (.docx), Markdown et texte.")
    if not texte.strip():
        raise ErreurSource(f"« {nom} » est vide.")
    return type_, titre[:300], texte


def recuperer_page(url: str) -> dict:
    """Récupère une seule page web. Erreur claire si la page n'est pas accessible."""
    url = url.strip()
    if not url:
        raise ErreurSource("Collez l'adresse de la page.")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Noese/0.1 (memoire de collectivite)"})
    except httpx.HTTPError as e:
        raise ErreurSource(f"La page {url} n'a pas pu être récupérée : le site ne répond pas, ou l'adresse est incorrecte.") from e
    if r.status_code >= 400:
        raise ErreurSource(f"La page {url} n'a pas pu être récupérée (le site répond « erreur {r.status_code} »). Vérifiez l'adresse.")
    if "text/html" not in r.headers.get("content-type", ""):
        if "pdf" in r.headers.get("content-type", ""):
            raise ErreurSource("Cette adresse mène à un PDF. Téléchargez-le, puis ajoutez-le avec « Importer un document ».")
        raise ErreurSource("Cette adresse ne mène pas à une page web lisible.")
    titre, texte, _ = ingestion.texte_depuis_html(r.text)
    if len(texte) < 80:
        raise ErreurSource("La page a été récupérée, mais elle ne contient presque pas de texte. Elle est peut-être "
                           "construite par un script : copiez son contenu dans « Ajouter une connaissance ».")
    return {"url": str(r.url), "titre": (titre or str(r.url))[:300], "texte": texte}


# ------------------------------------------------------------------ enregistrement


def enregistrer_source(s, type_: str, titre: str, texte: str, url: str = "", service_id: int | None = None,
                       sujet: str = "", ajoute_par: str = "", note: str = "") -> Document:
    """Enregistre la source avec sa provenance et la rend aussitôt consultable (recherche, vérification des besoins)."""
    from . import memoire

    doc = analyse.enregistrer_document(s, None, type_, titre, url, texte)
    doc.service_id, doc.sujet = service_id, memoire.normaliser_sujet(sujet)
    doc.ajoute_par, doc.note, doc.traitement = ajoute_par, note.strip(), "consultable"
    s.flush()
    try:
        analyse.indexer_extraits(s)
    except ErreurFournisseur:
        pass  # l'analyse suivante indexera ; la source est déjà enregistrée
    s.add(Evenement(type="source_ajoutee", objet="document", objet_id=doc.id, acteur=ajoute_par,
                    details={"nature": type_, "titre": titre}))
    return doc


# ------------------------------------------------------------------ propositions de connaissances

SYSTEME = ("Tu aides une collectivité à constituer sa mémoire de connaissances, à partir de ses propres sources. "
           "Tu n'inventes rien : tu n'utilises que le texte fourni. Tu réponds en JSON.")
CONSIGNE = """Source : {titre} ({nature})
{contexte}
Texte de la source :
\"\"\"{texte}\"\"\"

Repère dans ce texte de 1 à {n} connaissances utiles pour répondre aux habitants ou aux agents :
une règle, une procédure, une réponse pratique, une condition, une exception. Chaque connaissance
doit pouvoir se lire seule. N'en propose aucune si le texte n'en contient pas.

Pour chacune :
- "titre" : la question à laquelle elle répond, formulée simplement (moins de 12 mots) ;
- "reponse" : la réponse en 1 à 4 phrases claires, au vouvoiement ;
- "conditions" : conditions, étapes ou exceptions mentionnées, sinon "" ;
- "pieces" : liste des pièces à fournir mentionnées, sinon [] ;
- "lien_demarche" : une adresse web seulement si elle figure dans le texte, sinon "" ;
- "saisonniere" : true si l'information change souvent (tarif, date, horaire saisonnier, année) ;
- "a_verifier" : ce qui est ambigu ou incomplet dans le texte, en une phrase, sinon "".

JSON : {{"connaissances": [{{"titre": "", "reponse": "", "conditions": "", "pieces": [], "lien_demarche": "", "saisonniere": false, "a_verifier": ""}}]}}"""


def proposer(s, doc: Document, lacune: Groupe | None = None, n: int = 5) -> list[Fiche]:
    """Propose des connaissances à partir d'une source. Elles restent des propositions tant qu'un agent ne les a pas validées."""
    from . import memoire

    contexte = ""
    if lacune is not None:
        contexte = f"Besoin des habitants à couvrir en priorité : {lacune.libelle}.\n"
        if lacune.manque:
            contexte += f"Ce qui manque à la mémoire : {lacune.manque}\n"
    if doc.note:
        contexte += f"Précisions de l'agent : {doc.note}\n"
    texte = doc.contenu[:12000]
    r = analyse._objet(fournisseur().chat(SYSTEME, CONSIGNE.format(
        titre=doc.titre, nature=memoire.NATURES.get(doc.type, "source"), contexte=contexte, texte=texte, n=n)), "connaissances")
    fiches = []
    for item in (r.get("connaissances") or [])[:n]:
        if not isinstance(item, dict):
            continue
        titre, reponse = analyse.texte_brut(item.get("titre")), analyse.texte_brut(item.get("reponse"))
        if not titre or not reponse:
            continue
        pieces = item.get("pieces") or []
        pieces = [pieces] if isinstance(pieces, str) else pieces
        lien = str(item.get("lien_demarche") or "").strip()
        fiche = Fiche(service_id=doc.service_id, document_id=doc.id, groupe_id=lacune.id if lacune else None,
                      sujet=doc.sujet or (lacune.sujet if lacune else ""), statut="brouillon",
                      saisonniere=bool(item.get("saisonniere")) or bool(demandes.SIGNES_SAISONNIERS.search(titre + " " + reponse)))
        s.add(fiche)
        s.flush()
        s.add(FicheVersion(fiche_id=fiche.id, numero=1, titre=titre[:300], reponse=reponse,
                           conditions=analyse.texte_brut(item.get("conditions")),
                           pieces="\n".join(analyse.texte_brut(p) for p in pieces if str(p).strip()),
                           lien_demarche=lien if lien and lien in doc.contenu else "",
                           origine=f"{memoire.NATURES.get(doc.type, 'Source')} : {doc.titre}",
                           redigee_par="Proposition automatique à partir de la source",
                           a_verifier=analyse.texte_brut(item.get("a_verifier")), validee=False))
        fiches.append(fiche)
    s.flush()
    mettre_a_jour_traitement(s, doc)
    return fiches


def propositions(s, doc: Document) -> list[Fiche]:
    return s.scalars(select(Fiche).where(Fiche.document_id == doc.id).order_by(Fiche.id)).all()


def mettre_a_jour_traitement(s, doc: Document) -> None:
    statuts = [f.statut for f in propositions(s, doc)]
    if "brouillon" in statuts:
        doc.traitement = "a_relire"
    elif "validee" in statuts:
        doc.traitement = "traitee"
    else:
        doc.traitement = "consultable"


def valider(s, fiche: Fiche, par: str, sujet: str = "", service_id: int | None = None, **champs) -> FicheVersion:
    """Validation par un agent : seul chemin pour qu'une proposition devienne une connaissance."""
    from . import memoire

    if sujet:
        fiche.sujet = memoire.normaliser_sujet(sujet)
    if service_id is not None:
        fiche.service_id = service_id
    version = demandes.valider_fiche(s, fiche, par, **champs)
    if fiche.document_id:
        doc = s.get(Document, fiche.document_id)
        if doc:
            mettre_a_jour_traitement(s, doc)
    return version


def ecarter(s, fiche: Fiche, par: str) -> None:
    if fiche.statut != "brouillon":
        return
    fiche.statut = "archivee"
    s.add(Evenement(type="proposition_ecartee", objet="fiche", objet_id=fiche.id, acteur=par))
    if fiche.document_id:
        doc = s.get(Document, fiche.document_id)
        if doc:
            mettre_a_jour_traitement(s, doc)
