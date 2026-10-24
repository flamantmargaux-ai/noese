"""Export de la base validée en Markdown, JSON et CSV, et relecture de ces mêmes fichiers.

Seules les fiches validées par un humain sont exportées. Chaque format contient les mêmes
champs ; `lire` rend exactement ce que `collecter` a produit, d'où le réimport sans perte.
Les questions des habitants sont jointes : elles servent d'exemples de formulation aux
chatbots et callbots en aval."""
import csv
import io
import json
import re
from datetime import date, datetime

from sqlalchemy import select

from . import verifications
from .models import Fiche, FicheVersion, Service

FORMAT = "noese-base/1"
CHAMPS = ["id", "version", "titre", "reponse", "conditions", "pieces", "lien_demarche", "service", "source",
          "validee_par", "validee_le", "a_verifier_le", "saisonniere", "perimee", "questions_habitants"]
SEPARATEUR = "\n"  # dans une cellule CSV : un élément par ligne (cellule entre guillemets)


# ------------------------------------------------------------------ collecte


def collecter(s, avec_perimees: bool = True, nb_questions: int = 8) -> list[dict]:
    noms = {sv.id: sv.nom for sv in s.scalars(select(Service))}
    fiches = []
    for f in s.scalars(select(Fiche).where(Fiche.statut == "validee").order_by(Fiche.id)):
        v = s.scalar(select(FicheVersion).where(FicheVersion.fiche_id == f.id, FicheVersion.validee.is_(True))
                     .order_by(FicheVersion.numero.desc()).limit(1))
        if v is None:
            continue
        perimee = verifications.fraicheur(s, f) == "perimee"
        if perimee and not avec_perimees:
            continue
        questions = (f.questions_exemples or [])[:nb_questions]
        fiches.append({
            "id": f"noese-{f.id}",
            "version": v.numero,
            "titre": v.titre,
            "reponse": v.reponse,
            "conditions": v.conditions or "",
            "pieces": [p for p in (v.pieces or "").splitlines() if p.strip()],
            "lien_demarche": v.lien_demarche or "",
            "service": noms.get(f.service_id, ""),
            "source": v.origine or "",
            "validee_par": v.validee_par,
            "validee_le": v.validee_le.date().isoformat(),
            "a_verifier_le": f.perime_le.isoformat() if f.perime_le else "",
            "saisonniere": bool(f.saisonniere),
            "perimee": perimee,
            "questions_habitants": [" ".join(q.split()) for q in questions],
        })
    return fiches


def entete(collectivite: str, nb: int) -> dict:
    return {"format": FORMAT, "collectivite": collectivite, "exporte_le": date.today().isoformat(), "nb_fiches": nb}


# ------------------------------------------------------------------ JSON


def vers_json(fiches: list[dict], collectivite: str) -> str:
    return json.dumps({**entete(collectivite, len(fiches)), "fiches": fiches}, ensure_ascii=False, indent=2)


def lire_json(texte: str) -> list[dict]:
    donnees = json.loads(texte)
    if not isinstance(donnees, dict) or donnees.get("format") != FORMAT:
        raise ValueError("Ce fichier JSON n'est pas un export Noèse.")
    return [_normaliser(f) for f in donnees.get("fiches", [])]


# ------------------------------------------------------------------ CSV (point-virgule, UTF-8 avec BOM : s'ouvre dans Excel)


def vers_csv(fiches: list[dict]) -> str:
    sortie = io.StringIO()
    sortie.write("﻿")
    w = csv.DictWriter(sortie, fieldnames=CHAMPS, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writeheader()
    for f in fiches:
        w.writerow({**f, "pieces": SEPARATEUR.join(f["pieces"]),
                    "questions_habitants": SEPARATEUR.join(f["questions_habitants"]),
                    "saisonniere": "oui" if f["saisonniere"] else "non", "perimee": "oui" if f["perimee"] else "non"})
    return sortie.getvalue()


def est_csv_noese(texte: str) -> bool:
    return texte.lstrip("﻿").split("\n", 1)[0].strip().split(";")[:3] == CHAMPS[:3]


def lire_csv(texte: str) -> list[dict]:
    if not est_csv_noese(texte):
        raise ValueError("Ce fichier CSV n'est pas un export Noèse.")
    lecteur = csv.DictReader(io.StringIO(texte.lstrip("﻿")), delimiter=";")
    fiches = []
    for l in lecteur:
        fiches.append(_normaliser({**l,
                                   "pieces": [p for p in (l.get("pieces") or "").splitlines() if p.strip()],
                                   "questions_habitants": [q for q in (l.get("questions_habitants") or "").splitlines() if q.strip()],
                                   "saisonniere": l.get("saisonniere") == "oui", "perimee": l.get("perimee") == "oui"}))
    return fiches


# ------------------------------------------------------------------ Markdown (lisible, et découpable en morceaux pour un RAG)

ETIQUETTES = {"service": "Service", "source": "Source", "validee_par": "Validée par", "validee_le": "Validée le",
              "a_verifier_le": "À vérifier le", "id": "Identifiant", "version": "Version"}


def vers_markdown(fiches: list[dict], collectivite: str) -> str:
    e = entete(collectivite, len(fiches))
    lignes = ["---", *[f"{k}: {v}" for k, v in e.items()], "---", "",
              f"# Base de connaissances de {collectivite}", "",
              f"{len(fiches)} fiche{'s' if len(fiches) > 1 else ''} validée{'s' if len(fiches) > 1 else ''} par les services. "
              f"Exportée le {e['exporte_le']}.", ""]
    for f in fiches:
        lignes += [f"## {f['titre']}", ""]
        if f["perimee"]:
            lignes += ["> Fiche à vérifier : sa date de validité est dépassée.", ""]
        lignes += [f["reponse"], ""]
        if f["conditions"]:
            lignes += ["### Conditions", "", f["conditions"], ""]
        if f["pieces"]:
            lignes += ["### Pièces à fournir", "", *[f"- {p}" for p in f["pieces"]], ""]
        if f["lien_demarche"]:
            lignes += ["### Démarche en ligne", "", f["lien_demarche"], ""]
        if f["questions_habitants"]:
            lignes += ["### Questions des habitants", "", *[f"- {q}" for q in f["questions_habitants"]], ""]
        lignes += ["### Informations", ""]
        lignes += [f"- {ETIQUETTES[k]} : {f[k]}" for k in ("service", "source", "validee_par", "validee_le", "a_verifier_le", "id", "version")]
        lignes += [f"- Change souvent : {'oui' if f['saisonniere'] else 'non'}", ""]
    return "\n".join(lignes)


def est_markdown_noese(texte: str) -> bool:
    return texte.lstrip("﻿").startswith("---") and f"format: {FORMAT}" in texte.split("\n---", 1)[0]


def lire_markdown(texte: str) -> list[dict]:
    if not est_markdown_noese(texte):
        raise ValueError("Ce fichier Markdown n'est pas un export Noèse.")
    corps = texte.split("\n---", 1)[1]
    fiches = []
    for bloc in re.split(r"^## ", corps, flags=re.M)[1:]:
        titre, _, reste = bloc.partition("\n")
        sections = re.split(r"^### ", reste, flags=re.M)
        reponse = re.sub(r"^> Fiche à vérifier.*$", "", sections[0], flags=re.M).strip()
        f = {"titre": titre.strip(), "reponse": reponse, "conditions": "", "pieces": [], "lien_demarche": "",
             "questions_habitants": [], "perimee": "> Fiche à vérifier" in sections[0]}
        for section in sections[1:]:
            nom, _, contenu = section.partition("\n")
            contenu = contenu.strip()
            puces = [l[2:].strip() for l in contenu.splitlines() if l.startswith("- ")]
            if nom.strip() == "Conditions":
                f["conditions"] = contenu
            elif nom.strip() == "Pièces à fournir":
                f["pieces"] = puces
            elif nom.strip() == "Démarche en ligne":
                f["lien_demarche"] = contenu
            elif nom.strip() == "Questions des habitants":
                f["questions_habitants"] = puces
            elif nom.strip() == "Informations":
                inverse = {v: k for k, v in ETIQUETTES.items()}
                for p in puces:
                    etiquette, _, valeur = p.partition(" : ")
                    if etiquette in inverse:
                        f[inverse[etiquette]] = valeur.strip()
                    elif etiquette == "Change souvent":
                        f["saisonniere"] = valeur.strip() == "oui"
        fiches.append(_normaliser(f))
    return fiches


# ------------------------------------------------------------------ commun


def _normaliser(f: dict) -> dict:
    """Ramène une fiche lue (quel que soit le format) au même dictionnaire que `collecter`."""
    version = str(f.get("version") or "1")
    return {
        "id": str(f.get("id") or ""),
        "version": int(version) if version.isdigit() else 1,
        "titre": str(f.get("titre") or "").strip(),
        "reponse": str(f.get("reponse") or "").strip(),
        "conditions": str(f.get("conditions") or "").strip(),
        "pieces": [str(p).strip() for p in f.get("pieces") or [] if str(p).strip()],
        "lien_demarche": str(f.get("lien_demarche") or "").strip(),
        "service": str(f.get("service") or "").strip(),
        "source": str(f.get("source") or "").strip(),
        "validee_par": str(f.get("validee_par") or "").strip(),
        "validee_le": str(f.get("validee_le") or "").strip(),
        "a_verifier_le": str(f.get("a_verifier_le") or "").strip(),
        "saisonniere": bool(f.get("saisonniere")),
        "perimee": bool(f.get("perimee")),
        "questions_habitants": [str(q).strip() for q in f.get("questions_habitants") or [] if str(q).strip()],
    }


def lire(nom_fichier: str, contenu: bytes) -> list[dict] | None:
    """Reconnaît un export Noèse ; renvoie None si le fichier n'en est pas un."""
    texte = contenu.decode("utf-8-sig", errors="replace")
    nom = nom_fichier.lower()
    if nom.endswith(".json"):
        try:
            return lire_json(texte)
        except (ValueError, json.JSONDecodeError):
            return None
    if nom.endswith(".csv") and est_csv_noese(texte):
        return lire_csv(texte)
    if nom.endswith((".md", ".markdown")) and est_markdown_noese(texte):
        return lire_markdown(texte)
    return None


def _date(valeur: str) -> date | None:
    try:
        return date.fromisoformat(valeur[:10])
    except ValueError:
        return None


def importer(s, fiches: list[dict], nom_fichier: str) -> dict:
    """Réimporte un export. Principe 1 : une fiche sans nom ni date de validation est refusée.
    Une fiche déjà présente à l'identique est ignorée."""
    from . import fournisseur as f_
    from .demandes import texte_fiche
    from .models import Evenement, Extrait

    services = {sv.nom: sv for sv in s.scalars(select(Service))}
    existantes = {(v.titre, v.reponse) for v in s.scalars(
        select(FicheVersion).join(Fiche, Fiche.id == FicheVersion.fiche_id)
        .where(Fiche.statut == "validee", FicheVersion.validee.is_(True)))}
    resultat = {"importees": 0, "deja_presentes": 0, "refusees": 0}
    for donnees in fiches:
        validee_le = _date(donnees["validee_le"]) if donnees["validee_par"] else None
        if not donnees["titre"] or not donnees["reponse"] or validee_le is None:
            resultat["refusees"] += 1
            continue
        if (donnees["titre"], donnees["reponse"]) in existantes:
            resultat["deja_presentes"] += 1
            continue
        sv = services.get(donnees["service"])
        fiche = Fiche(service_id=sv.id if sv else None, statut="validee", saisonniere=donnees["saisonniere"],
                      perime_le=_date(donnees["a_verifier_le"]), questions_exemples=donnees["questions_habitants"])
        s.add(fiche)
        s.flush()
        v = FicheVersion(fiche_id=fiche.id, numero=1, titre=donnees["titre"][:300], reponse=donnees["reponse"],
                         conditions=donnees["conditions"], pieces="\n".join(donnees["pieces"]),
                         lien_demarche=donnees["lien_demarche"],
                         origine=(donnees["source"] or donnees["service"] or "Import") + f" (importée de {nom_fichier})",
                         redigee_par=donnees["validee_par"], validee=True, validee_par=donnees["validee_par"],
                         validee_le=datetime.combine(validee_le, datetime.min.time()))
        s.add(v)
        extrait = Extrait(fiche_id=fiche.id, titre=v.titre, url=f"fiche:{fiche.id}", texte=texte_fiche(v))
        extrait.embedding = f_.fournisseur().embeddings([f"{extrait.titre}\n{extrait.texte}"])[0]
        s.add(extrait)
        s.add(Evenement(type="fiche_importee", objet="fiche", objet_id=fiche.id, acteur="referent_base",
                        details={"fichier": nom_fichier, "id_origine": donnees["id"]}))
        existantes.add((donnees["titre"], donnees["reponse"]))
        resultat["importees"] += 1
    s.flush()
    return resultat
