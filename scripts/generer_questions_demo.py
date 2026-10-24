"""Génère demo/questions.csv et demo/mails/*.eml à partir de demo/verite_terrain.yaml.

Le fournisseur de modèle écrit les reformulations une seule fois ; le résultat est figé
dans le dépôt pour que la démo et l'évaluation soient reproductibles.
Usage : uv run python scripts/generer_questions_demo.py
"""
import csv
import json
import random
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import yaml

from app.fournisseur import fournisseur

DEMO = Path(__file__).resolve().parent.parent / "demo"
SYSTEME = (
    "Tu écris des données de test réalistes pour une mairie française fictive, Ville-Exemple. "
    "Réponds en JSON : {\"questions\": [\"...\"]}."
)
CONSIGNE = """Écris {n} questions différentes qu'un habitant pourrait poser au chatbot ou au standard téléphonique de la mairie, sur ce sujet :
« {sujet} »

Varie fortement : style oral, parfois sans majuscules ni ponctuation, parfois des abréviations façon SMS (c, jsp, svp, bjr) ou une faute d'orthographe, parfois très court (3 mots), parfois avec un peu de contexte personnel. Chaque question doit rester compréhensible et porter sur ce sujet précis. Aucun nom de personne, aucun nom de ville réel."""

REPONSE_TROU = "Je n'ai pas trouvé d'information sur ce sujet. Je vous mets en relation avec un agent."


def main():
    rng = random.Random(2026)
    intentions = yaml.safe_load((DEMO / "verite_terrain.yaml").read_text())["intentions"]
    lignes = []
    for it in intentions:
        qs: list[str] = []
        for _ in range(3):
            r = fournisseur().chat(SYSTEME, CONSIGNE.format(n=it["n"] + 2, sujet=it["sujet"]))
            for q in r.get("questions", []):
                if isinstance(q, dict):
                    q = next((v for v in q.values() if isinstance(v, str)), "")
                q = str(q).strip()
                if q and q.lower() not in {x.lower() for x in qs}:
                    qs.append(q)
            if len(qs) >= it["n"]:
                break
        qs = qs[: it["n"]]
        print(it["id"], len(qs))
        for q in qs:
            lignes.append({"intention": it["id"], "texte": q, "trou": it["attendu"] == "trou"})

    rng.shuffle(lignes)
    fin = date(2026, 10, 20)
    mails_dir = DEMO / "mails"
    mails_dir.mkdir(exist_ok=True)
    for f in mails_dir.glob("*.eml"):
        f.unlink()
    csv_lignes, cle = [], []
    for i, l in enumerate(lignes):
        jour = fin - timedelta(days=rng.randint(0, 59))
        cle.append({"texte": l["texte"], "intention": l["intention"]})
        if i % 12 == 0:  # environ 8 % des questions arrivent par mail
            m = EmailMessage()
            m["From"] = f"habitant{i}@exemple.test"
            m["To"] = "contact@ville-exemple.test"
            m["Subject"] = "Question"
            m["Date"] = datetime(jour.year, jour.month, jour.day, 10, 0).strftime("%a, %d %b %Y %H:%M:%S +0200")
            m.set_content(f"Bonjour,\n\n{l['texte']}\n\nMerci d'avance.\nCordialement")
            (mails_dir / f"question-{i:03d}.eml").write_bytes(bytes(m))
            continue
        transfere = l["trou"] or rng.random() < 0.08
        csv_lignes.append({
            "date": jour.isoformat(),
            "canal": "callbot" if rng.random() < 0.3 else "chatbot",
            "question": l["texte"],
            "reponse_donnee": REPONSE_TROU if l["trou"] else "Réponse fournie par l'assistant.",
            "transfert": "oui" if transfere else "non",
        })
    with open(DEMO / "questions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_lignes[0]), delimiter=";")
        w.writeheader()
        w.writerows(csv_lignes)
    (DEMO / "questions_cle.json").write_text(json.dumps(cle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(csv_lignes), "questions CSV,", len(lignes) - len(csv_lignes), "mails")


if __name__ == "__main__":
    main()
