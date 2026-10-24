"""Lecture des sources : questions d'habitants (CSV, mails, copier-coller) et base existante
(pages web, PDF, Markdown, texte). Ne fait aucun appel au modèle."""
from __future__ import annotations

import csv
import email
import io
import mailbox
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

# ---------------------------------------------------------------- questions


@dataclass
class QuestionLue:
    texte: str
    date: date | None = None
    canal: str = ""
    reponse_donnee: str = ""
    transfere: bool | None = None


def _sans_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


SYNONYMES_COLONNES = {
    "question": ["question", "message", "texte", "text", "utterance", "demande", "requete", "input", "verbatim"],
    "date": ["date", "horodatage", "timestamp", "created_at", "datetime", "jour"],
    "reponse_donnee": ["reponse", "reponse_donnee", "answer", "response", "output", "reponse du bot"],
    "transfere": ["transfert", "transfere", "escalade", "handover", "transfer", "agent", "transfert agent"],
    "canal": ["canal", "channel", "source"],
}


def deviner_colonnes(entetes: list[str]) -> dict[str, str | None]:
    normes = {_sans_accents(e): e for e in entetes}
    correspondance: dict[str, str | None] = {}
    for champ, synonymes in SYNONYMES_COLONNES.items():
        correspondance[champ] = next((normes[s] for s in synonymes if s in normes), None)
        if correspondance[champ] is None:
            correspondance[champ] = next(
                (orig for n, orig in normes.items() if any(s in n for s in synonymes)), None
            )
    return correspondance


def lire_csv(contenu: bytes) -> tuple[list[str], list[dict]]:
    texte = contenu.decode("utf-8-sig", errors="replace")
    try:
        dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=";,\t|")
    except csv.Error:
        dialecte = csv.excel
    lecteur = csv.DictReader(io.StringIO(texte), dialect=dialecte)
    lignes = [l for l in lecteur]
    return list(lecteur.fieldnames or []), lignes


def _date(valeur: str) -> date | None:
    valeur = (valeur or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(valeur[: len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", valeur)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _booleen(valeur: str) -> bool | None:
    v = _sans_accents(valeur or "")
    if v in ("oui", "yes", "true", "1", "vrai", "o", "y"):
        return True
    if v in ("non", "no", "false", "0", "faux", "n"):
        return False
    return None


def questions_depuis_csv(lignes: list[dict], colonnes: dict[str, str | None], canal_defaut="") -> list[QuestionLue]:
    resultat = []
    col_q = colonnes.get("question")
    if not col_q:
        return []
    for l in lignes:
        texte = (l.get(col_q) or "").strip()
        if not texte:
            continue
        resultat.append(
            QuestionLue(
                texte=texte[:2000],
                date=_date(l.get(colonnes["date"], "")) if colonnes.get("date") else None,
                canal=(l.get(colonnes["canal"]) or "").strip() if colonnes.get("canal") else canal_defaut,
                reponse_donnee=(l.get(colonnes["reponse_donnee"]) or "").strip() if colonnes.get("reponse_donnee") else "",
                transfere=_booleen(l.get(colonnes["transfere"], "")) if colonnes.get("transfere") else None,
            )
        )
    return resultat


FORMULES = re.compile(
    r"^(bonjour|bonsoir|madame|monsieur|cordialement|bien cordialement|merci.*|bonne journ.*|salutations.*)[ ,.!]*$",
    re.I,
)


def _corps_mail(msg: email.message.Message) -> str:
    partie = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if partie is None:
        return ""
    texte = partie.get_content()
    if partie.get_content_type() == "text/html":
        texte = texte_depuis_html(texte)[1]
    lignes = []
    for ligne in texte.splitlines():
        if ligne.strip() in ("--", "-- ") or re.match(r"^(Le .* a écrit|On .* wrote)", ligne):
            break
        if ligne.startswith(">") or FORMULES.match(ligne.strip()):
            continue
        lignes.append(ligne.strip())
    return re.sub(r"\s+", " ", " ".join(lignes)).strip()


def _question_depuis_message(msg) -> QuestionLue | None:
    corps = _corps_mail(msg)
    sujet = (msg.get("Subject") or "").strip()
    if sujet and sujet.lower() not in ("question", "demande", "renseignement", "(sans objet)") and sujet.lower() not in corps.lower():
        corps = f"{sujet}. {corps}"
    if not corps:
        return None
    try:
        quand = email.utils.parsedate_to_datetime(msg.get("Date")).date()
    except (TypeError, ValueError):
        quand = None
    return QuestionLue(texte=corps[:2000], date=quand, canal="mail")


def questions_depuis_eml(contenu: bytes) -> list[QuestionLue]:
    q = _question_depuis_message(email.message_from_bytes(contenu, policy=policy.default))
    return [q] if q else []


def questions_depuis_mbox(contenu: bytes) -> list[QuestionLue]:
    with tempfile.NamedTemporaryFile(suffix=".mbox") as f:
        f.write(contenu)
        f.flush()
        boite = mailbox.mbox(f.name, factory=lambda fp: email.message_from_binary_file(fp, policy=policy.default))
        return [q for msg in boite if (q := _question_depuis_message(msg))]


def questions_depuis_collage(texte: str) -> list[QuestionLue]:
    return [QuestionLue(texte=l.strip()[:2000], canal="collage") for l in texte.splitlines() if len(l.strip()) > 3]


# ---------------------------------------------------------------- documents


class _ExtracteurHTML(HTMLParser):
    IGNORES = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside"}
    BLOCS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "dd", "dt"}

    def __init__(self):
        super().__init__()
        self.titre, self.morceaux, self.liens = "", [], []
        self._ignore, self._dans_titre, self._h1 = 0, False, ""
        self._dans_h1 = False

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORES:
            self._ignore += 1
        if tag == "title":
            self._dans_titre = True
        if tag == "h1":
            self._dans_h1 = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.liens.append(href)
        if tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORES and self._ignore:
            self._ignore -= 1
        if tag == "title":
            self._dans_titre = False
        if tag == "h1":
            self._dans_h1 = False
        if tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_data(self, data):
        if self._dans_titre:
            self.titre += data
        elif not self._ignore:
            self.morceaux.append(data)
            if self._dans_h1:
                self._h1 += data


def texte_depuis_html(html: str) -> tuple[str, str, list[str]]:
    p = _ExtracteurHTML()
    p.feed(html)
    texte = re.sub(r"[ \t]+", " ", "".join(p.morceaux))
    texte = re.sub(r"\n\s*\n+", "\n\n", texte).strip()
    return (p._h1.strip() or p.titre.strip()), texte, p.liens


def crawler(url_depart: str, max_pages: int = 200, client: httpx.Client | None = None, rappel=None):
    """Parcourt un site en restant sur le même domaine (et sous le même chemin de départ)."""
    depart = urlparse(url_depart)
    prefixe = depart.path.rsplit("/", 1)[0] + "/" if not depart.path.endswith("/") else depart.path
    client = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "Noese-Jardinier/0.1"})
    a_voir, vus, pages = [url_depart], set(), []
    while a_voir and len(pages) < max_pages:
        url = urldefrag(a_voir.pop(0))[0]
        if url in vus:
            continue
        vus.add(url)
        try:
            r = client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        titre, texte, liens = texte_depuis_html(r.text)
        if len(texte) > 80:
            pages.append({"url": str(r.url), "titre": titre or url, "texte": texte})
            if rappel:
                rappel(len(pages), url)
        for lien in liens:
            cible = urldefrag(urljoin(str(r.url), lien))[0]
            u = urlparse(cible)
            if u.scheme in ("http", "https") and u.netloc == depart.netloc and u.path.startswith(prefixe) and cible not in vus:
                if not re.search(r"\.(pdf|jpg|jpeg|png|gif|zip|docx?|xlsx?|css|js)$", u.path, re.I):
                    a_voir.append(cible)
    return pages


def texte_depuis_pdf(contenu: bytes) -> str:
    from pypdf import PdfReader

    lecteur = PdfReader(io.BytesIO(contenu))
    return "\n\n".join((p.extract_text() or "") for p in lecteur.pages).strip()


def titre_markdown(texte: str, defaut: str) -> str:
    m = re.search(r"^#\s+(.+)$", texte, re.M)
    return m[1].strip() if m else defaut


def decouper(titre: str, texte: str, taille: int = 900) -> list[str]:
    """Découpe en extraits d'environ `taille` caractères, sur les paragraphes."""
    texte = re.sub(r"^#\s+.+$", "", texte, count=1, flags=re.M).strip()
    paragraphes = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)|\n", texte) if p.strip()]
    extraits, courant = [], ""
    for p in paragraphes:
        if courant and len(courant) + len(p) > taille:
            extraits.append(courant)
            courant = ""
        courant = f"{courant}\n{p}".strip()
    if courant:
        extraits.append(courant)
    return extraits or [titre]
