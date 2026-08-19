"""Spider gerado pelo orquestrador para cesgranrio (Transpetro).

GENERO na raiz: copie spiders/exemplo_banca.py e AJUSTE os seletores contra
o HTML real da página do concurso. NÃO altere pipelines (contrato v2.0).

Página-alvo descoberta: https://www.cesgranrio.org.br/concurso/transpetro-2026/
Bloqueios conhecidos: WAF Azure Front Door (403); requer imagem Playwright (docker stage playwright) + scrapy-playwright

Parâmetros (todos por -a, nada hardcoded):
    url          — URL da página do concurso (obrigatório)
    organ        — órgão/empresa (rotulo)
    concurso     — slug do concurso (ex.: transpetro_2023)
    cargo        — cargo-alvo (opcional; usado no metadado)

Status: ativo=false no watchlist até validação manual do seletor.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import scrapy

from ..items import ArquivoItem
from ..util import slugify
from .base import LexCorpusSpider


class CesgranrioSpider(LexCorpusSpider):
    name = "cesgranrio"
    # Ajuste após inspecionar o HTML real (scrapy fetch https://www.cesgranrio.org.br/concurso/transpetro-2026/ > page.html)
    PDF_SELECTOR = "a[href$='.pdf']"

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
        # WAF exige browser real — descomente após instalar
        # "DOWNLOADER_MIDDLEWARES": {
        #     "scrapy_playwright.middleware.ScrapyPlaywrightDownloadHandler": 800,
        # },
        # "PLAYWRIGHT_BROWSER_TYPE": "chromium",
    }

    def __init__(self, url=None, organ="", concurso="", cargo="",
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not url:
            raise ValueError('passe a URL da página do concurso: -a url="..."')
        self.start_url = url
        self.organ = organ or "desconhecido"
        self.concurso_slug = concurso or slugify(self.organ)
        self.cargo = cargo or ""

    async def start(self):
        yield scrapy.Request(self.start_url, callback=self.parse_concurso)

    def parse_concurso(self, response):
        for a in response.css(self.PDF_SELECTOR):
            href = a.attrib.get("href", "")
            pdf_url = urljoin(response.url, href)
            texto = " ".join(a.css("::text").getall()).strip()
            if not self.eh_relevante(texto, pdf_url):
                continue
            papel = self.classificar_papel(texto, pdf_url)
            if papel is None:
                continue
            yield self.make_item(
                pdf_url=pdf_url, nome=self._nome(pdf_url, papel),
                papel=papel, banca_rotulo="cesgranrio",
                concurso_rotulo=f"{self.organ} {self.concurso_slug}",
                cargos_rotulo={self.cargo or "geral": self.cargo or "Geral"},
            )

    def _nome(self, pdf_url, papel):
        import os
        base = os.path.basename(pdf_url)
        return base if base else "arquivo.pdf"
