"""Connexion du référent base par lien magique."""
from datetime import datetime, timedelta

import pytest
from conftest import connecter
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import connexion
from app.config import config
from app.models import JetonConnexion, MailEnvoye


@pytest.fixture()
def client(s):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _chemin(lien: str) -> str:
    return lien.replace(config.base_url.rstrip("/"), "")


def test_pages_du_referent_protegees_pages_des_services_ouvertes(client):
    for chemin in ("/", "/trous", "/fiches", "/parametres", "/import", "/export/base.json"):
        r = client.get(chemin, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/connexion?suite="), chemin
    assert client.get("/connexion").status_code == 200
    assert client.get("/static/css/noese.css").status_code == 200
    assert client.get("/r/" + "x" * 32).status_code == 404  # page publique : 404, pas de redirection
    r = client.get("/trous", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers["hx-redirect"].startswith("/connexion")


def test_parcours_complet_et_retour_a_la_page_demandee(s, client):
    connexion.ajouter(s, "Camille@Ville.test", "Camille")
    s.commit()
    client.get("/fiches?fraicheur=perimee")  # redirige vers la connexion en retenant la page
    client.get("/connexion?suite=/fiches%3Ffraicheur%3Dperimee")
    r = client.post("/connexion", data={"email": "camille@ville.test"})
    assert "Regardez vos mails" in r.text and "Mode développement" in r.text
    with s.begin_nested():
        mail = s.scalars(select(MailEnvoye).where(MailEnvoye.type == "connexion")).one()
        assert mail.destinataire == "camille@ville.test" and "15 minutes" in mail.corps
    r = client.get(_chemin(mail.lien), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/fiches?fraicheur=perimee"
    assert client.get("/").status_code == 200
    # Le lien ne sert qu'une fois.
    assert client.get(_chemin(mail.lien)).status_code == 400
    client.post("/deconnexion")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_adresse_inconnue_meme_reponse_et_aucun_mail(s, client):
    r = client.post("/connexion", data={"email": "inconnu@ailleurs.test"})
    assert "Regardez vos mails" in r.text and "Mode développement" not in r.text
    with s.begin_nested():
        assert s.scalars(select(MailEnvoye)).all() == []


def test_lien_expire_refuse(s):
    connexion.ajouter(s, "rb@ville.test")
    lien = connexion.demander_lien(s, "rb@ville.test", maintenant=datetime.now() - timedelta(minutes=16))
    assert connexion.consommer(s, lien.rsplit("/", 1)[1]) is None


def test_limite_de_liens_par_heure(s):
    connexion.ajouter(s, "rb@ville.test")
    liens_ = [connexion.demander_lien(s, "rb@ville.test") for _ in range(7)]
    assert sum(1 for l in liens_ if l) == connexion.LIENS_PAR_HEURE
    assert len(s.scalars(select(JetonConnexion)).all()) == connexion.LIENS_PAR_HEURE


def test_pas_de_redirection_vers_un_autre_site():
    assert connexion.suite_sure("https://ailleurs.test/") == "/"
    assert connexion.suite_sure("//ailleurs.test") == "/"
    assert connexion.suite_sure("/trous?service=3") == "/trous?service=3"


def test_retirer_un_referent_coupe_sa_session(s, client):
    connecter(client, "a@ville.test")
    assert client.get("/", follow_redirects=False).status_code == 200
    connexion.ajouter(s, "b@ville.test")
    connexion.retirer(s, connexion.par_email(s, "a@ville.test").id)
    s.commit()
    assert client.get("/", follow_redirects=False).status_code == 303
    with pytest.raises(ValueError):  # il reste un seul référent : on ne peut pas le retirer
        connexion.retirer(s, connexion.par_email(s, "b@ville.test").id)


def test_gestion_des_referents_dans_les_parametres(s, client):
    connecter(client)
    r = client.post("/parametres/referents", data={"email": "nouveau@ville.test", "nom": "Dominique"}, follow_redirects=True)
    assert "Le référent est ajouté" in r.text and "nouveau@ville.test" in r.text
    r = client.post("/parametres/referents", data={"email": "pas-une-adresse"}, follow_redirects=True)
    assert "Indiquez une adresse mail valide" in r.text
