"""Repérer les trous : intentions, regroupement, libellés, verdicts.

Principe 2 : un verdict « couvert » ou « partiel » n'est retenu que s'il cite au moins un
extrait réellement renvoyé par la recherche. Sinon le groupe est classé « trou ».
"""
import os
import re
from collections import Counter
from datetime import datetime
from typing import Callable

import numpy as np
from sqlalchemy import delete, func, select, update

from . import ingestion
from .fournisseur import fournisseur
from .models import Document, Extrait, Groupe, Question, Service

Progression = Callable[[str, int, int], None]
SEUIL_REGROUPEMENT = float(os.environ.get("NOESE_SEUIL_REGROUPEMENT", "0.55"))
SEUIL_THEME = float(os.environ.get("NOESE_SEUIL_THEME", "0.3"))
SEUIL_MEME_GROUPE = 0.85
NB_EXTRAITS = 6


def _rien(etape: str, fait: int, total: int) -> None:
    pass


def _objet(r, cle_liste: str | None = None) -> dict:
    """Les petits modèles renvoient parfois une liste là où on attend un objet."""
    if isinstance(r, dict):
        return r
    if isinstance(r, list):
        if cle_liste:
            return {cle_liste: r}
        return next((x for x in r if isinstance(x, dict)), {})
    return {}


# ------------------------------------------------------------ enregistrement


def enregistrer_questions(s, import_id: int, questions: list[ingestion.QuestionLue]) -> int:
    deja = {(t, d) for t, d in s.execute(select(Question.texte, Question.date))}
    n = 0
    for q in questions:
        if (q.texte, q.date) in deja:
            continue
        deja.add((q.texte, q.date))
        s.add(Question(import_id=import_id, texte=q.texte, date=q.date, canal=q.canal,
                       reponse_donnee=q.reponse_donnee, transfere=q.transfere))
        n += 1
    return n


def enregistrer_document(s, import_id: int | None, type_: str, titre: str, url: str, texte: str) -> Document:
    if url:
        ancien = s.scalar(select(Document).where(Document.url == url))
        if ancien:
            s.delete(ancien)
            s.flush()
    doc = Document(import_id=import_id, type=type_, titre=titre[:300], url=url, contenu=texte)
    s.add(doc)
    s.flush()
    for morceau in ingestion.decouper(titre, texte):
        s.add(Extrait(document_id=doc.id, titre=titre[:300], url=url, texte=morceau))
    return doc


def indexer_extraits(s, progression: Progression = _rien) -> int:
    extraits = s.scalars(select(Extrait).where(Extrait.embedding.is_(None))).all()
    for i in range(0, len(extraits), 64):
        lot = extraits[i : i + 64]
        vecteurs = fournisseur().embeddings([f"{e.titre}\n{e.texte}" for e in lot])
        for e, v in zip(lot, vecteurs):
            e.embedding = v
        progression("Indexation de la base", min(i + 64, len(extraits)), len(extraits))
    return len(extraits)


# ------------------------------------------------------------ intentions

SYSTEME_INTENTIONS = (
    "Tu aides le service relation usagers d'une mairie française à regrouper les questions "
    "des habitants par besoin. Tu réponds uniquement en JSON."
)
CONSIGNE_INTENTIONS = """Intentions déjà repérées :
{connues}

Pour chaque question numérotée ci-dessous, donne son intention : une formule courte (4 à 10 mots), neutre, au niveau de détail d'une fiche d'information d'un site de mairie, par exemple « Carte d'identité et passeport : faire la demande » ou « Encombrants : prendre rendez-vous ».
- Si la question porte sur le même besoin qu'une intention déjà repérée, reprends cette intention mot pour mot.
- Sinon, crée une nouvelle intention.
- Reste général : jamais de détail propre à la personne (objet précis, lieu, âge, date, prénom).
- Sépare deux besoins différents même s'ils partagent un mot (piscine municipale et piscine privée dans son jardin ne sont pas le même besoin). Sépare aussi une question sur les tarifs d'une année précise.

Questions :
{questions}

Réponds en JSON : {{"intentions": [{{"n": 1, "intention": "..."}}]}}"""


def reformuler_intentions(s, progression: Progression = _rien, lot: int = 20) -> None:
    connues = Counter(i for (i,) in s.execute(select(Question.intention).where(Question.intention.is_not(None))))
    a_faire = s.scalars(select(Question).where(Question.intention.is_(None)).order_by(Question.id)).all()
    for debut in range(0, len(a_faire), lot):
        paquet = a_faire[debut : debut + lot]
        liste_connues = "\n".join(f"- {i}" for i, _ in connues.most_common(150)) or "(aucune)"
        numerotees = "\n".join(f"{k + 1}. {q.texte[:400]}" for k, q in enumerate(paquet))
        r = fournisseur().chat(SYSTEME_INTENTIONS, CONSIGNE_INTENTIONS.format(connues=liste_connues, questions=numerotees))
        par_numero = {}
        for item in _objet(r, "intentions").get("intentions", []):
            try:
                par_numero[int(item["n"])] = str(item["intention"]).strip().rstrip(".")
            except (KeyError, TypeError, ValueError):
                continue
        for k, q in enumerate(paquet):
            q.intention = par_numero.get(k + 1) or q.texte[:200]
            connues[q.intention] += 1
        s.flush()
        progression("Lecture des questions", min(debut + lot, len(a_faire)), len(a_faire))


# ------------------------------------------------------------ regroupement


def regrouper(vecteurs: np.ndarray, seuil: float = SEUIL_REGROUPEMENT) -> list[int]:
    """Regroupement hiérarchique agglomératif, liaison moyenne, similarité cosinus.
    Suffisant pour quelques milliers d'intentions distinctes."""
    n = len(vecteurs)
    if n == 0:
        return []
    sim = vecteurs @ vecteurs.T
    np.fill_diagonal(sim, -np.inf)
    tailles = np.ones(n)
    actifs = np.ones(n, dtype=bool)
    etiquettes = list(range(n))
    while True:
        i, j = np.unravel_index(np.argmax(sim), sim.shape)
        if sim[i, j] < seuil:
            break
        ni, nj = tailles[i], tailles[j]
        nouvelle = (ni * sim[i] + nj * sim[j]) / (ni + nj)
        sim[i, :], sim[:, i] = nouvelle, nouvelle
        sim[i, i] = -np.inf
        sim[j, :], sim[:, j] = -np.inf, -np.inf
        tailles[i] += nj
        actifs[j] = False
        etiquettes = [i if e == j else e for e in etiquettes]
    renum = {e: k for k, e in enumerate(dict.fromkeys(etiquettes))}
    return [renum[e] for e in etiquettes]


SYSTEME_CONSOLIDATION = "Tu aides une mairie à regrouper les questions des habitants par besoin. Tu réponds uniquement en JSON."
CONSIGNE_CONSOLIDATION = """Voici des sous-groupes de questions d'habitants qui se ressemblent :

{sous_groupes}

Rassemble les numéros des sous-groupes qui expriment le même besoin d'information, c'est-à-dire qu'une seule fiche pratique du site de la mairie y répondrait. Laisse séparés les besoins différents, même proches (par exemple les horaires d'un équipement et ses tarifs d'une année précise, ou une piscine municipale et une piscine privée).
Chaque numéro apparaît exactement une fois.

JSON : {{"groupes": [[1, 3], [2]]}}"""


def consolider(intentions: list[str], fins: list[int], themes: list[int], exemples: dict[str, list[str]],
               progression: Progression = _rien) -> list[int]:
    """Dans chaque thème large, le LLM décide quels sous-groupes fins fusionner."""
    resultat = list(fins)
    par_theme: dict[int, list[int]] = {}
    for fin, theme in zip(fins, themes):
        if fin not in par_theme.setdefault(theme, []):
            par_theme[theme].append(fin)
    a_traiter = [f for f in par_theme.values() if len(f) > 1]
    for k, sous in enumerate(a_traiter):
        lignes = []
        for n, fin in enumerate(sous[:20]):
            membres = [t for t, f in zip(intentions, fins) if f == fin]
            textes = membres[:3] + [q for t in membres[:2] for q in exemples.get(t, [])[:1]]
            lignes.append(f"{n + 1}. " + " | ".join(x[:160] for x in textes))
        r = fournisseur().chat(SYSTEME_CONSOLIDATION, CONSIGNE_CONSOLIDATION.format(sous_groupes="\n".join(lignes)))
        for paquet in _objet(r, "groupes").get("groupes") or []:
            try:
                ids = [sous[int(n) - 1] for n in paquet if 1 <= int(n) <= len(sous[:20])]
            except (TypeError, ValueError):
                continue
            if len(ids) > 1:
                cible = ids[0]
                resultat = [cible if e in ids else e for e in resultat]
        progression("Regroupement des questions", k + 1, len(a_traiter))
    return resultat


def _questions_fixes(s) -> set[int]:
    """Questions que l'analyse ne déplace pas : rangées à la main, ou dans un besoin déjà
    pris en charge (demandé, répondu, écarté, résolu)."""
    return set(s.scalars(
        select(Question.id).outerjoin(Groupe, Groupe.id == Question.groupe_id)
        .where((Question.groupe_manuel.is_(True)) | (Groupe.statut.is_not(None) & (Groupe.statut != "ouvert")))
    ))


def regrouper_questions(s, progression: Progression = _rien) -> None:
    fixes = _questions_fixes(s)
    questions = [q for q in s.scalars(select(Question).order_by(Question.id)) if q.id not in fixes]
    anciens = s.scalars(select(Groupe)).all()
    proteges = {gid for (gid,) in s.execute(select(Question.groupe_id).where(Question.id.in_(fixes)))} if fixes else set()
    reutilises: set[int] = set()
    if questions:
        # Une intention = un point : les questions de même intention sont regroupées d'office.
        intentions = sorted({q.intention or q.texte for q in questions})
        progression("Regroupement des questions", 0, len(intentions))
        vecteurs = fournisseur().embeddings(intentions)
        exemples: dict[str, list[str]] = {}
        for q in questions:
            exemples.setdefault(q.intention or q.texte, []).append(q.texte)
        etiquettes = consolider(intentions, regrouper(vecteurs), regrouper(vecteurs, SEUIL_THEME), exemples, progression)
        index = {t: k for k, t in enumerate(intentions)}

        par_groupe: dict[int, list[Question]] = {}
        for q in questions:
            k = index[q.intention or q.texte]
            q.embedding = vecteurs[k]
            par_groupe.setdefault(etiquettes[k], []).append(q)

        for membres in par_groupe.values():
            centroide = np.mean([np.asarray(q.embedding) for q in membres], axis=0)
            centroide /= np.linalg.norm(centroide) or 1.0
            groupe = None
            meilleur = SEUIL_MEME_GROUPE
            for g in anciens:
                if g.id in reutilises or g.centroide is None:
                    continue
                similarite = float(np.dot(centroide, np.asarray(g.centroide)))
                if similarite >= meilleur:
                    groupe, meilleur = g, similarite
            if groupe is None:
                groupe = Groupe(libelle="", centroide=centroide)
                s.add(groupe)
                s.flush()
            elif groupe.id not in proteges:
                groupe.centroide = centroide
            reutilises.add(groupe.id)
            for q in membres:
                q.groupe_id = groupe.id
        s.flush()
        progression("Regroupement des questions", len(intentions), len(intentions))
    for g in anciens:
        if g.id not in reutilises and g.id not in proteges and g.statut == "ouvert":
            s.delete(g)
    s.flush()
    recompter(s)


def recompter(s) -> None:
    comptes = dict(s.execute(select(Question.groupe_id, func.count(Question.id)).group_by(Question.groupe_id)).all())
    for g in s.scalars(select(Groupe)):
        g.nb = comptes.get(g.id, 0)


# ------------------------------------------------------------ libellés et services

SYSTEME_LIBELLE = "Tu aides une mairie française à organiser les questions des habitants. Tu réponds uniquement en JSON."
CONSIGNE_LIBELLE = """Voici des questions d'habitants regroupées car elles portent sur le même besoin :
{exemples}

Services de la mairie : {services}

Donne :
- "libelle" : le besoin, en une phrase nominale claire de 4 à 12 mots, sans jargon (ex. « Horaires d'ouverture de la déchèterie »).
- "service" : le service concerné, recopié exactement depuis la liste, ou "Autre".
- "mots_cles" : 4 à 8 mots que la page du site de la mairie utiliserait pour en parler (vocabulaire officiel).

JSON : {{"libelle": "...", "service": "...", "mots_cles": ["..."]}}"""


def _exemples(s, groupe_id: int, n: int = 8) -> list[Question]:
    return s.scalars(
        select(Question).where(Question.groupe_id == groupe_id).order_by(Question.date.desc().nulls_last()).limit(n)
    ).all()


def nommer_groupes(s, progression: Progression = _rien, tous: bool = False) -> None:
    services = {sv.nom: sv for sv in s.scalars(select(Service))}
    groupes = s.scalars(select(Groupe)).all()
    a_faire = [g for g in groupes if tous or not g.libelle]
    for k, g in enumerate(a_faire):
        exemples = "\n".join(f"- {q.texte[:300]}" for q in _exemples(s, g.id))
        r = fournisseur().chat(SYSTEME_LIBELLE, CONSIGNE_LIBELLE.format(exemples=exemples, services=", ".join(services) or "(aucun)"))
        r = _objet(r)
        g.libelle = (str(r.get("libelle") or "").strip().rstrip(".") or _exemples(s, g.id, 1)[0].texte)[:300]
        if g.service_id is None and r.get("service") in services:
            g.service_id = services[r["service"]].id
        g.mots_cles = " ".join(str(m) for m in r.get("mots_cles") or [] if isinstance(m, str))
        s.flush()
        progression("Nommage des groupes", k + 1, len(a_faire))


# ------------------------------------------------------------ verdicts

SYSTEME_JUGE = (
    "Tu vérifies si la base de connaissances d'une mairie permet de répondre aux questions des habitants. "
    "Tu es strict : tu ne t'appuies que sur les extraits fournis, jamais sur tes connaissances générales. "
    "Tu réponds uniquement en JSON."
)
CONSIGNE_JUGE = """Besoin des habitants : {libelle}
Exemples de vraies questions :
{exemples}

Extraits de la base de connaissances :
{extraits}

Classe ce besoin :
- "couvert" : les extraits donnent une réponse précise et suffisante à la plupart des questions.
- "partiel" : les extraits parlent du sujet mais il manque une information clé demandée (un tarif, un délai, une année, une condition, une pièce à fournir).
- "trou" : les extraits ne répondent pas, ou ne parlent que de sujets voisins.

Si deux extraits se contredisent, une fiche validée par un service est plus récente et prime sur une page du site.
Attention aux faux amis : un extrait sur un sujet voisin (même mot, autre besoin) ne couvre pas le besoin. Un tarif d'une autre année ne couvre pas une question sur une année précise.

JSON :
{{"verdict": "couvert|partiel|trou",
  "reponse": "la réponse en 1 à 3 phrases, uniquement à partir des extraits, avec les numéros cités entre crochets ; vide si trou",
  "manque": "en une ou deux phrases courtes, l'information précise qui manque, à demander au service ; vide si couvert",
  "citations": [numéros des extraits utilisés]}}"""


def texte_brut(valeur) -> str:
    """Retire le Markdown que les modèles glissent parfois dans leurs réponses."""
    texte = str(valeur or "").strip()
    texte = re.sub(r"\*\*|__|`", "", texte)
    return re.sub(r"(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)", "", texte)


def rechercher(s, texte: str, k: int = NB_EXTRAITS) -> list[Extrait]:
    """Recherche hybride : pgvector (sens) + plein texte français de Postgres (mots),
    fusionnées par rang réciproque."""
    vecteur = fournisseur().embeddings([texte])[0]
    par_vecteur = s.scalars(
        select(Extrait.id).where(Extrait.embedding.is_not(None))
        .order_by(Extrait.embedding.cosine_distance(vecteur)).limit(k * 3)
    ).all()
    mots = {m for m in re.findall(r"\w{3,}", texte.lower())}
    par_mots = []
    if mots:
        requete = func.to_tsquery("french", " | ".join(sorted(mots)))
        document = func.to_tsvector("french", Extrait.titre + " " + Extrait.titre + " " + Extrait.texte)
        par_mots = s.scalars(
            select(Extrait.id).where(document.op("@@")(requete))
            .order_by(func.ts_rank_cd(document, requete).desc()).limit(k * 3)
        ).all()
    scores: dict[int, float] = {}
    for liste in (par_vecteur, par_mots):
        for rang, i in enumerate(liste):
            scores[i] = scores.get(i, 0.0) + 1.0 / (60 + rang)
    meilleurs = sorted(scores, key=scores.get, reverse=True)[:k]
    extraits = {e.id: e for e in s.scalars(select(Extrait).where(Extrait.id.in_(meilleurs)))}
    return [extraits[i] for i in meilleurs if i in extraits]


def _nature(s, e: Extrait) -> str:
    if e.fiche_id:
        from .models import FicheVersion

        v = s.scalar(select(FicheVersion).where(FicheVersion.fiche_id == e.fiche_id, FicheVersion.validee.is_(True))
                     .order_by(FicheVersion.numero.desc()).limit(1))
        if v and v.validee_le:
            return f"Fiche validée par le service le {v.validee_le:%d/%m/%Y}"
        return "Fiche validée par le service"
    return "Page du site" if (e.url or "").startswith("http") else "Document"


def juger_groupe(s, g: Groupe) -> None:
    exemples = _exemples(s, g.id, 6)
    requete = " ".join([g.libelle, g.mots_cles or ""] + [q.intention or "" for q in exemples[:3]])
    extraits = rechercher(s, requete)
    if not extraits:
        g.verdict, g.reponse, g.sources = "trou", "", []
        g.manque = "La base ne contient encore aucun document."
        g.juge_le = datetime.now()
        return
    bloc = "\n\n".join(f"[{k + 1}] {_nature(s, e)} : {e.titre}\n{e.texte}" for k, e in enumerate(extraits))
    r = fournisseur().chat(
        SYSTEME_JUGE,
        CONSIGNE_JUGE.format(libelle=g.libelle, exemples="\n".join(f"- {q.texte[:300]}" for q in exemples), extraits=bloc),
    )
    r = _objet(r)
    verdict = str(r.get("verdict", "")).strip().lower()
    verdict = verdict if verdict in ("couvert", "partiel", "trou") else "trou"
    citations = []
    for c in r.get("citations") or []:
        try:
            n = int(str(c).strip("[] "))
        except ValueError:
            continue
        if 1 <= n <= len(extraits) and n not in citations:
            citations.append(n)
    # Toujours une source : sans citation valide, pas de « couvert » ni de « partiel ».
    if verdict in ("couvert", "partiel") and not citations:
        verdict = "trou"
    g.verdict = verdict
    g.reponse = texte_brut(r.get("reponse")) if verdict != "trou" else ""
    g.manque = texte_brut(r.get("manque")) if verdict != "couvert" else ""
    if verdict == "trou" and not g.manque:
        g.manque = f"La base ne dit rien sur : {g.libelle.lower()}."
    g.sources = [
        {"n": n, "extrait_id": extraits[n - 1].id, "titre": extraits[n - 1].titre, "url": extraits[n - 1].url,
         "fiche_id": extraits[n - 1].fiche_id, "texte": extraits[n - 1].texte[:500]}
        for n in citations
    ] if verdict != "trou" else []
    g.juge_le = datetime.now()


def juger_groupes(s, progression: Progression = _rien) -> None:
    groupes = s.scalars(select(Groupe).where(Groupe.statut.in_(("ouvert", "demande")))).all()
    for k, g in enumerate(groupes):
        juger_groupe(s, g)
        s.flush()
        progression("Vérification dans la base", k + 1, len(groupes))


def verifier_modele_embeddings(s) -> None:
    """Des vecteurs de deux modèles différents ne sont pas comparables : on réindexe tout."""
    from .config import config
    from .models import Parametres

    p = s.get(Parametres, 1) or Parametres(id=1)
    s.add(p)
    if p.modele_embeddings != config.llm_embed_model:
        s.execute(update(Extrait).values(embedding=None))
        s.execute(update(Question).values(embedding=None))
        s.execute(update(Groupe).values(centroide=None))
        p.modele_embeddings = config.llm_embed_model
        s.flush()


def analyser(s, progression: Progression = _rien) -> dict:
    """Chaîne complète. Idempotente : relancée après chaque import."""
    verifier_modele_embeddings(s)
    indexer_extraits(s, progression)
    reformuler_intentions(s, progression)
    regrouper_questions(s, progression)
    nommer_groupes(s, progression)
    juger_groupes(s, progression)
    return bilan(s)


def bilan(s) -> dict:
    groupes = s.scalars(select(Groupe)).all()
    # Un groupe écarté ou résolu n'est plus un trou ouvert.
    compte = Counter(g.verdict for g in groupes if g.statut not in ("ecarte", "resolu"))
    questions = sum(g.nb for g in groupes)
    couvertes = sum(g.nb for g in groupes if g.verdict == "couvert")
    return {
        "groupes": len(groupes),
        "questions": questions,
        "couvert": compte.get("couvert", 0),
        "partiel": compte.get("partiel", 0),
        "trou": compte.get("trou", 0),
        "taux_couverture": round(100 * couvertes / questions) if questions else 0,
    }


def tout_effacer(s) -> None:
    for modele in (Question, Groupe, Extrait, Document):
        s.execute(delete(modele))
