# Arquivo:  lexcorpus/heuristics.py
# Função:   heurísticas de classificação de papel (prova/gabarito) e relevância
#           de arquivos, como MÓDULO de funções puras — desacoplado de qualquer
#           spider: importável por spiders, pipelines, scripts e testes sem
#           exigir herança de LexCorpusSpider (ver ADR-0004).
# Funções:  classificar_papel(texto, url, ...) -> str | None
#           eh_relevante(texto, url, ...)      -> bool
# Constantes (regex compilados, sobrescritíveis via parâmetro):
#           RE_GAB_DEF, RE_GAB_PRE, RE_GAB, RE_PROVA, RE_DESCARTAR
"""Heurísticas compartilhadas de classificação de arquivos de concurso.

Uso típico num spider (sem herdar de nada):

    from .. import heuristics

    papel = heuristics.classificar_papel(texto_do_link, pdf_url)

Override por banca (ex.: FCC, cujo descarte forte vence "prova" no nome):

    RE_DESCARTAR_FCC = re.compile(
        r"resultado|convoca|reclassifica|homologa|inscri|condicoes_espec|habilitad",
        re.I,
    )
    papel = heuristics.classificar_papel(texto, url, re_descartar=RE_DESCARTAR_FCC)
"""
from __future__ import annotations

import re

# --- Heurísticas de classificação (defaults gerais) ---------------------------

RE_GAB_DEF = re.compile(r"gabarito.*(definitiv|final|oficial|p[óo]s.?recurs)", re.I)
RE_GAB_PRE = re.compile(r"gabarito.*(preliminar|provis[óo]ri)", re.I)
RE_GAB = re.compile(r"gabarito|gab[\W_]", re.I)
RE_PROVA = re.compile(r"prova|caderno|padr[ãa]o\s+de\s+resposta", re.I)

# PDFs a descartar: editais, convocações, resultados etc.
# Versão FRACA: só descarta se não houver pista de prova/gabarito (ver
# eh_relevante). Bancas como a FCC precisam da versão forte — passar
# re_descartar próprio a classificar_papel (ver docstring do módulo).
RE_DESCARTAR = re.compile(
    r"edital|retifica|convoca|resultado|homologa|cronograma|inscri|isen[çc]",
    re.I,
)


def eh_relevante(texto: str, url: str = "", *, re_descartar: re.Pattern | None = None) -> bool:
    """True se o alvo NÃO deve ser descartado de cara.

    Dois regimes:
    - DEFAULT (fraco): descarta quando casa com RE_DESCARTAR E não há
      nenhuma pista de prova/gabarito — protege falsos positivos como
      "edital_de_abertura_das_provas.pdf".
    - OVERRIDE (forte): quando a banca passa seu próprio re_descartar,
      casar com ele descarta SEMPRE, mesmo havendo "prova" no nome —
      é o caso da FCC, cujos editais de resultado mencionam
      "prova_objetiva" (ex.: "resultado_preliminar_prova_objetiva.pdf").
    """
    alvo = f"{texto} {url}".lower()
    if re_descartar is not None:
        return not re_descartar.search(alvo)  # override = descarte forte
    if RE_DESCARTAR.search(alvo):
        return bool(RE_GAB.search(alvo) or RE_PROVA.search(alvo))
    return True


def classificar_papel(
    texto: str,
    url: str = "",
    *,
    re_descartar: re.Pattern | None = None,
    re_gab_def: re.Pattern | None = None,
    re_gab_pre: re.Pattern | None = None,
    re_gab: re.Pattern | None = None,
    re_prova: re.Pattern | None = None,
) -> str | None:
    """Decide o papel (prova/gabarito) com base no texto e na URL.

    Retorna "gabarito_definitivo", "gabarito_preliminar", "prova" ou None
    (não classificável / irrelevante). Gabarito sem qualificador é tratado
    como definitivo.

    Todos os regex são parâmetros nomeados: bancas com particularidades
    passam os seus, sem copiar a função.
    """
    alvo = f"{texto} {url}".lower()
    gab_def = re_gab_def or RE_GAB_DEF
    gab_pre = re_gab_pre or RE_GAB_PRE
    gab = re_gab or RE_GAB
    prova = re_prova or RE_PROVA

    if not eh_relevante(texto, url, re_descartar=re_descartar):
        return None

    if gab_def.search(alvo):
        return "gabarito_definitivo"
    if gab_pre.search(alvo):
        return "gabarito_preliminar"
    if gab.search(alvo):
        return "gabarito_definitivo"
    if prova.search(alvo):
        return "prova"
    return None
