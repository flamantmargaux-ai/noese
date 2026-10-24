"""Les tests tournent sur une base PostgreSQL dédiée (noese_test) et un fournisseur factice, hors ligne."""
import os

import pytest


def _url_test() -> str:
    if os.environ.get("NOESE_TEST_DATABASE_URL"):
        return os.environ["NOESE_TEST_DATABASE_URL"]
    from pathlib import Path

    import pgserver
    import psycopg

    serveur = pgserver.get_server(str(Path(__file__).resolve().parent.parent / ".pgdata"), cleanup_mode=None)
    uri = serveur.get_uri()
    with psycopg.connect(uri, autocommit=True) as c:
        if not c.execute("select 1 from pg_database where datname = 'noese_test'").fetchone():
            c.execute("create database noese_test")
    return uri.replace("/postgres?", "/noese_test?").replace("postgresql://", "postgresql+psycopg://", 1)


os.environ["NOESE_DATABASE_URL"] = _url_test()
os.environ["NOESE_LLM_FOURNISSEUR"] = "factice"
os.environ["NOESE_WORKER_INTEGRE"] = "0"
os.environ["NOESE_SMTP_HOST"] = "journal"


@pytest.fixture(scope="session")
def schema():
    from app import db, models

    db.initialiser()
    models.Base.metadata.drop_all(db.engine)
    models.Base.metadata.create_all(db.engine)


@pytest.fixture()
def s(schema):
    from sqlalchemy import text

    from app import db, models

    tables = ", ".join(f'"{t.name}"' for t in models.Base.metadata.sorted_tables)
    with db.engine.begin() as c:
        c.execute(text(f"truncate {tables} restart identity cascade"))
    with db.session() as session:
        yield session
        session.rollback()
