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
