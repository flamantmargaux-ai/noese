"""Vérifie les contrastes texte/fond de la charte dans les deux thèmes (RGAA : 4,5:1 pour le texte).
Les couleurs sont lues dans app/static/css/noese.css. Usage : uv run python -m scripts.contrastes"""
import re
import sys
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app/static/css/noese.css").read_text()
PAIRES = [  # (texte, fond)
    ("encre", "fond"), ("encre", "fond-doux"), ("encre-douce", "fond"), ("encre-douce", "fond-doux"),
    ("cobalt", "fond"), ("cobalt", "fond-doux"), ("sur-encre", "encre"), ("sur-pop", "atome"),
    ("sur-pop", "lagon"), ("sur-pop", "soleil"), ("sur-cobalt", "cobalt"),
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
    clair = jetons(CSS.split("@media")[0])
    sombre = {**clair, **jetons(CSS.split(':root[data-theme="dark"]')[1].split("}")[0])}
    echecs = 0
    for nom, theme in (("clair", clair), ("sombre", sombre)):
        print(f"Thème {nom}")
        for texte, fond in PAIRES:
            r = ratio(theme[texte], theme[fond])
            ok = r >= 4.5
            echecs += not ok
            print(f"  {texte:<12} sur {fond:<10} {r:5.2f}:1  {'ok' if ok else 'INSUFFISANT'}")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
