import httpx

from app import ingestion


def test_csv_point_virgule_colonnes_devinees():
    contenu = "Date;Question;Réponse;Transfert\n2026-10-01;c quand les encombrants;Voir la page;oui\n".encode()
    entetes, lignes = ingestion.lire_csv(contenu)
    colonnes = ingestion.deviner_colonnes(entetes)
    assert colonnes["question"] == "Question"
    q = ingestion.questions_depuis_csv(lignes, colonnes)[0]
    assert q.texte == "c quand les encombrants"
    assert q.date.isoformat() == "2026-10-01"
    assert q.transfere is True


def test_csv_sans_colonne_question_reconnue():
    entetes, _ = ingestion.lire_csv(b"col1,col2\na,b\n")
    assert ingestion.deviner_colonnes(entetes)["question"] is None


def test_mail_eml_sans_formules_ni_citation():
    eml = (
        b"From: a@exemple.test\r\nSubject: Encombrants\r\nDate: Thu, 01 Oct 2026 10:00:00 +0200\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nBonjour,\r\n\r\nQuand passent les encombrants ?\r\n\r\n"
        b"Cordialement\r\n> ancien message\r\n"
    )
    q = ingestion.questions_depuis_eml(eml)[0]
    assert q.texte == "Quand passent les encombrants ?"  # objet déjà présent dans le corps
    assert q.canal == "mail"


def test_collage_une_question_par_ligne():
    assert len(ingestion.questions_depuis_collage("une question ?\n\nune autre ?\nok")) == 2


def test_crawl_reste_sur_le_domaine():
    pages = {
        "https://ville.test/": '<a href="/a">A</a><a href="https://ailleurs.test/x">X</a><main><h1>Accueil</h1><p>' + "texte " * 30 + "</p></main>",
        "https://ville.test/a": "<h1>Page A</h1><p>" + "contenu " * 30 + "</p>",
    }

    def repondre(requete):
        corps = pages.get(str(requete.url))
        if corps is None:
            return httpx.Response(404)
        return httpx.Response(200, html=corps)

    client = httpx.Client(transport=httpx.MockTransport(repondre))
    resultat = ingestion.crawler("https://ville.test/", client=client)
    assert {p["url"] for p in resultat} == set(pages)
    assert resultat[1]["titre"] == "Page A"


def test_decoupage_en_extraits():
    texte = "# Titre\n" + "\n".join(f"Paragraphe {i} " + "x" * 200 for i in range(10))
    extraits = ingestion.decouper("Titre", texte)
    assert len(extraits) > 1
    assert all(len(e) < 1200 for e in extraits)
