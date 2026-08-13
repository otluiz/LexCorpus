"""Testa a lógica de parsing do spider FGV sem rede."""
from scrapy.http import HtmlResponse, Request

from lexcorpus.spiders.fgv import FgvSpider


HTML = """
<html>
  <head><title>CPNU 2 | FGV Conhecimento</title></head>
  <body>
    <p class="Indent1"><strong>Bloco Temático 1: Seguridade Social</strong></p>
    <p class="Indent2">
      <a href="/sites/default/files/concursos/cpnu2/b1101.pdf">Tipo 1</a>
    </p>
    <p class="Indent2">
      <a href="/sites/default/files/concursos/cpnu2/b1102.pdf">Tipo 2</a>
    </p>
    <p class="Indent2">
      <a href="/sites/default/files/concursos/cpnu2/b1101.pdf">Tipo 1 duplicado</a>
    </p>
    <p>
      <a href="/sites/default/files/concursos/cpnu2/ic7i2fda65.pdf">
        Gabarito Oficial Preliminar da Prova Objetiva
      </a>
    </p>
    <p>
      <a href="/sites/default/files/concursos/cpnu2/cpnu_gabarito_oficial_definitivo_retificado.pdf">
        Gabarito Oficial Definitivo da Prova Objetiva
      </a>
    </p>
    <p>
      <a href="/sites/default/files/concursos/cpnu2/edital_resultado.pdf">
        Resultado preliminar
      </a>
    </p>
    <p>
      <a href="/sites/default/files/termos-de-uso.pdf">Termos de uso</a>
    </p>
    <p>
      <a href="https://inscricao-cpnu.conhecimento.fgv.br/consulta">
        Vista de Prova
      </a>
    </p>
  </body>
</html>
"""


def make_response(url, body):
    return HtmlResponse(
        url=url,
        body=body.encode("utf-8"),
        request=Request(url=url),
        encoding="utf-8",
    )


def test_parse_fgv_cnu_com_blocos_e_gabaritos():
    spider = FgvSpider(
        url="https://conhecimento.fgv.br/cpnu2",
        concurso="cnu_2025",
        concurso_rotulo="Concurso Público Nacional Unificado 2025",
    )
    resp = make_response(spider.start_url, HTML)
    items = list(spider.parse_concurso(resp))

    assert len(items) == 4
    papeis = sorted(item["papel"] for item in items)
    assert papeis == [
        "gabarito_definitivo",
        "gabarito_preliminar",
        "prova",
        "prova",
    ]

    for item in items:
        assert item["banca"] == "fgv"
        assert item["concurso"] == "cnu_2025"
        assert item["concurso_rotulo"] == "Concurso Público Nacional Unificado 2025"
        assert "/" not in item["nome"]
        assert item["fonte_url"].startswith("https://conhecimento.fgv.br/")

    provas = sorted(
        (item for item in items if item["papel"] == "prova"),
        key=lambda item: item["tipo_prova"],
    )
    assert [item["tipo_prova"] for item in provas] == ["1", "2"]
    assert provas[0]["cargos"] == ["bloco_tematico_1_seguridade_social"]
    assert provas[0]["cargos_rotulo"] == {
        "bloco_tematico_1_seguridade_social": "Bloco Temático 1: Seguridade Social"
    }
    assert provas[0]["nome"] == (
        "cnu_2025_bloco_tematico_1_seguridade_social_tipo_1.pdf"
    )

    preliminar = next(item for item in items if item["papel"] == "gabarito_preliminar")
    definitivo = next(item for item in items if item["papel"] == "gabarito_definitivo")
    assert preliminar["multi_cargo"] is True
    assert preliminar["cargos"] == ["*"]
    assert definitivo["multi_cargo"] is True
    assert definitivo["nome"] == (
        "cnu_2025_gabarito_definitivo_cpnu_gabarito_oficial_definitivo_retificado.pdf"
    )
