# Arquivo:  tests/test_base.py
# Função:   testes da base fina spiders/base.py (ADR-0004): DELEGAÇÃO ao
#           módulo heuristics.py (paridade total), hooks RE_* de classe com
#           regime FORTE, e a fábrica make_item (ArquivoItem do contrato v2.0).
"""Rodar:  pytest tests/test_base.py -v"""
import re

import pytest

from lexcorpus import heuristics as h
from lexcorpus.spiders.base import LexCorpusSpider


@pytest.fixture
def spider():
    return LexCorpusSpider(name="teste_base")


# --- delegação: paridade base <-> módulo (regime default) ---------------------

CASOS = [
    ("Gabarito Definitivo", "https://x/gab.pdf"),
    ("Gabarito Preliminar", "https://x/gab_pre.pdf"),
    ("Prova Objetiva", "https://x/prova.pdf"),
    ("Edital de Abertura das Provas", "https://x/edital_provas.pdf"),
    ("Edital de Abertura", "https://x/edital.pdf"),
    ("resultado_preliminar_prova_objetiva.pdf", "https://x/r.pdf"),
    ("Tipo 1", "https://x/b1101.pdf"),
    ("Termos de Uso", "https://x/termos.pdf"),
]


@pytest.mark.parametrize("texto,url", CASOS)
def test_classificar_papel_delega_ao_modulo(spider, texto, url):
    assert spider.classificar_papel(texto, url) == h.classificar_papel(texto, url)


@pytest.mark.parametrize("texto,url", CASOS)
def test_eh_relevante_delega_ao_modulo(spider, texto, url):
    assert spider.eh_relevante(texto, url) == h.eh_relevante(texto, url)


def test_base_nao_tem_logica_propria():
    # ADR-0004: "nenhuma lógica de classificação vive na base" — os hooks
    # de classe são None por default (quem classifica é o módulo)
    for hook in ("RE_GAB_DEF", "RE_GAB_PRE", "RE_GAB", "RE_PROVA", "RE_DESCARTAR"):
        assert getattr(LexCorpusSpider, hook) is None


# --- hooks de classe: override = regime FORTE ---------------------------------

class _SpiderComDescarteForte(LexCorpusSpider):
    name = "teste_forte"
    RE_DESCARTAR = re.compile(
        r"resultado|convoca|reclassifica|homologa|inscri|condicoes_espec|habilitad",
        re.I,
    )


def test_hook_de_classe_tem_regime_forte():
    s = _SpiderComDescarteForte()
    alvo = "resultado_preliminar_prova_objetiva.pdf"
    assert s.eh_relevante(alvo, "") is False       # forte: "prova" não resgata
    assert s.classificar_papel(alvo, "") is None


def test_hook_nao_afeta_demais_classificacoes():
    s = _SpiderComDescarteForte()
    assert s.classificar_papel("Gabarito Definitivo", "https://x/g.pdf") == "gabarito_definitivo"
    assert s.classificar_papel("Prova", "https://x/p.pdf") == "prova"


# --- make_item: fábrica do ArquivoItem (contrato v2.0) -------------------------

def test_make_item_campos_do_contrato(spider):
    item = spider.make_item(
        pdf_url="https://x/prova_t1.pdf",
        nome="prova_t1.pdf",
        papel="prova",
        banca_rotulo="FGV",
        concurso_rotulo="TJ-RJ 2024",
        cargos_rotulo={"analista_judiciario": "Analista Judiciário"},
        tipo_prova="1",
    )
    assert item["file_urls"] == ["https://x/prova_t1.pdf"]
    assert item["fonte_url"] == "https://x/prova_t1.pdf"
    assert item["nome"] == "prova_t1.pdf"
    assert item["banca"] == "fgv"                       # slug
    assert item["concurso"] == "tj_rj_2024"             # slug
    assert item["banca_rotulo"] == "FGV"                # rótulo cru preservado
    assert item["concurso_rotulo"] == "TJ-RJ 2024"
    assert item["cargos_rotulo"] == {"analista_judiciario": "Analista Judiciário"}
    assert item["cargos"] == ["analista_judiciario"]    # chaves do dict
    assert item["papel"] == "prova"
    assert item["tipo_prova"] == "1"
    assert item["multi_cargo"] is False                 # defaults
    assert item["vigente"] is True


def test_make_item_multi_cargo(spider):
    item = spider.make_item(
        pdf_url="https://x/gab.pdf", nome="gab.pdf", papel="gabarito_definitivo",
        banca_rotulo="CEBRASPE", concurso_rotulo="PRF 2021",
        cargos_rotulo={"*": "*"}, multi_cargo=True,
    )
    assert item["cargos"] == ["*"]
    assert item["multi_cargo"] is True
    assert item["tipo_prova"] is None


def test_make_item_permite_slug_canonico_distinto_do_rotulo(spider):
    item = spider.make_item(
        pdf_url="https://x/gab.pdf",
        nome="gab.pdf",
        papel="gabarito_preliminar",
        banca_rotulo="FGV",
        concurso_rotulo="Concurso Público Nacional Unificado 2025",
        cargos_rotulo={"*": "*"},
        concurso="cnu_2025",
    )
    assert item["concurso"] == "cnu_2025"
    assert item["concurso_rotulo"] == "Concurso Público Nacional Unificado 2025"


# --- spiders que herdam a base seguem funcionando ------------------------------

def test_spiders_reais_usam_a_delegacao():
    from lexcorpus.spiders.agregador_generico import AgregadorGenericoSpider
    from lexcorpus.spiders.estrategia import EstrategiaSpider

    ag = AgregadorGenericoSpider(start_url="https://x")
    es = EstrategiaSpider()
    assert ag.classificar_papel("Prova", "https://x/p.pdf") == "prova"
    assert es.classificar_papel("Gabarito", "https://x/g.pdf") == "gabarito_definitivo"
    assert ag.eh_relevante("Edital de Abertura", "https://x/e.pdf") is False
