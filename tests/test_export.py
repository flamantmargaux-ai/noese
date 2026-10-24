"""Export de la base validée et réimport, tableau de bord."""
import json
from datetime import date, datetime, timedelta

import pytest
from conftest import connecter
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import analyse, demandes, export, fournisseur, tableau_de_bord
from app.models import Demande, Extrait, Fiche, FicheVersion, Groupe, Question, Service


def repondeur(systeme, utilisateur):
    if "mets en forme" in systeme:
        return {"titre": "Brouillon", "reponse": "Brouillon non validé."}
    if "vérifies" in systeme:
        return {"verdict": "couvert", "reponse": "Oui [1].", "citations": [1]}
    if "relier des questions" in systeme:
        return {"repond_a": [1, 2]}
    return {}


@pytest.fixture()
def base(s):
    fournisseur.definir(fournisseur.Factice(repondeur=repondeur))
    sv = Service(nom="État civil", referent_nom="Sacha", referent_email="ec@ville.test")
    s.add(sv)
    s.flush()
    g = Groupe(libelle="Passeport", verdict="trou", statut="ouvert", service_id=sv.id)
    s.add(g)
    s.flush()
    s.add_all([Question(texte="c combien le passeport ; svp", groupe_id=g.id, date=date(2026, 10, 1)),
               Question(texte='passeport "urgent" | délai ?', groupe_id=g.id, date=date(2026, 10, 2))])
    d, _ = demandes.creer_demande(s, g, "Quel délai pour un passeport ?", "ec@ville.test", "Sacha")
    p = demandes.proposer_fiche(s, d, "4 semaines")
    demandes.valider_fiche(s, p.fiche, "Sacha", "Délai d'un passeport", "Environ 4 semaines ; sur rendez-vous.",
                           "Présence obligatoire,\nmême pour un mineur.", "Photo d'identité\nTimbre fiscal ; 86 €",
                           "https://ville.test/passeport", saisonniere=True)
    # Une seconde demande, en brouillon seulement : elle ne doit jamais sortir.
    d2, _ = demandes.creer_demande(s, g, "Et la carte d'identité ?", "ec@ville.test")
    demandes.proposer_fiche(s, d2, "pareil")
    analyse.recompter(s)
    s.flush()
    return sv, g, p.fiche


@pytest.mark.parametrize("format_", ["json", "csv", "md"])
def test_aller_retour_sans_perte(s, base, format_):
    fiches = export.collecter(s)
    assert len(fiches) == 1 and fiches[0]["validee_par"] == "Sacha"
    assert "Brouillon" not in json.dumps(fiches)
    texte = {"json": lambda: export.vers_json(fiches, "Ville-Exemple"), "csv": lambda: export.vers_csv(fiches),
             "md": lambda: export.vers_markdown(fiches, "Ville-Exemple")}[format_]()
    relues = export.lire(f"base.{format_}", texte.encode("utf-8"))
    assert relues == fiches


def test_exemples_choisis_a_la_validation(s, base):
    assert export.collecter(s)[0]["questions_habitants"] == ['passeport "urgent" | délai ?', "c combien le passeport ; svp"]


def test_markdown_lisible(s, base):
    md = export.vers_markdown(export.collecter(s), "Ville-Exemple")
    assert "## Délai d'un passeport" in md
    assert "### Pièces à fournir\n\n- Photo d'identité\n- Timbre fiscal ; 86 €" in md
    assert "- Validée par : Sacha" in md


def test_fiche_perimee_signalee_ou_exclue(s, base):
    fiche = base[2]
    fiche.perime_le = date.today() - timedelta(days=1)
    s.flush()
    assert export.collecter(s)[0]["perimee"] is True
    assert "> Fiche à vérifier" in export.vers_markdown(export.collecter(s), "X")
    assert export.collecter(s, avec_perimees=False) == []


def test_reimport_conserve_la_validation_et_refuse_le_reste(s, base):
    fiches = export.collecter(s)
    assert export.importer(s, fiches, "base.json") == {"importees": 0, "deja_presentes": 1, "refusees": 0}
    nouvelle = {**fiches[0], "titre": "Délai d'une carte d'identité"}
    sans_validation = {**fiches[0], "titre": "Inventée", "validee_par": ""}
    r = export.importer(s, [nouvelle, sans_validation], "base.json")
    assert r == {"importees": 1, "deja_presentes": 0, "refusees": 1}
    v = s.scalar(select(FicheVersion).where(FicheVersion.titre == "Délai d'une carte d'identité"))
    assert v.validee and v.validee_par == "Sacha" and "importée de base.json" in v.origine
    assert s.scalar(select(func.count(Extrait.id)).where(Extrait.fiche_id == v.fiche_id)) == 1
    assert s.scalar(select(FicheVersion).where(FicheVersion.titre == "Inventée")) is None


def test_un_fichier_quelconque_n_est_pas_pris_pour_un_export():
    assert export.lire("notes.md", "# Mes notes\nBonjour".encode()) is None
    assert export.lire("questions.csv", "date;question\n2026-01-01;test".encode()) is None
    assert export.lire("autre.json", b'{"a": 1}') is None


def test_jours_ouvres_et_delai_median(s, base):
    assert tableau_de_bord.jours_ouvres_entre(date(2026, 11, 6), date(2026, 11, 9)) == 1  # vendredi -> lundi
    assert tableau_de_bord.jours_ouvres_entre(date(2026, 11, 10), date(2026, 11, 12)) == 1  # 11 novembre férié
    _, g, _ = base
    d = s.scalars(select(Demande).where(Demande.statut == "repondue")).one()
    d.envoyee_le, d.repondue_le = datetime(2026, 11, 2, 9), datetime(2026, 11, 5, 9)  # 3 jours ouvrés
    s.flush()
    c = tableau_de_bord.chiffres(s)
    assert c["delai_median"] == 3 and c["nb_reponses"] == 1
    assert tableau_de_bord.formuler_delai(3) == "3 jours ouvrés"
    assert tableau_de_bord.formuler_delai(0) == "le jour même"
    assert tableau_de_bord.formuler_delai(1.5) == "1,5 jour ouvré"


def test_classement_des_services(s, base):
    sv, g, _ = base
    autre = Service(nom="Urbanisme")
    s.add(autre)
    s.flush()
    for k in range(3):
        s.add(Groupe(libelle=f"Urba {k}", verdict="trou", statut="ouvert", service_id=autre.id))
    s.add(Service(nom="Sans activité"))
    s.flush()
    lignes = tableau_de_bord.par_service(s)
    assert [l.service.nom for l in lignes] == ["Urbanisme", "État civil"]
    assert (lignes[1].fiches, lignes[1].demandes_en_cours) == (1, 1)


def test_pages_export_et_accueil(s, base):
    from app.main import app

    s.commit()
    with TestClient(app) as client:
        connecter(client)
        r = client.get("/export/base.csv")
        assert r.headers["content-disposition"].startswith('attachment; filename="base-')
        assert r.text.startswith("﻿id;version;titre")
        assert client.get("/export/base.json").json()["format"] == "noese-base/1"
        assert client.get("/export/base.pdf").status_code == 404
        md = client.get("/export/base.md").content
        # Réimport par l'écran d'import : rien de nouveau, tout est déjà présent.
        client.post("/import/documents", files={"fichiers": ("base.md", md, "text/markdown")})
        accueil = client.get("/").text
        assert "Où un coup de main serait utile" in accueil and "&lt; 1" in accueil and "le jour même" in accueil
    with s.begin_nested():
        assert s.scalar(select(func.count(Fiche.id)).where(Fiche.statut == "validee")) == 1
