"""File de tâches dans PostgreSQL et worker. Pas de Redis, pas de Celery."""
import logging
import threading
import time
import traceback
from datetime import datetime

from sqlalchemy import select, text

from . import analyse, db, ingestion
from .models import Import, Tache

log = logging.getLogger("noese.worker")


def planifier(s, type_: str, **params) -> None:
    s.add(Tache(type=type_, params=params))


def _suivi(import_id: int):
    """Écrit la progression dans sa propre transaction, pour que l'écran la voie en direct."""
    dernier = [0.0]

    def rappel(etape: str, fait: int, total: int) -> None:
        if time.monotonic() - dernier[0] < 0.4 and fait < total:
            return
        dernier[0] = time.monotonic()
        with db.session() as s:
            imp = s.get(Import, import_id)
            if imp:
                imp.statut, imp.etape, imp.fait, imp.total = "en_cours", etape, fait, total

    return rappel


def _terminer(import_id: int, statut: str, journal: str = "") -> None:
    with db.session() as s:
        imp = s.get(Import, import_id)
        if imp:
            imp.statut, imp.fini_le = statut, datetime.now()
            imp.etape = "Analyse terminée" if statut == "termine" else "L'analyse s'est arrêtée"
            if journal:
                imp.journal = (imp.journal + "\n" + journal).strip()


def executer(tache: Tache) -> None:
    p = tache.params
    if tache.type == "crawler":
        rappel = _suivi(p["import_id"])
        pages = ingestion.crawler(p["url"], max_pages=p.get("max_pages", 200),
                                  rappel=lambda n, url: rappel("Lecture du site", n, 0))
        with db.session() as s:
            for page in pages:
                analyse.enregistrer_document(s, p["import_id"], "url", page["titre"], page["url"], page["texte"])
            imp = s.get(Import, p["import_id"])
            imp.journal = (imp.journal + f"\n{len(pages)} pages lues sur {p['url']}").strip()
            planifier(s, "analyser", import_id=p["import_id"])
    elif tache.type == "analyser":
        rappel = _suivi(p["import_id"])
        try:
            with db.session() as s:
                resultat = analyse.analyser(s, rappel)
        except Exception as e:
            _terminer(p["import_id"], "erreur", f"Erreur : {e}")
            raise
        _terminer(p["import_id"], "termine",
                  f"{resultat['groupes']} groupes : {resultat['couvert']} couverts, "
                  f"{resultat['partiel']} partiels, {resultat['trou']} trous.")
    else:
        raise ValueError(f"Type de tâche inconnu : {tache.type}")


def prendre_une() -> bool:
    with db.session() as s:
        tache = s.scalars(
            select(Tache).where(Tache.statut == "a_faire", Tache.executer_apres <= text("now()"))
            .order_by(Tache.id).limit(1).with_for_update(skip_locked=True)
        ).first()
        if not tache:
            return False
        tache.statut = "en_cours"
        tache_id = tache.id
    with db.session() as s:
        tache = s.get(Tache, tache_id)
        try:
            executer(tache)
            tache.statut = "faite"
        except Exception:
            log.exception("Tâche %s en échec", tache_id)
            tache.statut, tache.erreur = "echec", traceback.format_exc()[-2000:]
    return True


INTERVALLE_ECHEANCES = 15 * 60  # secondes


def lancer_echeances() -> None:
    from . import echeances

    with db.session() as s:
        bilan = echeances.traiter(s)
    if bilan.verifications or bilan.relances or bilan.signalements:
        log.info("Échéances : %s vérifications, %s relances, %s signalements",
                 bilan.verifications, bilan.relances, bilan.signalements)


def boucle(arret: threading.Event | None = None) -> None:
    arret = arret or threading.Event()
    with db.session() as s:  # tâches interrompues par un arrêt brutal
        for t in s.scalars(select(Tache).where(Tache.statut == "en_cours")):
            t.statut = "a_faire"
    prochaines_echeances = time.monotonic() + 30
    while not arret.is_set():
        try:
            if time.monotonic() >= prochaines_echeances:
                prochaines_echeances = time.monotonic() + INTERVALLE_ECHEANCES
                lancer_echeances()
            if not prendre_une():
                arret.wait(1.0)
        except Exception:
            log.exception("Erreur du worker")
            arret.wait(5.0)


def demarrer_en_arriere_plan() -> threading.Event:
    arret = threading.Event()
    threading.Thread(target=boucle, args=(arret,), daemon=True, name="noese-worker").start()
    return arret


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.initialiser()
    log.info("Worker démarré")
    boucle()
