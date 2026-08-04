"""Testa a lógica de parsing do spider PCI sem rede."""
from scrapy.http import HtmlResponse, Request
from lexcorpus.spiders.pci import PciSpider, classificar_papel, eh_relevante


HTML = """
<html><body>
  <div class="download">
    <a href="/arquivos/prova_analista_2023.pdf">Prova - Analista Judiciário</a>
    <a href="/arquivos/gabarito_definitivo_analista.pdf">Gabarito Definitivo</a>
    <a href="/arquivos/gabarito_preliminar_analista.pdf">Gabarito Preliminar</a>
    <a href="https://www.pciconcursos.com.br/arquivos/edital.pdf">Edital</a>
    <a href="/provas/outra-coisa">link que não é PDF</a>
    <a href="/arquivos/prova_analista_2023.pdf">duplicado (deve ser ignorado)</a>
  </div>
</body></html>
"""


def make_response(url, body):
    return HtmlResponse(url=url, body=body.encode("utf-8"),
                        request=Request(url=url), encoding="utf-8")


def test_classificar():
    assert classificar_papel("Gabarito Definitivo", "x.pdf") == "gabarito_definitivo"
    assert classificar_papel("Gabarito Preliminar", "x.pdf") == "gabarito_preliminar"
    assert classificar_papel("Gabarito", "gab_x.pdf") == "gabarito_definitivo"
    assert classificar_papel("Prova Objetiva", "prova.pdf") == "prova"
    print("[OK] classificação de papel")


def test_relevancia():
    assert eh_relevante("Prova", "prova.pdf") is True
    assert eh_relevante("Edital", "edital.pdf") is False
    assert eh_relevante("Retificação", "retificacao_01.pdf") is False
    assert eh_relevante("Convocação", "convoca.pdf") is False
    # borda: 'edital' no nome mas é prova -> mantém
    assert eh_relevante("Prova pós-edital", "prova.pdf") is True
    print("[OK] filtro de relevância (descarta edital/retificação/convocação)")


def test_parse():
    spider = PciSpider(
        url="https://www.pciconcursos.com.br/provas/download/analista-tjes-2023",
        banca="CEBRASPE", concurso="TJ-ES 2023", cargo="Analista Judiciário",
        tipo_prova="1",
    )
    resp = make_response(spider.start_url, HTML)
    items = list(spider.parse_download(resp))

    # agora 3 itens: prova, gab def, gab prelim (edital DESCARTADO, dup ignorado)
    assert len(items) == 3, f"esperava 3 itens, veio {len(items)}"
    papeis = sorted(i["papel"] for i in items)
    assert papeis == ["gabarito_definitivo", "gabarito_preliminar", "prova"], papeis
    print(f"[OK] {len(items)} PDFs relevantes (edital descartado, dup ignorado)")

    for it in items:
        assert it["banca"] == "cebraspe"
        assert it["concurso"] == "tj_es_2023"
        assert it["cargos"] == ["analista_judiciario"]
        assert "/" not in it["nome"]
        # nome não deve duplicar o papel
        assert it["nome"].count("gabarito_definitivo") <= 1
    print("[OK] slugs corretos, nomes sem barra nem papel duplicado")

    print("\nNomes finais no storage:")
    for it in sorted(items, key=lambda x: x["papel"]):
        print(f"  [{it['papel']:20s}] {it['nome']}")


if __name__ == "__main__":
    test_classificar()
    test_relevancia()
    test_parse()
    print("\nTODOS OS TESTES DE PARSING PASSARAM.")
