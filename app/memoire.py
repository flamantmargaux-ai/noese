"""La mémoire de la collectivité : ce qu'elle sait, où elle a des manques, ce qui est à vérifier.

Tout est calculé à partir des données existantes (questions, besoins regroupés, fiches,
sources). Aucun chiffre n'est inventé : quand une donnée n'existe pas, on le dit.

Les quatre objets :
- la collectivité (paramètres et services) ;
- les sources (documents, pages web, savoirs oraux, saisies) : table `document` ;
- les connaissances (fiches et leurs versions) : tables `fiche` et `fiche_version` ;
- les lacunes (besoins des habitants mal couverts) : table `groupe`, avec ses questions.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select

from . import verifications
from .models import Document, Evenement, Extrait, Fiche, FicheVersion, Groupe, Import, Parametres, Question, Service

PERIODES = {"30": "30 derniers jours", "90": "90 derniers jours", "tout": "Toute la période"}
SIGNALEMENTS = {
    "erreur": "Information erronée",
    "contradiction": "Contradiction avec une autre source",
    "depassee": "Information dépassée",
    "incomplete": "Information incomplète",
    "autre": "Autre",
}


# ------------------------------------------------------------------ sujets


def normaliser_sujet(texte: str) -> str:
    texte = " ".join((texte or "").replace("’", "'").split()).strip(" .:-")
    return (texte[:1].upper() + texte[1:])[:200] if texte else ""


def sujet_d_intention(intention: str | None) -> str:
    """Les intentions sont rédigées « Sujet : précision » ; le sujet est la partie avant les deux-points."""
    if not intention or ":" not in intention:
        return ""
    return normaliser_sujet(intention.split(":", 1)[0])


def calculer_sujets(s) -> None:
    """Rattache chaque besoin, fiche et source à un sujet, à partir des intentions des questions.
    Un sujet choisi par un agent sur une fiche ou une source n'est jamais écrasé."""
    par_groupe: dict[int, Counter] = defaultdict(Counter)
    for gid, intention in s.execute(select(Question.groupe_id, Question.intention).where(Question.groupe_id.is_not(None))):
        sujet = sujet_d_intention(intention)
        if sujet:
            par_groupe[gid][sujet] += 1
    groupes = {g.id: g for g in s.scalars(select(Groupe))}
    for gid, g in groupes.items():
        if par_groupe.get(gid) and not g.sujet_fixe:
            g.sujet = par_groupe[gid].most_common(1)[0][0]
    for f in s.scalars(select(Fiche).where(Fiche.sujet == "", Fiche.groupe_id.is_not(None))):
        if f.groupe_id in groupes:
            f.sujet = groupes[f.groupe_id].sujet
    # Sources : service et sujet des besoins qui les citent comme réponse.
    extrait_doc = dict(s.execute(select(Extrait.id, Extrait.document_id).where(Extrait.document_id.is_not(None))).all())
    services_doc: dict[int, Counter] = defaultdict(Counter)
    sujets_doc: dict[int, Counter] = defaultdict(Counter)
    for g in groupes.values():
        for src in g.sources or []:
            doc = extrait_doc.get(src.get("extrait_id"))
            if doc:
                if g.service_id:
                    services_doc[doc][g.service_id] += g.nb or 1
                if g.sujet:
                    sujets_doc[doc][g.sujet] += g.nb or 1
    for d in s.scalars(select(Document)):
        if d.service_id is None and services_doc.get(d.id):
            d.service_id = services_doc[d.id].most_common(1)[0][0]
        if not d.sujet and sujets_doc.get(d.id):
            d.sujet = sujets_doc[d.id].most_common(1)[0][0]
    s.flush()


def sujets(s, service_id: int | None = None) -> list[tuple[str, int]]:
    """Sujets connus, avec le nombre de besoins, fiches et sources qui s'y rattachent."""
    compte: Counter = Counter()
    for modele in (Groupe, Fiche, Document):
        requete = select(modele.sujet).where(modele.sujet != "")
        if service_id:
            requete = requete.where(modele.service_id == service_id)
        if modele is Fiche:
            requete = requete.where(Fiche.statut != "archivee")
        compte.update(x for (x,) in s.execute(requete))
    return sorted(compte.items(), key=lambda kv: (-kv[1], kv[0]))


# ------------------------------------------------------------------ outils communs


def parametres(s) -> Parametres:
    return s.get(Parametres, 1) or Parametres(id=1, collectivite="Votre collectivité")


def est_demo(s) -> bool:
    return bool(s.scalar(select(Import.id).where(Import.libelle.like("Données de démo%")).limit(1)))


def depuis(periode: str) -> date | None:
    return date.today() - timedelta(days=int(periode)) if periode in ("30", "90") else None


def filtre_service(requete, colonne, service: str):
    if service == "aucun":
        return requete.where(colonne.is_(None))
    if service.isdigit():
        return requete.where(colonne == int(service))
    return requete


def services_tries(s) -> list[Service]:
    from .trous import cle_tri

    return sorted(s.scalars(select(Service)).all(), key=lambda sv: cle_tri(sv.nom))


# ------------------------------------------------------------------ connaissances


@dataclass
class Etat:
    cle: str
    libelle: str
    classe: str  # couvert | partiel | trou | nouveau | neutre


def etat_connaissance(s, f: Fiche) -> Etat:
    if f.statut == "brouillon":
        return Etat("proposee", "Proposée, à relire", "nouveau")
    if f.statut == "archivee":
        return Etat("retiree", "Retirée", "neutre")
    if f.signalee_le:
        return Etat("signalee", "Signalée", "partiel")
    fraicheur = verifications.fraicheur(s, f)
    if fraicheur == "perimee":
        return Etat("depassee", "Révision dépassée", "trou")
    if fraicheur == "bientot":
        return Etat("a_verifier", "À vérifier bientôt", "partiel")
    return Etat("validee", "Validée", "couvert")


STATUTS_CONNAISSANCE = {
    "": "Tous les statuts",
    "validee": "Validées",
    "a_verifier": "À vérifier bientôt",
    "depassee": "Révision dépassée",
    "signalee": "Signalées",
    "proposee": "Proposées, à relire",
    "retiree": "Retirées",
}


def version_affichee(s, f: Fiche) -> FicheVersion | None:
    """La dernière version validée ; pour une proposition, la dernière proposée."""
    requete = select(FicheVersion).where(FicheVersion.fiche_id == f.id)
    if f.statut != "brouillon":
        requete = requete.where(FicheVersion.validee.is_(True))
    return s.scalar(requete.order_by(FicheVersion.numero.desc()).limit(1))


def provenance(s, f: Fiche, v: FicheVersion | None) -> str:
    if f.document_id:
        d = s.get(Document, f.document_id)
        if d:
            return f"{NATURES.get(d.type, 'Source')} : {d.titre}"
    if f.demande_id:
        return (v.origine if v and v.origine else "Réponse d'un service à une question d'habitants")
    if v and "importée" in (v.origine or ""):
        return v.origine
    return (v.origine if v and v.origine else "Saisie directe")


def resume(texte: str, n: int = 180) -> str:
    texte = " ".join((texte or "").split())
    if len(texte) <= n:
        return texte
    coupe = texte[:n].rsplit(" ", 1)[0]
    return coupe.rstrip(",;:") + "…"


@dataclass
class LigneConnaissance:
    fiche: Fiche
    version: FicheVersion
    etat: Etat
    service: str
    provenance: str
    verifiee_le: date | None


def lister_connaissances(s, q: str = "", service: str = "", sujet: str = "", statut: str = "",
                         ids: list[int] | None = None) -> list[LigneConnaissance]:
    requete = select(Fiche)
    requete = filtre_service(requete, Fiche.service_id, service)
    if sujet:
        requete = requete.where(Fiche.sujet == sujet)
    if ids is not None:
        requete = requete.where(Fiche.id.in_(ids or [-1]))
    noms = {sv.id: sv.nom for sv in s.scalars(select(Service))}
    motif = q.strip().lower()
    lignes = []
    for f in s.scalars(requete):
        v = version_affichee(s, f)
        if v is None:
            continue
        etat = etat_connaissance(s, f)
        if statut and etat.cle != statut:
            continue
        if not statut and etat.cle == "retiree":
            continue
        if motif and ids is None and motif not in f"{v.titre} {v.reponse} {v.conditions} {v.pieces} {f.sujet} {noms.get(f.service_id, '')}".lower():
            continue
        derniere = f.verifiee_le or (v.validee_le.date() if v.validee_le else None)
        lignes.append(LigneConnaissance(f, v, etat, noms.get(f.service_id, ""), provenance(s, f, v), derniere))
    ordre = {"signalee": 0, "depassee": 1, "a_verifier": 2, "proposee": 3, "validee": 4, "retiree": 5}
    return sorted(lignes, key=lambda l: (ordre.get(l.etat.cle, 9), l.version.titre.lower()))


# ------------------------------------------------------------------ sources

NATURES = {"url": "Page web", "pdf": "Document PDF", "md": "Document Markdown", "txt": "Document texte",
           "docx": "Document Word", "oral": "Savoir oral", "saisie": "Saisie d'un agent"}
TRAITEMENTS = {
    "consultable": Etat("consultable", "Consultable", "neutre"),
    "a_relire": Etat("a_relire", "Propositions à relire", "nouveau"),
    "traitee": Etat("traitee", "Connaissances validées", "couvert"),
}


@dataclass
class LigneSource:
    document: Document
    nature: str
    service: str
    etat: Etat
    nb_connaissances: int


def connaissances_par_source(s) -> Counter:
    return Counter(d for (d,) in s.execute(select(Fiche.document_id).where(Fiche.document_id.is_not(None),
                                                                           Fiche.statut == "validee")))


def lister_sources(s, q: str = "", service: str = "", sujet: str = "", ids: list[int] | None = None) -> list[LigneSource]:
    requete = filtre_service(select(Document), Document.service_id, service)
    if sujet:
        requete = requete.where(Document.sujet == sujet)
    if ids is not None:
        requete = requete.where(Document.id.in_(ids or [-1]))
    elif q.strip():
        motif = f"%{q.strip()}%"
        requete = requete.where(or_(Document.titre.ilike(motif), Document.contenu.ilike(motif), Document.sujet.ilike(motif)))
    noms = {sv.id: sv.nom for sv in s.scalars(select(Service))}
    nb = connaissances_par_source(s)
    lignes = [LigneSource(d, NATURES.get(d.type, "Source"), noms.get(d.service_id, ""),
                          TRAITEMENTS.get(d.traitement, TRAITEMENTS["consultable"]), nb.get(d.id, 0))
              for d in s.scalars(requete.order_by(Document.cree_le.desc(), Document.titre))]
    return lignes


def besoins_citant(s, document_id: int) -> list[Groupe]:
    extraits = {i for (i,) in s.execute(select(Extrait.id).where(Extrait.document_id == document_id))}
    return [g for g in s.scalars(select(Groupe).order_by(Groupe.nb.desc()))
            if any(src.get("extrait_id") in extraits for src in g.sources or [])]


def recherche(s, q: str) -> tuple[list[int], list[int]]:
    """Recherche dans les titres et le contenu de la mémoire : renvoie des fiches et des sources.
    Le contenu est cherché par mots, avec leurs variantes (pluriels, conjugaisons), grâce à la
    recherche plein texte française de PostgreSQL. Ce n'est pas une recherche par le sens."""
    fiches, docs = [], []
    if not q.strip():
        return fiches, docs

    def ajouter(liste, valeur):
        if valeur and valeur not in liste:
            liste.append(valeur)

    motif = f"%{q.strip()}%"
    for (fid,) in s.execute(select(FicheVersion.fiche_id).where(or_(FicheVersion.titre.ilike(motif), FicheVersion.reponse.ilike(motif)))):
        ajouter(fiches, fid)
    for (fid,) in s.execute(select(Fiche.id).where(Fiche.sujet.ilike(motif))):
        ajouter(fiches, fid)
    for (did,) in s.execute(select(Document.id).where(or_(Document.titre.ilike(motif), Document.sujet.ilike(motif)))):
        ajouter(docs, did)
    requete = func.websearch_to_tsquery("french", q.strip())
    texte = func.to_tsvector("french", Extrait.titre + " " + Extrait.texte)
    for fid, did in s.execute(select(Extrait.fiche_id, Extrait.document_id).where(texte.op("@@")(requete))
                              .order_by(func.ts_rank_cd(texte, requete).desc()).limit(40)):
        ajouter(fiches, fid)
        ajouter(docs, did)
    return fiches, docs


# ------------------------------------------------------------------ lacunes


@dataclass
class StatsBesoin:
    nb: int = 0
    transferts: int = 0
    derniere: date | None = None
    exemple: str = ""


def stats_besoins(s, periode: str = "tout") -> dict[int, StatsBesoin]:
    debut = depuis(periode)
    stats: dict[int, StatsBesoin] = defaultdict(StatsBesoin)
    requete = select(Question.groupe_id, Question.texte, Question.date, Question.transfere).where(Question.groupe_id.is_not(None))
    if debut:
        requete = requete.where(Question.date >= debut)
    for gid, texte, quand, transfere in s.execute(requete.order_by(Question.date.desc().nulls_last())):
        st = stats[gid]
        st.nb += 1
        st.transferts += 1 if transfere else 0
        if not st.exemple:
            st.exemple, st.derniere = texte, quand
    return stats


PRIORITES = {"haute": "Haute", "moyenne": "Moyenne", "basse": "Basse"}
REGLE_PRIORITE = ("Priorité calculée à partir de la fréquence des questions et de la part passée à un agent : "
                  "haute à partir de 8 questions, ou de 4 si la moitié a été transférée ; moyenne à partir de 3 ; "
                  "basse sinon. Un agent peut la relever pour tenir compte de l'importance métier ou du risque.")


def priorite(g: Groupe, st: StatsBesoin | None) -> tuple[str, bool]:
    """(niveau, calculée ?) — une règle simple et explicable, ou le choix d'un agent."""
    if g.priorite_manuelle in PRIORITES:
        return g.priorite_manuelle, False
    nb = st.nb if st else g.nb
    transferts = st.transferts if st else 0
    if nb >= 8 or (nb >= 4 and transferts * 2 >= nb):
        return "haute", True
    if nb >= 3:
        return "moyenne", True
    return "basse", True


STATUTS_LACUNE = {
    "a_traiter": "À traiter",
    "en_cours": "En cours",
    "resolu": "Résolues",
    "ecarte": "Écartées",
    "tous": "Toutes",
}
LIBELLES_STATUT = {"ouvert": "À traiter", "demande": "Demandée au service", "repondu": "Réponse reçue",
                   "resolu": "Résolue", "ecarte": "Écartée"}
TYPES_LACUNE = {"trou": "Sans réponse", "partiel": "Réponse incomplète", "couvert": "Couvert"}


@dataclass
class LigneLacune:
    groupe: Groupe
    service: str
    nb: int
    transferts: int
    exemple: str
    priorite: str
    priorite_calculee: bool


def lister_lacunes(s, service: str = "", sujet: str = "", statut: str = "a_traiter", prio: str = "",
                   type_: str = "", q: str = "", periode: str = "tout") -> list[LigneLacune]:
    requete = select(Groupe).where(Groupe.verdict.in_(("trou", "partiel")) if statut != "resolu" else Groupe.id.is_not(None))
    requete = filtre_service(requete, Groupe.service_id, service)
    if statut == "a_traiter":
        requete = requete.where(Groupe.statut == "ouvert")
    elif statut == "en_cours":
        requete = requete.where(Groupe.statut.in_(("demande", "repondu")))
    elif statut == "resolu":
        requete = requete.where(Groupe.statut == "resolu")
    elif statut == "ecarte":
        requete = requete.where(Groupe.statut == "ecarte")
    if sujet:
        requete = requete.where(Groupe.sujet == sujet)
    if type_ in ("trou", "partiel"):
        requete = requete.where(Groupe.verdict == type_)
    if q.strip():
        motif = f"%{q.strip()}%"
        requete = requete.where(or_(Groupe.libelle.ilike(motif), Groupe.sujet.ilike(motif),
                                    Groupe.id.in_(select(Question.groupe_id).where(Question.texte.ilike(motif)))))
    stats = stats_besoins(s, periode)
    noms = {sv.id: sv.nom for sv in s.scalars(select(Service))}
    lignes = []
    for g in s.scalars(requete):
        st = stats.get(g.id)
        if periode != "tout" and not st:
            continue
        niveau, calculee = priorite(g, st)
        if prio and niveau != prio:
            continue
        lignes.append(LigneLacune(g, noms.get(g.service_id, ""), st.nb if st else g.nb, st.transferts if st else 0,
                                  st.exemple if st else "", niveau, calculee))
    rang = {"haute": 0, "moyenne": 1, "basse": 2}
    return sorted(lignes, key=lambda l: (rang[l.priorite], -l.nb, l.groupe.libelle))


def resume_lacunes(s, service: str = "") -> dict:
    def compte(*conditions) -> int:
        return s.scalar(filtre_service(select(func.count(Groupe.id)).where(*conditions), Groupe.service_id, service))

    il_y_a_30j = datetime.now() - timedelta(days=30)
    resolus = {g for (g,) in s.execute(select(Evenement.objet_id).where(Evenement.type == "lacune_resolue",
                                                                         Evenement.cree_le >= il_y_a_30j))}
    resolus |= {g for (g,) in s.execute(filtre_service(select(Groupe.id).where(Groupe.statut == "resolu", Groupe.juge_le >= il_y_a_30j),
                                                       Groupe.service_id, service))}
    qualite = qualite_connaissances(s, service)
    return {
        "sans_reponse": compte(Groupe.verdict == "trou", Groupe.statut == "ouvert"),
        "incompletes": compte(Groupe.verdict == "partiel", Groupe.statut == "ouvert"),
        "en_cours": compte(Groupe.statut.in_(("demande", "repondu"))),
        "resolues_30j": len(resolus),
        "a_actualiser": sum(len(v) for v in qualite.values()),
    }


def qualite_connaissances(s, service: str = "") -> dict[str, list[LigneConnaissance]]:
    """Connaissances validées qui demandent une révision, par motif. Une fiche n'est « dépassée »
    que si sa date de révision, fixée par la règle de fraîcheur, est passée."""
    lignes = lister_connaissances(s, service=service)
    return {
        "signalee": [l for l in lignes if l.etat.cle == "signalee"],
        "depassee": [l for l in lignes if l.etat.cle == "depassee"],
        "a_verifier": [l for l in lignes if l.etat.cle == "a_verifier"],
    }


# ------------------------------------------------------------------ vue d'ensemble


@dataclass
class Couverture:
    couvertes: int = 0
    evaluees: int = 0

    @property
    def taux(self) -> int | None:
        return round(100 * self.couvertes / self.evaluees) if self.evaluees else None


def couverture_questions(s, service: str = "", periode: str = "tout") -> Couverture:
    """Questions d'habitants dont le besoin est couvert par la mémoire, sur celles qui ont été évaluées."""
    debut = depuis(periode)
    requete = select(Groupe.verdict, Groupe.statut, func.count(Question.id)).join(Question, Question.groupe_id == Groupe.id)
    requete = filtre_service(requete, Groupe.service_id, service).where(Groupe.verdict.is_not(None), Groupe.statut != "ecarte")
    if debut:
        requete = requete.where(Question.date >= debut)
    c = Couverture()
    for verdict, _statut, n in s.execute(requete.group_by(Groupe.verdict, Groupe.statut)):
        c.evaluees += n
        c.couvertes += n if verdict == "couvert" else 0
    return c


def indicateurs(s, service: str = "", periode: str = "tout") -> dict:
    lacunes = lister_lacunes(s, service=service, statut="a_traiter", periode=periode)
    qualite = qualite_connaissances(s, service)
    validees = filtre_service(select(func.count(Fiche.id)).where(Fiche.statut == "validee"), Fiche.service_id, service)
    sources = filtre_service(select(func.count(Document.id)), Document.service_id, service)
    return {
        "couverture": couverture_questions(s, service, periode),
        "sans_reponse": sum(1 for l in lacunes if l.groupe.verdict == "trou"),
        "incompletes": sum(1 for l in lacunes if l.groupe.verdict == "partiel"),
        "questions_lacunes": sum(l.nb for l in lacunes),
        "a_verifier": sum(len(v) for v in qualite.values()),
        "qualite": {k: len(v) for k, v in qualite.items()},
        "connaissances": s.scalar(validees),
        "sources": s.scalar(sources),
        "propositions": s.scalar(filtre_service(select(func.count(Fiche.id)).where(Fiche.statut == "brouillon"), Fiche.service_id, service)),
    }


@dataclass
class LigneService:
    id: int | None
    nom: str
    connaissances: int = 0
    couverture: Couverture = field(default_factory=Couverture)
    lacunes: int = 0
    a_verifier: int = 0
    sources: int = 0


def couverture_par_service(s, periode: str = "tout") -> list[LigneService]:
    lignes: dict[int | None, LigneService] = {sv.id: LigneService(sv.id, sv.nom) for sv in services_tries(s)}
    lignes[None] = LigneService(None, "Sans service attribué")
    for sid, n in s.execute(select(Fiche.service_id, func.count(Fiche.id)).where(Fiche.statut == "validee").group_by(Fiche.service_id)):
        lignes[sid].connaissances = n
    for sid, n in s.execute(select(Document.service_id, func.count(Document.id)).group_by(Document.service_id)):
        lignes[sid].sources = n
    debut = depuis(periode)
    requete = (select(Groupe.service_id, Groupe.verdict, func.count(Question.id)).join(Question, Question.groupe_id == Groupe.id)
               .where(Groupe.verdict.is_not(None), Groupe.statut != "ecarte"))
    if debut:
        requete = requete.where(Question.date >= debut)
    for sid, verdict, n in s.execute(requete.group_by(Groupe.service_id, Groupe.verdict)):
        lignes[sid].couverture.evaluees += n
        lignes[sid].couverture.couvertes += n if verdict == "couvert" else 0
    for l in lister_lacunes(s, statut="a_traiter", periode=periode):
        lignes[l.groupe.service_id].lacunes += 1
    for liste in qualite_connaissances(s).values():
        for l in liste:
            lignes[l.fiche.service_id].a_verifier += 1
    return [l for l in lignes.values() if l.connaissances or l.couverture.evaluees or l.lacunes or l.a_verifier or l.sources]


@dataclass
class Evolution:
    quand: datetime
    texte: str
    lien: str
    nature: str  # connaissance | source | lacune | verification


def activite_recente(s, service: str = "", n: int = 8) -> list[Evolution]:
    """Les évolutions qui comptent pour la mémoire, pas le journal technique."""
    evolutions: list[Evolution] = []
    fiches = {f.id: f for f in s.scalars(select(Fiche))}

    def titre(fid: int) -> str:
        f = fiches.get(fid)
        v = version_affichee(s, f) if f else None
        return v.titre if v else "une connaissance"

    def du_service(fid: int | None) -> bool:
        if not service:
            return True
        f = fiches.get(fid)
        return bool(f) and ((service == "aucun" and f.service_id is None) or str(f.service_id) == service)

    # Première validation d'une fiche = nouvelle connaissance ; les suivantes = actualisations.
    premieres = {}
    for eid, fid in s.execute(select(Evenement.id, Evenement.objet_id).where(Evenement.type == "fiche_validee").order_by(Evenement.id)):
        premieres.setdefault(fid, eid)
    for e in s.scalars(select(Evenement).order_by(Evenement.id.desc()).limit(200)):
        if e.type in ("fiche_validee", "fiche_importee", "verification_toujours_juste", "verification_modifiee",
                      "fiche_retiree", "fiche_signalee", "fiche_corrigee") and not du_service(e.objet_id):
            continue
        lien = f"/memoire/connaissances/{e.objet_id}"
        if e.type == "fiche_validee":
            nature = "Nouvelle connaissance validée" if premieres.get(e.objet_id) == e.id else "Connaissance actualisée"
            texte = f"{nature} : « {titre(e.objet_id)} », par {e.acteur}"
            evolutions.append(Evolution(e.cree_le, texte, lien, "connaissance"))
        elif e.type == "fiche_importee":
            evolutions.append(Evolution(e.cree_le, f"Connaissance reprise d'un export : « {titre(e.objet_id)} »", lien, "connaissance"))
        elif e.type == "verification_toujours_juste":
            evolutions.append(Evolution(e.cree_le, f"« {titre(e.objet_id)} » confirmée toujours juste par {e.acteur}", lien, "verification"))
        elif e.type == "fiche_retiree":
            evolutions.append(Evolution(e.cree_le, f"« {titre(e.objet_id)} » retirée de la mémoire par {e.acteur}", lien, "verification"))
        elif e.type == "fiche_signalee":
            evolutions.append(Evolution(e.cree_le, f"« {titre(e.objet_id)} » signalée à vérifier par {e.acteur}", lien, "verification"))
        elif e.type == "source_ajoutee":
            d = s.get(Document, e.objet_id)
            if d and (not service or str(d.service_id) == service):
                evolutions.append(Evolution(e.cree_le, f"Source ajoutée : {d.titre}", f"/memoire/sources/{d.id}", "source"))
        elif e.type == "lacune_resolue":
            g = s.get(Groupe, e.objet_id)
            if g and (not service or str(g.service_id) == service):
                evolutions.append(Evolution(e.cree_le, f"Lacune résolue : {g.libelle}", f"/lacunes/{g.id}", "lacune"))
    if not service:
        import re

        for imp in s.scalars(select(Import).where(Import.statut == "termine").order_by(Import.id.desc()).limit(5)):
            bilan = re.search(r"(\d+) groupes : (\d+) couverts, (\d+) partiels, (\d+) trous", imp.journal or "")
            origine = imp.libelle.removeprefix("Questions : ").removeprefix("Base : ")
            if bilan:
                total, _, partiels, trous = (int(x) for x in bilan.groups())
                texte = (f"Questions d’habitants analysées ({origine}) : {total} besoins identifiés, "
                         f"dont {trous} sans réponse et {partiels} avec une réponse incomplète")
            else:
                texte = f"Analyse terminée : {origine}"
            evolutions.append(Evolution(imp.fini_le or imp.cree_le, texte, "/lacunes", "lacune"))
    return sorted(evolutions, key=lambda e: e.quand or datetime.min, reverse=True)[:n]


def sujets_a_surveiller(s, service: str = "", periode: str = "tout", n: int = 6) -> list[LigneLacune]:
    lignes = lister_lacunes(s, service=service, statut="a_traiter", periode=periode)
    lignes += lister_lacunes(s, service=service, statut="en_cours", periode=periode)
    rang = {"haute": 0, "moyenne": 1, "basse": 2}
    return sorted(lignes, key=lambda l: (rang[l.priorite], -l.nb))[:n]
