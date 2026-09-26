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


def _itens_sefazce():
    spider = CebraspeSpider(slug="sefaz_ce_21")
    body = open("tests/fixtures/cebraspe_sefazce21_sample.json").read()
    resp = make_response("https://apis.cebraspe.org.br/cebraspe/eventos/sefaz_ce_21", body)
    return {it["nome"]: it for it in spider.parse_api(resp)}


def test_multicargo_prova_de_um_cargo():
    """Concurso com 4 cargos: 'PROVA OBJETIVA - CARGO 2' é de UM cargo só,
    não multi_cargo — o número vem da descricaoArquivo e casa com eventoCargos."""
    itens = _itens_sefazce()
    assert len(itens) == 17
    prova = itens["MATRIZ_598_SEFAZ_002.PDF"]
    assert prova["papel"] == "prova"
    assert prova["cargos"] == ["auditor_fiscal_contabil_financeiro_da_receita_estadual"]
    assert prova["multi_cargo"] is False
    disc = itens["598_SEFAZ_004_DISC.PDF"]
    assert disc["cargos"] == ["auditor_fiscal_de_tecnologia_da_informacao_da_receita_estadual"]
    assert disc["multi_cargo"] is False


def test_multicargo_gabarito_de_um_cargo():
    itens = _itens_sefazce()
    gab = itens["GAB_DEFINITIVO_MATRIZ_598_SEFAZ_001_00_PAG_5.PDF"]
    assert gab["papel"] == "gabarito_definitivo"
    assert gab["cargos"] == ["auditor_fiscal_da_receita_estadual"]
    assert gab["multi_cargo"] is False


def test_multicargo_cargo_10_nao_casa_com_cargo_1():
    from lexcorpus.spiders.cebraspe import resolver_cargo
    por_numero = {1: "um", 10: "dez"}
    assert resolver_cargo("PROVA OBJETIVA - CARGO 10", "", por_numero) == "dez"
    assert resolver_cargo("PROVA OBJETIVA - CARGO 1", "", por_numero) == "um"
    assert resolver_cargo("", "CARGO_1_DELEGADO.PDF", por_numero) == "um"
    assert resolver_cargo("GABARITO - CARGOS DE NÍVEL SUPERIOR", "", por_numero) is None
    assert resolver_cargo("PROVA OBJETIVA - CARGO 7", "", por_numero) is None


def test_multicargo_arquivo_comum_a_todos():
    """Sem número de cargo: cobre todos. O gabarito pode vir subdividido por
    caderno (caso BACEN13) -> multi_cargo; a prova é um caderno só -> não."""
    itens = _itens_sefazce()
    todos = sorted([
        "auditor_fiscal_da_receita_estadual",
        "auditor_fiscal_contabil_financeiro_da_receita_estadual",
        "auditor_fiscal_juridico_da_receita_estadual",
        "auditor_fiscal_de_tecnologia_da_informacao_da_receita_estadual",
    ])
    gab = itens["GAB_DEFINITIVO_MATRIZ_598_SEFAZ_CB1_00_PAG_4.PDF"]
    assert sorted(gab["cargos"]) == todos
    assert gab["multi_cargo"] is True


if __name__ == "__main__":
    test_classificar()
    test_parse_api()
    print("\nTODOS OS TESTES DO SPIDER CEBRASPE PASSARAM.")
