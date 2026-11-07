# Arquivo:  lexcorpus/spiders/base.py
# Função:   base FINA e opcional para spiders do LexCorpus (ADR-0004).
#           Guarda apenas a fábrica make_item() (ArquivoItem do contrato
#           v2.0) e a DELEGAÇÃO da classificação de papel ao módulo
#           lexcorpus/heuristics.py — NENHUMA lógica de classificação vive
#           aqui (a fonte da verdade é o módulo).
# Classes:  LexCorpusSpider — make_item(), classificar_papel(), eh_relevante()
"""Classe base fina para spiders do LexCorpus.

Centraliza só o que é de fato "ser um spider LexCorpus": montar o
ArquivoItem do contrato v2.0 (make_item). A classificação de papel
(prova/gabarito) é delegada a lexcorpus/heuristics.py — módulo de funções
puras, importável por qualquer código sem exigir herança (ADR-0004).

OVERRIDE POR BANCA — hooks de classe:

    class FccSpider(LexCorpusSpider):
        RE_DESCARTAR = re.compile(
            r"resultado|convoca|reclassifica|homologa|inscri|condicoes_espec|habilitad",
            re.I,
        )

Os hooks são repassados a heuristics.classificar_papel() como parâmetros
nomeados. SEMÂNTICA: hook sobrescrito = override, com o mesmo regime do
módulo — um RE_DESCARTAR próprio é FORTE (descarta sempre, mesmo com
"prova" no nome; caso da FCC: "resultado_preliminar_prova_objetiva.pdf").
Hook = None (default) usa a regex default do módulo (descarte fraco).
"""
from __future__ import annotations

import re

import scrapy

from .. import heuristics
from ..items import ArquivoItem
from ..util import slugify


class LexCorpusSpider(scrapy.Spider):
    # Hooks de classificação — None = usar o default de heuristics.py.
    # Subclasses sobrescrevem com um re.compile próprio quando a banca
    # exige (ex.: RE_DESCARTAR forte da FCC).
    RE_GAB_DEF: re.Pattern | None = None
    RE_GAB_PRE: re.Pattern | None = None
    RE_GAB: re.Pattern | None = None
    RE_PROVA: re.Pattern | None = None
    RE_DESCARTAR: re.Pattern | None = None

    def eh_relevante(self, texto: str, url: str = "") -> bool:
        """Delega a heuristics.eh_relevante repassando o hook de descarte."""
        return heuristics.eh_relevante(
            texto, url, re_descartar=self.RE_DESCARTAR
        )

    def classificar_papel(self, texto: str, url: str = "") -> str | None:
        """Delega a heuristics.classificar_papel repassando os hooks RE_*.

        Retorna "gabarito_definitivo", "gabarito_preliminar", "prova" ou
        None (não classificável / irrelevante).
        """
        return heuristics.classificar_papel(
            texto, url,
            re_descartar=self.RE_DESCARTAR,
            re_gab_def=self.RE_GAB_DEF,
            re_gab_pre=self.RE_GAB_PRE,
            re_gab=self.RE_GAB,
            re_prova=self.RE_PROVA,
        )

    def make_item(self, *, pdf_url, nome, papel, banca_rotulo, concurso_rotulo,
                  cargos_rotulo, banca=None, concurso=None, tipo_prova=None,
                  multi_cargo=False, vigente=True):
        """Cria um ArquivoItem populado e slugificado (contrato v2.0)."""
        banca = banca or slugify(banca_rotulo)
        concurso = concurso or slugify(concurso_rotulo)
        cargos = list(cargos_rotulo.keys())

        item = ArquivoItem()
        item["file_urls"] = [pdf_url]
        item["fonte_url"] = pdf_url
        item["nome"] = nome
        item["banca"] = banca
        item["concurso"] = concurso
        item["banca_rotulo"] = banca_rotulo
        item["concurso_rotulo"] = concurso_rotulo
        item["cargos_rotulo"] = cargos_rotulo
        item["papel"] = papel
        item["cargos"] = cargos
        item["tipo_prova"] = tipo_prova
        item["multi_cargo"] = multi_cargo
        item["vigente"] = vigente
        return item
