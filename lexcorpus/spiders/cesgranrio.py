"""Spider da CESGRANRIO — API pública do portal de concursos (sem browser).

HISTÓRICO (ver BACKLOG): em 05/08 o cesgranrio.org.br respondia 403 para
qualquer cliente não-navegador (Azure Front Door) e o item ficou BLOQUEADO
aguardando Playwright. Re-diagnóstico em 18/08: o site foi reformulado, o
WAF não bloqueia mais HTTP simples e a SPA do portal consome uma API JSON
PÚBLICA — este spider fala direto com ela, como o cebraspe.py.

FLUXO:
    GET /api/PortalEventos/{id}                     -> nome, nomeFantasia, cliente
    GET /api/PortalEventoConteudos/publico/{id}     -> blocos {titulo, texto(HTML)}
O texto de cada bloco é HTML com <a href=".../media/.../{guid}.pdf?sv=...&sig=...">
— URLs Azure Blob com token SAS próprio e longa validade (se=2036). O NOME
do arquivo é opaco (GUID): quem carrega o significado é o TÍTULO DO BLOCO +
o TEXTO DO LINK. Estrutura real observada (BNB, BNDES, BANESE, BASA, CEF):

    [PROVAS / Provas - DD/MM/AAAA]         'PROVA 1 - ARQUITETO',
                                           'PROVA A - GABARITO 1 - TÉCNICO BANCÁRIO I',
                                           'TÉCNICO BANCÁRIO - PROVA A - GABARITO 1'
    [GABARITOS / Gabaritos - DD/MM/AAAA]   'GABARITOS - ANALISTA BANCÁRIO 1',
                                           'Gabarito Prova A - TÉCNICO BANCÁRIO I'
    [GABARITO FINAL / Gabarito Final]      'GABARITO FINAL BANCO DO NORDESTE',
                                           'RESPOSTAS AOS RECURSOS - ...' (descartado)
    [PADRÃO DE RESPOSTA (- DISCURSIVA)]    'PROVA 1 - Padrão de resposta - ARQUITETO'
                                           (vira papel=prova — comportamento
                                           conhecido, LexLearn reclassifica)

CLASSIFICAÇÃO: o termo INICIAL do link manda ("PROVA ..." -> prova,
"GABARITO ..." -> gabarito); o título do bloco desempata e qualifica
("GABARITO FINAL" -> definitivo; "GABARITOS" solto -> preliminar, pois sai
logo após a prova, antes dos recursos). Descarte FORTE (resultado, edital,
convocação, lista(gem) de títulos, cartão-resposta etc.) vence sempre —
ver heuristics.eh_relevante com RE_DESCARTAR próprio (regime forte, ADR-0004).

CICLO PRELIMINAR→DEFINITIVO: preliminar e final coexistem na página; ambos
saem com vigente=true — marcar a substituição é trabalho do StateStore.

USO:
    scrapy crawl cesgranrio -a evento_id=10          # BNB 01/2024
    scrapy crawl cesgranrio -a evento_id=14 -a concurso="bndes_2024"
O evento_id sai de /api/PortalEventos (lista os eventos ativos no portal).
"""
from __future__ import annotations

import json
import re

import scrapy

from .base import LexCorpusSpider
from ..util import slugify


EVENTO_URL = "https://concursos.cesgranrio.org.br/api/PortalEventos/{id}"
CONTEUDOS_URL = (
    "https://concursos.cesgranrio.org.br/api/PortalEventoConteudos/publico/{id}"
)

_RE_GAB_FINAL = re.compile(r"gabarito\s+(oficial\s+)?(final|definitiv)", re.I)
_RE_LINK_GAB = re.compile(r"^gabaritos?\b", re.I)
_RE_LINK_PROVA = re.compile(r"^prova\b|^padr[ãa]o\s+de\s+resposta", re.I)
_RE_BLOCO_GAB = re.compile(r"gabarito", re.I)
_RE_BLOCO_PROVA = re.compile(r"prova|padr[ãa]o\s+de\s+resposta", re.I)

_RE_TIPO_PROVA = re.compile(r"\bprova\s+(\d+|[a-z])\b", re.I)
_RE_TIPO_CADERNO = re.compile(r"\bgabarito\s+(\d+)\b", re.I)
_RE_LINK_GENERICO = re.compile(r"^(acesse|acessar|clique|veja|confira)\b", re.I)

# tokens estruturais que NÃO fazem parte do nome do cargo
_RE_RUIDO_CARGO = re.compile(
    r"\d{2}/\d{2}/\d{4}|\d+\s*a\s*\d+"           # datas e faixas "1 a 6"
    r"|\bprova\s+(\d+|[a-z])\b|\bprovas?\b"      # "PROVA 1", "PROVA A", "PROVAS"
    r"|\bgabarito\s+\d+\b|\bgabaritos?\b"        # "GABARITO 1", "GABARITOS"
    r"|\bfinal\b|\boficial\b|\bdefinitivo\b"
    r"|padr[ãa]o\s+de\s+resposta",
    re.I,
)
_ROMANOS = {"i", "ii", "iii", "iv", "v", "vi"}


def _limpar_cargo(texto: str) -> str | None:
    """Extrai o rótulo de cargo do texto do link (None se genérico/vazio).

    Remove tokens estruturais (prova/gabarito/datas) e letras soltas de
    caderno ("PROVA A"), mas preserva numeral romano FINAL, que é parte do
    cargo ("TÉCNICO BANCÁRIO I" vs "TÉCNICO BANCÁRIO III").
    """
    if _RE_LINK_GENERICO.match(texto.strip()):
        return None
    t = _RE_RUIDO_CARGO.sub(" ", texto)
    t = re.sub(r"[-–_/]+", " ", t)
    palavras = t.split()
    filtradas = [
        p for i, p in enumerate(palavras)
        if not (len(p) == 1 and p.isalpha()
                and not (p.lower() in _ROMANOS and i == len(palavras) - 1))
    ]
    t = " ".join(filtradas)
    return t or None


class CesgranrioSpider(LexCorpusSpider):
    name = "cesgranrio"
    allowed_domains = ["concursos.cesgranrio.org.br"]

    # Descarte FORTE (regime do módulo: casa -> descarta sempre). Cobre os
    # blocos administrativos do portal: resultados, editais, convocações,
    # listagens de títulos ("LISTAGEM GERAL - PROVA DE TÍTULOS" tem "prova"
    # no título e NÃO é prova), cartão-resposta, local de prova...
    RE_DESCARTAR = re.compile(
        r"resultado|edital|convoca|cronograma|recurso|homologa|inscri"
        r"|cart[aã]o|heteroidentifica|atendimento|prazo|\blista(gem)?\b"
        r"|composi[çc][aã]o|curr[íi]culo|local\s+de\s+provas|endere[çc]o"
        r"|portaria|manual|retifica",
        re.I,
    )

    custom_settings = {
        "DOWNLOAD_DELAY": 1.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, evento_id=None, banca="CESGRANRIO", concurso=None,
                 concurso_rotulo=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not evento_id:
            raise ValueError(
                'passe -a evento_id=N (a lista de eventos ativos está em '
                'https://concursos.cesgranrio.org.br/api/PortalEventos)'
            )
        self.evento_id = evento_id
        self.banca_rotulo = banca
        self.concurso_slug = concurso
        self.concurso_rotulo_arg = concurso_rotulo
        self.concurso = None
        self.concurso_rotulo = None

    async def start(self):
        yield scrapy.Request(
            EVENTO_URL.format(id=self.evento_id),
            callback=self.parse_evento,
            headers={"Accept": "application/json"},
        )

    def parse_evento(self, response):
        dados = self._json(response)
        if dados is None:
            return
        ev = dados.get("data") or {}
        # slug: "BNB0124" -> "bnb0124"; override via -a concurso=
        self.concurso = slugify(self.concurso_slug or ev.get("nome") or f"evento_{self.evento_id}")
        self.concurso_rotulo = (
            self.concurso_rotulo_arg
            or ev.get("nomeFantasia")
            or ev.get("nome")
            or self.concurso
        )
        yield scrapy.Request(
            CONTEUDOS_URL.format(id=self.evento_id),
            callback=self.parse_conteudos,
            headers={"Accept": "application/json"},
        )

    def parse_conteudos(self, response):
        dados = self._json(response)
        if dados is None:
            return
        blocos = dados.get("data") or []
        concurso = self.concurso or slugify(f"evento_{self.evento_id}")

        vistos = set()
        n = 0
        for bloco in blocos:
            titulo = (bloco.get("titulo") or "").strip()
            html = bloco.get("texto") or ""
            for a in scrapy.Selector(text=html).css("a[href]"):
                href = a.attrib.get("href", "")
                if ".pdf" not in href.lower():
                    continue  # ex.: link do portal/login no meio do bloco
                pdf_url = href
                if pdf_url in vistos:
                    continue
                vistos.add(pdf_url)

                texto_link = " ".join(" ".join(a.css("::text").getall()).split())
                alvo = f"{titulo} {texto_link}"
                if not self.eh_relevante(alvo, pdf_url):
                    self.logger.info("descartado: [%s] %s", titulo[:40], texto_link[:60])
                    continue

                papel = self._papel(titulo, texto_link)
                if papel is None:
                    self.logger.info("não classificável: [%s] %s", titulo[:40], texto_link[:60])
                    continue

                tipo_prova = self._tipo_prova(texto_link) if papel == "prova" else None
                cargo_rotulo = _limpar_cargo(texto_link)
                if cargo_rotulo:
                    cargos_rotulo = {slugify(cargo_rotulo): cargo_rotulo}
                    multi = False
                else:
                    cargos_rotulo = {"*": "*"}
                    multi = True

                nome = self._nome_final(concurso, papel, titulo, texto_link, n)
                item = self.make_item(
                    pdf_url=pdf_url,
                    nome=nome,
                    papel=papel,
                    banca_rotulo=self.banca_rotulo,
                    concurso_rotulo=self.concurso_rotulo or concurso,
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
                "nenhuma prova/gabarito no evento %s — provas digitais e "
                "concursos em andamento não publicam blocos de PDF (normal).",
                self.evento_id,
            )

    # --- classificação específica CESGRANRIO (bloco + link) -------------------
    @staticmethod
    def _papel(titulo: str, texto_link: str) -> str | None:
        """Papel pelo termo inicial do link, com o bloco como contexto."""
        if _RE_GAB_FINAL.search(f"{titulo} {texto_link}"):
            return "gabarito_definitivo"
        if _RE_LINK_GAB.match(texto_link.strip()):
            return "gabarito_preliminar"
        if _RE_LINK_PROVA.match(texto_link.strip()):
            return "prova"
        if _RE_BLOCO_GAB.search(titulo):
            return "gabarito_preliminar"
        if _RE_BLOCO_PROVA.search(titulo):
            return "prova"
        return None

    @staticmethod
    def _tipo_prova(texto_link: str) -> str | None:
        """Identificador do caderno: 'PROVA A - GABARITO 1' -> 'a_gabarito_1'."""
        partes = []
        m = _RE_TIPO_PROVA.search(texto_link)
        if m:
            partes.append(m.group(1).lower())
        m = _RE_TIPO_CADERNO.search(texto_link)
        if m:
            partes.append(f"gabarito_{m.group(1)}")
        return "_".join(partes) or None

    @staticmethod
    def _nome_final(concurso, papel, titulo, texto_link, seq):
        """Nomes opacos (GUID) viram nomes semânticos a partir do rótulo."""
        if texto_link and not _RE_LINK_GENERICO.match(texto_link.strip()):
            stem = slugify(texto_link)[:90].strip("_")
            return f"{concurso}_{stem}.pdf"
        # link genérico ("Acesse aqui"): usa papel + bloco + sequência
        stem = slugify(titulo)[:60].strip("_") or papel
        return f"{concurso}_{papel}_{stem}_{seq}.pdf"

    def _json(self, response):
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error("resposta da API não é JSON válido: %s", response.url)
            return None
