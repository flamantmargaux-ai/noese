from scripts import contrastes


def test_contrastes_rgaa_deux_themes():
    assert contrastes.main() == 0
