"""Boucle demande → réponse → fiche → validation.

Principe 1 : l'IA prépare, l'agent valide. Une fiche n'entre dans la base (table `extrait`,
utilisée par la recherche et par l'export) qu'au moment de `valider_fiche`, avec le nom
de la personne qui valide.
"""
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import analyse, liens, mails
from .config import config
from .fournisseur import fournisseur
from .models import Demande, Evenement, Extrait, Fiche, FicheVersion, Groupe, Parametres, Question, ReponseBrute, Service

MOIS = "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split()


def date_fr(d) -> str:
    return f"{'1er' if d.day == 1 else d.day} {MOIS[d.month - 1]} {d.year}"


def _hacher(jeton: str) -> str:
    return liens.hacher(jeton)


def lien(jeton: str) -> str:
    return liens.url("r", jeton)


def jeton_de(d: Demande) -> str:
    """Recalcule le lien d'une demande (pour une relance). Une demande créée avant l'ajout du sel
    reçoit un nouveau lien : l'ancien cesse de fonctionner."""
    if not d.sel:
        d.sel = liens.nouveau_sel()
        d.jeton_hash = liens.hacher(liens.jeton("demande", d.id, d.sel))
    return liens.jeton("demande", d.id, d.sel)


def _journal(s, type_: str, objet: str, objet_id: int, acteur: str = "", **details) -> None:
    s.add(Evenement(type=type_, objet=objet, objet_id=objet_id, acteur=acteur, details=details))


def exemples(s, groupe_id: int, n: int = 5) -> list[str]:
    return s.scalars(
        select(Question.texte).where(Question.groupe_id == groupe_id)
        .order_by(Question.date.desc().nulls_last()).limit(n)
    ).all()


def nb_recent(s, groupe_id: int, jours: int = 30) -> int:
    depuis = date.today() - timedelta(days=jours)
    return len(s.scalars(select(Question.id).where(Question.groupe_id == groupe_id, Question.date >= depuis)).all())


# ------------------------------------------------------------------ rédaction de la demande

SYSTEME_DEMANDE = (
    "Tu aides le référent de la base de connaissances d'une mairie à poser une question précise à un service. "
    "Tu réponds uniquement en JSON."
)
CONSIGNE_DEMANDE = """Besoin des habitants : {libelle}
Ce qui manque dans la base : {manque}
Ce que la base dit déjà : {reponse}
Vraies questions d'habitants :
{exemples}

Rédige la question à poser au service {service}. Elle doit être concrète et appeler une réponse factuelle courte,
comme « Quel est le délai d'instruction d'une déclaration préalable de travaux ? ».
Une seule question, ou deux au maximum si elles sont indissociables. Vouvoiement, pas de formule de politesse,
pas de jargon, jamais une demande vague comme « merci de mettre à jour vos fiches ».

JSON : {{"question": "..."}}"""


def rediger_question(s, g: Groupe) -> str:
    r = fournisseur().chat(SYSTEME_DEMANDE, CONSIGNE_DEMANDE.format(
        libelle=g.libelle, manque=g.manque or "(non précisé)", reponse=g.reponse or "(rien)",
        exemples="\n".join(f"- {e}" for e in exemples(s, g.id)),
        service=g.service.nom if g.service else "concerné",
    ))
    question = analyse.texte_brut(analyse._objet(r).get("question"))
    return question or f"Que faut-il répondre aux habitants sur ce sujet : {g.libelle.lower()} ?"


# ------------------------------------------------------------------ envoi


def corps_mail(s, d: Demande, g: Groupe, jeton: str, transfert_par: str = "", relance: bool = False) -> tuple[str, str]:
    recent = nb_recent(s, g.id)
    n, periode = (recent, " ces 30 derniers jours") if recent else (g.nb, "")
    objet = "Une question d'habitant attend votre réponse (2 minutes)"
    intro = f"{transfert_par} vous transfère cette question, en pensant que vous avez la réponse.\n\n" if transfert_par else ""
    if relance:
        objet = "Rappel : une question d'habitant attend toujours votre réponse (2 minutes)"
        intro = (f"Nous vous avons écrit le {date_fr(d.envoyee_le)}. Si vous avez eu le temps d'y penser, "
                 "votre réponse aidera directement les habitants et vos collègues de l'accueil.\n\n")
    corps = f"""Bonjour{(' ' + d.destinataire_nom) if d.destinataire_nom else ''},

{intro}{n} habitant{'s ont' if n > 1 else ' a'} posé cette question{periode}, et notre base de connaissances ne sait pas y répondre :

« {d.question} »

Répondez avec vos mots, même en style télégraphique, ou dictez votre réponse depuis votre téléphone. Nous la mettons en forme, vous relisez et vous validez. Rien n'est publié sans votre accord.

Répondre (sans compte, sans mot de passe) :
{lien(jeton)}

Vous n'êtes pas la bonne personne ? Le même lien vous permet de transmettre la question à un collègue.

Le Jardinier, outil de la base de connaissances de la relation usagers
"""
    return objet, corps


def creer_demande(s, g: Groupe, question: str, destinataire_email: str, destinataire_nom: str = "",
                  transferee_de: Demande | None = None, transfert_par: str = "") -> tuple[Demande, str]:
    d = Demande(groupe_id=g.id, service_id=g.service_id, destinataire_nom=destinataire_nom.strip(),
                destinataire_email=destinataire_email.strip().lower(), question=question.strip(),
                jeton_hash=liens.hacher(secrets.token_hex(16)), sel=liens.nouveau_sel(), statut="envoyee",
                envoyee_le=datetime.now(), transferee_de=transferee_de.id if transferee_de else None)
    s.add(d)
    s.flush()
    jeton = liens.jeton("demande", d.id, d.sel)
    d.jeton_hash = liens.hacher(jeton)
    objet, corps = corps_mail(s, d, g, jeton, transfert_par)
    mails.envoyer(s, d.destinataire_email, objet, corps, lien=lien(jeton), demande_id=d.id,
                  nom_destinataire=d.destinataire_nom)
    g.statut = "demande"
    _journal(s, "demande_envoyee", "demande", d.id, acteur="referent_base" if not transfert_par else transfert_par,
             groupe=g.id, destinataire=d.destinataire_email)
    return d, jeton


def demande_par_jeton(s, jeton: str) -> Demande | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", jeton or ""):
        return None
    return s.scalar(select(Demande).where(Demande.jeton_hash == _hacher(jeton)))


def demandes_du_groupe(s, groupe_id: int) -> list[Demande]:
    return s.scalars(select(Demande).where(Demande.groupe_id == groupe_id).order_by(Demande.id.desc())).all()


def transferer(s, d: Demande, email: str, nom: str, par: str) -> Demande:
    g = s.get(Groupe, d.groupe_id)
    d.statut = "transferee"
    nouvelle, _ = creer_demande(s, g, d.question, email, nom, transferee_de=d, transfert_par=par or d.destinataire_email)
    nouvelle.service_id = d.service_id
    return nouvelle


# ------------------------------------------------------------------ réponse et fiche

SYSTEME_FICHE = (
    "Tu mets en forme la réponse d'un agent de mairie pour la base de connaissances que consultent les assistants "
    "de relation usagers. Tu n'inventes rien : tu n'utilises que ce que l'agent a écrit. Tu réponds en JSON."
)
CONSIGNE_FICHE = """Question posée au service : {question}
Exemples de questions d'habitants :
{exemples}

Réponse de l'agent, telle qu'il l'a écrite ou dictée :
\"\"\"{reponse}\"\"\"

Rédige une fiche claire pour un habitant, en langage simple, sans jargon administratif, au vouvoiement.
- "titre" : la question de l'habitant, reformulée simplement (moins de 12 mots).
- "reponse" : la réponse en 1 à 4 phrases courtes.
- "conditions" : les conditions ou cas particuliers que l'agent mentionne, sinon "". Jamais de remarque sur ce qui manque : cela va dans "a_verifier".
- "pieces" : la liste des pièces à fournir que l'agent mentionne, sinon [].
- "lien_demarche" : une adresse web seulement si l'agent en donne une, sinon "".
- "saisonniere" : true si l'information change souvent (tarif, date, horaire saisonnier, année), sinon false.
- "a_verifier" : ce qui est ambigu ou incomplet dans la réponse de l'agent, en une phrase, sinon "".
N'ajoute aucune information absente de la réponse de l'agent.

JSON : {{"titre": "", "reponse": "", "conditions": "", "pieces": [], "lien_demarche": "", "saisonniere": false, "a_verifier": ""}}"""


SIGNES_SAISONNIERS = re.compile(r"tarif|prix|€|\beuros?\b|\b20[2-9]\d\b|été|hiver|vacances|saison", re.I)


@dataclass
class Proposition:
    fiche: Fiche
    version: FicheVersion
    a_verifier: str


def _lien_valide(url: str, texte_agent: str) -> str:
    """On ne garde un lien que s'il figure dans ce qu'a écrit l'agent."""
    url = (url or "").strip()
    return url if url and url in texte_agent else ""


def proposer_fiche(s, d: Demande, texte: str, dictee: bool = False) -> Proposition:
    texte = texte.strip()
    s.add(ReponseBrute(demande_id=d.id, texte=texte, dictee=dictee))
    r = analyse._objet(fournisseur().chat(SYSTEME_FICHE, CONSIGNE_FICHE.format(
        question=d.question, exemples="\n".join(f"- {e}" for e in exemples(s, d.groupe_id, 4)), reponse=texte[:6000],
    )))
    pieces = r.get("pieces") or []
    if isinstance(pieces, str):
        pieces = [pieces]
    fiche = s.scalar(select(Fiche).where(Fiche.demande_id == d.id, Fiche.statut == "brouillon"))
    if fiche is None:
        fiche = Fiche(service_id=d.service_id, groupe_id=d.groupe_id, demande_id=d.id, statut="brouillon")
        s.add(fiche)
        s.flush()
    # Par prudence, un tarif, un prix ou une année rend la fiche « saisonnière » (vérifiée plus souvent).
    fiche.saisonniere = bool(r.get("saisonniere")) or bool(SIGNES_SAISONNIERS.search(texte + " " + d.question))
    numero = (s.scalar(select(FicheVersion.numero).where(FicheVersion.fiche_id == fiche.id)
                       .order_by(FicheVersion.numero.desc()).limit(1)) or 0) + 1
    version = FicheVersion(
        fiche_id=fiche.id, numero=numero,
        titre=analyse.texte_brut(r.get("titre"))[:300] or d.question[:300],
        reponse=analyse.texte_brut(r.get("reponse")) or texte,
        conditions=analyse.texte_brut(r.get("conditions")),
        pieces="\n".join(analyse.texte_brut(p) for p in pieces if str(p).strip()),
        lien_demarche=_lien_valide(str(r.get("lien_demarche") or ""), texte),
        origine=_origine(s, d),
        redigee_par="IA, d'après la réponse de " + (d.destinataire_nom or d.destinataire_email),
        a_verifier=analyse.texte_brut(r.get("a_verifier")),
        validee=False,
    )
    s.add(version)
    s.flush()
    _journal(s, "fiche_proposee", "fiche", fiche.id, acteur=d.destinataire_email, version=numero, dictee=dictee)
    return Proposition(fiche, version, analyse.texte_brut(r.get("a_verifier")))


def _origine(s, d: Demande) -> str:
    service = s.get(Service, d.service_id).nom if d.service_id else "Service"
    return f"{service}, réponse de {d.destinataire_nom or d.destinataire_email}"


def reponse_d_origine(s, d: Demande) -> str:
    return s.scalar(select(ReponseBrute.texte).where(ReponseBrute.demande_id == d.id)
                    .order_by(ReponseBrute.id.desc()).limit(1)) or ""


def version_courante(s, fiche: Fiche) -> FicheVersion | None:
    return s.scalar(select(FicheVersion).where(FicheVersion.fiche_id == fiche.id).order_by(FicheVersion.numero.desc()).limit(1))


def brouillon_de(s, d: Demande) -> Fiche | None:
    return s.scalar(select(Fiche).where(Fiche.demande_id == d.id).order_by(Fiche.id.desc()).limit(1))


def duree_peremption(s, saisonniere: bool) -> int:
    p = s.get(Parametres, 1) or Parametres()
    return (p.peremption_saison_jours or 30) if saisonniere else (p.peremption_jours or 182)


def texte_fiche(v: FicheVersion) -> str:
    """Texte indexé dans la base pour la recherche (et l'export)."""
    morceaux = [v.titre, v.reponse]
    if v.conditions:
        morceaux.append(f"Conditions : {v.conditions}")
    if v.pieces:
        morceaux.append("Pièces à fournir : " + "; ".join(v.pieces.splitlines()))
    if v.lien_demarche:
        morceaux.append(f"Démarche en ligne : {v.lien_demarche}")
    return "\n".join(morceaux)


SYSTEME_EXEMPLES = "Tu aides une mairie à relier des questions d'habitants à une fiche d'information. Tu réponds en JSON."
CONSIGNE_EXEMPLES = """Fiche : {titre}
{reponse}

Questions d'habitants :
{questions}

Donne les numéros des questions auxquelles cette fiche répond directement. Exclus celles qui portent sur un autre sujet,
même voisin. JSON : {{"repond_a": [1, 2]}}"""


def choisir_exemples(s, fiche: Fiche, v: FicheVersion, n: int = 8) -> list[str]:
    """Questions du besoin auxquelles la fiche répond vraiment ; servent d'exemples dans l'export."""
    if not fiche.groupe_id:
        return []
    questions = s.scalars(select(Question.texte).where(Question.groupe_id == fiche.groupe_id)
                          .order_by(Question.date.desc().nulls_last()).limit(25)).all()
    if not questions:
        return []
    try:
        r = analyse._objet(fournisseur().chat(SYSTEME_EXEMPLES, CONSIGNE_EXEMPLES.format(
            titre=v.titre, reponse=v.reponse, questions="\n".join(f"{k + 1}. {q[:300]}" for k, q in enumerate(questions)))))
    except Exception:  # un échec ici ne doit pas bloquer la validation
        return []
    choisis = []
    for numero in r.get("repond_a") or []:
        try:
            k = int(str(numero).strip("[] ")) - 1
        except ValueError:
            continue
        if 0 <= k < len(questions) and questions[k] not in choisis:
            choisis.append(" ".join(questions[k].split()))
    return choisis[:n]


class ValidationRefusee(ValueError):
    pass


def valider_fiche(s, fiche: Fiche, valide_par: str, titre: str, reponse: str, conditions: str, pieces: str,
                  lien_demarche: str, saisonniere: bool) -> FicheVersion:
    """Seul chemin par lequel une fiche entre dans la base."""
    valide_par = valide_par.strip()
    if not valide_par:
        raise ValidationRefusee("Indiquez votre nom pour valider la fiche.")
    if not titre.strip() or not reponse.strip():
        raise ValidationRefusee("Le titre et la réponse sont nécessaires.")
    if lien_demarche.strip() and not re.match(r"^https?://", lien_demarche.strip()):
        raise ValidationRefusee("Le lien vers la démarche doit commencer par http:// ou https://.")
    precedente = version_courante(s, fiche)
    maintenant = datetime.now()
    version = FicheVersion(
        fiche_id=fiche.id, numero=(precedente.numero if precedente else 0) + 1,
        titre=titre.strip()[:300], reponse=reponse.strip(), conditions=conditions.strip(),
        pieces="\n".join(l.strip(" -•\t") for l in pieces.splitlines() if l.strip(" -•\t")),
        lien_demarche=lien_demarche.strip(), origine=precedente.origine if precedente else "",
        redigee_par=precedente.redigee_par if precedente else valide_par,
        validee=True, validee_par=valide_par, validee_le=maintenant,
    )
    s.add(version)
    fiche.statut = "validee"
    fiche.saisonniere = saisonniere
    fiche.perime_le = date.today() + timedelta(days=duree_peremption(s, saisonniere))
    s.flush()

    # La fiche validée devient un extrait de la base, cité comme source.
    for ancien in s.scalars(select(Extrait).where(Extrait.fiche_id == fiche.id)):
        s.delete(ancien)
    extrait = Extrait(fiche_id=fiche.id, titre=version.titre, url=f"fiche:{fiche.id}", texte=texte_fiche(version))
    extrait.embedding = fournisseur().embeddings([f"{extrait.titre}\n{extrait.texte}"])[0]
    s.add(extrait)
    fiche.questions_exemples = choisir_exemples(s, fiche, version)

    d = s.get(Demande, fiche.demande_id) if fiche.demande_id else None
    if d:
        d.statut, d.repondue_le = "repondue", maintenant
    g = s.get(Groupe, fiche.groupe_id) if fiche.groupe_id else None
    if g:
        s.flush()
        analyse.juger_groupe(s, g)
        g.statut = "resolu" if g.verdict == "couvert" else "repondu"
    _journal(s, "fiche_validee", "fiche", fiche.id, acteur=valide_par, version=version.numero)
    return version
