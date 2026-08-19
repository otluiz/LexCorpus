# -*- coding: utf-8 -*-
"""Testes unitários do lexcorpus.buscador — sem rede (mock dos backends)."""
from unittest import mock

from lexcorpus.buscador import (BANCAS_CONHECIDAS, coocorrencia,
                                extrair_banca, extrair_anos,
                                montar_candidatas, construir_query)

RESULTADO_FAKE = [
    {"titulo": "Concurso Transpetro 2023 organizado pela Cesgranrio",
     "url": "https://www.cesgranrio.org.br/concurso/transpetro-2023/",
     "snippet": "A Fundação Cesgranrio realizou o certame da Transpetro em 2023 "
                "com provas para analista de sistemas e administrador."},
    {"titulo": "PRF 2021 Cebraspe Cespe",
     "url": "https://www.cebraspe.org.br/concursos/prf_21",
     "snippet": "Cebraspe/Cespe aplicou as provas da PRF em 2021."},
    {"titulo": "Página aleatória de concursos FGV",
     "url": "https://fgv.br/artigo",
     "snippet": "Concursos gerais, sem órgão mencionado."},
]


def test_extrair_banca_explicita():
    assert extrair_banca(
        "prova organizada pela Fundação Cesgranrio", BANCAS_CONHECIDAS)[0] \
        == "cesgranrio"
    assert extrair_banca(
        "exame Cebraspe/Cespe para a PRF", BANCAS_CONHECIDAS)[0] == "cebraspe"
    assert extrair_banca("banca: Vunesp — 2024", BANCAS_CONHECIDAS)[0] \
        == "vunesp"


def test_extrair_banca_nenhuma():
    assert extrair_banca("texto sem banca alguma", BANCAS_CONHECIDAS) is None


def test_coocorrencia():
    aliases = BANCAS_CONHECIDAS["cesgranrio"]["aliases"]
    assert coocorrencia(
        "concurso Transpetro 2023 realizado pela Cesgranrio",
        "Transpetro", aliases)
    aliases_cb = BANCAS_CONHECIDAS["cebraspe"]["aliases"]
    assert not coocorrencia(
        "Transpetro nada a ver com assunto distante " * 6 + "Cebraspe",
        "Transpetro", aliases_cb)


def test_extrair_anos():
    assert extrair_anos("edital 2021, prova em 2022", 2025) == [2022, 2021]
    assert extrair_anos("sem ano citado", 2025) == []
    # 2026 > ano_max (2025); 1998 < ANO_MIN (2000) — ambos excluídos
    assert extrair_anos("ano 1998 e 2026", 2025) == []


def test_construir_query():
    q = construir_query("Transpetro", "analista", modo="anterior")
    assert "transpetro" in q.lower() and "anterior" in q.lower()
    q2 = construir_query("Transpetro", None, ano=2021)
    assert "2021" in q2


@mock.patch("lexcorpus.buscador.validar_em_agregadores",
            side_effect=lambda s, c, o: c)  # validação sem rede nos testes
def test_montar_candidatas(mvalida):
    candidatas = montar_candidatas(RESULTADO_FAKE, "Transpetro",
                                   dict(BANCAS_CONHECIDAS), 2025)
    # FGV entra por alias (menção em resultado) — foco do teste é a
    # co-ocorrência: só a Cesgranrio tem órgão+banca próximos
    slugs = {c["banca_slug"] for c in candidatas}
    assert "cesgranrio" in slugs and "cebraspe" in slugs
    ces = next(c for c in candidatas if c["banca_slug"] == "cesgranrio")
    cb = next(c for c in candidatas if c["banca_slug"] == "cebraspe")
    assert ces["coocorrencia_orgao_banca"] is True
    assert cb["coocorrencia_orgao_banca"] is False


if __name__ == "__main__":
    import traceback
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {nome}")
            except Exception:
                print(f"FAIL {nome}")
                traceback.print_exc()
