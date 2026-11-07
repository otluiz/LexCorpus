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

HOST PRÓPRIO POR CLIENTE: alguns clientes têm portal num subdomínio, com a
MESMA API e eventos próprios (a Transpetro 2023 não está no portal geral):
    scrapy crawl cesgranrio -a host=transpetro.cesgranrio.org.br -a evento_id=12 \
        -a dividir_editais=1
Só subdomínios de cesgranrio.org.br são aceitos.

EVENTO COM VÁRIOS EDITAIS (-a dividir_editais=1): o PSP-RH-2023 da Transpetro
junta três concursos (TRANSPETRO/PSP/TERRA/NÍVELMÉDIO-2023.1, .../NÍVELSUPERIOR-
2023.2, .../MAR-2023.3), cada um com provas e gabaritos próprios. Com a opção,
o código do edital achado no título do bloco ou no link vira o concurso — o
gabarito de um edital cobre só os cargos DELE (["*"] dentro do concurso) e os
nomes de arquivo não colidem entre níveis.

LINK COM HOST ERRADO: a página oficial traz ".cesgranrio.org.brr/" em alguns
links (Transpetro 2023, ênfases 8 e 13 do nível médio); o mesmo caminho no
host certo devolve o PDF. O spider corrige e registra no log.
"""
from __future__ import annotations

import json
import re

import scrapy

from .base import LexCorpusSpider
from ..util import slugify


HOST_PADRAO = "concursos.cesgranrio.org.br"
EVENTO_URL = "https://{host}/api/PortalEventos/{id}"
CONTEUDOS_URL = "https://{host}/api/PortalEventoConteudos/publico/{id}"

_RE_HOST_CESGRANRIO = re.compile(r"^[a-z0-9-]+\.cesgranrio\.org\.br$")
_RE_HOST_BRR = re.compile(r"^(https://[a-z0-9-]+\.cesgranrio\.org\.br)r+/", re.I)
# código do edital: "TRANSPETRO/PSP/TERRA/NÍVELMÉDIO-2023.1", "TRANSPETRO/PSP/MAR-2023.3"
_RE_EDITAL = re.compile(r"[^\s-]+-\d{4}\.\d+\b")

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
    allowed_domains = ["cesgranrio.org.br"]  # host do cliente é subdomínio

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
                 concurso_rotulo=None, host=HOST_PADRAO, dividir_editais=None,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not evento_id:
            raise ValueError(
                'passe -a evento_id=N (a lista de eventos ativos está em '
                'https://concursos.cesgranrio.org.br/api/PortalEventos)'
            )
        host = (host or HOST_PADRAO).lower()
        if not _RE_HOST_CESGRANRIO.match(host):
            raise ValueError(f"host tem de ser subdomínio de cesgranrio.org.br: {host!r}")
        self.host = host
        self.dividir_editais = str(dividir_editais).lower() in ("1", "true", "sim", "yes")
        self.evento_id = evento_id
        self.banca_rotulo = banca
        self.concurso_slug = concurso
        self.concurso_rotulo_arg = concurso_rotulo
        self.concurso = None
        self.concurso_rotulo = None

    async def start(self):
        yield scrapy.Request(
            EVENTO_URL.format(host=self.host, id=self.evento_id),
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
            CONTEUDOS_URL.format(host=self.host, id=self.evento_id),
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
        nomes = set()
        n = 0
        for bloco in blocos:
            titulo = (bloco.get("titulo") or "").strip()
            html = bloco.get("texto") or ""
            for a in scrapy.Selector(text=html).css("a[href]"):
                href = a.attrib.get("href", "")
                if ".pdf" not in href.lower():
                    continue  # ex.: link do portal/login no meio do bloco
                pdf_url = _RE_HOST_BRR.sub(r"\1/", href)
                if pdf_url != href:
                    self.logger.warning("host errado no link oficial, corrigido: %s",
                                        href.split("/")[2])
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
                concurso_item, concurso_rotulo_item = concurso, self.concurso_rotulo or concurso
                texto_cargo = texto_link
                if self.dividir_editais:
                    m = _RE_EDITAL.search(texto_link) or _RE_EDITAL.search(titulo)
                    if m:
                        concurso_item, concurso_rotulo_item = slugify(m.group(0)), m.group(0)
                        texto_cargo = _RE_EDITAL.sub(" ", texto_link)
                cargo_rotulo = _limpar_cargo(texto_cargo)
                if cargo_rotulo:
                    cargos_rotulo = {slugify(cargo_rotulo): cargo_rotulo}
                    multi = False
                else:
                    cargos_rotulo = {"*": "*"}
                    multi = True

                nome = self._nome_final(concurso_item, papel, titulo, texto_cargo, nomes)
                nomes.add(nome)
                item = self.make_item(
                    pdf_url=pdf_url,
                    nome=nome,
                    papel=papel,
                    banca_rotulo=self.banca_rotulo,
                    concurso_rotulo=concurso_rotulo_item,
                    cargos_rotulo=cargos_rotulo,
                    concurso=concurso_item,
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
    def _nome_final(concurso, papel, titulo, texto_link, usados=frozenset()):
        """Nomes opacos (GUID) viram nomes semânticos a partir do rótulo.

        Sem rótulo útil no link ("Acesse aqui", ou só o código do edital):
        papel + bloco. Sufixo numérico SÓ em colisão — posição na página não
        entra no nome, senão o mesmo PDF mudaria de nome se a banca reordenasse.
        """
        stem = slugify(texto_link or "")[:90].strip("_")
        if stem and not _RE_LINK_GENERICO.match(texto_link.strip()):
            base = f"{concurso}_{stem}"
        else:
            base = f"{concurso}_{papel}_{slugify(titulo)[:60].strip('_') or papel}"
        nome, i = f"{base}.pdf", 2
        while nome in usados:
            nome, i = f"{base}_{i}.pdf", i + 1
        return nome

    def _json(self, response):
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error("resposta da API não é JSON válido: %s", response.url)
            return None
