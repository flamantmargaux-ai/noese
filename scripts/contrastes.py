"""Vérifie les contrastes texte/fond de la charte (RGAA : 4,5:1 pour le texte).
Les couleurs sont lues dans app/static/css/noese.css. Usage : uv run python -m scripts.contrastes"""
import re
import sys
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app/static/css/noese.css").read_text()
PAIRES = [  # (texte, fond, usage)
    ("encre", "fond", "texte courant"),
    ("encre", "fond-doux", "texte sur panneau, bouton secondaire"),
    ("encre-texte", "fond", "chapeaux"),
    ("encre-douce", "fond", "légendes, surtitres"),
    ("encre-douce", "fond-doux", "légendes dans un panneau"),
    ("accent", "fond", "liens"),
    ("accent", "fond-doux", "liens dans un panneau"),
    ("sur-encre", "accent", "bouton principal"),
    ("sur-encre", "accent-survol", "bouton principal survolé"),
    ("accent", "accent-doux", "onglet actif, étiquette « en cours »"),
    ("ok-texte", "ok-fond", "étiquette couvert"),
    ("ambre-texte", "ambre-fond", "étiquette partiel"),
    ("rouge-texte", "rouge-fond", "étiquette trou"),
    ("gris-texte", "gris-fond", "étiquette neutre"),
]


def jetons(bloc: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})", bloc))


def luminance(hexa: str) -> float:
    canaux = [int(hexa[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main() -> int:
    theme = jetons(CSS.split(":root {", 1)[1].split("}", 1)[0])
    echecs = 0
    for texte, fond, usage in PAIRES:
        r = ratio(theme[texte], theme[fond])
        ok = r >= 4.5
        echecs += not ok
        print(f"  {texte:<12} sur {fond:<14} {r:5.2f}:1  {'ok' if ok else 'INSUFFISANT'}  ({usage})")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
