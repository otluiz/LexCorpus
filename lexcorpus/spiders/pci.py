"""Spider do PCI Concursos — modo teste controlado (UMA prova).

Aponta para UMA página de download do PCI, extrai os links de PDF (prova +
gabarito), classifica o papel de cada um e emite ArquivoItems. O download,
checksum, sidecar e evento são responsabilidade dos pipelines (não do spider).

USO:
    # a URL da página de download é passada por argumento -a
    scrapy crawl pci -a url="https://www.pciconcursos.com.br/provas/download/..." \
                     -a banca="CEBRASPE" -a concurso="TJ-ES 2023" -a cargo="Analista"

Estratégia de robustez:
    Em vez de depender de um seletor CSS frágil (que quebra quando o PCI mexe no
    layout), o spider varre TODOS os links <a> da página e seleciona os que
    terminam em .pdf. A classificação prova/gabarito é heurística, por pistas no
    texto do link e no nome do arquivo. Heurística é do coletor; o LexLearn
    reclassifica com autoridade — então "errar pra prova" aqui é aceitável.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import scrapy

from ..items import ArquivoItem
from ..util import slugify


# pistas textuais para classificar o papel do arquivo (heurística)
_RE_GAB_DEF = re.compile(r"gabarito.*(definitiv|final|oficial|p[óo]s.?recurs)", re.I)
_RE_GAB_PRE = re.compile(r"gabarito.*(preliminar|provis[óo]ri)", re.I)
_RE_GAB = re.compile(r"gabarito|gab[\W_]", re.I)
_RE_PROVA = re.compile(r"prova|caderno", re.I)

# PDFs que NÃO são prova nem gabarito — descartados (não é papel do coletor pegar)
_RE_DESCARTAR = re.compile(
    r"edital|retifica|convoca|resultado|homologa|cronograma|inscri|isen[çc]", re.I
)


def eh_relevante(texto: str, url: str) -> bool:
    """False para PDFs que não são prova nem gabarito (edital, retificação...)."""
    alvo = f"{texto} {url}".lower()
    if _RE_DESCARTAR.search(alvo):
        # só descarta se NÃO houver também pista forte de prova/gabarito
        if not (_RE_GAB.search(alvo) or _RE_PROVA.search(alvo)):
            return False
    return True


def classificar_papel(texto: str, url: str) -> str:
    """Decide papel a partir do texto do link + nome do arquivo.

    Ordem importa: definitivo antes de preliminar antes de gabarito genérico.
    Default: 'prova' (o caso mais comum e o mais seguro de errar para).
    """
    alvo = f"{texto} {url}".lower()
    if _RE_GAB_DEF.search(alvo):
        return "gabarito_definitivo"
    if _RE_GAB_PRE.search(alvo):
        return "gabarito_preliminar"
    if _RE_GAB.search(alvo):
        # gabarito sem qualificador -> tratamos como definitivo por padrão
        # (a maioria dos agregadores só hospeda o gabarito final)
        return "gabarito_definitivo"
    return "prova"


class PciSpider(scrapy.Spider):
    name = "pci"
    allowed_domains = ["pciconcursos.com.br"]

    # custom_settings garante o modo "gentil" mesmo se o settings global mudar
    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, url=None, banca="", concurso="", cargo="",
                 tipo_prova=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not url:
            raise ValueError(
                "passe a URL da página de download: -a url=\"https://...\""
            )
        self.start_url = url
        # rótulos crus (como o usuário informou); slugificados ao montar o item
        self.banca_rotulo = banca or "desconhecida"
        self.concurso_rotulo = concurso or "desconhecido"
        self.cargo_rotulo = cargo or "desconhecido"
        self.tipo_prova = tipo_prova

    async def start(self):
        # Scrapy 2.13+: start() assíncrono substitui start_requests()
        yield scrapy.Request(self.start_url, callback=self.parse_download)

    def parse_download(self, response):
        banca = slugify(self.banca_rotulo)
        concurso = slugify(self.concurso_rotulo)
        cargo = slugify(self.cargo_rotulo)

        vistos = set()
        # varre todos os links; seleciona PDFs
        for a in response.css("a[href]"):
            href = a.attrib.get("href", "")
            if not href.lower().endswith(".pdf"):
                continue
            pdf_url = urljoin(response.url, href)
            if pdf_url in vistos:
                continue
            vistos.add(pdf_url)

            texto_link = " ".join(a.css("::text").getall()).strip()
            if not eh_relevante(texto_link, pdf_url):
                self.logger.info("descartado (não é prova/gabarito): %s", pdf_url)
                continue
            papel = classificar_papel(texto_link, pdf_url)
            nome = self._nome_final(pdf_url, papel, cargo)

            item = ArquivoItem()
            item["file_urls"] = [pdf_url]
            item["fonte_url"] = pdf_url
            item["nome"] = nome
            item["banca"] = banca
            item["concurso"] = concurso
            item["banca_rotulo"] = self.banca_rotulo
            item["concurso_rotulo"] = self.concurso_rotulo
            item["cargos_rotulo"] = {cargo: self.cargo_rotulo}
            item["papel"] = papel
            item["cargos"] = [cargo]
            item["tipo_prova"] = self.tipo_prova
            item["multi_cargo"] = False
            item["vigente"] = True
            self.logger.info("PDF encontrado [%s]: %s", papel, pdf_url)
            yield item

        if not vistos:
            self.logger.warning(
                "nenhum PDF encontrado em %s — confira o seletor ou a URL",
                response.url,
            )

    @staticmethod
    def _nome_final(pdf_url: str, papel: str, cargo: str) -> str:
        """Basename final no storage, sem barra (contrato: pattern ^[^/]+$).

        Prefixa cargo+papel para evitar colisão, mas evita duplicar o papel
        quando o nome cru já o contém (ex.: 'gabarito_definitivo_x.pdf')."""
        base = urlparse(pdf_url).path.rsplit("/", 1)[-1] or "arquivo.pdf"
        base = base.replace("/", "_")
        stem = base[:-4] if base.lower().endswith(".pdf") else base
        stem_slug = slugify(stem)
        partes = [cargo]
        # só acrescenta o papel se o nome cru ainda não o expressa
        marcador = "gabarito" if papel.startswith("gabarito") else "prova"
        if marcador not in stem_slug:
            partes.append(papel)
        partes.append(stem_slug)
        return "_".join(p for p in partes if p) + ".pdf"
