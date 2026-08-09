"""Classe base para spiders do LexCorpus.

Centraliza a lógica de classificação de papel (prova/gabarito) e a criação
padronizada de ArquivoItem, seguindo o contrato v2.0.
"""
from __future__ import annotations

import re
import scrapy
from ..items import ArquivoItem
from ..util import slugify

class LexCorpusSpider(scrapy.Spider):
    # Heurísticas de classificação (podem ser sobrescritas pelas subclasses)
    RE_GAB_DEF = re.compile(r"gabarito.*(definitiv|final|oficial|p[óo]s.?recurs)", re.I)
    RE_GAB_PRE = re.compile(r"gabarito.*(preliminar|provis[óo]ri)", re.I)
    RE_GAB = re.compile(r"gabarito|gab[\W_]", re.I)
    RE_PROVA = re.compile(r"prova|caderno|padr[ãa]o\s+de\s+resposta", re.I)
    
    # PDFs a descartar
    RE_DESCARTAR = re.compile(
        r"edital|retifica|convoca|resultado|homologa|cronograma|inscri|isen[çc]", re.I
    )

    def classificar_papel(self, texto: str, url: str) -> str | None:
        """Decide o papel (prova/gabarito) com base no texto e URL."""
        alvo = f"{texto} {url}".lower()
        
        # Primeiro verifica se deve descartar
        if self.RE_DESCARTAR.search(alvo):
            # Só descarta se não houver pista forte de prova/gabarito
            if not (self.RE_GAB.search(alvo) or self.RE_PROVA.search(alvo)):
                return None

        if self.RE_GAB_DEF.search(alvo):
            return "gabarito_definitivo"
        if self.RE_GAB_PRE.search(alvo):
            return "gabarito_preliminar"
        if self.RE_GAB.search(alvo):
            return "gabarito_definitivo"
        if self.RE_PROVA.search(alvo):
            return "prova"
        return None

    def make_item(self, *, pdf_url, nome, papel, banca_rotulo, concurso_rotulo, 
                  cargos_rotulo, tipo_prova=None, multi_cargo=False, vigente=True):
        """Cria um ArquivoItem populado e slugificado."""
        banca = slugify(banca_rotulo)
        concurso = slugify(concurso_rotulo)
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
