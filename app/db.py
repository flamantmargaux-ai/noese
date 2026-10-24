"""Connexion à PostgreSQL. En développement sans Docker, un Postgres embarqué (pgserver) est lancé."""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import RACINE, config


def _url() -> str:
    if config.database_url:
        return config.database_url
    import pgserver  # dépendance de développement uniquement

    serveur = pgserver.get_server(str(RACINE / ".pgdata"), cleanup_mode=None)
    return serveur.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)


engine = create_engine(_url(), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def initialiser() -> None:
    from . import models

    with engine.begin() as c:
        c.execute(text("create extension if not exists vector"))
    models.Base.metadata.create_all(engine)
    from . import migrations

    with engine.begin() as c:
        migrations.appliquer(c)
