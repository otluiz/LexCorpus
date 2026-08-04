"""Testa o spider CEBRASPE contra o JSON real da API (fixture), sem rede."""
import json
from scrapy.http import TextResponse, Request
from lexcorpus.spiders.cebraspe import CebraspeSpider, classificar_papel


def make_response(url, body):
    return TextResponse(url=url, body=body.encode("utf-8"),
                        request=Request(url=url), encoding="utf-8")


def test_classificar():
    assert classificar_papel("GABARITO DEFINITIVO - ITENS", "GAB_DEFINITIVO_x.PDF") == "gabarito_definitivo"
    assert classificar_papel("GABARITO PRELIMINAR - 1.ª PROVA", "GAB_PRELIMINAR_x.PDF") == "gabarito_preliminar"
    assert classificar_papel("PROVA OBJETIVA - ITENS DE 9 A 120", "578_PRF_001.PDF") == "prova"
    assert classificar_papel("PROVA DISCURSIVA", "x.PDF") == "prova"
    assert classificar_papel("PADRÃO DE RESPOSTA DEFINITIVO", "x.PDF") == "prova"
    assert classificar_papel("Edital nº 1 - Abertura", "ED_1.PDF") is None
    print("[OK] classificação por descricaoArquivo (prova/gab_def/gab_prelim/None)")


def test_parse_api():
    spider = CebraspeSpider(slug="prf_21")
    body = open("tests/fixtures/cebraspe_prf21_sample.json").read()
    resp = make_response("https://apis.cebraspe.org.br/cebraspe/eventos/prf_21", body)
    items = list(spider.parse_api(resp))

    # 8 arquivos em arquivosGabarito, todos PDF, todos prova/gabarito
    assert len(items) == 8, f"esperava 8, veio {len(items)}"
    papeis = {}
    for it in items:
        papeis[it["papel"]] = papeis.get(it["papel"], 0) + 1
    print(f"[OK] {len(items)} itens: {papeis}")

    # verifica URLs do CDN e campos
    for it in items:
        assert it["fonte_url"].startswith("https://cdn.cebraspe.org.br/concursos/prf_21/arquivos/")
        assert it["banca"] == "cebraspe"
        assert "/" not in it["nome"]
        assert it["cargos"] == ["policial_rodoviario_federal"], it["cargos"]
        assert it["cargos_rotulo"]["policial_rodoviario_federal"] == "POLICIAL RODOVIÁRIO FEDERAL"
    print("[OK] URLs do CDN corretas, cargo extraído, rótulos crus preservados")

    # amostra
    print("\nAmostra dos arquivos classificados:")
    for it in items[:5]:
        print(f"  [{it['papel']:20s}] {it['nome']}")
    print(f"\nURL exemplo: {items[0]['fonte_url']}")


if __name__ == "__main__":
    test_classificar()
    test_parse_api()
    print("\nTODOS OS TESTES DO SPIDER CEBRASPE PASSARAM.")
