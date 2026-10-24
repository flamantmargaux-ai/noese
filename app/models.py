"""Modèle de données. Une instance = une collectivité."""
from __future__ import annotations
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import config

DIM = config.embed_dim


class Base(DeclarativeBase):
    pass


class Parametres(Base):
    __tablename__ = "parametres"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    collectivite: Mapped[str] = mapped_column(String(200), default="Ville-Exemple")
    peremption_jours: Mapped[int] = mapped_column(Integer, default=182)
    peremption_saison_jours: Mapped[int] = mapped_column(Integer, default=30)
    relance_jours_ouvres: Mapped[int] = mapped_column(Integer, default=5)
    # Préavis avant péremption : le service est sollicité ce nombre de jours avant l'échéance.
    preavis_jours: Mapped[int] = mapped_column(Integer, default=14, server_default="14")
    preavis_saison_jours: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    referent_base_nom: Mapped[str] = mapped_column(String(120), default="", server_default="")
    referent_base_email: Mapped[str] = mapped_column(String(200), default="", server_default="")
    modele_embeddings: Mapped[str] = mapped_column(String(200), default="")


class Service(Base):
    __tablename__ = "service"
    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    referent_nom: Mapped[str] = mapped_column(String(120), default="")
    referent_email: Mapped[str] = mapped_column(String(200), default="")


class Utilisateur(Base):
    __tablename__ = "utilisateur"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    nom: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(30))  # referent_base | referent_metier
    __table_args__ = (CheckConstraint("role in ('referent_base','referent_metier')"),)


class JetonConnexion(Base):
    """Lien de connexion du référent base : valable 15 minutes, une seule fois."""

    __tablename__ = "jeton_connexion"
    id: Mapped[int] = mapped_column(primary_key=True)
    utilisateur_id: Mapped[int] = mapped_column(ForeignKey("utilisateur.id", ondelete="CASCADE"))
    jeton_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expire_le: Mapped[datetime] = mapped_column(DateTime)
    utilise_le: Mapped[datetime | None] = mapped_column(DateTime)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Import(Base):
    __tablename__ = "import"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20))  # questions | documents
    libelle: Mapped[str] = mapped_column(String(300))
    statut: Mapped[str] = mapped_column(String(20), default="en_attente")
    etape: Mapped[str] = mapped_column(String(200), default="En attente")
    fait: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    journal: Mapped[str] = mapped_column(Text, default="")
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fini_le: Mapped[datetime | None] = mapped_column(DateTime)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(10))  # url | pdf | md | txt
    titre: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000), default="")
    contenu: Mapped[str] = mapped_column(Text)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Extrait(Base):
    """Morceau indexé de la base : extrait de document ou fiche validée."""

    __tablename__ = "extrait"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    fiche_id: Mapped[int | None] = mapped_column(ForeignKey("fiche.id", ondelete="CASCADE"))
    titre: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000), default="")
    texte: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(DIM))
    document: Mapped["Document | None"] = relationship()


class Groupe(Base):
    __tablename__ = "groupe"
    id: Mapped[int] = mapped_column(primary_key=True)
    libelle: Mapped[str] = mapped_column(String(300))
    mots_cles: Mapped[str] = mapped_column(Text, default="")
    nb: Mapped[int] = mapped_column(Integer, default=0)
    centroide = mapped_column(Vector(DIM))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("service.id", ondelete="SET NULL"))
    verdict: Mapped[str | None] = mapped_column(String(10))  # couvert | partiel | trou
    reponse: Mapped[str] = mapped_column(Text, default="")
    manque: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    statut: Mapped[str] = mapped_column(String(15), default="ouvert")
    juge_le: Mapped[datetime | None] = mapped_column(DateTime)
    service: Mapped["Service | None"] = relationship()
    __table_args__ = (
        CheckConstraint("verdict is null or verdict in ('couvert','partiel','trou')"),
        CheckConstraint("statut in ('ouvert','demande','repondu','resolu','ecarte')"),
    )


class Question(Base):
    __tablename__ = "question"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import.id", ondelete="SET NULL"))
    texte: Mapped[str] = mapped_column(Text)
    date: Mapped[date | None] = mapped_column(Date)
    canal: Mapped[str] = mapped_column(String(20), default="")
    reponse_donnee: Mapped[str] = mapped_column(Text, default="")
    transfere: Mapped[bool | None] = mapped_column(Boolean)
    intention: Mapped[str | None] = mapped_column(Text)  # reformulation canonique par le LLM
    embedding = mapped_column(Vector(DIM))
    groupe_id: Mapped[int | None] = mapped_column(ForeignKey("groupe.id", ondelete="SET NULL"))
    # Rangée à la main par un agent : l'analyse automatique ne la déplace plus.
    groupe_manuel: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Demande(Base):
    __tablename__ = "demande"
    id: Mapped[int] = mapped_column(primary_key=True)
    groupe_id: Mapped[int] = mapped_column(ForeignKey("groupe.id", ondelete="CASCADE"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("service.id", ondelete="SET NULL"))
    destinataire_nom: Mapped[str] = mapped_column(String(120), default="")
    destinataire_email: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(Text)
    jeton_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sel: Mapped[str | None] = mapped_column(String(32))  # le lien se recalcule avec le secret, pour les relances
    statut: Mapped[str] = mapped_column(String(15), default="envoyee")
    transferee_de: Mapped[int | None] = mapped_column(ForeignKey("demande.id"))
    envoyee_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    relancee_le: Mapped[datetime | None] = mapped_column(DateTime)
    signalee_le: Mapped[datetime | None] = mapped_column(DateTime)
    repondue_le: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("statut in ('envoyee','relancee','transferee','repondue','close')"),
    )


class ReponseBrute(Base):
    __tablename__ = "reponse_brute"
    id: Mapped[int] = mapped_column(primary_key=True)
    demande_id: Mapped[int] = mapped_column(ForeignKey("demande.id", ondelete="CASCADE"))
    texte: Mapped[str] = mapped_column(Text)
    dictee: Mapped[bool] = mapped_column(Boolean, default=False)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Fiche(Base):
    __tablename__ = "fiche"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("service.id", ondelete="SET NULL"))
    groupe_id: Mapped[int | None] = mapped_column(ForeignKey("groupe.id", ondelete="SET NULL"))
    demande_id: Mapped[int | None] = mapped_column(ForeignKey("demande.id", ondelete="SET NULL"))
    statut: Mapped[str] = mapped_column(String(15), default="brouillon")
    saisonniere: Mapped[bool] = mapped_column(Boolean, default=False)
    perime_le: Mapped[date | None] = mapped_column(Date)
    verifiee_le: Mapped[date | None] = mapped_column(Date)
    verifiee_par: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Questions d'habitants auxquelles la fiche répond vraiment : exemples exportés vers les chatbots.
    questions_exemples: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    service: Mapped["Service | None"] = relationship()
    __table_args__ = (CheckConstraint("statut in ('brouillon','validee','archivee')"),)


class Verification(Base):
    """Demande au service propriétaire de confirmer qu'une fiche est toujours juste."""

    __tablename__ = "verification"
    id: Mapped[int] = mapped_column(primary_key=True)
    fiche_id: Mapped[int] = mapped_column(ForeignKey("fiche.id", ondelete="CASCADE"))
    destinataire_nom: Mapped[str] = mapped_column(String(120), default="")
    destinataire_email: Mapped[str] = mapped_column(String(200))
    jeton_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sel: Mapped[str] = mapped_column(String(32))
    statut: Mapped[str] = mapped_column(String(15), default="envoyee")
    envoyee_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    relancee_le: Mapped[datetime | None] = mapped_column(DateTime)
    signalee_le: Mapped[datetime | None] = mapped_column(DateTime)
    repondue_le: Mapped[datetime | None] = mapped_column(DateTime)
    reponse: Mapped[str] = mapped_column(String(20), default="")  # toujours_juste | modifiee | retiree
    __table_args__ = (CheckConstraint("statut in ('envoyee','relancee','repondue','close')"),)


class FicheVersion(Base):
    __tablename__ = "fiche_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    fiche_id: Mapped[int] = mapped_column(ForeignKey("fiche.id", ondelete="CASCADE"))
    numero: Mapped[int] = mapped_column(Integer)
    titre: Mapped[str] = mapped_column(String(300))
    reponse: Mapped[str] = mapped_column(Text)
    conditions: Mapped[str] = mapped_column(Text, default="")
    pieces: Mapped[str] = mapped_column(Text, default="")
    lien_demarche: Mapped[str] = mapped_column(String(1000), default="")
    origine: Mapped[str] = mapped_column(Text, default="")
    redigee_par: Mapped[str] = mapped_column(String(200), default="")
    a_verifier: Mapped[str] = mapped_column(Text, default="")  # remarque de l'IA sur un brouillon
    validee: Mapped[bool] = mapped_column(Boolean, default=False)
    validee_par: Mapped[str | None] = mapped_column(String(200))
    validee_le: Mapped[datetime | None] = mapped_column(DateTime)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Principe 1 : une version validée porte forcément un nom humain et une date.
    __table_args__ = (
        CheckConstraint(
            "not validee or (validee_par is not null and validee_par <> '' and validee_le is not null)",
            name="version_validee_par_un_humain",
        ),
    )


class Evenement(Base):
    __tablename__ = "evenement"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    objet: Mapped[str] = mapped_column(String(40), default="")
    objet_id: Mapped[int | None] = mapped_column(Integer)
    acteur: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Tache(Base):
    """File de tâches simple, dépilée par le worker (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "tache"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    statut: Mapped[str] = mapped_column(String(15), default="a_faire")
    erreur: Mapped[str] = mapped_column(Text, default="")
    executer_apres: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MailEnvoye(Base):
    """Journal des mails : ce qui est parti, ce qui a échoué, et le lien qu'il contenait."""

    __tablename__ = "mail_envoye"
    id: Mapped[int] = mapped_column(primary_key=True)
    demande_id: Mapped[int | None] = mapped_column(ForeignKey("demande.id", ondelete="SET NULL"))
    verification_id: Mapped[int | None] = mapped_column(ForeignKey("verification.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(20), default="demande", server_default="demande")
    destinataire: Mapped[str] = mapped_column(String(200))
    objet: Mapped[str] = mapped_column(String(300))
    corps: Mapped[str] = mapped_column(Text)
    lien: Mapped[str] = mapped_column(String(1000), default="")
    statut: Mapped[str] = mapped_column(String(15))  # envoye | journal | echec
    erreur: Mapped[str] = mapped_column(Text, default="")
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
