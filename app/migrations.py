"""Évolutions de schéma idempotentes, appliquées au démarrage.

`create_all` crée les tables manquantes mais n'ajoute pas les colonnes aux tables existantes.
Avant les pilotes, ces instructions seront reprises dans un outil de migration (Alembic)."""
from sqlalchemy import text

INSTRUCTIONS = [
    "alter table groupe add column if not exists mots_cles text default ''",
    "alter table parametres add column if not exists modele_embeddings varchar(200) default ''",
    "alter table question add column if not exists groupe_manuel boolean not null default false",
    "alter table fiche add column if not exists demande_id integer references demande(id) on delete set null",
    "alter table fiche_version add column if not exists a_verifier text default ''",
    "alter table parametres add column if not exists preavis_jours integer not null default 14",
    "alter table parametres add column if not exists preavis_saison_jours integer not null default 7",
    "alter table parametres add column if not exists referent_base_nom varchar(120) not null default ''",
    "alter table parametres add column if not exists referent_base_email varchar(200) not null default ''",
    "alter table demande add column if not exists sel varchar(32)",
    "alter table fiche add column if not exists verifiee_le date",
    "alter table fiche add column if not exists verifiee_par varchar(200) not null default ''",
    "alter table mail_envoye add column if not exists verification_id integer references verification(id) on delete set null",
    "alter table mail_envoye add column if not exists type varchar(20) not null default 'demande'",
    "alter table fiche add column if not exists questions_exemples json not null default '[]'",
]


def appliquer(connexion) -> None:
    for instruction in INSTRUCTIONS:
        connexion.execute(text(instruction))
