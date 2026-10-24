"""Couche d'abstraction des modèles (chat, embeddings, transcription).

Une seule implémentation réelle : l'API « compatible OpenAI », que parlent Mistral,
l'API Albert de l'État, vLLM et Ollama. Changer de fournisseur = changer l'URL,
la clé et les noms de modèles dans la configuration.
"""
import hashlib
import json
import re
import time
import unicodedata

import httpx
import numpy as np

from .config import config


class ErreurFournisseur(RuntimeError):
    pass


class Fournisseur:
    def chat(self, systeme: str, utilisateur: str, json_attendu: bool = True) -> dict | str:
        raise NotImplementedError

    def embeddings(self, textes: list[str]) -> np.ndarray:
        raise NotImplementedError

    def transcrire(self, audio: bytes, nom_fichier: str, type_mime: str) -> str:
        raise NotImplementedError


def _sans_accents(cle: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", cle) if not unicodedata.combining(c)).lower()


def _cles_normalisees(objet):
    """Les petits modèles écrivent parfois « libellé » au lieu de « libelle » : on normalise les clés."""
    if isinstance(objet, dict):
        return {_sans_accents(k) if isinstance(k, str) else k: _cles_normalisees(v) for k, v in objet.items()}
    if isinstance(objet, list):
        return [_cles_normalisees(v) for v in objet]
    return objet


def _extraire_json(texte: str) -> dict:
    texte = texte.strip()
    texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte)
    try:
        return _cles_normalisees(json.loads(texte))
    except json.JSONDecodeError:
        debut, fin = texte.find("{"), texte.rfind("}")
        if debut >= 0 and fin > debut:
            return _cles_normalisees(json.loads(texte[debut : fin + 1]))
        raise


class OpenAICompatible(Fournisseur):
    def __init__(self, base_url: str, api_key: str, chat_model: str, embed_model: str, stt_model: str):
        self.chat_model, self.embed_model, self.stt_model = chat_model, embed_model, stt_model
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(90.0, connect=10.0),
        )

    def _post(self, chemin: str, **kwargs) -> dict:
        for essai in range(9):
            try:
                r = self.client.post(chemin, **kwargs)
            except httpx.TransportError as e:
                if essai == 8:
                    raise ErreurFournisseur(f"Fournisseur injoignable : {e}") from e
                time.sleep(2**essai)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and essai < 8:
                time.sleep(min(2**essai, 30))
                continue
            if r.status_code >= 400:
                raise ErreurFournisseur(f"{r.status_code} sur {chemin} : {r.text[:300]}")
            return r.json()
        raise ErreurFournisseur(f"Échec répété sur {chemin}")

    def chat(self, systeme, utilisateur, json_attendu=True):
        corps = {
            "model": self.chat_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": systeme},
                {"role": "user", "content": utilisateur},
            ],
        }
        if json_attendu:
            corps["response_format"] = {"type": "json_object"}
        contenu = self._post("/chat/completions", json=corps)["choices"][0]["message"]["content"]
        return _extraire_json(contenu) if json_attendu else contenu.strip()

    def embeddings(self, textes):
        if self.embed_model == "local":
            return embeddings_lexicales(textes)
        vecteurs = []
        for i in range(0, len(textes), 64):
            lot = [t[:6000] or " " for t in textes[i : i + 64]]
            donnees = self._post("/embeddings", json={"model": self.embed_model, "input": lot})["data"]
            vecteurs.extend(d["embedding"] for d in sorted(donnees, key=lambda d: d["index"]))
        return _normaliser(np.array(vecteurs, dtype=np.float32))

    def transcrire(self, audio, nom_fichier, type_mime):
        donnees = self._post(
            "/audio/transcriptions",
            data={"model": self.stt_model, "language": "fr"},
            files={"file": (nom_fichier, audio, type_mime)},
        )
        return donnees.get("text", "").strip()


class Factice(Fournisseur):
    """Fournisseur déterministe et hors ligne, pour les tests."""

    def __init__(self, dim: int = config.embed_dim, repondeur=None):
        self.dim = dim
        self.repondeur = repondeur or (lambda systeme, utilisateur: {})

    def chat(self, systeme, utilisateur, json_attendu=True):
        return self.repondeur(systeme, utilisateur)

    def embeddings(self, textes):
        return embeddings_lexicales(textes, self.dim)

    def transcrire(self, audio, nom_fichier, type_mime):
        return "transcription factice"


MOTS_VIDES = set(
    """a au aux avec c ca ce ces cet cette d dans de des du elle en est et etc eu il ils j je
    l la le les leur lui m ma mais me mes moi mon n ne nos notre nous on ou par pas pour qu que
    quel quelle quels quelles qui s sa se ses si son sur t ta te tes toi ton tu un une vos votre
    vous y bonjour bjr svp merci stp slt salut comment faire faut peut peux veux voudrais
    voudrait savoir est-ce estce quoi ou quand combien mon ma mes est sont etre avoir ai as
    jsp c'est cest bien tres plus aussi alors donc car""".split()
)


def _normaliser_texte(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    mots = re.findall(r"[a-z0-9]+", t)
    return [_racine(m) for m in mots if m not in MOTS_VIDES and len(m) > 1]


def _racine(mot: str) -> str:
    if len(mot) > 4 and mot[-1] in "sx":
        mot = mot[:-1]
    return mot[:6]


def embeddings_lexicales(textes: list[str], dim: int = config.embed_dim) -> np.ndarray:
    """Embeddings locaux sans modèle : mots racinisés, bigrammes et trigrammes de caractères,
    hachés dans un vecteur de dimension fixe. Moins fins qu'un modèle sémantique, mais
    souverains par construction et suffisants une fois les questions reformulées par le LLM."""
    m = np.zeros((len(textes), dim), dtype=np.float32)
    for i, t in enumerate(textes):
        mots = _normaliser_texte(t)
        traits: list[tuple[str, float]] = [("m:" + w, 1.0) for w in mots]
        traits += [("b:" + a + "_" + b, 0.7) for a, b in zip(mots, mots[1:])]
        for w in mots:
            traits += [("c:" + w[k : k + 4], 0.3) for k in range(max(1, len(w) - 3))]
        for trait, poids in traits:
            h = int.from_bytes(hashlib.blake2b(trait.encode(), digest_size=8).digest(), "little")
            m[i, h % dim] += poids if (h >> 63) == 0 else -poids
    return _normaliser(m)


def _normaliser(m: np.ndarray) -> np.ndarray:
    normes = np.linalg.norm(m, axis=1, keepdims=True)
    normes[normes == 0] = 1.0
    return m / normes


_actuel: Fournisseur | None = None


def fournisseur() -> Fournisseur:
    global _actuel
    if _actuel is None:
        if config.llm_fournisseur == "factice" or not config.llm_api_key:
            _actuel = Factice()
        else:
            _actuel = OpenAICompatible(
                config.llm_base_url,
                config.llm_api_key,
                config.llm_chat_model,
                config.llm_embed_model,
                config.llm_stt_model,
            )
    return _actuel


def definir(f: Fournisseur) -> None:
    global _actuel
    _actuel = f
