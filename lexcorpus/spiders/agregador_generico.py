# Arquivo:  lexcorpus/spiders/agregador_generico.py
# Função:   spider parametrizável para sites agregadores — URL, seletores CSS e
#           rótulos de banca/concurso vêm de argumentos (-a), sem código novo.
# Funções:  AgregadorGenericoSpider.parse()          -> navega listagem -> posts
#           AgregadorGenericoSpider.parse_pdf_page() -> extrai PDFs, classifica e
#                                                     emite itens (dedup global)
#           AgregadorGenericoSpider._nome_arquivo()  -> nome a partir da URL
"""Spider Genérico para Agregadores.

Permite configurar seletores e URLs via argumentos, facilitando a adição
de novas fontes sem criar novos arquivos de código.

USO:
    scrapy crawl agregador_generico \
        -a start_url="https://exemplo.com/concursos" \
        -a post_selector="h2.titulo a" \
        -a pdf_selector="a[href$='.pdf']" \
        -a banca="FGV" -a concurso="TJ-RJ 2024"

NOTA: por ser genérico, este spider não define allowed_domains nem
custom_settings próprios (delay, robots.txt). Avalie cada nova fonte e,
se necessário, ajuste via linha de comando:
    -s DOWNLOAD_DELAY=2 -s ROBOTSTXT_OBEY=True

A classificação de papel vem de lexcorpus/heuristics.py (via delegação da
base, ADR-0004). Se a fonte exigir regex próprios, sobrescreva os atributos
RE_* da classe ou chame heuristics.classificar_papel diretamente.
"""
from __future__ import annotations

from urllib.parse import urljoin, unquote

import scrapy

from .base import LexCorpusSpider
from ..util import slugify


class AgregadorGenericoSpider(LexCorpusSpider):
    name = "agregador_generico"

    def __init__(self, start_url=None, post_selector=None,
                 pdf_selector="a[href$='.pdf']",
                 banca="Desconhecida", concurso="Desconhecido", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [start_url] if start_url else []
        self.post_selector = post_selector
        self.pdf_selector = pdf_selector
        self.banca_rotulo = banca
        self.concurso_rotulo = concurso
        # deduplicação no nível do spider: o mesmo PDF pode aparecer
        # em vários posts/páginas diferentes
        self._vistos: set[str] = set()

    def parse(self, response):
        if self.post_selector:
            # Se houver um seletor de posts, navega para cada um
            for link in response.css(self.post_selector + "::attr(href)").getall():
                yield response.follow(link, self.parse_pdf_page)
        else:
            # Caso contrário, assume que a página atual já contém os PDFs
            yield from self.parse_pdf_page(response)

    def parse_pdf_page(self, response):
        for a in response.css(self.pdf_selector):
            href = a.attrib.get("href", "")
            if not href:
                continue
            pdf_url = urljoin(response.url, href)
            if pdf_url in self._vistos:
                continue
            self._vistos.add(pdf_url)

            texto_link = " ".join(a.css("::text").getall()).strip()
            papel = self.classificar_papel(texto_link, pdf_url)
            if not papel:
                continue

            cargo_rotulo = texto_link or "Geral"
            yield self.make_item(
                pdf_url=pdf_url,
                nome=self._nome_arquivo(pdf_url, texto_link),
                papel=papel,
                banca_rotulo=self.banca_rotulo,
                concurso_rotulo=self.concurso_rotulo,
                cargos_rotulo={slugify(cargo_rotulo): cargo_rotulo},
            )

    @staticmethod
    def _nome_arquivo(pdf_url: str, texto_link: str) -> str:
        """Prefere o nome original do arquivo na URL; usa o texto do link
        (truncado) apenas como fallback, evitando nomes longos e redundantes."""
        nome = unquote(pdf_url.rsplit("/", 1)[-1])
        if nome.lower().endswith(".pdf"):
            return nome
        base = slugify(texto_link)[:80] or "arquivo"
        return f"{base}.pdf"
