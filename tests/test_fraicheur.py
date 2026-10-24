"""Fraîcheur : vérifications avant péremption, « Toujours juste », relances et signalements."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import demandes, echeances, fournisseur, verifications
from app.models import Demande, Extrait, Fiche, Groupe, MailEnvoye, Parametres, Question, Service, Verification

LUNDI = datetime(2026, 11, 2, 10, 0)  # lundi ouvré, 10 h


def repondeur(systeme, utilisateur):
    if "vérifies" in systeme:
        return {"verdict": "couvert", "reponse": "Oui [1].", "citations": [1]} if "Fiche validée" in utilisateur \
            else {"verdict": "trou", "manque": "Tout."}
    if "mets en forme" in systeme:
        return {"titre": "Horaires", "reponse": "9 h - 17 h."}
    return {}


@pytest.fixture()
def base(s):
    fournisseur.definir(fournisseur.Factice(repondeur=repondeur))
    s.add(Parametres(id=1, referent_base_nom="Référent base", referent_base_email="base@ville.test"))
    sv = Service(nom="Déchets", referent_nom="Dominique", referent_email="dechets@ville.test")
    s.add(sv)
    s.flush()
    g = Groupe(libelle="Horaires déchèterie", verdict="trou", statut="ouvert", service_id=sv.id)
    s.add(g)
    s.flush()
    s.add(Question(texte="horaires dechetterie ?", groupe_id=g.id))
    s.flush()
    return sv, g


def _fiche_validee(s, g, perime_dans: int, saisonniere=False) -> Fiche:
    d, _ = demandes.creer_demande(s, g, "Quels horaires ?", "dechets@ville.test", "Dominique")
    p = demandes.proposer_fiche(s, d, "9h 17h")
    demandes.valider_fiche(s, p.fiche, "Dominique", "Horaires de la déchèterie", "De 9 h à 17 h.", "", "", "", saisonniere)
    p.fiche.perime_le = LUNDI.date() + timedelta(days=perime_dans)
    s.flush()
    return p.fiche


def _mails(s, type_):
    return s.scalars(select(MailEnvoye).where(MailEnvoye.type == type_).order_by(MailEnvoye.id)).all()


def test_jours_ouvres_et_feries():
    assert not echeances.est_ouvre(date(2026, 11, 11))  # 11 novembre
    assert not echeances.est_ouvre(date(2026, 5, 25))  # lundi de Pentecôte
    assert not echeances.est_ouvre(date(2026, 11, 7))  # samedi
    assert echeances.ajouter_jours_ouvres(date(2026, 11, 9), 5) == date(2026, 11, 17)


def test_pas_de_mail_hors_heures_ouvrees(s, base):
    _fiche_validee(s, base[1], perime_dans=3)
    bilan = echeances.traiter(s, datetime(2026, 11, 7, 10, 0))  # samedi
    assert bilan.ignore and s.scalar(select(func.count(Verification.id))) == 0
    assert echeances.traiter(s, LUNDI.replace(hour=20)).ignore


def test_verification_demandee_au_proprietaire_une_seule_fois(s, base):
    fiche = _fiche_validee(s, base[1], perime_dans=10)  # dans le préavis de 14 jours
    _fiche_validee(s, base[1], perime_dans=60)  # pas encore
    assert verifications.fraicheur(s, fiche, LUNDI.date()) == "bientot"
    assert echeances.traiter(s, LUNDI).verifications == 1
    assert echeances.traiter(s, LUNDI).verifications == 0
    mail = _mails(s, "verification")[0]
    assert mail.destinataire == "dechets@ville.test"
    assert mail.objet == "Cette fiche est-elle toujours juste ? (30 secondes)"


def test_preavis_court_pour_une_fiche_saisonniere(s, base):
    _fiche_validee(s, base[1], perime_dans=10, saisonniere=True)  # préavis de 7 jours
    assert echeances.traiter(s, LUNDI).verifications == 0
    assert echeances.traiter(s, LUNDI + timedelta(days=4)).verifications == 1


def test_toujours_juste_prolonge_d_une_duree_complete(s, base):
    fiche = _fiche_validee(s, base[1], perime_dans=5)
    v = verifications.demander_verification(s, fiche)
    verifications.toujours_juste(s, v)
    assert v.statut == "repondue" and v.reponse == "toujours_juste"
    assert fiche.perime_le == date.today() + timedelta(days=182)
    assert fiche.verifiee_par == "Dominique"
    assert verifications.fraicheur(s, fiche) == "a_jour"


def test_retirer_sort_la_fiche_de_la_base_et_rouvre_le_trou(s, base):
    _, g = base
    fiche = _fiche_validee(s, g, perime_dans=5)
    assert g.statut == "resolu"
    v = verifications.demander_verification(s, fiche)
    with pytest.raises(demandes.ValidationRefusee):
        verifications.retirer(s, v, "")
    verifications.retirer(s, v, "Dominique", "La déchèterie a fermé")
    assert fiche.statut == "archivee"
    assert s.scalar(select(func.count(Extrait.id)).where(Extrait.fiche_id == fiche.id)) == 0
    assert g.statut == "ouvert" and g.verdict == "trou"


def test_relance_douce_puis_signalement_d_une_demande(s, base):
    _, g = base
    d, jeton = demandes.creer_demande(s, g, "Quels horaires ?", "dechets@ville.test")
    d.envoyee_le = LUNDI
    s.flush()
    assert echeances.traiter(s, LUNDI + timedelta(days=4)).relances == 0  # vendredi : 4 jours ouvrés
    assert echeances.traiter(s, LUNDI + timedelta(days=7)).relances == 1  # lundi suivant : 5 jours ouvrés
    relance = _mails(s, "relance")[0]
    assert relance.objet.startswith("Rappel")
    assert relance.lien.endswith(jeton)  # le même lien que dans le premier mail
    assert echeances.traiter(s, LUNDI + timedelta(days=8)).signalements == 0
    bilan = echeances.traiter(s, LUNDI + timedelta(days=16))  # 11 novembre férié : un jour de plus
    assert bilan.signalements == 1
    signalement = _mails(s, "signalement")[0]
    assert signalement.destinataire == "base@ville.test"
    assert "Quels horaires ?" in signalement.corps
    assert echeances.traiter(s, LUNDI + timedelta(days=30)).signalements == 0  # une seule fois
    assert echeances.a_suivre(s)["demandes"] == [d]


def test_relance_et_signalement_d_une_verification(s, base):
    fiche = _fiche_validee(s, base[1], perime_dans=10)
    echeances.traiter(s, LUNDI)
    assert echeances.traiter(s, LUNDI + timedelta(days=7)).relances == 1
    assert echeances.traiter(s, LUNDI + timedelta(days=16)).signalements == 1
    assert "attend sa vérification" in _mails(s, "signalement")[0].objet


def test_pages_verification_fiches_et_parametres(s, base):
    from app.main import app

    fiche = _fiche_validee(s, base[1], perime_dans=5)
    v = verifications.demander_verification(s, fiche)
    jeton = verifications.jeton_de(v)
    s.commit()
    with TestClient(app) as client:
        page = client.get(f"/v/{jeton}")
        assert "Cette fiche est-elle toujours juste ?" in page.text and "De 9 h à 17 h." in page.text
        assert page.headers["x-robots-tag"] == "noindex, nofollow"
        r = client.post(f"/v/{jeton}/toujours-juste", follow_redirects=True)
        assert "La fiche est confirmée. Nous vous redemanderons de la vérifier le" in r.text
        # Un second clic ne prolonge pas deux fois.
        client.post(f"/v/{jeton}/toujours-juste")
        assert client.get("/v/abcdefghijklmnopqrstuvwxyz012345").status_code == 404

        liste = client.get("/fiches").text
        assert "Horaires de la déchèterie" in liste and "À jour" in liste
        detail = client.get(f"/fiches/{fiche.id}").text
        assert "Confirmée « toujours juste » par Dominique" in detail
        assert client.get("/parametres").status_code == 200
        client.post("/parametres/fraicheur", data={"peremption_mois": 3, "peremption_saison_jours": 30, "preavis_jours": 10,
                                                   "preavis_saison_jours": 5, "relance_jours_ouvres": 4,
                                                   "referent_base_nom": "RB", "referent_base_email": "rb@ville.test"})
    with s.begin_nested():
        p = s.get(Parametres, 1)
        s.refresh(p)
        assert (p.peremption_jours, p.relance_jours_ouvres, p.referent_base_email) == (91, 4, "rb@ville.test")
        s.refresh(fiche)
        assert fiche.perime_le == date.today() + timedelta(days=182)
