# Arquivo:  tests/test_heuristics.py
# Função:   testes unitários de lexcorpus/heuristics.py — funções puras,
#           sem instanciar spiders (ADR-0004). Golden cases de classificação
#           de papel + regimes de descarte fraco (default) e forte (override).
#           Inclui o caso FGV "Gabarito Oficial Preliminar", que não pode
#           ser tratado como definitivo só por conter "oficial".
"""Rodar:  pytest tests/test_heuristics.py -v"""
import re

import pytest

from lexcorpus import heuristics as h


# --- classificar_papel: casos felizes ----------------------------------------

@pytest.mark.parametrize("texto,url,esperado", [
    ("Gabarito Definitivo", "https://x/gab_def.pdf", "gabarito_definitivo"),
    ("Gabarito Final", "https://x/gab_final.pdf", "gabarito_definitivo"),
    ("Gabarito Preliminar", "https://x/gab_pre.pdf", "gabarito_preliminar"),
    ("Gabarito Provisório", "https://x/gab_prov.pdf", "gabarito_preliminar"),
    # gabarito sem qualificador -> definitivo (regra vigente do contrato)
    ("Gabarito", "https://x/gabarito.pdf", "gabarito_definitivo"),
    ("Prova Objetiva", "https://x/prova.pdf", "prova"),
    ("Caderno de Provas", "https://x/caderno.pdf", "prova"),
    ("Padrão de Resposta", "https://x/padrao.pdf", None),              # discursivo
    ("Prova Discursiva", "https://x/disc.pdf", None),
    ("Justificativas de alteração de gabarito", "https://x/j.pdf", None),
    ("Prova Objetiva e Discursiva", "https://x/caderno.pdf", "prova"), # tem as objetivas
    ("Tipo 1", "https://x/b1101.pdf", None),          # FGV: sem pista no texto
    ("Termos de Uso", "https://x/termos.pdf", None),
    ("", "https://x/arquivo.pdf", None),
])
def test_classificar_papel_defaults(texto, url, esperado):
    assert h.classificar_papel(texto, url) == esperado


# --- regressão [#A]: "oficial" não implica gabarito definitivo ----------------
# Rótulo padrão FGV: TODO gabarito é "Oficial" (preliminar e definitivo).

def test_gabarito_oficial_preliminar_nao_eh_definitivo():
    assert h.classificar_papel(
        "Gabarito Oficial Preliminar da Prova Objetiva", "https://x/gab.pdf"
    ) == "gabarito_preliminar"


def test_gabarito_oficial_definitivo():
    # este passa hoje e DEVE continuar passando após a correção do bug
    assert h.classificar_papel(
        "Gabarito Oficial Definitivo da Prova Objetiva", "https://x/gab.pdf"
    ) == "gabarito_definitivo"


# --- eh_relevante: regime FRACO (default) -------------------------------------

@pytest.mark.parametrize("texto,url,esperado", [
    ("Edital de Abertura", "https://x/edital.pdf", False),       # descarta
    ("Resultado Final", "https://x/resultado.pdf", False),
    ("Edital de Abertura das Provas", "https://x/edital_provas.pdf", True),  # resgata
    ("Prova Objetiva", "https://x/prova.pdf", True),
])
def test_eh_relevante_fraco(texto, url, esperado):
    assert h.eh_relevante(texto, url) is esperado


# --- override: regime FORTE (caso FCC) ----------------------------------------

RE_DESCARTAR_FCC = re.compile(
    r"resultado|convoca|reclassifica|homologa|inscri|condicoes_espec|habilitad",
    re.I,
)


def test_override_descarte_forte_vence_pista_de_prova():
    # caso real FCC: edital de resultado menciona prova_objetiva no nome
    alvo = "resultado_preliminar_prova_objetiva.pdf"
    assert h.eh_relevante(alvo, "", re_descartar=RE_DESCARTAR_FCC) is False
    assert h.classificar_papel(alvo, "", re_descartar=RE_DESCARTAR_FCC) is None
    # contraste: no regime fraco (default) o mesmo nome é resgatado
    assert h.eh_relevante(alvo, "") is True


def test_override_nao_descarta_gabarito_em_edital():
    # FCC publica gabarito oficial DENTRO de edital — regex forte não tem "edital"
    assert h.classificar_papel(
        "Edital nº 45 - Divulgação dos Gabaritos", "https://x/edital_gab.pdf",
        re_descartar=RE_DESCARTAR_FCC,
    ) == "gabarito_definitivo"


# --- override dos demais regex (padrão FGV estrito) ---------------------------

RE_GAB_DEF_FGV = re.compile(r"gabarito\s+oficial\s+definitiv", re.I)
RE_GAB_PRE_FGV = re.compile(r"gabarito\s+oficial\s+preliminar", re.I)


def test_override_regex_fgv_resolve_preliminar_corretamente():
    # demonstra o caminho de migração do fgv.py (ADR-0005, passo 4):
    # com os regex estritos como parâmetro, o "oficial" não atrapalha
    assert h.classificar_papel(
        "Gabarito Oficial Preliminar", "https://x/gab.pdf",
        re_gab_def=RE_GAB_DEF_FGV, re_gab_pre=RE_GAB_PRE_FGV,
    ) == "gabarito_preliminar"



@pytest.mark.parametrize("nome,discursivo", [
    ("PRF_21_PADRAO_DE_RESPOSTA_DEFINITIVO.PDF", True),
    ("SEFAZ_CE_21_PADRO_DE_RESPOSTA_DEFINITIVO_CARGO_1.PDF", True),   # o Ã some
    ("SEFAZAL515002A02E3425_PADRAORESPOSTA_CARGO_2_.PDF", True),      # sem separador
    ("598_SEFAZ_001_DISC.PDF", True),
    ("MATRIZ_504_SEECDF001_JUSTIFICATIVA.PDF", True),
    ("prova_redacao.pdf", True),
    ("peca_pratica_procurador.pdf", True),
    ("estudo_de_caso_cargo_2.pdf", True),
    ("MATRIZ_598_SEFAZ_001.PDF", False),
    ("GAB_DEFINITIVO_MATRIZ_598_SEFAZ_001_00_PAG_5.PDF", False),
    ("prova_objetiva_e_discursiva.pdf", False),
    ("disciplinas_basicas.pdf", False),                               # "disc" de disciplina
])
def test_eh_discursivo(nome, discursivo):
    assert h.eh_discursivo(nome) is discursivo
