"""Spider do CEBRASPE — consome a API oficial de eventos (JSON) e baixa os PDFs.

A página do concurso é uma SPA que consome a API:
    https://apis.cebraspe.org.br/cebraspe/eventos/{slug}
Este spider fala direto com a API (sem browser), lê a lista de arquivos e
baixa prova/gabarito do CDN:
    https://cdn.cebraspe.org.br/concursos/{slug}/arquivos/{nomeArquivo}

USO:
    scrapy crawl cebraspe -a slug="prf_21"

A API separa arquivos em:
  - arquivosGabarito[] -> PROVAS e GABARITOS (é o que queremos)
  - arquivosEdital[]   -> editais/comunicados/retificações (ignorado)
  - aplicativos[]      -> links de sistemas (ignorado)

Classificação de papel vem da descricaoArquivo (confiável, não heurística):
  "GABARITO DEFINITIVO ..."  -> gabarito_definitivo
  "GABARITO PRELIMINAR ..."  -> gabarito_preliminar
  "PROVA OBJETIVA ...", "PROVA DISCURSIVA ...", "PADRÃO DE RESPOSTA ..." -> prova
"""
from __future__ import annotations

import json
import re

import scrapy

from ..items import ArquivoItem
from ..util import slugify


API_BASE = "https://apis.cebraspe.org.br/cebraspe/eventos/{slug}"
CDN_BASE = "https://cdn.cebraspe.org.br/concursos/{slug}/arquivos/{nome}"

_RE_GAB_DEF = re.compile(r"gabarito\s+definitiv", re.I)
_RE_GAB_PRE = re.compile(r"gabarito\s+preliminar", re.I)
_RE_GAB = re.compile(r"gabarito", re.I)
_RE_PROVA = re.compile(r"prova|padr[ãa]o\s+de\s+resposta|caderno", re.I)


def classificar_papel(descricao: str, nome: str) -> str | None:
    """Papel a partir da descrição (primária) e do nome (reforço).

    Retorna None se não for prova nem gabarito (ex.: edital que escapou).
    """
    alvo = f"{descricao} {nome}".lower()
    if _RE_GAB_DEF.search(alvo):
        return "gabarito_definitivo"
    if _RE_GAB_PRE.search(alvo):
        return "gabarito_preliminar"
    if _RE_GAB.search(alvo):
        return "gabarito_definitivo"  # gabarito sem qualificador -> definitivo
    if _RE_PROVA.search(alvo):
        return "prova"
    return None  # não classificável como prova/gabarito


class CebraspeSpider(scrapy.Spider):
    name = "cebraspe"
    allowed_domains = ["cebraspe.org.br"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, slug=None, banca="CEBRASPE", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slug = slug
        self.banca_rotulo = banca

    async def start(self):
        if not self.slug:
            raise ValueError('passe o slug do concurso: -a slug="prf_21"')
        slug = self.slug.lower()
        yield scrapy.Request(
            API_BASE.format(slug=slug),
            callback=self.parse_api,
            headers={"Accept": "application/json"},
        )

    def parse_api(self, response):
        try:
            dados = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error("resposta da API não é JSON válido: %s", response.url)
            return

        slug = self.slug.lower()
        banca = slugify(self.banca_rotulo)

        # rótulos crus vindos da própria API
        concurso_rotulo = (
            dados.get("eventoNomeCompleto")
            or dados.get("eventoNomeAbreviado")
            or self.slug
        )
        # slug do concurso: preferir eventoURL (já é o identificador canônico do
        # CEBRASPE, ex.: "PRF_21"); cai para o slug passado. NÃO concatenar ano,
        # que já costuma estar embutido e geraria duplicação (prf_2021_2021).
        concurso = slugify(dados.get("eventoURL") or self.slug)

        # cargos do concurso (rótulo cru -> slug)
        cargos_rotulo = {}
        for c in dados.get("eventoCargos", []):
            area = c.get("area", "")
            # "Cargo 1: POLICIAL RODOVIÁRIO FEDERAL - Subsídio..." -> pega o miolo
            m = re.search(r":\s*(.+?)(?:\s*-\s*Subs[íi]dio|\s*-\s*R\$|$)", area)
            nome_cargo = (m.group(1).strip() if m else area.strip()) or "geral"
            cargos_rotulo[slugify(nome_cargo)] = nome_cargo
        if not cargos_rotulo:
            cargos_rotulo = {"geral": "Geral"}
        cargos_slugs = list(cargos_rotulo.keys())

        # A API não amarra arquivo->cargo; o concurso PRF é cargo único.
        # Para concursos multi-cargo, cargos_slugs terá vários; marcamos todos.
        multi = len(cargos_slugs) > 1

        arquivos = dados.get("arquivosGabarito", [])
        if not arquivos:
            self.logger.warning("arquivosGabarito vazio para %s", slug)
            return

        n = 0
        for arq in arquivos:
            nome = arq.get("nomeArquivo", "")
            desc = arq.get("descricaoArquivo", "")
            if not nome.lower().endswith(".pdf"):
                continue  # ignora .mp4 etc.
            papel = classificar_papel(desc, nome)
            if papel is None:
                self.logger.info("ignorado (não é prova/gabarito): %s", desc[:60])
                continue

            pdf_url = CDN_BASE.format(slug=slug, nome=nome)
            item = ArquivoItem()
            item["file_urls"] = [pdf_url]
            item["fonte_url"] = pdf_url
            item["nome"] = nome                      # nome ORIGINAL do CEBRASPE, sem barra
            item["banca"] = banca
            item["concurso"] = concurso
            item["banca_rotulo"] = self.banca_rotulo
            item["concurso_rotulo"] = concurso_rotulo
            item["cargos_rotulo"] = cargos_rotulo
            item["papel"] = papel
            item["cargos"] = cargos_slugs if multi else [cargos_slugs[0]]
            item["tipo_prova"] = None
            item["multi_cargo"] = multi
            item["vigente"] = True
            self.logger.info("PDF [%s]: %s", papel, nome)
            n += 1
            yield item

        self.logger.info("%s arquivos de prova/gabarito emitidos para %s", n, slug)
