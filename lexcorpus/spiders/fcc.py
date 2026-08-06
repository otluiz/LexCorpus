"""Spider da FCC — Fundação Carlos Chagas (concursosfcc.com.br).

A página de um concurso FCC é estática e lista comunicados/editais:
    https://www.concursosfcc.com.br/concursos/{slug}/index.html
O rótulo do concurso vem do <title> ("FCC - Defensoria Publica do Estado da
Bahia") e o ano do próprio slug ("dpeba125" -> 2025).

O QUE DÁ PARA COLETAR (fonte primária, público):
  - "Edital ... de Divulgação dos Gabaritos"  -> gabarito_definitivo
  - "Alteração de Gabarito e Atribuição de Questões" -> gabarito_definitivo
  - PDFs de prova, QUANDO publicados abertamente (raro; ver limitação abaixo)

LIMITAÇÃO CONHECIDA (documentada, não é bug):
  Os cadernos de prova da FCC ficam no portal do candidato, atrás de acesso
  individual (hash derivado do nº do caderno + código do candidato — endpoint
  Publicacao/ListarPublicacaoLiberadasPorHashAcesso). Não são raspáveis sem
  credencial de candidato. Para cadernos FCC, a fonte continua sendo o PCI
  Concursos (spider `pci`). Este spider garante os GABARITOS oficiais, que
  saem publicamente como editais.

PEGADINHA DO SITE:
  Os links de PDF passam por um wrapper de acessibilidade:
      /rybena/web/index.html?file=<URL real do PDF>
  O spider desempacota o parâmetro `file` e baixa o PDF direto.

ARMADILHA DE CLASSIFICAÇÃO:
  "Provas e Condições Específicas Deferidas" NÃO é prova — é comunicado
  sobre atendimento especializado. As regras de descarte rodam antes e
  eliminam "condicoes_especificas", "resultado", "convocacao", "habilitados"
  etc. "Edital" só é descartado se NÃO mencionar gabarito (na FCC o gabarito
  definitivo sai DENTRO de um edital).

USO:
    scrapy crawl fcc -a url="https://www.concursosfcc.com.br/concursos/dpeba125/index.html"
    # ou só o slug:
    scrapy crawl fcc -a slug="dpeba125"
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

import scrapy

from ..items import ArquivoItem
from ..util import slugify


INDEX_BASE = "https://www.concursosfcc.com.br/concursos/{slug}/index.html"

# --- classificação de papel (heurística do coletor; LexLearn reclassifica) ---
_RE_GAB_DEF = re.compile(r"gabarito", re.I)  # na FCC, gabarito em edital = definitivo
_RE_GAB_ALT = re.compile(r"altera[çc][ãa]o.*gabarito|gabarito.*altera[çc]", re.I)
_RE_PROVA = re.compile(r"\bprova\b|caderno", re.I)

# PDFs que NÃO são prova nem gabarito — descartados
_RE_DESCARTAR = re.compile(
    r"condi[çc][õo]es_espec|condi[çc][õo]es\s+espec"
    r"|resultado|homologa|convoca|cronograma|inscri|isen[çc]"
    r"|habilitad|retifica|vagas_reservadas|estatistica|candidatos"
    r"|local.*exame|atendimento|deferid|sub.?judice",
    re.I,
)


def desempacotar_rybena(href: str) -> str:
    """Extrai a URL real do PDF do wrapper /rybena/web/index.html?file=..."""
    if "rybena" in href and "file=" in href:
        qs = parse_qs(urlparse(href).query)
        if qs.get("file"):
            return qs["file"][0]
    return href


def eh_relevante(texto: str, url: str) -> bool:
    """False para comunicados/editais que não são prova nem gabarito."""
    alvo = f"{texto} {url}".lower()
    if "gabarito" in alvo:
        return True  # gabarito sempre interessa, mesmo dentro de edital
    if _RE_DESCARTAR.search(alvo):
        return False
    return bool(_RE_PROVA.search(alvo))


def classificar_papel(texto: str, url: str) -> str | None:
    """Papel a partir do texto do link + nome do arquivo. None = descartar."""
    alvo = f"{texto} {url}".lower()
    if not eh_relevante(texto, url):
        return None
    if _RE_GAB_DEF.search(alvo):
        # FCC não publica "gabarito preliminar" fora do portal do candidato;
        # o que sai em edital/comunicado público é sempre o definitivo
        # (inclui "Alteração de Gabarito", que consolida o pós-recurso).
        return "gabarito_definitivo"
    return "prova"


class FccSpider(scrapy.Spider):
    name = "fcc"
    allowed_domains = ["concursosfcc.com.br"]

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, url=None, slug=None, banca="FCC", *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not url and not slug:
            raise ValueError(
                'passe -a url="https://www.concursosfcc.com.br/concursos/<slug>/index.html" '
                'ou -a slug="dpeba125"'
            )
        self.start_url = url or INDEX_BASE.format(slug=slug.strip().lower())
        self.banca_rotulo = banca

    async def start(self):
        yield scrapy.Request(self.start_url, callback=self.parse_concurso)

    def parse_concurso(self, response):
        banca = slugify(self.banca_rotulo)

        # slug canônico do concurso: penúltimo segmento do path (ex.: "dpeba125")
        partes = [p for p in urlparse(response.url).path.split("/") if p]
        slug_concurso = partes[-2] if len(partes) >= 2 else partes[-1].split(".")[0]
        concurso = slugify(slug_concurso)

        # rótulo cru: <title> "FCC - Defensoria Publica do Estado da Bahia"
        titulo = (response.css("title::text").get() or slug_concurso).strip()
        concurso_rotulo = re.sub(r"^FCC\s*-\s*", "", titulo, flags=re.I).strip()
        # ano embutido no slug ("...125" -> 2025): anexa ao rótulo se ausente
        m_ano = re.search(r"(\d)(\d{2})$", slug_concurso)
        if m_ano and m_ano.group(2) not in concurso_rotulo:
            concurso_rotulo = f"{concurso_rotulo} 20{m_ano.group(2)}"

        vistos = set()
        n = 0
        for a in response.css("a[href]"):
            href = desempacotar_rybena(a.attrib.get("href", ""))
            if not href.lower().split("?")[0].endswith(".pdf"):
                continue
            pdf_url = urljoin(response.url, href)
            if pdf_url in vistos:
                continue
            vistos.add(pdf_url)

            texto_link = " ".join(a.css("::text").getall()).strip()
            papel = classificar_papel(texto_link, pdf_url)
            if papel is None:
                self.logger.info("descartado (não é prova/gabarito): %s", pdf_url)
                continue

            nome = self._nome_final(pdf_url, papel, slug_concurso)

            item = ArquivoItem()
            item["file_urls"] = [pdf_url]
            item["fonte_url"] = pdf_url
            item["nome"] = nome
            item["banca"] = banca
            item["concurso"] = concurso
            item["banca_rotulo"] = self.banca_rotulo
            item["concurso_rotulo"] = concurso_rotulo
            # a página não discrimina gabarito por cargo; o edital cobre o concurso
            item["cargos_rotulo"] = {"*": "*"}
            item["papel"] = papel
            item["cargos"] = ["*"]
            item["tipo_prova"] = None
            item["multi_cargo"] = True
            item["vigente"] = True
            self.logger.info("PDF [%s]: %s", papel, nome)
            n += 1
            yield item

        if n == 0:
            self.logger.warning(
                "nenhuma prova/gabarito público em %s — se o concurso está em "
                "andamento, os cadernos ficam no portal do candidato (acesso "
                "individual, não raspável); os gabaritos saem como edital ao "
                "final do certame.",
                response.url,
            )

    @staticmethod
    def _nome_final(pdf_url: str, papel: str, slug_concurso: str) -> str:
        """Basename final no storage, sem barra (contrato: pattern ^[^/]+$).

        Prefixa o slug do concurso quando o nome cru não o contém (evita
        colisão entre concursos com nomes genéricos tipo 'edital.pdf')."""
        base = urlparse(pdf_url).path.rsplit("/", 1)[-1] or "arquivo.pdf"
        base = base.replace("/", "_")
        stem = base[:-4] if base.lower().endswith(".pdf") else base
        stem_slug = slugify(stem)
        if slug_concurso.lower() in stem_slug:
            return f"{stem_slug}.pdf"
        return f"{slugify(slug_concurso)}_{stem_slug}.pdf"
