# Arquivo:  lexcorpus/orquestrador.py
# Função:   orquestração "descoberta -> coleta". Recebe o resultado do
#           buscador (candidata banca/ano/concurso), mapeia à banca um
#           spider existente OU gera um esqueleto parametrizável quando não
#           existe spider, e dispara a coleta imediata (CrawlerRunner) ou
#           apenas EMITE o comando scrapy equivalente para o operador.
#           (ADR-0006)
# CLI: python -m lexcorpus.orquestrador --organ Transpetro --banca Cesgranrio \\
#        --concurso transpetro_2023 --cargos "Analista de Sistemas"
#      python -m lexcorpus.orquestrador --from-json /tmp/transpetro_result.json \\
#        --rank 0
"""Orquestrador de coleta por descoberta.

Fecha o ciclo do ADR-0006:

    busca (buscador.py) -> candidata ranqueada -> este orquestrador ->
    spider existente OU esqueleto novo -> coleta (pipelines intactos:
    download, sidecar, evento) -> storage compartilhado do LexLearn

Regras:
  1. O orquestrador NUNCA altera pipelines (download/checksum/sidecar/
     evento continuam responsabilidade exclusiva deles — contrato v2.0).
  2. Spider existente? usa-o com os params certos (mapeamento em
     SPIDERS_POR_BANCA — parametrizável, pode vir de env/watchlist).
  3. Spider inexistente? gera um esqueleto parametrizável a partir de
     spiders/exemplo_banca.py + URL da fonte primária descoberta, e avisa
     o operador (a validação do esqueleto é manual — coerente com o
     watchlist, onde alvos novos nascem com ativo=false).
  4. Banca com bloqueio conhecido (WAF etc., campo "bloqueio" na tabela)?
     o orquestrador sinaliza a rota Playwright (spider gerado já carrega
     o custom_settings certo). Nenhuma banca ativa está bloqueada hoje —
     a Cesgranrio foi desbloqueada em 18/08 via API JSON pública.

Modos de disparo (parametrizáveis):
  - `comando` (default): imprime o `scrapy crawl ...` exato para o operador
    rodar (máximo controle, nada escondido)
  - `executar`: roda o spider via CrawlerRunner no processo (dev local)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .util import slugify

# ---------------------------------------------------------------------------
# Mapeamento banca -> spider (tabela única, editável; nada hardcoded no fluxo)
# ---------------------------------------------------------------------------
# Adicione novas bancas aqui. O valor é o nome do spider + o conjunto de
# parâmetros -a que ele aceita. Params com valor None = obrigatório na
# execução (o orquestrador pede ou deriva).
SPIDERS_POR_BANCA = {
    "cebraspe": {
        "spider": "cebraspe",
        "params": {"slug": None},            # ex.: transpetro_2023
        "fonte_primaria": "https://apis.cebraspe.org.br/cebraspe/eventos/{slug}",
        "bloqueio": None,
    },
    "cesgranrio": {
        "spider": "cesgranrio",              # API pública do portal (18/08)
        "params": {"evento_id": None},       # sai de /api/PortalEventos
        "fonte_primaria": "https://concursos.cesgranrio.org.br/api/PortalEventos/{evento_id}",
        "bloqueio": None,
    },
    "fgv": {
        "spider": "fgv",
        "params": {"url": None},
        "fonte_primaria": "https://conhecimento.fgv.br/concursos/{slug}",
        "bloqueio": None,
    },
    "fcc": {
        "spider": "fcc",
        "params": {"codigo": None},
        "fonte_primaria": "https://www.concursosfcc.com.br/concursos.html",
        "bloqueio": None,
    },
    "fundatec": {
        "spider": None,                      # sem spider ainda — usar agregador
        "params": {"url": None},
        "fonte_primaria": "https://www.fundatec.org.br/concursos/",
        "bloqueio": None,
    },
    "ibfc": {
        "spider": None,
        "params": {"url": None},
        "fonte_primaria": "https://www.ibfc.org.br/",
        "bloqueio": None,
    },
    "quadrix": {
        "spider": None,
        "params": {"url": None},
        "fonte_primaria": "https://quadrix.org.br/",
        "bloqueio": None,
    },
    "vunesp": {
        "spider": None,
        "params": {"url": None},
        "fonte_primaria": "https://www.vunesp.com.br/",
        "bloqueio": None,
    },
}

# Fallback agregador (ADR-0001: banco de provas como ÍNDICE, NÃO fonte de PDF).
# Só entra em ação quando a fonte primária é inacessível e o operador
# explicitamente pede --fallback agregador.
FALLBACK_AGREGADOR = {
    "pci": {
        "base": "https://www.pciconcursos.com.br/provas/download/{slug}",
        "nota": "PCI é ÍNDICE (ADR-0001): Turnstile protege os PDFs reais. "
                "Use só para confirmar ano/banca/cargos — o spider da banca "
                "vai ao site oficial pelos PDFs.",
    },
}

# Roteiro de discovery da página do concurso na fonte primária (banca ->
# padrão de URL do catálogo da banca). O orquestrador tenta cada padrão
# (GET leve, sem download de PDF) até achar a página que cita o órgão.
CATALOGOS_BANCA = {
    "cebraspe": [
        "https://www.cebraspe.org.br/concursos/",  # SPA: a API resolve o slug
    ],
    "cesgranrio": [
        "https://www.cesgranrio.org.br/concurso/{organ}/",
        "https://www.cesgranrio.org.br/concursos/",
    ],
    "fgv": [
        "https://conhecimento.fgv.br/concursos",
    ],
    "fcc": [
        "https://www.concursosfcc.com.br/concursos.html",
    ],
    "fundatec": [
        "https://www.fundatec.org.br/concursos/",
    ],
    "ibfc": [
        "https://www.ibfc.org.br/",
    ],
    "quadrix": [
        "https://quadrix.org.br/",
    ],
    "vunesp": [
        "https://www.vunesp.com.br/",
    ],
}

TEMPLATE_ESQUELETO = '''"""Spider gerado pelo orquestrador para {banca_rotulo} ({organ}).

GENERO na raiz: copie spiders/exemplo_banca.py e AJUSTE os seletores contra
o HTML real da página do concurso. NÃO altere pipelines (contrato v2.0).

Página-alvo descoberta: {fonte_url}
Bloqueios conhecidos: {bloqueio}

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


class {classe}Spider(LexCorpusSpider):
    name = "{slug}"
    # Ajuste após inspecionar o HTML real (scrapy fetch {fonte_url} > page.html)
    PDF_SELECTOR = "a[href$='.pdf']"

    custom_settings = {{
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
{playwright_block}    }}

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
                papel=papel, banca_rotulo="{banca_rotulo}",
                concurso_rotulo=f"{{self.organ}} {{self.concurso_slug}}",
                cargos_rotulo={{self.cargo or "geral": self.cargo or "Geral"}},
            )

    def _nome(self, pdf_url, papel):
        import os
        base = os.path.basename(pdf_url)
        return base if base else "arquivo.pdf"
'''


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def mapear_spider(banca_slug: str) -> dict:
    """Retorna o roteiro do spider para a banca (ou None se desconhecida)."""
    cfg = SPIDERS_POR_BANCA.get(banca_slug)
    return dict(cfg, banca_slug=banca_slug) if cfg else None


def montar_comando(banca_slug: str, concurso: str, *, organ: str = "",
                   cargo: str = "", url: str = "", spider_nome: str | None = None,
                   extra_params: dict | None = None) -> str:
    """Monta o comando `scrapy crawl` completo e parametrizado.

    Honra a assinatura do spider: os parâmetros do spider existente são
    descobertos em SPIDERS_POR_BANCA["params"] ("slug": None = obrigatório;
    "url": None = obrigatório). URL só entra se o spider aceita `url`.
    Params fora de url/slug/banca/cargo (ex.: evento_id) vão direto quando
    têm valor; obrigatórios sem valor viram placeholder <PREENCHER>.
    Extra_params sobrepõe (override explícito do operador).
    """
    cfg = mapear_spider(banca_slug) or {"spider": spider_nome, "params": {}}
    spider = spider_nome or cfg.get("spider")
    if not spider:
        raise ValueError(f"banca {banca_slug!r} não tem spider e nenhum "
                         f"spider_nome foi passado")
    params = dict(cfg.get("params") or {})
    params.update(extra_params or {})
    pares = []
    # só passar `url` se o spider aceita esse parâmetro
    if url and "url" in params:
        pares.append(f'url="{url}"')
    elif url and "url" not in params:
        pares.append(None)  # URL disponível mas spider não aceita — ignorar
    if "slug" in params:
        pares.append(f'slug="{concurso}"')
    if "banca" in params and organ:
        pares.append(f'banca="{organ}"')
    if "cargo" in params and cargo:
        pares.append(f'cargo="{cargo}"')
    # demais params da assinatura do spider (ex.: evento_id da Cesgranrio):
    # valor preenchido vai direto; None = obrigatório, vira placeholder
    for nome, valor in params.items():
        if nome in ("url", "slug", "banca", "cargo"):
            continue
        pares.append(f'{nome}="{valor}"' if valor is not None
                     else f"{nome}=<PREENCHER>")
    pares = [x for x in pares if x]
    return f"scrapy crawl {spider} " + " ".join(pares)


def gerar_esqueleto(banca_slug: str, organ: str, concurso: str,
                    fonte_url: str, destino: str | Path | None = None,
                    forcar: bool = False) -> Path:
    """Gera o esqueleto parametrizável do spider (quando não existe)."""
    cfg = mapear_spider(banca_slug) or {}
    rota_playwright = ""
    if cfg.get("bloqueio"):
        rota_playwright = (
            '        # WAF exige browser real — descomente após instalar\n'
            '        # "DOWNLOADER_MIDDLEWARES": {\n'
            '        #     "scrapy_playwright.middleware.ScrapyPlaywrightDownloadHandler": 800,\n'
            '        # },\n'
            '        # "PLAYWRIGHT_BROWSER_TYPE": "chromium",\n'
        )
    classe = "".join(p.capitalize() for p in re.split(r"[_\W]+", banca_slug))
    conteudo = TEMPLATE_ESQUELETO.format(
        classe=classe, slug=banca_slug, organ=organ,
        banca_rotulo=cfg.get("rotulo") or banca_slug,
        fonte_url=fonte_url, bloqueio=cfg.get("bloqueio") or "nenhum",
        playwright_block=rota_playwright,
    )
    destino = Path(destino) if destino else (
        Path(__file__).resolve().parent / "spiders" / f"{banca_slug}.py")
    if destino.exists() and not forcar:
        print(f"AVISO: {destino} já existe — não sobrescrito. "
              "Reexecuta com --forcar (via CLI: ainda não suportado; "
              "use gerar_esqueleto(..., forcar=True) programaticamente) "
              "ou remova o arquivo manualmente.")
        return destino
    destino.write_text(conteudo, encoding="utf-8")
    return destino


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Orquestra coleta: descoberta (buscador) -> spider -> storage",
    )
    p.add_argument("--organ", help="órgão/empresa")
    p.add_argument("--banca", help="banca descoberta (slug ou rotulo)")
    p.add_argument("--concurso", required=True,
                   help="slug do concurso, ex.: transpetro_2023")
    p.add_argument("--cargos", help="cargos separados por vírgula")
    p.add_argument("--url", help="URL da página do concurso (override)")
    p.add_argument("--from-json", help="JSON de saída do buscador.py")
    p.add_argument("--rank", type=int, default=0,
                   help="índice da candidata no JSON (default 0 = melhor)")
    p.add_argument("--modo", choices=["comando", "executar"], default="comando",
                   help="comando = imprime o scrapy crawl; executar = roda")
    p.add_argument("--fallback", choices=list(FALLBACK_AGREGADOR),
                   help="banca sem spider: usar índice agregador (ADR-0001)")
    p.add_argument("--gerar-esqueleto", action="store_true",
                   help="sem spider: gera esqueleto parametrizável")
    p.add_argument("--destino-esqueleto",
                   help="path do esqueleto (default: lexcorpus/spiders/{banca}.py)")
    p.add_argument("--forcar", action="store_true",
                   help="sobrescrever o esqueleto se já existir")
    args = p.parse_args()

    banca_slug = slugify(args.banca) if args.banca else None

    if args.from_json:
        p_json = Path(args.from_json)
        if not p_json.exists():
            sys.exit(f"arquivo não encontrado: {args.from_json} "
                     "(salve com `python3 -m lexcorpus.buscador ... --saida <arq>`)")
        dados = json.loads(p_json.read_text(encoding="utf-8"))
        candidatas = dados.get("candidatas", [])
        if args.rank >= len(candidatas):
            sys.exit(f"rank {args.rank} fora do intervalo "
                     f"(candidatas: {len(candidatas)})")
        c = candidatas[args.rank]
        banca_slug = banca_slug or c["banca_slug"]
        organ = dados.get("organ", "")
    else:
        organ = args.organ or ""
        c = None

    # resolve URL: override > catálogo da banca > catálogo agregador
    url = args.url or ""
    if not url and c:
        url = c.get("url", "")
    if not url and banca_slug and organ:
        url = _descobrir_url_concurso(banca_slug, organ)
    if not url and args.fallback and organ:
        base = FALLBACK_AGREGADOR[args.fallback]["base"]
        url = base.format(slug=f"{slugify(organ)}-{banca_slug}-{c['ano'] if c else ''}".rstrip("-"))

    cfg = mapear_spider(banca_slug) if banca_slug else None
    bloqueado = bool(cfg and cfg.get("bloqueio"))

    print(f"alvo: {organ} / {args.concurso} (banca={banca_slug})")
    if cfg:
        print(f"spider: {cfg['spider'] or 'NÃO EXISTE'}")
        if cfg.get("bloqueio"):
            print(f"BLOQUEIO: {cfg['bloqueio']}")

    if args.gerar_esqueleto or (cfg and not cfg.get("spider")):
        if not url:
            sys.exit("sem URL da fonte primária — passe --url ou corrija o "
                     "catálogo da banca em CATALOGOS_BANCA")
        destino = gerar_esqueleto(
            banca_slug, organ, args.concurso, url,
            destino=args.destino_esqueleto,
            forcar=args.forcar,
        )
        print(f"esqueleto gerado: {destino}")
        print("PRÓXIMO PASSO: inspecione o HTML (scrapy fetch ... > page.html), "
              "ajuste PDF_SELECTOR e ative com ativo=true no watchlist.yaml")
        return

    if not url:
        sys.exit("sem URL da página do concurso — passe --url "
                 "(ou ajuste CATALOGOS_BANCA[{banca_slug}])")

    cargos = [x.strip() for x in args.cargos.split(",")] if args.cargos else []
    cmd = montar_comando(
        banca_slug, args.concurso, organ=organ,
        cargo=cargos[0] if cargos else "", url=url,
    )
    print(f"\nCOMANDO (pronto para colar):\n  {cmd}")
    if "<PREENCHER>" in cmd:
        print("ATENÇÃO: substitua os <PREENCHER> pelos valores reais "
              "(ex.: evento_id sai de /api/PortalEventos da Cesgranrio).")

    if args.modo == "executar":
        if bloqueado:
            sys.exit("não é possível executar: a banca está bloqueada "
                     "(ver BLOQUEIO acima). Gere o esqueleto e valide.")
        _executar(cmd, organ, args.concurso)


def _descobrir_url_concurso(banca_slug: str, organ: str) -> str:
    """GET leve nos catálogos conhecidos da banca até achar página que
    cita o órgão. Devolve a URL ou ''."""
    try:
        import requests
    except ImportError:
        return ""
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    })
    padroes = CATALOGOS_BANCA.get(banca_slug, [])
    for padrao in padroes:
        url = padrao.replace("{organ}", slugify(organ)).replace("{slug}", slugify(organ))
        try:
            resp = s.get(url, timeout=10)
            if resp.status_code == 200 and organ.lower() in resp.text.lower():
                return url
        except requests.RequestException:
            continue
    return ""


def _executar(cmd: str, organ: str, concurso: str):
    """Dispara o spider via CrawlerRunner (modo dev local)."""
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    nome_spider = cmd.split()[2]
    settings = get_project_settings()
    processo = CrawlerProcess(settings)
    processo.crawl(nome_spider)
    processo.start()


if __name__ == "__main__":
    main()
