"""Données de démo Ville-Exemple : services, site, questions."""
from pathlib import Path

from sqlalchemy import select

from . import analyse, ingestion
from .config import RACINE, config
from .models import Import, Parametres, Service

DEMO = RACINE / "demo"

SERVICES = [
    ("Accueil", "Accueil général de la mairie, horaires, contact"),
    ("État civil", "Identité, actes, mariage, PACS, décès, recensement"),
    ("Élections", "Listes électorales, procurations"),
    ("Enfance", "Écoles, cantine, périscolaire, accueil de loisirs"),
    ("Petite enfance", "Crèches, relais petite enfance"),
    ("Déchets", "Collecte, encombrants, déchèterie, bacs"),
    ("Urbanisme", "Autorisations de travaux, permis, PLU"),
    ("Voirie", "Voie publique, stationnement, signalements"),
    ("Espaces verts", "Parcs, jardins familiaux, arbres"),
    ("Sports", "Piscine, équipements sportifs"),
    ("Culture", "Médiathèque, spectacles"),
    ("Vie associative", "Associations, salles municipales"),
    ("Action sociale", "CCAS, aides, seniors"),
    ("Police municipale", "Tranquillité publique, objets trouvés, bruit"),
    ("Cimetières", "Concessions, cimetières"),
]


def semer_services(s) -> None:
    if s.get(Parametres, 1) is None:
        s.add(Parametres(id=1))
    existants = {nom for (nom,) in s.execute(select(Service.nom))}
    for nom, description in SERVICES:
        if nom not in existants:
            slug = nom.lower().replace(" ", "-").replace("é", "e").replace("è", "e").replace("â", "a")
            s.add(Service(nom=nom, description=description, referent_nom=f"Référent {nom}",
                          referent_email=f"{slug}@ville-exemple.test"))
    s.flush()


def charger_site(s, import_id: int | None = None) -> int:
    pages = sorted((DEMO / "site").glob("*.md"))
    for p in pages:
        texte = p.read_text(encoding="utf-8")
        analyse.enregistrer_document(s, import_id, "url", ingestion.titre_markdown(texte, p.stem),
                                     f"{config.base_url}/demo-site/{p.stem}", texte)
    return len(pages)


def questions_demo() -> list[ingestion.QuestionLue]:
    entetes, lignes = ingestion.lire_csv((DEMO / "questions.csv").read_bytes())
    questions = ingestion.questions_depuis_csv(lignes, ingestion.deviner_colonnes(entetes))
    for f in sorted((DEMO / "mails").glob("*.eml")):
        questions += ingestion.questions_depuis_eml(f.read_bytes())
    return questions


def charger_tout(s) -> Import:
    semer_services(s)
    imp = Import(type="questions", libelle="Données de démo Ville-Exemple", statut="en_attente")
    s.add(imp)
    s.flush()
    charger_site(s, imp.id)
    analyse.enregistrer_questions(s, imp.id, questions_demo())
    return imp
