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

    # DESCOBERTA: percorre a listagem /concursos (com paginação ?page=N) e
    # extrai os slugs disponíveis — NÃO baixa PDF. Saída: JSONL em
    # {EVENTOS_OUT_DIR}/descoberta/fgv.jsonl (alimenta o watchlist):
    scrapy crawl fgv -a descoberta=1
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import scrapy

from .base import LexCorpusSpider
from ..util import slugify


CONCURSOS_BASE = "https://conhecimento.fgv.br/concursos/{slug}"
LISTAGEM_URL = "https://conhecimento.fgv.br/concursos"

_RE_TIPO = re.compile(r"^tipo\s*(\d+)$", re.I)
_RE_BLOCO = re.compile(r"bloco\s+tem[áa]tico\s*(\d+)\s*[:\-–]?\s*(.*)", re.I)

class FgvSpider(LexCorpusSpider):
    name = "fgv"
    allowed_domains = ["conhecimento.fgv.br"]

    RE_GAB_DEF = re.compile(r"gabarito\s+oficial\s+definitiv", re.I)
    RE_GAB_PRE = re.compile(r"gabarito\s+oficial\s+preliminar", re.I)
    RE_GAB = re.compile(r"gabarito", re.I)
    RE_DESCARTAR = re.compile(
        r"edital|resultado|convoca|recurso|homologa|cronograma"
        r"|inscri|isen[çc]|curriculo|nota.?de.?corte|certificado|sub.?judice"
        r"|rela[çc][ãa]o|notas.?m[íi]nimas|provimento|vagas.?remanescentes"
        r"|termo|aviso.?de.?cookies|pol[íi]tica|anexo",
        re.I,
    )

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
    }

    def eh_relevante(self, texto: str, url: str = "") -> bool:
        """Na FGV, gabarito retificado continua sendo gabarito válido."""
        alvo = f"{texto} {url}".lower()
        if self.RE_GAB.search(alvo):
            return True
        return super().eh_relevante(texto, url)

    def __init__(self, url=None, slug=None, banca="FGV", concurso=None,
                 concurso_rotulo=None, descoberta=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.descoberta = bool(descoberta)
        if not self.descoberta and not url and not slug:
            raise ValueError(
                'passe -a url="https://conhecimento.fgv.br/<pagina>" '
                'ou -a slug="dataprev26" (ou -a descoberta=1 para listar '
                'os slugs de /concursos)'
            )
        self.start_url = (
            LISTAGEM_URL if self.descoberta
            else url or CONCURSOS_BASE.format(slug=slug.strip().lower())
        )
        self.banca_rotulo = banca
        self.concurso_slug = concurso          # override: ex. "cnu_2025"
        self.concurso_rotulo_arg = concurso_rotulo
        self._descobertos = {}                 # slug -> {slug, rotulo, url}

    async def start(self):
        callback = self.parse_listagem if self.descoberta else self.parse_concurso
        yield scrapy.Request(self.start_url, callback=callback)

    def parse_listagem(self, response):
        """Modo descoberta: extrai slugs da listagem /concursos (sem download).

        Os cards de concurso são <a href="/concursos/{slug}" hreflang="pt-br">
        — o atributo hreflang distingue os cards dos links de navegação
        (/concursos, /concursos/nosso-portfolio#tabs). Paginação via
        a[rel="next"] (?page=N).
        """
        for a in response.css('a[href^="/concursos/"][hreflang]'):
            href = a.attrib.get("href", "")
            partes = [p for p in urlparse(href).path.split("/") if p]
            if len(partes) != 2:               # só /concursos/{slug}
                continue
            slug = partes[1]
            if slug in self._descobertos:
                continue
            rotulo = " ".join(" ".join(a.css("::text").getall()).split())
            self._descobertos[slug] = {
                "banca": "fgv",
                "slug": slug,
                "rotulo": rotulo,
                "url": urljoin(response.url, href),
            }
            self.logger.info("descoberto: %s — %s", slug, rotulo)

        proxima = response.css('a[rel="next"]::attr(href)').get()
        if proxima:
            yield response.follow(proxima, callback=self.parse_listagem)

    def closed(self, reason):
        if not self.descoberta:
            return
        out_dir = Path(self.settings.get("EVENTOS_OUT_DIR", "eventos_debug"))
        out_dir = out_dir / "descoberta"
        out_dir.mkdir(parents=True, exist_ok=True)
        destino = out_dir / "fgv.jsonl"
        with destino.open("w", encoding="utf-8") as f:
            for d in sorted(self._descobertos.values(), key=lambda d: d["slug"]):
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self.logger.info(
            "descoberta FGV: %d slugs -> %s", len(self._descobertos), destino
        )

    def parse_concurso(self, response):
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
                if not self.eh_relevante(texto_link, pdf_url):
                    self.logger.info("descartado: %s", pdf_url)
                    continue

                papel, tipo_prova, cargos_rotulo, multi = self._classificar(
                    texto_link, pdf_url, bloco_atual
                )
                if papel is None:
                    self.logger.info("não classificável, ignorado: %s", pdf_url)
                    continue

                nome = self._nome_final(pdf_url, papel, concurso,
                                        bloco_atual, tipo_prova)

                item = self.make_item(
                    pdf_url=pdf_url,
                    nome=nome,
                    papel=papel,
                    banca_rotulo=self.banca_rotulo,
                    concurso_rotulo=concurso_rotulo,
                    cargos_rotulo=cargos_rotulo,
                    concurso=concurso,
                    tipo_prova=tipo_prova,
                    multi_cargo=multi,
                )
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

    def _classificar(self, texto_link, pdf_url, bloco_atual):
        """Retorna (papel, tipo_prova, cargos_rotulo, multi_cargo)."""
        papel = self.classificar_papel(texto_link, pdf_url)
        if papel == "gabarito_definitivo":
            # gabarito consolidado cobre TODOS os blocos/tipos do concurso
            return ("gabarito_definitivo", None, {"*": "*"}, True)
        if papel == "gabarito_preliminar":
            return ("gabarito_preliminar", None, {"*": "*"}, True)

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
