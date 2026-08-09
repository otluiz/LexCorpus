"""Spider da FGV — conhecimento.fgv.br (inclui o CNU — Concurso Nacional Unificado).

Site Drupal, HTML estático, SEM captcha e robots.txt permissivo para
/concursos/ e /sites/default/files/ (verificado 08/2026) — o crawl roda
com ROBOTSTXT_OBEY=True mesmo, ao contrário da FCC.

A página do concurso lista eventos em ordem cronológica reversa. Quando a
prova é aplicada, surge a seção de gabaritos/cadernos com esta estrutura:

    <p class="Indent1"><strong>Bloco Temático 1: Seguridade Social...</strong></p>
    <p class="Indent2"><a href=".../b1101.pdf">Tipo 1</a></p>
    <p class="Indent2"><a href=".../b1102.pdf">Tipo 2</a></p>
    ...
    <a href=".../ic7i2fda65.pdf">Gabarito Oficial Preliminar da Prova Objetiva</a>
    <a href=".../cpnu_gabarito_oficial_definitivo_retificado....pdf">
        Gabarito Oficial Definitivo da Prova Objetiva</a>

PECULIARIDADES TRATADAS AQUI:
  1. Os nomes dos PDFs são OPACOS (b1101.pdf, ic7i2fda65.pdf) — quem carrega
     o significado é o TEXTO DO LINK ("Tipo 1") e o CABEÇALHO da seção
     ("Bloco Temático 1: ..."). O spider percorre o documento EM ORDEM
     mantendo o bloco corrente e o associa a cada caderno.
  2. Só entram PDFs sob /sites/default/files/concursos/ — o rodapé do site
     tem termos de uso/aviso de cookies em PDF que NÃO são do concurso.
  3. O concurso cpnu2 vive na RAIZ (/cpnu2), não em /concursos/{slug} —
     por isso o spider aceita URL completa via -a url=.
  4. "Vista de Prova" / "Consulta individual" / recursos apontam para o
     portal do candidato (inscricao-*.conhecimento.fgv.br, exige login
     gov.br) — não são PDFs, nem chegam a ser considerados.

CICLO PRELIMINAR→DEFINITIVO: a página mantém os DOIS gabaritos (preliminar
e definitivo retificado). Hoje ambos saem com vigente=true; marcar o
preliminar como substituído é trabalho do StateStore (ver BACKLOG).

USO:
    # CNU 2025 (página na raiz do site):
    scrapy crawl fgv -a url="https://conhecimento.fgv.br/cpnu2" \
                     -a concurso="cnu_2025" \
                     -a concurso_rotulo="Concurso Público Nacional Unificado 2025"

    # concurso regular (página em /concursos/{slug}):
    scrapy crawl fgv -a slug="dataprev26"
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import scrapy

from ..items import ArquivoItem
from ..util import slugify


CONCURSOS_BASE = "https://conhecimento.fgv.br/concursos/{slug}"

_RE_GAB_DEF = re.compile(r"gabarito\s+oficial\s+definitiv", re.I)
_RE_GAB_PRE = re.compile(r"gabarito\s+oficial\s+preliminar", re.I)
_RE_GAB = re.compile(r"gabarito", re.I)
_RE_TIPO = re.compile(r"^tipo\s*(\d+)$", re.I)
_RE_BLOCO = re.compile(r"bloco\s+tem[áa]tico\s*(\d+)\s*[:\-–]?\s*(.*)", re.I)

# PDFs que NÃO são prova nem gabarito — descartados
_RE_DESCARTAR = re.compile(
    r"edital|resultado|retifica|convoca|recurso|homologa|cronograma"
    r"|inscri|isen[çc]|curriculo|nota.?de.?corte|certificado|sub.?judice"
    r"|rela[çc][ãa]o|notas.?m[íi]nimas|provimento|vagas.?remanescentes"
    r"|termo|aviso.?de.?cookies|pol[íi]tica|anexo",
    re.I,
)


def eh_relevante(texto: str, url: str) -> bool:
    alvo = f"{texto} {url}".lower()
    if "gabarito" in alvo:
        return True
    if _RE_DESCARTAR.search(alvo):
        return False
    return True


class FgvSpider(scrapy.Spider):
    name = "fgv"
    allowed_domains = ["conhecimento.fgv.br"]

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, url=None, slug=None, banca="FGV", concurso=None,
                 concurso_rotulo=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not url and not slug:
            raise ValueError(
                'passe -a url="https://conhecimento.fgv.br/<pagina>" '
                'ou -a slug="dataprev26"'
            )
        self.start_url = url or CONCURSOS_BASE.format(slug=slug.strip().lower())
        self.banca_rotulo = banca
        self.concurso_slug = concurso          # override: ex. "cnu_2025"
        self.concurso_rotulo_arg = concurso_rotulo

    async def start(self):
        yield scrapy.Request(self.start_url, callback=self.parse_concurso)

    def parse_concurso(self, response):
        banca = slugify(self.banca_rotulo)

        # slug do concurso: override > penúltimo segmento do path
        partes = [p for p in urlparse(response.url).path.split("/") if p]
        slug_pag = partes[-1].split(".")[0]
        concurso = slugify(self.concurso_slug or slug_pag)

        titulo = (response.css("title::text").get() or slug_pag).strip()
        concurso_rotulo = (
            self.concurso_rotulo_arg
            or re.sub(r"\s*\|\s*FGV.*$", "", titulo, flags=re.I).strip()
        )

        vistos = set()
        n = 0
        bloco_atual = None   # {"slug": ..., "rotulo": ...} do cabeçalho corrente

        # percorre o documento EM ORDEM: cabeçalhos atualizam o contexto,
        # links de PDF consomem o contexto
        for el in response.css("p, h2, h3, h4, li"):
            texto_el = " ".join(el.css("::text").getall()).strip()
            m_bloco = _RE_BLOCO.search(texto_el)
            if m_bloco and not el.css("a"):
                num, nome_bloco = m_bloco.group(1), m_bloco.group(2).strip()
                rotulo = f"Bloco Temático {num}" + (f": {nome_bloco}" if nome_bloco else "")
                bloco_atual = {"slug": slugify(rotulo), "rotulo": rotulo}

            for a in el.css("a[href]"):
                href = a.attrib.get("href", "")
                pdf_url = urljoin(response.url, href)
                path = urlparse(pdf_url).path.lower()
                if not path.endswith(".pdf"):
                    continue
                # só PDFs do concurso; rodapé (termos/cookies) fica de fora
                if "/concursos/" not in path:
                    self.logger.info("fora de /concursos/, ignorado: %s", pdf_url)
                    continue
                if pdf_url in vistos:
                    continue
                vistos.add(pdf_url)

                texto_link = " ".join(a.css("::text").getall()).strip()
                if not eh_relevante(texto_link, pdf_url):
                    self.logger.info("descartado: %s", pdf_url)
                    continue

                papel, tipo_prova, cargos_rotulo, multi = self._classificar(
                    texto_link, bloco_atual
                )
                if papel is None:
                    self.logger.info("não classificável, ignorado: %s", pdf_url)
                    continue

                nome = self._nome_final(pdf_url, papel, concurso,
                                        bloco_atual, tipo_prova)

                item = ArquivoItem()
                item["file_urls"] = [pdf_url]
                item["fonte_url"] = pdf_url
                item["nome"] = nome
                item["banca"] = banca
                item["concurso"] = concurso
                item["banca_rotulo"] = self.banca_rotulo
                item["concurso_rotulo"] = concurso_rotulo
                item["cargos_rotulo"] = cargos_rotulo
                item["papel"] = papel
                item["cargos"] = list(cargos_rotulo.keys())
                item["tipo_prova"] = tipo_prova
                item["multi_cargo"] = multi
                item["vigente"] = True
                self.logger.info("PDF [%s]: %s", papel, nome)
                n += 1
                yield item

        if n == 0:
            self.logger.warning(
                "nenhuma prova/gabarito em %s — se o concurso ainda não "
                "aplicou provas, a seção 'Provas e Gabaritos' não existe "
                "na página ainda (normal).",
                response.url,
            )

    def _classificar(self, texto_link, bloco_atual):
        """Retorna (papel, tipo_prova, cargos_rotulo, multi_cargo)."""
        if _RE_GAB_DEF.search(texto_link):
            # gabarito consolidado cobre TODOS os blocos/tipos do concurso
            return ("gabarito_definitivo", None, {"*": "*"}, True)
        if _RE_GAB_PRE.search(texto_link):
            return ("gabarito_preliminar", None, {"*": "*"}, True)
        if _RE_GAB.search(texto_link):
            return ("gabarito_definitivo", None, {"*": "*"}, True)

        m_tipo = _RE_TIPO.match(texto_link)
        if m_tipo:
            tipo_prova = m_tipo.group(1)
            if bloco_atual:
                return ("prova", tipo_prova,
                        {bloco_atual["slug"]: bloco_atual["rotulo"]}, False)
            return ("prova", tipo_prova, {"geral": "Geral"}, False)

        if re.search(r"prova|caderno", texto_link, re.I):
            # prova com rótulo livre (ex.: "Prova Discursiva")
            rotulo = texto_link or "Prova"
            return ("prova", None, {slugify(rotulo): rotulo}, False)

        return (None, None, None, None)

    @staticmethod
    def _nome_final(pdf_url, papel, concurso, bloco_atual, tipo_prova):
        """Nomes opacos (b1101.pdf) viram nomes semânticos e únicos.

        Prova:   {concurso}_{bloco}_tipo_{n}.pdf
        Gabarito:{concurso}_{papel}_{data-ou-stem}.pdf
        """
        base = urlparse(pdf_url).path.rsplit("/", 1)[-1] or "arquivo.pdf"
        stem = slugify(base[:-4] if base.lower().endswith(".pdf") else base)
        if papel == "prova":
            partes = [concurso]
            if bloco_atual:
                partes.append(bloco_atual["slug"])
            if tipo_prova:
                partes.append(f"tipo_{tipo_prova}")
            if len(partes) == 1:          # sem bloco nem tipo: mantém o stem
                partes.append(stem)
            return "_".join(partes) + ".pdf"
        # gabarito: nome original costuma ser descritivo; opaco vira papel+stem
        if stem and stem not in ("", "arquivo"):
            return f"{concurso}_{papel}_{stem}.pdf"
        return f"{concurso}_{papel}.pdf"
