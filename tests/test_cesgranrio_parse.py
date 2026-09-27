"""Testa a lógica de parsing do spider CESGRANRIO sem rede."""
import json

from scrapy.http import TextResponse, Request

from lexcorpus.spiders.cesgranrio import CesgranrioSpider, _limpar_cargo


EVENTO_JSON = {
    "success": True,
    "data": {
        "id": 10,
        "nome": "BNB0124",
        "nomeFantasia": "BNB - EDITAL N.º 01/2024",
        "cliente": {"razaoSocial": "BANCO DO NORDESTE DO BRASIL S.A."},
    },
}

MEDIA = "https://concursos.cesgranrio.org.br/media/x/eventos/10/conteudos"

CONTEUDOS_JSON = {
    "success": True,
    "data": [
        {
            "titulo": "PROVAS E GABARITOS - 29/04/2024",
            "texto": (
                f'<p><a href="{MEDIA}/g1.pdf?sv=1&sig=a">GABARITOS - ANALISTA BANCÁRIO 1</a></p>'
                f'<p><a href="{MEDIA}/p1.pdf?sv=1&sig=b">PROVA ANALISTA BANCÁRIO 1 - GABARITO 1</a></p>'
                f'<p><a href="{MEDIA}/p2.pdf?sv=1&sig=c">PROVA ANALISTA BANCÁRIO 1 - GABARITO 2</a></p>'
            ),
        },
        {
            "titulo": "GABARITO FINAL",
            "texto": (
                f'<p><a href="{MEDIA}/gf.pdf?sv=1&sig=d">GABARITO FINAL BANCO DO NORDESTE</a></p>'
                f'<p><a href="{MEDIA}/rec.pdf?sv=1&sig=e">RESPOSTAS AOS RECURSOS - BANCO DO NORDESTE</a></p>'
            ),
        },
        {
            "titulo": "TAC - RESULTADO DO DIA 28/06/2024 - FINAL",
            "texto": f'<p><a href="{MEDIA}/res.pdf?sv=1&sig=f">Acesse aqui</a></p>',
        },
        {
            "titulo": "PROVAS",
            "texto": (
                f'<p><a href="{MEDIA}/pa.pdf?sv=1&sig=g">PROVA A - GABARITO 1 - TÉCNICO BANCÁRIO I</a></p>'
                f'<p><a href="{MEDIA}/pa.pdf?sv=1&sig=g">PROVA A - GABARITO 1 - TÉCNICO BANCÁRIO I</a></p>'
                '<p><a href="https://concursos.cesgranrio.org.br/portal/login">'
                "Acesse o seu Local de Provas</a></p>"
            ),
        },
    ],
}


def make_response(url, payload):
    return TextResponse(
        url=url,
        body=json.dumps(payload).encode("utf-8"),
        request=Request(url=url),
        encoding="utf-8",
    )


def run_spider():
    spider = CesgranrioSpider(evento_id="10")
    reqs = list(spider.parse_evento(make_response(
        "https://concursos.cesgranrio.org.br/api/PortalEventos/10", EVENTO_JSON)))
    assert len(reqs) == 1
    return list(spider.parse_conteudos(make_response(reqs[0].url, CONTEUDOS_JSON)))


def test_parse_conteudos_classifica_blocos_e_links():
    items = run_spider()

    # 3 do bloco PROVAS E GABARITOS + 1 gabarito final + 1 prova A (dedup)
    # descartados: respostas a recursos, resultado, link do portal (não-pdf)
    assert len(items) == 5

    for item in items:
        assert item["banca"] == "cesgranrio"
        assert item["concurso"] == "bnb0124"
        assert item["concurso_rotulo"] == "BNB - EDITAL N.º 01/2024"
        assert "/" not in item["nome"]

    por_nome = {item["nome"]: item for item in items}

    gab_pre = por_nome["bnb0124_gabaritos_analista_bancario_1.pdf"]
    assert gab_pre["papel"] == "gabarito_preliminar"
    assert gab_pre["cargos"] == ["analista_bancario_1"]
    assert gab_pre["multi_cargo"] is False

    prova = por_nome["bnb0124_prova_analista_bancario_1_gabarito_1.pdf"]
    assert prova["papel"] == "prova"
    assert prova["tipo_prova"] == "gabarito_1"
    assert prova["cargos"] == ["analista_bancario_1"]

    gab_def = por_nome["bnb0124_gabarito_final_banco_do_nordeste.pdf"]
    assert gab_def["papel"] == "gabarito_definitivo"

    prova_a = por_nome["bnb0124_prova_a_gabarito_1_tecnico_bancario_i.pdf"]
    assert prova_a["papel"] == "prova"
    assert prova_a["tipo_prova"] == "a_gabarito_1"
    # numeral romano final é parte do cargo
    assert prova_a["cargos"] == ["tecnico_bancario_i"]


def test_limpar_cargo():
    assert _limpar_cargo("PROVA A - GABARITO 1 - TÉCNICO BANCÁRIO I") == "TÉCNICO BANCÁRIO I"
    assert _limpar_cargo("TÉCNICO BANCÁRIO - PROVA A - GABARITO 1") == "TÉCNICO BANCÁRIO"
    assert _limpar_cargo("PROVA 1 - ARQUITETO") == "ARQUITETO"
    assert _limpar_cargo("PROVA 2 - Padrão de resposta - ENGENHEIRO CIVIL") == "ENGENHEIRO CIVIL"
    assert _limpar_cargo("AGENTE UNIVERSITÁRIO - NÍVEL MÉDIO - PROVA 1") == "AGENTE UNIVERSITÁRIO NÍVEL MÉDIO"
    assert _limpar_cargo("GABARITO NIVEL MEDIO_TÉCNICO - PROVAS 1 a 6") == "NIVEL MEDIO TÉCNICO"
    assert _limpar_cargo("Acesse aqui") is None


# --- Transpetro 2023: host próprio, links com host errado, evento com 3 editais ----
# Fixture real: GET https://transpetro.cesgranrio.org.br/api/PortalEvento(s|Conteudos/publico)/12

import pytest

HOST_TP = "transpetro.cesgranrio.org.br"


def run_transpetro(**kw):
    spider = CesgranrioSpider(evento_id="12", host=HOST_TP, **kw)
    ev = json.load(open("tests/fixtures/cesgranrio_transpetro2023_evento.json"))
    ct = json.load(open("tests/fixtures/cesgranrio_transpetro2023_conteudos.json"))
    reqs = list(spider.parse_evento(make_response(
        f"https://{HOST_TP}/api/PortalEventos/12", ev)))
    assert reqs[0].url == f"https://{HOST_TP}/api/PortalEventoConteudos/publico/12"
    return list(spider.parse_conteudos(make_response(reqs[0].url, ct)))


def test_host_fora_da_cesgranrio_recusado():
    with pytest.raises(ValueError, match="host"):
        CesgranrioSpider(evento_id="12", host="exemplo.com")
    with pytest.raises(ValueError, match="host"):
        CesgranrioSpider(evento_id="12", host="cesgranrio.org.br.exemplo.com")


def test_start_usa_o_host():
    import asyncio
    spider = CesgranrioSpider(evento_id="12", host=HOST_TP)

    async def primeiro():
        async for r in spider.start():
            return r
    assert asyncio.run(primeiro()).url == f"https://{HOST_TP}/api/PortalEventos/12"


def test_host_digitado_errado_pela_banca_corrigido():
    """Ênfases 8 e 13 do nível médio vêm com '.org.brr' no link oficial;
    o mesmo caminho no host certo devolve o PDF (verificado em 26/09/2026)."""
    itens = run_transpetro(concurso="transpetro_2023", dividir_editais="1")
    hosts = {it["fonte_url"].split("/")[2] for it in itens}
    assert hosts == {HOST_TP}
    medio = [it for it in itens if it["concurso"].endswith("nivelmedio_2023_1")
             and it["papel"] == "prova"]
    assert len(medio) == 13


def test_dividir_editais_um_concurso_por_edital():
    itens = run_transpetro(concurso="transpetro_2023", dividir_editais="1")
    por = {}
    for it in itens:
        por.setdefault(it["concurso"], []).append(it)
    assert set(por) == {"transpetro_psp_terra_nivelmedio_2023_1",
                        "transpetro_psp_terra_nivelsuperior_2023_2",
                        "transpetro_psp_mar_2023_3"}
    papeis = lambda c: sorted(it["papel"] for it in por[c])
    assert papeis("transpetro_psp_mar_2023_3") == \
        ["gabarito_definitivo", "gabarito_preliminar"] + ["prova"] * 10
    assert papeis("transpetro_psp_terra_nivelmedio_2023_1") == \
        ["gabarito_definitivo", "gabarito_preliminar"] + ["prova"] * 13
    # 28 provas + 2 padrões de resposta (papel=prova, comportamento conhecido)
    assert papeis("transpetro_psp_terra_nivelsuperior_2023_2") == \
        ["gabarito_definitivo", "gabarito_preliminar"] + ["prova"] * 30
    assert por["transpetro_psp_mar_2023_3"][0]["concurso_rotulo"] == "TRANSPETRO/PSP/MAR-2023.3"
    nomes = [it["nome"] for it in itens]
    assert len(nomes) == len(set(nomes))


def test_dividir_editais_gabarito_cobre_o_edital_todo():
    itens = run_transpetro(concurso="transpetro_2023", dividir_editais="1")
    gabs = [it for it in itens if it["papel"].startswith("gabarito")]
    assert len(gabs) == 6
    for g in gabs:
        assert g["cargos"] == ["*"], (g["nome"], g["cargos"])
        assert g["multi_cargo"] is True
    prova = next(it for it in itens if it["nome"].endswith("enfase_1_administracao.pdf"))
    assert prova["cargos"] == ["enfase_1_administracao"]


def test_controle_sem_dividir_um_concurso_so():
    itens = run_transpetro(concurso="transpetro_2023")
    assert {it["concurso"] for it in itens} == {"transpetro_2023"}


def test_nome_do_gabarito_sem_rotulo_e_estavel():
    """Link só com o código do edital: nome vem de papel + bloco, sem sequência
    (a ordem da página pode mudar entre crawls; o nome não)."""
    itens = run_transpetro(concurso="transpetro_2023", dividir_editais="1")
    nomes = {it["nome"] for it in itens}
    assert not [n for n in nomes if n.endswith("_.pdf")]
    assert "transpetro_psp_mar_2023_3_gabarito_preliminar_gabaritos.pdf" in nomes
    invertido = run_transpetro(concurso="transpetro_2023", dividir_editais="1")
    assert {it["nome"] for it in invertido} == nomes
