"""Évaluation sur les données de démo : trous volontaires retrouvés, sources des « couvert ».

Usage : uv run python -m scripts.evaluer [--reinitialiser]
Critère d'acceptation : au moins 80 % des trous volontaires retrouvés, chaque « couvert » cite une source réelle.
"""
import json
import sys
from collections import Counter, defaultdict

import yaml
from sqlalchemy import select

from app import analyse, db, demo
from app.models import Extrait, Groupe, Question


def main():
    db.initialiser()
    with db.session() as s:
        if "--reinitialiser" in sys.argv:
            analyse.tout_effacer(s)
        if not s.scalar(select(Question.id).limit(1)):
            demo.charger_tout(s)
        print(analyse.analyser(s, lambda e, f, t: print(f"  {e} {f}/{t}", end="\r")))

    cle = {d["texte"]: d["intention"] for d in json.loads((demo.DEMO / "questions_cle.json").read_text())}
    attendu = {i["id"]: i for i in yaml.safe_load((demo.DEMO / "verite_terrain.yaml").read_text())["intentions"]}
    with db.session() as s:
        groupes = {g.id: g for g in s.scalars(select(Groupe))}
        verdicts = defaultdict(Counter)
        for q in s.scalars(select(Question)):
            intention = cle.get(q.texte) or next((i for t, i in cle.items() if t in q.texte), None)
            if intention and q.groupe_id in groupes:
                verdicts[intention][groupes[q.groupe_id].verdict] += 1
        ids_extraits = {i for (i,) in s.execute(select(Extrait.id))}
        couverts_sans_source = [g.libelle for g in groupes.values() if g.verdict == "couvert"
                                and not any(src.get("extrait_id") in ids_extraits for src in g.sources)]

        print("\nIntention  attendu  verdicts obtenus")
        trous, trouves, faux_trous, faux_trous_stricts = 0, 0, [], []
        for iid, it in attendu.items():
            c = verdicts[iid]
            detecte = (c["trou"] + c["partiel"]) > c["couvert"]
            if it["attendu"] == "trou":
                trous += 1
                trouves += detecte
            elif detecte:
                faux_trous.append(iid)
                if c["trou"] > c["couvert"] + c["partiel"]:
                    faux_trous_stricts.append(iid)
            print(f"{iid:<10} {it['attendu']:<8} {dict(c)}  {'OK' if detecte == (it['attendu'] == 'trou') else '--'}")
        print(f"\nTrous volontaires retrouvés : {trouves}/{trous} ({100 * trouves // trous} %)")
        print(f"Sujets couverts jugés partiels ou trous : {len(faux_trous)} {faux_trous}")
        print(f"  dont classés trou franc : {len(faux_trous_stricts)} {faux_trous_stricts}")
        print(f"Groupes « couvert » sans source réelle : {len(couverts_sans_source)}")
        print(f"Groupes : {len(groupes)}")
        ok = trouves / trous >= 0.8 and not couverts_sans_source
        print("CRITÈRE ATTEINT" if ok else "CRITÈRE NON ATTEINT")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
