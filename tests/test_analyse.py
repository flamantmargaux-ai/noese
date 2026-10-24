import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

from app import analyse, fournisseur, ingestion
from app.models import Fiche, FicheVersion, Groupe, Question


def test_regroupement_separe_les_besoins():
    v = fournisseur.embeddings_lexicales(
        ["Horaires de la déchèterie", "Déchèterie : horaires d'ouverture", "Inscription à la cantine scolaire",
         "Cantine scolaire : inscription"]
    )
    e = analyse.regrouper(v, 0.4)
    assert e[0] == e[1] and e[2] == e[3] and e[0] != e[2]


def _preparer(s, repondeur):
    fournisseur.definir(fournisseur.Factice(repondeur=repondeur))
    analyse.enregistrer_document(s, None, "md", "Déchèterie", "https://ville.test/decheterie",
                                 "# Déchèterie\nOuverte du lundi au samedi de 9 h à 18 h.")
    analyse.enregistrer_questions(s, None, [ingestion.QuestionLue("horaires dechetterie dimanche ?")])
    s.flush()


def test_couvert_sans_citation_devient_trou(s):
    def repondeur(systeme, utilisateur):
        if "vérifies" in systeme:
            return {"verdict": "couvert", "reponse": "Ouverte.", "citations": []}
        return {}

    _preparer(s, repondeur)
    analyse.analyser(s)
    g = s.query(Groupe).one()
    assert g.verdict == "trou"
    assert g.sources == []


def test_citation_inventee_ignoree(s):
    def repondeur(systeme, utilisateur):
        if "vérifies" in systeme:
            return {"verdict": "couvert", "reponse": "Ouverte [9].", "citations": [9]}
        return {}

    _preparer(s, repondeur)
    analyse.analyser(s)
    assert s.query(Groupe).one().verdict == "trou"


def test_couvert_cite_une_source_reelle(s):
    def repondeur(systeme, utilisateur):
        if "vérifies" in systeme:
            return {"verdict": "couvert", "reponse": "Du lundi au samedi [1].", "citations": [1]}
        if "regrouper" in systeme and "Intentions" in utilisateur:
            return {"intentions": [{"n": 1, "intention": "Déchèterie : horaires"}]}
        return {"libelle": "Horaires de la déchèterie", "service": "Déchets", "mots_cles": ["déchèterie"]}

    _preparer(s, repondeur)
    analyse.analyser(s)
    g = s.query(Groupe).one()
    assert g.verdict == "couvert"
    assert g.sources[0]["url"] == "https://ville.test/decheterie"
    assert s.query(Question).one().groupe_id == g.id


def test_aucune_version_validee_sans_humain(s):
    fiche = Fiche()
    s.add(fiche)
    s.flush()
    s.add(FicheVersion(fiche_id=fiche.id, numero=1, titre="t", reponse="r", validee=True))
    with pytest.raises(IntegrityError):
        s.flush()
