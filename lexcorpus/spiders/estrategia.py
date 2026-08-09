# Arquivo:  lexcorpus/spiders/estrategia.py
# Função:   spider do blog do Estratégia Concursos — descobre postagens recentes
#           sobre provas/gabaritos e extrai os links de PDF.
# Funções:  EstrategiaSpider.parse()         -> listagem: descobre posts + pagina
#           EstrategiaSpider.parse_post()    -> extrai PDFs do post, classifica e
#                                             emite itens (dedup global)
#           EstrategiaSpider._inferir_banca() -> banca a partir do título do post
#           EstrategiaSpider._nome_arquivo()  -> nome a partir da URL
"""Spider do blog do Estratégia Concursos.

Descobre postagens recentes sobre provas/gabaritos e extrai os links de PDF.
O blog publica posts no formato "Prova e Gabarito <ORGAO>: ..." contendo
links para os PDFs hospedados no próprio domínio ou em CDN.

USO:
    # varre a busca do blog por "prova gabarito" (3 páginas de resultados)
    scrapy crawl estrategia

A classificação de papel vem de lexcorpus/heuristics.py (via delegação da
base, ADR-0004).

    # ou aponte para uma listagem/categoria específica
    scrapy crawl estrategia -a start_url="https://www.estrategiaconcursos.com.br/blog/?s=prova+gabarito" -a paginas=5

    # rótulos fixos (útil quando a listagem é de um único concurso)
    scrapy crawl estrategia -a start_url="..." -a banca="FGV" -a concurso="TJ-RJ 2024"
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, unquote

import scrapy

from .base import LexCorpusSpider
from ..util import slugify


BUSCA_PADRAO = "https://www.estrategiaconcursos.com.br/blog/?s=prova+gabarito"

# banca mencionada no título do post, ex.: "Prova e Gabarito TJ-SP FGV: ..."
_RE_BANCAS = re.compile(
    r"\b(CEBRASPE|CESPE|FGV|FCC|VUNESP|CESGRANRIO|IBFC|IDECAN|QUADRIX|"
    r"CONSULPLAN|AOCP|IBADE|IBAM|MS CONCURSOS|FGV)\b", re.I
)


class EstrategiaSpider(LexCorpusSpider):
    name = "estrategia"
    allowed_domains = ["estrategiaconcursos.com.br"]

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, start_url=None, paginas=3, banca=None, concurso=None,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [start_url or BUSCA_PADRAO]
        self.paginas_max = int(paginas)
        self.banca_fixa = banca
        self.concurso_fixo = concurso
        self._pagina = 1
        self._vistos: set[str] = set()

    def parse(self, response):
        # links de posts na listagem (cards do blog usam <article> ou títulos <h2>/<h3>)
        links = response.css(
            "article a::attr(href), "
            "h2 a::attr(href), h3 a::attr(href)"
        ).getall()
        for href in dict.fromkeys(links):  # dedup preservando ordem
            url = urljoin(response.url, href)
            if "/blog/" in url and url.rstrip("/") != response.url.rstrip("/"):
                yield response.follow(url, self.parse_post)

        # paginação da listagem
        self._pagina += 1
        if self._pagina <= self.paginas_max:
            prox = response.css(
                "a.next::attr(href), "
                "a[rel='next']::attr(href), "
                "link[rel='next']::attr(href), "
                ".pagination a:last-child::attr(href)"
            ).get()
            if prox:
                yield response.follow(prox, self.parse)

    def parse_post(self, response):
        titulo = (response.css("h1::text").get() or "").strip()
        banca_rotulo = self.banca_fixa or self._inferir_banca(titulo)
        concurso_rotulo = self.concurso_fixo or titulo or "Desconhecido"

        for a in response.css("a[href$='.pdf'], a[href*='.pdf?']"):
            href = a.attrib.get("href", "")
            if not href:
                continue
            pdf_url = urljoin(response.url, href)
            if pdf_url in self._vistos:
                continue

            texto_link = " ".join(a.css("::text").getall()).strip()
            papel = self.classificar_papel(texto_link, pdf_url)
            if not papel:
                continue

            self._vistos.add(pdf_url)
            cargo_rotulo = texto_link or "Geral"
            yield self.make_item(
                pdf_url=pdf_url,
                nome=self._nome_arquivo(pdf_url, texto_link),
                papel=papel,
                banca_rotulo=banca_rotulo,
                concurso_rotulo=concurso_rotulo,
                cargos_rotulo={slugify(cargo_rotulo): cargo_rotulo},
            )

    @staticmethod
    def _inferir_banca(titulo: str) -> str:
        m = _RE_BANCAS.search(titulo)
        return m.group(1).upper() if m else "Desconhecida"

    @staticmethod
    def _nome_arquivo(pdf_url: str, texto_link: str) -> str:
        nome = unquote(pdf_url.rsplit("/", 1)[-1].split("?")[0])
        if nome.lower().endswith(".pdf"):
            return nome
        base = slugify(texto_link)[:80] or "arquivo"
        return f"{base}.pdf"
