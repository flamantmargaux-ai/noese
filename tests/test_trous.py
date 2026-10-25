from datetime import date, timedelta

from conftest import connecter
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import analyse, fournisseur, trous
from app.models import Evenement, Groupe, Question, Service


def _groupe(s, libelle, verdict, nb, service=None, statut="ouvert", transferts=0):
    g = Groupe(libelle=libelle, verdict=verdict, statut=statut, service_id=service.id if service else None)
    s.add(g)
    s.flush()
    for k in range(nb):
        s.add(Question(texte=f"{libelle} {k}", groupe_id=g.id, date=date.today() - timedelta(days=k * 20),
                       transfere=k < transferts, intention=libelle))
    s.flush()
    analyse.recompter(s)
    return g


def _jeu(s):
    urba = Service(nom="Urbanisme")
    enfance = Service(nom="Enfance")
    s.add_all([urba, enfance])
    s.flush()
    return {
        "dp": _groupe(s, "Délai déclaration préalable", "trou", 5, urba, transferts=5),
        "plu": _groupe(s, "Consulter le PLU", "trou", 2, urba),
        "cantine": _groupe(s, "Tarifs cantine 2026", "partiel", 3, enfance),
        "piscine": _groupe(s, "Horaires piscine", "couvert", 8),
        "hs": _groupe(s, "Question hors sujet", "trou", 1, statut="ecarte"),
    }


def test_a_traiter_trie_par_volume_sans_couverts_ni_ecartes(s):
    g = _jeu(s)
    lignes = trous.lister(s)
    assert [l.groupe.id for l in lignes] == [g["dp"].id, g["cantine"].id, g["plu"].id]
    dp = lignes[0]
    assert (dp.nb, dp.nb_30j, dp.part_transferts) == (5, 2, 100)


def test_filtres_service_statut_recherche(s):
    g = _jeu(s)
    urba = g["dp"].service_id
    assert {l.groupe.id for l in trous.lister(s, service=str(urba))} == {g["dp"].id, g["plu"].id}
    assert [l.groupe.id for l in trous.lister(s, statut="couvert")] == [g["piscine"].id]
    assert [l.groupe.id for l in trous.lister(s, statut="ecarte")] == [g["hs"].id]
    assert [l.groupe.id for l in trous.lister(s, service="aucun", statut="tous")] == [g["piscine"].id, g["hs"].id]
    assert [l.groupe.id for l in trous.lister(s, statut="tous", recherche="plu 1")] == [g["plu"].id]
    comptes = trous.compter_par_statut(s)
    assert comptes["a_traiter"] == 3 and comptes["tous"] == 5


def test_ecarte_ne_compte_plus_comme_trou_ouvert(s):
    g = _jeu(s)
    assert analyse.bilan(s)["trou"] == 2
    trous.ecarter(s, g["plu"])
    assert analyse.bilan(s)["trou"] == 1
    trous.rouvrir(s, g["plu"])
    assert g["plu"].statut == "ouvert"
    assert s.scalars(select(Evenement.type).order_by(Evenement.id)).all() == ["groupe_ecarte", "groupe_rouvert"]


def test_rassembler_puis_reanalyser_ne_separe_pas(s):
    fournisseur.definir(fournisseur.Factice(repondeur=lambda systeme, utilisateur: {}))
    g = _jeu(s)
    for q in s.scalars(select(Question)):
        q.embedding = fournisseur.embeddings_lexicales([q.intention])[0]
    trous.rassembler(s, g["plu"], g["dp"])
    assert s.get(Groupe, g["plu"].id) is None
    assert g["dp"].nb == 7
    analyse.regrouper_questions(s)
    assert s.get(Groupe, g["dp"].id).nb == 7
    assert {q.groupe_id for q in s.scalars(select(Question).where(Question.texte.like("Consulter le PLU%")))} == {g["dp"].id}


def test_pages_liste_detail_et_actions(s):
    from app.main import app

    g = _jeu(s)
    s.commit()
    with TestClient(app) as client:
        connecter(client)
        r = client.get("/lacunes?statut=a_traiter")
        assert r.status_code == 200 and "Délai déclaration préalable" in r.text and "Horaires piscine" not in r.text
        partiel = client.get("/lacunes?statut=ecarte", headers={"HX-Request": "true", "HX-Target": "resultats"})
        assert "<html" not in partiel.text and "Question hors sujet" in partiel.text
        r = client.get(f"/lacunes/{g['dp'].id}")
        assert "5 questions d’habitants" in r.text and "Passer à l’action" in r.text
        r = client.post(f"/lacunes/{g['plu'].id}/ecarter", data={"retour": "/lacunes"}, follow_redirects=False)
        assert r.status_code == 303 and "message=ecarte" in r.headers["location"]
        r = client.post(f"/lacunes/{g['dp'].id}/libelle", data={"libelle": "Délai d'instruction d'une déclaration préalable"})
        assert "Délai d&#39;instruction d&#39;une déclaration préalable" in r.text
        assert client.get("/lacunes/999999").status_code == 404
        # Un paramètre de retour hors de l'application est ignoré.
        assert 'href="/lacunes"' in client.get(f"/lacunes/{g['dp'].id}?retour=https://ailleurs.test").text
        # Les anciennes adresses mènent aux nouvelles.
        assert client.get(f"/trous/{g['dp'].id}", follow_redirects=False).headers["location"] == f"/lacunes/{g['dp'].id}"
