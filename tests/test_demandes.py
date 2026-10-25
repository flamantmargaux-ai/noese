"""Boucle demande → réponse → fiche → validation.

Critère d'acceptation : aucune fiche n'entre dans la base sans validation humaine."""
import re
from dataclasses import replace

import pytest
from conftest import connecter
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import analyse, demandes, fournisseur, mails
from app.models import Demande, Extrait, Fiche, FicheVersion, Groupe, MailEnvoye, Question, Service

FICHE_IA = {
    "titre": "Délai d'une déclaration préalable",
    "reponse": "Le délai d'instruction est d'un mois.",
    "conditions": "",
    "pieces": ["Formulaire cerfa", "Plan de situation"],
    "lien_demarche": "https://inventee.example/demarche",  # absent de la réponse de l'agent : doit être retiré
    "saisonniere": False,
    "a_verifier": "",
}


def repondeur(systeme, utilisateur):
    if "mets en forme" in systeme:
        return FICHE_IA
    if "poser une question précise" in systeme:
        return {"question": "Quel est le délai d'instruction d'une déclaration préalable ?"}
    if "vérifies" in systeme:
        cite = 1 if "Fiche validée" in utilisateur else None
        return {"verdict": "couvert", "reponse": "Un mois [1].", "citations": [cite]} if cite else {"verdict": "trou", "manque": "Le délai."}
    return {}


@pytest.fixture()
def besoin(s):
    fournisseur.definir(fournisseur.Factice(repondeur=repondeur))
    sv = Service(nom="Urbanisme", referent_nom="Référent Urbanisme", referent_email="urba@ville.test")
    s.add(sv)
    s.flush()
    g = Groupe(libelle="Délai déclaration préalable", verdict="trou", statut="ouvert", service_id=sv.id,
               manque="Le délai d'instruction.")
    s.add(g)
    s.flush()
    for t in ("délai DP fenêtres ?", "combien de temps pour une déclaration préalable"):
        s.add(Question(texte=t, groupe_id=g.id, intention="Déclaration préalable : délai"))
    analyse.recompter(s)
    s.flush()
    return g


def _jeton(s, d: Demande) -> str:
    lien = s.scalar(select(MailEnvoye.lien).where(MailEnvoye.demande_id == d.id))
    return lien.rsplit("/", 1)[1]


def _extraits_de_fiches(s) -> int:
    return s.scalar(select(func.count(Extrait.id)).where(Extrait.fiche_id.is_not(None)))


def test_le_jeton_n_est_pas_stocke_en_clair(s, besoin):
    d, jeton = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test")
    assert jeton not in d.jeton_hash
    assert demandes.demande_par_jeton(s, jeton).id == d.id
    assert demandes.demande_par_jeton(s, jeton[:-1] + ("A" if jeton[-1] != "A" else "B")) is None
    assert besoin.statut == "demande"


def test_une_fiche_proposee_n_entre_pas_dans_la_base(s, besoin):
    d, _ = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test")
    p = demandes.proposer_fiche(s, d, "1 mois, cerfa + plan de situation")
    assert p.fiche.statut == "brouillon" and not p.version.validee
    assert _extraits_de_fiches(s) == 0
    assert all(e.fiche_id is None for e in analyse.rechercher(s, "délai déclaration préalable"))
    assert p.version.lien_demarche == ""  # le lien inventé par le modèle est retiré
    assert p.version.pieces == "Formulaire cerfa\nPlan de situation"


def test_validation_sans_nom_refusee(s, besoin):
    d, _ = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test")
    p = demandes.proposer_fiche(s, d, "1 mois")
    with pytest.raises(demandes.ValidationRefusee):
        demandes.valider_fiche(s, p.fiche, "  ", "Titre", "Réponse", "", "", "", False)
    assert p.fiche.statut == "brouillon"
    assert _extraits_de_fiches(s) == 0


def test_validation_humaine_fait_entrer_la_fiche_et_ferme_le_trou(s, besoin):
    d, _ = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test")
    p = demandes.proposer_fiche(s, d, "1 mois")
    v = demandes.valider_fiche(s, p.fiche, "Camille Instructrice", "Délai d'une DP", "Un mois.", "",
                               "- Formulaire cerfa\n- Plan", "", saisonniere=False)
    assert v.validee and v.validee_par == "Camille Instructrice" and v.validee_le
    assert v.pieces == "Formulaire cerfa\nPlan"
    assert _extraits_de_fiches(s) == 1
    assert p.fiche.statut == "validee" and p.fiche.perime_le is not None
    assert d.statut == "repondue"
    assert besoin.verdict == "couvert" and besoin.statut == "resolu"
    assert besoin.sources[0]["fiche_id"] == p.fiche.id


def test_peremption_courte_pour_un_tarif(s, besoin):
    d, _ = demandes.creer_demande(s, besoin, "Quels tarifs pour 2026 ?", "urba@ville.test")
    p = demandes.proposer_fiche(s, d, "3 euros le repas")
    assert p.fiche.saisonniere  # détecté même si le modèle a répondu false
    demandes.valider_fiche(s, p.fiche, "Camille", "Tarifs", "3 €.", "", "", "", saisonniere=True)
    assert (p.fiche.perime_le - p.fiche.cree_le.date()).days <= 31


def test_echec_smtp_garde_le_lien_pour_le_transmettre_autrement(s, besoin, monkeypatch):
    monkeypatch.setattr(mails, "config", replace(mails.config, smtp_host="127.0.0.1", smtp_port=1))
    d, jeton = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test")
    m = s.scalar(select(MailEnvoye).where(MailEnvoye.demande_id == d.id))
    assert m.statut == "echec" and m.lien.endswith(jeton)


def test_parcours_complet_par_les_pages(s, besoin):
    from app.main import app

    s.commit()
    with TestClient(app) as client:
        connecter(client)
        r = client.post(f"/lacunes/{besoin.id}/demande", follow_redirects=False, data={
            "question": "Quel est le délai d'instruction d'une déclaration préalable ?",
            "destinataire_email": "urba@ville.test", "destinataire_nom": "Référent Urbanisme", "retour": "/lacunes"})
        assert r.status_code == 303
        with s.begin_nested():
            d = s.scalars(select(Demande)).one()
            jeton = _jeton(s, d)

        page = client.get(f"/r/{jeton}")
        assert "2 habitants ont posé cette question" in page.text
        assert page.headers["referrer-policy"] == "no-referrer"
        assert client.post(f"/r/{jeton}/dictee", files={"audio": ("d.wav", b"RIFF", "audio/wav")}).json()["texte"]

        r = client.post(f"/r/{jeton}/reponse", data={"texte": "1 mois. cerfa + plan de situation"}, follow_redirects=False)
        assert r.headers["location"] == f"/r/{jeton}/fiche"
        relecture = client.get(f"/r/{jeton}/fiche").text
        assert "Rien n'entre dans la base avant votre validation" in relecture
        assert "inventee.example" not in relecture
        assert "Ce que vous avez écrit" in relecture and "1 mois. cerfa + plan de situation" in relecture

        # Sans nom, la validation est refusée et la saisie est conservée.
        r = client.post(f"/r/{jeton}/valider", data={"valide_par": "", "titre": "Délai modifié", "reponse": "Un mois."})
        assert r.status_code == 422 and "Délai modifié" in r.text

        r = client.post(f"/r/{jeton}/valider", follow_redirects=True, data={
            "valide_par": "Camille", "titre": "Délai d'une déclaration préalable", "reponse": "Un mois.",
            "pieces": "Cerfa", "conditions": "", "lien_demarche": ""})
        assert "Votre fiche est dans la base. Nous vous redemanderons de la vérifier le" in r.text
        detail = client.get(f"/lacunes/{besoin.id}").text
        assert "Fiche validée" in detail and "Camille" in detail


def test_transfert_a_un_collegue(s, besoin):
    from app.main import app

    d, jeton = demandes.creer_demande(s, besoin, "Quel délai ?", "urba@ville.test", "Référent Urbanisme")
    s.commit()
    with TestClient(app) as client:
        r = client.post(f"/r/{jeton}/transferer", data={"email": "urba@ville.test"})
        assert r.status_code == 422  # sa propre adresse
        client.post(f"/r/{jeton}/transferer", data={"email": "ads@ville.test", "nom": "Instructeur", "par": "Référent"})
        assert "La question a été transmise à Instructeur" in client.get(f"/r/{jeton}").text
        # L'ancien lien ne permet plus de répondre.
        client.post(f"/r/{jeton}/reponse", data={"texte": "une réponse"})
    with s.begin_nested():
        assert s.scalar(select(func.count(Fiche.id))) == 0
        nouvelle = s.scalar(select(Demande).where(Demande.transferee_de == d.id))
        assert nouvelle.destinataire_email == "ads@ville.test"
        corps = s.scalar(select(MailEnvoye.corps).where(MailEnvoye.demande_id == nouvelle.id))
        assert "Référent vous transfère cette question" in corps
        assert re.search(r"/r/[A-Za-z0-9_-]{20,}", corps)
