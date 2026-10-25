"""Refonte « mémoire de la collectivité » : sujets, indicateurs, priorités, recherche, enrichissement, lacunes."""
import io
import pathlib
import re
import zipfile
from datetime import date, timedelta

import pytest
from conftest import connecter
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import enrichir, fournisseur, memoire
from app.fournisseur import ErreurFournisseur
from app.models import Document, Evenement, Extrait, Fiche, Groupe, Question, Service


def repondeur(systeme, utilisateur):
    if "constituer sa mémoire" in systeme:
        return {"connaissances": [{"titre": "Horaires de la déchèterie", "reponse": "Du lundi au samedi, de 9 h à 18 h.",
                                   "conditions": "", "pieces": [], "lien_demarche": "https://inventee.example", "saisonniere": False}]}
    if "vérifies" in systeme:
        if "Fiche validée" in utilisateur:
            return {"verdict": "couvert", "reponse": "De 9 h à 18 h [1].", "citations": [1]}
        return {"verdict": "trou", "manque": "Les horaires."}
    return {}


@pytest.fixture()
def ville(s):
    fournisseur.definir(fournisseur.Factice(repondeur=repondeur))
    dechets = Service(nom="Déchets", referent_nom="Dominique", referent_email="dechets@ville.test")
    urba = Service(nom="Urbanisme")
    s.add_all([dechets, urba])
    s.flush()
    trou = Groupe(libelle="Horaires de la déchèterie", verdict="trou", statut="ouvert", service_id=dechets.id, manque="Les horaires.")
    couvert = Groupe(libelle="Jours de collecte", verdict="couvert", statut="ouvert", service_id=dechets.id)
    partiel = Groupe(libelle="Clôture : hauteur autorisée", verdict="partiel", statut="ouvert", service_id=urba.id)
    s.add_all([trou, couvert, partiel])
    s.flush()
    aujourd_hui = date.today()
    for k in range(9):  # 9 questions, dont 5 transférées : priorité haute
        s.add(Question(texte=f"horaires decheterie {k}", groupe_id=trou.id, intention="Déchèterie : horaires d'ouverture",
                       date=aujourd_hui - timedelta(days=k * 10), transfere=k < 5))
    for k in range(3):
        s.add(Question(texte=f"jour poubelle {k}", groupe_id=couvert.id, intention="Collecte des déchets : jours", date=aujourd_hui))
    s.add(Question(texte="hauteur cloture ?", groupe_id=partiel.id, intention="Clôture : hauteur", date=aujourd_hui - timedelta(days=60)))
    s.flush()
    memoire.calculer_sujets(s)
    from app import analyse

    analyse.recompter(s)
    return {"dechets": dechets, "urba": urba, "trou": trou, "couvert": couvert, "partiel": partiel}


def _docx(*paragraphes: str) -> bytes:
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphes) + "</w:body></w:document>")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", xml)
    return b.getvalue()


# ------------------------------------------------------------------ sujets, indicateurs, priorités


def test_sujets_deduits_des_intentions_et_choix_d_un_agent_conserve(s, ville):
    assert ville["trou"].sujet == "Déchèterie"
    assert dict(memoire.sujets(s))["Déchèterie"] == 1
    ville["trou"].sujet, ville["trou"].sujet_fixe = "Horaires des équipements", True
    memoire.calculer_sujets(s)
    assert ville["trou"].sujet == "Horaires des équipements"


def test_indicateurs_coherents_avec_les_donnees(s, ville):
    i = memoire.indicateurs(s)
    assert (i["couverture"].couvertes, i["couverture"].evaluees, i["couverture"].taux) == (3, 13, 23)
    assert (i["sans_reponse"], i["incompletes"], i["questions_lacunes"]) == (1, 1, 10)
    assert (i["a_verifier"], i["connaissances"]) == (0, 0)
    # Filtre par service et par période
    assert memoire.indicateurs(s, service=str(ville["urba"].id))["incompletes"] == 1
    # Sur 30 jours : 3 questions de collecte et 4 de déchèterie (il y a 0, 10, 20 et 30 jours)
    assert memoire.couverture_questions(s, periode="30").evaluees == 3 + 4
    lignes = {l.nom: l for l in memoire.couverture_par_service(s)}
    assert (lignes["Déchets"].couverture.taux, lignes["Déchets"].lacunes) == (25, 1)


def test_priorite_simple_et_explicable(s, ville):
    stats = memoire.stats_besoins(s)
    assert memoire.priorite(ville["trou"], stats[ville["trou"].id]) == ("haute", True)
    assert memoire.priorite(ville["partiel"], stats[ville["partiel"].id]) == ("basse", True)
    ville["partiel"].priorite_manuelle = "haute"
    assert memoire.priorite(ville["partiel"], stats[ville["partiel"].id]) == ("haute", False)


def test_recherche_par_mots_sans_resultats_hors_sujet(s, ville):
    enrichir.enregistrer_source(s, "md", "Déchèterie", "La déchèterie est ouverte du lundi au samedi.")
    enrichir.enregistrer_source(s, "md", "Piscine", "Bassin de 25 mètres, bonnet obligatoire.")
    _, docs = memoire.recherche(s, "déchèteries ouvertes")
    titres = [s.get(Document, d).titre for d in docs]
    assert titres == ["Déchèterie"]
    assert memoire.recherche(s, "xyzzy") == ([], [])


# ------------------------------------------------------------------ enrichir la mémoire


def test_lecture_des_formats_et_messages_clairs():
    assert enrichir.lire_fichier("note.docx", _docx("Titre", "Le service ouvre à 9 h."))[2] == "Titre\n\nLe service ouvre à 9 h."
    with pytest.raises(enrichir.ErreurSource, match="Word ancien"):
        enrichir.lire_fichier("vieux.doc", b"xx")
    with pytest.raises(enrichir.ErreurSource, match="pas d'un format pris en charge"):
        enrichir.lire_fichier("image.png", b"xx")
    with pytest.raises(enrichir.ErreurSource, match="vide"):
        enrichir.lire_fichier("vide.txt", b"   ")


def test_une_proposition_n_est_pas_une_connaissance_tant_qu_elle_n_est_pas_validee(s, ville):
    doc = enrichir.enregistrer_source(s, "saisie", "Note", "La déchèterie ouvre du lundi au samedi, de 9 h à 18 h.",
                                      service_id=ville["dechets"].id, sujet="déchèterie", ajoute_par="Camille")
    assert doc.traitement == "consultable" and doc.sujet == "Déchèterie"
    assert s.scalar(select(func.count(Extrait.id)).where(Extrait.document_id == doc.id)) == 1
    (f,) = enrichir.proposer(s, doc, ville["trou"])
    assert f.statut == "brouillon" and doc.traitement == "a_relire"
    assert memoire.version_affichee(s, f).lien_demarche == ""  # lien absent du texte : retiré
    assert memoire.indicateurs(s)["connaissances"] == 0
    assert s.scalar(select(func.count(Extrait.id)).where(Extrait.fiche_id == f.id)) == 0
    assert memoire.etat_connaissance(s, f).cle == "proposee"


def test_valider_une_proposition_resout_la_lacune_si_la_memoire_la_couvre(s, ville):
    doc = enrichir.enregistrer_source(s, "saisie", "Note", "Ouvert de 9 h à 18 h.", service_id=ville["dechets"].id)
    (f,) = enrichir.proposer(s, doc, ville["trou"])
    v = memoire.version_affichee(s, f)
    enrichir.valider(s, f, "Camille", titre=v.titre, reponse=v.reponse, conditions="", pieces="", lien_demarche="", saisonniere=False)
    assert f.statut == "validee" and doc.traitement == "traitee"
    assert ville["trou"].statut == "resolu" and ville["trou"].verdict == "couvert"
    assert s.scalar(select(Evenement).where(Evenement.type == "lacune_resolue", Evenement.objet_id == ville["trou"].id))
    assert memoire.indicateurs(s)["connaissances"] == 1


def test_ecarter_une_proposition(s, ville):
    doc = enrichir.enregistrer_source(s, "saisie", "Note", "Ouvert de 9 h à 18 h.")
    (f,) = enrichir.proposer(s, doc)
    enrichir.ecarter(s, f, "Camille")
    assert f.statut == "archivee" and doc.traitement == "consultable"


def test_la_source_reste_enregistree_si_la_proposition_echoue(s, ville):
    def en_panne(systeme, utilisateur):
        raise ErreurFournisseur("indisponible")

    fournisseur.definir(fournisseur.Factice(repondeur=en_panne))
    doc = enrichir.enregistrer_source(s, "md", "Guide", "Contenu utile.")
    with pytest.raises(ErreurFournisseur):
        enrichir.proposer(s, doc)
    assert s.get(Document, doc.id).traitement == "consultable"


# ------------------------------------------------------------------ pages


def test_les_quatre_espaces_et_le_parcours_d_ajout(s, ville, monkeypatch):
    from app.main import app

    s.commit()
    with TestClient(app) as client:
        connecter(client)
        import html

        accueil = html.unescape(client.get("/").text)
        assert "La mémoire de votre collectivité" in accueil and "Enrichir la mémoire" in accueil
        assert "23\u202f%" in accueil and "3 questions d’habitants sur 13" in accueil
        assert "Sujets à surveiller" in accueil and "Horaires de la déchèterie" in accueil
        for nom in ("Vue d'ensemble", "Explorer la mémoire", "Enrichir la mémoire", "Lacunes et qualité"):
            assert nom in accueil
        assert "Que souhaitez-vous ajouter à la mémoire ?" in client.get("/enrichir").text
        for mode in ("document", "page", "oral", "saisie"):
            assert client.get(f"/enrichir/{mode}").status_code == 200
        assert client.get("/enrichir/inconnu").status_code == 404

        # Saisie trop courte : refusée, rien d'enregistré
        r = client.post("/enrichir/saisie", data={"texte": "court"})
        assert r.status_code == 422 and "Décrivez la connaissance" in r.text

        # Document Word rattaché à une lacune : source enregistrée, proposition à relire
        r = client.post("/enrichir/document", data={"lacune": str(ville["trou"].id)}, follow_redirects=False,
                        files={"fichiers": ("horaires.docx", _docx("Horaires", "Ouvert de 9 h à 18 h."))})
        assert r.status_code == 303 and "message=propose" in r.headers["location"]
        relecture = client.get(r.headers["location"]).text
        assert "Relisez ce que nous avons tiré de votre source" in relecture and "Horaires de la déchèterie" in relecture

        # Plusieurs fichiers, dont un refusé : les bons sont gardés, l'erreur est dite
        r = client.post("/enrichir/document", follow_redirects=True, files=[
            ("fichiers", ("a.md", b"# A\\n\\nContenu A")), ("fichiers", ("b.txt", b"Contenu B")), ("fichiers", ("c.doc", b"xx"))])
        assert "2 documents enregistrés" in r.text and "format Word ancien" in r.text

        # Page web : aperçu avant ajout, puis erreur claire si la page est inaccessible
        monkeypatch.setattr(enrichir, "recuperer_page", lambda url: {"url": "https://ville.test/decheterie", "titre": "Déchèterie", "texte": "x" * 100})
        r = client.post("/enrichir/page", data={"url": "ville.test/decheterie"})
        assert "Page récupérée" in r.text and "Ajouter cette page" in r.text

        def inaccessible(url):
            raise enrichir.ErreurSource("La page n'a pas pu être récupérée.")

        monkeypatch.setattr(enrichir, "recuperer_page", inaccessible)
        r = client.post("/enrichir/page", data={"url": "https://nulle-part.test"})
        assert r.status_code == 422 and "La page n'a pas pu être récupérée." in html.unescape(r.text)

        # Explorer la mémoire
        assert "Horaires" in client.get("/memoire?vue=sources&q=horaires").text
        assert client.get("/memoire", headers={"HX-Request": "true", "HX-Target": "resultats"}).text.lstrip().startswith("<div class=\"onglets-vue\"")

        # Lacunes et qualité
        lacunes = client.get("/lacunes").text
        assert "Besoins sans réponse" in lacunes and "Clôture : hauteur autorisée" in lacunes
        # « Vérifier et clore » refuse si la mémoire ne couvre pas le besoin
        r = client.post(f"/lacunes/{ville['partiel'].id}/cloturer", follow_redirects=False)
        assert "message=pas_encore" in r.headers["location"]
        client.post(f"/lacunes/{ville['partiel'].id}/sujet", data={"sujet": "Clôtures et murs"})
        client.post(f"/lacunes/{ville['partiel'].id}/priorite", data={"priorite": "haute"})
    with s.begin_nested():
        g = s.get(Groupe, ville["partiel"].id)
        s.refresh(g)
        assert (g.statut, g.sujet, g.sujet_fixe, g.priorite_manuelle) == ("ouvert", "Clôtures et murs", True, "haute")


def test_signaler_puis_corriger_une_connaissance(s, ville):
    from app.main import app

    doc = enrichir.enregistrer_source(s, "saisie", "Note", "Ouvert de 9 h à 18 h.")
    (f,) = enrichir.proposer(s, doc)
    v = memoire.version_affichee(s, f)
    enrichir.valider(s, f, "Camille", titre=v.titre, reponse=v.reponse, conditions="", pieces="", lien_demarche="", saisonniere=False)
    doc_id, fiche_id = doc.id, f.id
    s.commit()
    with TestClient(app) as client:
        connecter(client)
        client.post(f"/memoire/connaissances/{f.id}/signaler", data={"type_": "contradiction", "description": "Le site dit 17 h."})
        page = client.get(f"/memoire/connaissances/{f.id}").text
        assert "Contradiction avec une autre source" in page and "Le site dit 17 h." in page
        assert "Signalées par les agents" in client.get("/lacunes?vue=qualite").text
        r = client.post(f"/memoire/connaissances/{f.id}/valider", follow_redirects=True, data={
            "valide_par": "Camille", "titre": "Horaires de la déchèterie", "reponse": "De 9 h à 17 h.", "conditions": "",
            "pieces": "", "lien_demarche": ""})
        assert "une nouvelle version, signée de votre nom" in r.text and "Signalée" not in r.text
        # Retirer la source garde la connaissance validée
        client.post(f"/memoire/sources/{doc_id}/retirer")
    s.expire_all()
    assert s.get(Document, doc_id) is None and s.get(Fiche, fiche_id).statut == "validee"


def test_plus_aucune_mention_du_jardinier():
    racine = pathlib.Path(__file__).resolve().parent.parent
    fichiers = list((racine / "app").rglob("*.py")) + list((racine / "app" / "templates").glob("*.html")) + [racine / "README.md"]
    assert [str(f) for f in fichiers if re.search("jardinier", f.read_text(encoding="utf-8"), re.I)] == []
