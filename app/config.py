"""Configuration lue dans l'environnement (et dans .env en développement)."""
import os
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _charger_env(chemin: Path) -> None:
    if not chemin.exists():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        os.environ.setdefault(cle.strip(), valeur.strip())


_charger_env(RACINE / ".env")


@dataclass(frozen=True)
class Config:
    database_url: str = os.environ.get("NOESE_DATABASE_URL", "")
    secret: str = os.environ.get("NOESE_SECRET", "dev-secret-a-changer")
    base_url: str = os.environ.get("NOESE_BASE_URL", "http://localhost:8000")

    # Fournisseur de modèles : toute API compatible OpenAI (Mistral, Albert, vLLM, Ollama).
    llm_fournisseur: str = os.environ.get("NOESE_LLM_FOURNISSEUR", "openai_compatible")
    llm_base_url: str = os.environ.get("NOESE_LLM_BASE_URL", "https://api.mistral.ai/v1")
    llm_api_key: str = os.environ.get("NOESE_LLM_API_KEY", "")
    llm_chat_model: str = os.environ.get("NOESE_LLM_CHAT_MODEL", "mistral-small-latest")
    llm_embed_model: str = os.environ.get("NOESE_LLM_EMBED_MODEL", "mistral-embed")
    llm_stt_model: str = os.environ.get("NOESE_LLM_STT_MODEL", "voxtral-mini-latest")
    embed_dim: int = int(os.environ.get("NOESE_EMBED_DIM", "1024"))

    # Premier référent de la base, créé au démarrage s'il n'existe pas encore.
    referent_base_email: str = os.environ.get("NOESE_REFERENT_BASE_EMAIL", "")

    smtp_host: str = os.environ.get("NOESE_SMTP_HOST", "localhost")
    smtp_port: int = int(os.environ.get("NOESE_SMTP_PORT", "1025"))
    smtp_user: str = os.environ.get("NOESE_SMTP_USER", "")
    smtp_password: str = os.environ.get("NOESE_SMTP_PASSWORD", "")
    smtp_from: str = os.environ.get("NOESE_SMTP_FROM", "jardinier@ville-exemple.test")


config = Config()
