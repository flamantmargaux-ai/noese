"""Envoi des mails par SMTP. Chaque mail est aussi journalisé en base.

NOESE_SMTP_HOST=journal : rien ne part, les mails sont seulement journalisés (développement).
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import config
from .models import MailEnvoye


def envoyer(s, destinataire: str, objet: str, corps: str, lien: str = "", demande_id: int | None = None,
            nom_destinataire: str = "") -> MailEnvoye:
    journal = MailEnvoye(demande_id=demande_id, destinataire=destinataire, objet=objet, corps=corps, lien=lien,
                         statut="journal")
    s.add(journal)
    if config.smtp_host == "journal":
        s.flush()
        return journal
    msg = EmailMessage()
    msg["From"] = formataddr(("Noèse", config.smtp_from))
    msg["To"] = formataddr((nom_destinataire, destinataire)) if nom_destinataire else destinataire
    msg["Subject"] = objet
    msg["Message-ID"] = make_msgid(domain=config.smtp_from.split("@")[-1])
    msg.set_content(corps)
    try:
        if config.smtp_port == 465:
            serveur = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            serveur = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15)
            if config.smtp_port == 587:
                serveur.starttls(context=ssl.create_default_context())
        with serveur:
            if config.smtp_user:
                serveur.login(config.smtp_user, config.smtp_password)
            serveur.send_message(msg)
        # Le lien magique n'est gardé en clair que si le mail n'est pas parti, pour le transmettre autrement.
        journal.statut, journal.lien = "envoye", ""
    except (OSError, smtplib.SMTPException) as e:
        journal.statut, journal.erreur = "echec", str(e)[:500]
    s.flush()
    return journal
