# Arquivo:  lexcorpus/varredura.py
# Função:   varredura do acervo por carreira (ADR-0009): inventário por banca
#           (só leitura, sem PDF), sugestão de carreira por regras versionadas,
#           revisão do operador em lote e watchlist da varredura, com teto.
"""Varredura por carreira — a carreira é o eixo da BUSCA; a coleta continua
nos spiders de banca.

    # 1. inventário (lê a fonte oficial; não baixa PDF)
    scrapy crawl fgv -a descoberta=1                  # gera a listagem da FGV
    python -m lexcorpus.varredura inventariar --banca fgv
    python -m lexcorpus.varredura inventariar --banca cesgranrio

    # 2. revisão do operador (a regra só SUGERE)
    python -m lexcorpus.varredura revisar --carreira fiscal
    python -m lexcorpus.varredura revisar --carreira fiscal --confirmar fgv:sefazes22,fgv:sefazmt23
    python -m lexcorpus.varredura revisar --carreira fiscal --descartar fgv:x21 --motivo "não é tributário"

    # 3. a liberação semanal (varredura.novos_por_semana)
    python -m lexcorpus.varredura agenda

O confirmado vai para o catálogo (com a carreira) e para o watchlist da
varredura, até `varredura.max_por_carreira_e_banca` por banca. O scheduler
lê esse watchlist junto com o manual e o automático; o manual é soberano.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .alvos import CEBRASPE_API, CESGRANRIO_EVENTOS, INVENTARIO_FGV, HttpGet, http_get_padrao
from .catalogo import CATALOGO_PATH, Catalogo
from .consumidor import POLITICA_PADRAO, carregar_politica
from .contratos_lexpubli import carreiras
from .util import slugify

log = logging.getLogger("lexcorpus.varredura")

RAIZ = Path(__file__).resolve().parent.parent
REGRAS_PADRAO = RAIZ / "carreiras_regras.yaml"
INVENTARIO_DIR = Path("state/inventario")
DECISOES_PADRAO = Path("state/varredura.yaml")
WATCHLIST_VARREDURA_PADRAO = Path("state/watchlist.varredura.yaml")

# Cebraspe: a API não lista eventos. O índice é a listagem do PCI por banca
# (só leitura de /provas/, permitido no robots.txt; nunca PDF), e cada
# concurso só vira alvo depois de CONFIRMADO na API oficial por slug.
PCI_LISTAGEM = "https://www.pciconcursos.com.br/provas/{banca}/{pagina}"
PCI_PAUSA_S = 1.5
_RE_PCI_LINHA = re.compile(r'<tr class="lk_link[^"]*" data-url="([^"]+)">(.*?)</tr>', re.S)
_RE_PCI_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_RE_TAG = re.compile(r"<[^>]+>")

_RE_PREFIXO_FGV = re.compile(
    r"^concurso\s+p[uú]blico\s+(?:para\s+)?(?:(?:a|o|as|os|da|do|de)\s+)?", re.I)
_RE_ANO_SLUG = re.compile(r"(\d{4}|\d{2})$")


# ---------------------------------------------------------------------------
# sugestão de carreira
# ---------------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    """Sem acento, sem caixa, palavras separadas por um espaço e cercadas
    de espaço: casar ' termo ' casa a palavra inteira."""
    return f" {slugify(texto or '').replace('_', ' ')} "


def carregar_regras(path: str | Path = REGRAS_PADRAO) -> dict[str, dict]:
    dados = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    regras = dados.get("carreiras") or {}
    fora = set(regras) - set(carreiras())
    if fora:
        raise ValueError(f"carreiras fora da lista do contrato em {path}: {sorted(fora)}")
    return {c: {"termos": [_normalizar(t) for t in r.get("termos", [])],
                "exceto": [_normalizar(t) for t in r.get("exceto", [])]}
            for c, r in regras.items()}


def sugerir_por_cargos(orgao: str, cargos: list[str], regras: dict[str, dict]) -> str | None:
    """Para um concurso com vários cargos: vale o ÓRGÃO; se ele não diz a
    carreira, vale a MAIORIA dos cargos. Um "Auditor Fiscal" entre dezenas
    de cargos de prefeitura não faz do concurso um concurso fiscal."""
    pelo_orgao = sugerir_carreira(orgao, regras)
    if pelo_orgao:
        return pelo_orgao
    votos: dict[str, int] = {}
    for c in cargos:
        s = sugerir_carreira(c, regras)
        if s:
            votos[s] = votos.get(s, 0) + 1
    if len(votos) != 1:
        return None
    (carreira, n), = votos.items()
    return carreira if n * 2 > len(cargos) else None


def sugerir_carreira(texto: str, regras: dict[str, dict]) -> str | None:
    """A carreira cujos termos aparecem no texto (órgão + cargos). Nenhuma ou
    mais de uma: None — o operador decide sem sugestão."""
    t = _normalizar(texto)
    achadas = [c for c, r in regras.items()
               if any(termo in t for termo in r["termos"])
               and not any(ex in t for ex in r["exceto"])]
    return achadas[0] if len(achadas) == 1 else None


# ---------------------------------------------------------------------------
# inventário por banca
# ---------------------------------------------------------------------------

def _ano_do_slug(slug: str) -> int | None:
    m = _RE_ANO_SLUG.search(slug)
    if not m:
        return None
    n = int(m.group(1))
    ano = n if n >= 1000 else 2000 + n
    return ano if 2000 <= ano <= 2100 else None


def _entrada(banca, slug, concurso, rotulo, organ, ano, texto, url, spider, params,
             regras, lido_em) -> dict:
    return {"chave": f"{banca}:{slug}", "banca": banca, "slug": slug, "concurso": concurso,
            "rotulo": rotulo, "organ": organ, "ano": ano, "url": url,
            "spider": spider, "params": params,
            "carreira_sugerida": sugerir_carreira(texto, regras), "lido_em": lido_em}


def inventario_fgv(linhas: list[dict], regras: dict, lido_em: str) -> list[dict]:
    """A listagem oficial da FGV (modo descoberta do spider): slug + rótulo.
    Sem cargos: a sugestão usa só o órgão."""
    saida = []
    for d in linhas:
        rotulo = d.get("rotulo") or d["slug"]
        organ = _RE_PREFIXO_FGV.sub("", rotulo).strip() or rotulo
        saida.append(_entrada("fgv", d["slug"], slugify(d["slug"]), rotulo, organ,
                              _ano_do_slug(d["slug"]), rotulo, d.get("url"), "fgv",
                              {"slug": d["slug"]}, regras, lido_em))
    return saida


def inventario_cesgranrio(corpo: dict, regras: dict, lido_em: str) -> list[dict]:
    """O PortalEventos da Cesgranrio (eventos do portal; 15 em 13/10, de
    2023 em diante)."""
    saida = []
    for ev in (corpo.get("data") or {}).get("items") or []:
        cliente = ev.get("cliente") or {}
        organ = cliente.get("razaoSocial") or cliente.get("nome") or ev.get("nome")
        texto = " ".join(str(x) for x in (ev.get("nome"), ev.get("nomeFantasia"), organ,
                                          ev.get("descricao")) if x)
        inicio = ev.get("dataInicio") or ""
        saida.append(_entrada("cesgranrio", str(ev["id"]), slugify(ev.get("nome") or ""),
                              ev.get("nomeFantasia") or ev.get("nome"), organ,
                              int(inicio[:4]) if inicio[:4].isdigit() else None, texto,
                              ev.get("codigo"), "cesgranrio", {"evento_id": ev["id"]},
                              regras, lido_em))
    return saida


# --- FGV: a página oficial completa o que a listagem não diz -----------------

FGV_CONCURSO = "https://conhecimento.fgv.br/concursos/{slug}"
_RE_PDF_LINK = re.compile(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.I | re.S)
_RE_ANO_TEXTO = re.compile(r"\b(20[0-3]\d)\b")
ANO_MIN_MENCOES = 3


def ano_mais_citado(texto: str) -> int | None:
    """O ano que domina o texto: no mínimo ANO_MIN_MENCOES menções e mais que
    o segundo colocado. Empate ou poucas menções: None (não chutar)."""
    contagem: dict[int, int] = {}
    for a in _RE_ANO_TEXTO.findall(texto):
        contagem[int(a)] = contagem.get(int(a), 0) + 1
    ordem = sorted(contagem.items(), key=lambda kv: -kv[1])
    if not ordem or ordem[0][1] < ANO_MIN_MENCOES:
        return None
    if len(ordem) > 1 and ordem[1][1] == ordem[0][1]:
        return None
    return ordem[0][0]


def analisar_pagina_fgv(html: str) -> dict:
    """O que a página oficial diz: os PDFs (url, texto), quantos são prova
    ou gabarito, o título e o ano dominante."""
    from .heuristics import classificar_papel

    from urllib.parse import urljoin

    pdfs = [(urljoin(FGV_CONCURSO.format(slug=""), u), _RE_TAG.sub(" ", t).strip())
            for u, t in _RE_PDF_LINK.findall(html)]
    pdfs = [(u, t) for u, t in pdfs if "/concursos/" in u.lower()]     # rodapé fora
    titulo = re.search(r"<title>(.*?)</title>", html, re.S)
    texto = _RE_TAG.sub(" ", html)
    nomes = " ".join(u.rsplit("/", 1)[-1] + " " + t for u, t in pdfs)
    return {
        "titulo": (titulo.group(1).split("|")[0].strip() if titulo else ""),
        "pdfs": pdfs,
        "n_provas": sum(1 for u, t in pdfs if classificar_papel(t, u)),
        # dois sinais: a página (datas de publicação: em concurso que atravessa
        # anos, puxa para o mais recente) e os nomes dos PDFs
        "ano": ano_mais_citado(texto),
        "ano_pdfs": ano_mais_citado(nomes),
    }


def enriquecer_fgv(entradas: list[dict], regras: dict, *, http_html, pausa: float = PCI_PAUSA_S,
                   ler_pdf=None) -> list[dict]:
    """Para os sugeridos: abre a página oficial do concurso.

    - ano: quando a página e os nomes dos PDFs concordam, esse. Quando
      divergem (concurso que atravessa anos: a página puxa para a publicação
      mais recente) ou faltam, ÚLTIMA instância: o "Edital nº X/AAAA" da 1ª
      página do edital de abertura. Sem PDF legível, o da página, a conferir.
    - nome: slug sem ano ganha o ano (tjba -> tjba_2015), como as outras
      pastas do acervo (cnu_2025, sefaz_ce_21); o spider recebe -a concurso=.
    - alerta: termo de exceção da carreira nos PDFs (ex.: "juiz" num TJ cujo
      rótulo não diz): o operador decide.
    - sem_provas: nenhum PDF de prova ou gabarito na página.
    """
    import time

    for e in entradas:
        if not e["carreira_sugerida"]:
            continue
        html = http_html(FGV_CONCURSO.format(slug=e["slug"]))
        if pausa:
            time.sleep(pausa)
        if not html:
            e["alerta"] = "página oficial não respondeu"
            continue
        info = analisar_pagina_fgv(html)
        e["n_provas"] = info["n_provas"]
        e["sem_provas"] = info["n_provas"] == 0
        nomes = " ".join(u.rsplit("/", 1)[-1] + " " + t for u, t in info["pdfs"])
        if sugerir_carreira(f"{e['rotulo']} {info['titulo']} {nomes}", regras) != e["carreira_sugerida"]:
            exc = [t.strip() for t in regras[e["carreira_sugerida"]]["exceto"] if t in _normalizar(nomes)]
            e["alerta"] = f"nos PDFs: {', '.join(exc) or 'outra carreira'}"
        if not e["ano"]:
            ano_p, ano_n = info["ano"], info["ano_pdfs"]
            ano, origem = ano_p, "pagina"
            if ano_p is None or (ano_n is not None and ano_n != ano_p):
                lido = None
                if info["pdfs"]:
                    url = _pdf_para_ano(info["pdfs"])
                    texto_link = next(t for u, t in info["pdfs"] if u == url)
                    # o nome do edital de abertura costuma trazer o ano: sem download
                    lido = ano_do_edital(f"{url.rsplit('/', 1)[-1]} {texto_link}")
                    if lido is None and ler_pdf:
                        lido = ler_pdf(url)
                if lido:
                    ano, origem = lido, "edital"
                else:
                    ano, origem = ano_p or ano_n, "pagina_conferir"
            if ano:
                e.update(ano=ano, ano_origem=origem,
                         concurso=f"{slugify(e['slug'])}_{ano}",
                         params={**e["params"], "concurso": f"{slugify(e['slug'])}_{ano}"})
    return entradas


_RE_ANO_EDITAL = re.compile(r"edital[^|]{0,80}?\b(20[0-3]\d)\b", re.I)


def ano_do_edital(texto: str) -> int | None:
    """O ano que vem logo depois de "EDITAL": "EDITAL DE ABERTURA Nº 1, DE 22
    DE AGOSTO DE 2025", "Edital nº 1/2015", ou o nome do arquivo
    "edital-de-abertura-no-1-de-22-de-agosto-de-2025". Não confunde com a
    data de publicação do DOU, que vem antes."""
    m = _RE_ANO_EDITAL.search(re.sub(r"[\s_-]+", " ", texto))
    return int(m.group(1)) if m else None


def _pdf_para_ano(pdfs: list[tuple[str, str]]) -> str:
    """O PDF que melhor data o concurso: o edital de abertura; senão uma
    prova ou gabarito; senão o primeiro."""
    for padrao in (r"edital.*(abertura|n[ºo°]?\s*0*1\b)|abertura", r"prova|caderno|gabarito"):
        for u, t in pdfs:
            if re.search(padrao, f"{t} {u}", re.I):
                return u
    return pdfs[0][0]


def ano_do_pdf(url: str, *, max_bytes: int = 15_000_000) -> int | None:
    """ÚLTIMA instância: baixa UM PDF oficial (em memória; nada é gravado) e
    lê o ano da 1ª página. Mesmo leitor do LexLearn (PyMuPDF, page.get_text()),
    reproduzido aqui: os projetos não importam o código um do outro."""
    try:
        import pymupdf  # PyMuPDF (o LexLearn usa o nome antigo, fitz)
        import requests
    except ImportError:
        log.warning("PyMuPDF ausente: sem a última instância (ano pelo PDF)")
        return None
    resp = requests.get(url, timeout=60, stream=True, headers={
        "User-Agent": "LexCorpus/1.0 (+coletor de provas; fonte oficial)"})
    if resp.status_code != 200:
        return None
    dados = resp.raw.read(max_bytes + 1, decode_content=True)
    if len(dados) > max_bytes:
        return None
    try:
        with pymupdf.open(stream=dados, filetype="pdf") as doc:
            texto = doc[0].get_text() if len(doc) else ""
    except Exception as exc:                     # PDF corrompido ou cifrado
        log.warning("PDF ilegível (%s): %s", url, exc)
        return None
    return ano_do_edital(texto) or ano_mais_citado(texto)


def ler_pci(html: str) -> list[dict]:
    """Linhas da tabela de provas do PCI: cargo, ano, órgão (rótulo e slug)."""
    linhas = []
    for url, linha in _RE_PCI_LINHA.findall(html):
        tds = [_RE_TAG.sub("", td).strip() for td in _RE_PCI_TD.findall(linha)]
        if len(tds) < 3 or not tds[1].isdigit():
            continue
        m = re.search(r'class="[a-z]c"><a href="https://www\.pciconcursos\.com\.br/provas/([^"]+)"', linha)
        linhas.append({"cargo": tds[0], "ano": int(tds[1]), "orgao": tds[2],
                       "orgao_slug": m.group(1) if m else slugify(tds[2]).replace("_", "-"),
                       "url": url})
    return linhas


def candidatos_slug(orgao: str, ano: int) -> list[str]:
    """Slugs prováveis na Cebraspe ({sigla}_{aa}). O ano do PCI é o da PROVA;
    o slug costuma levar o do EDITAL (Sefaz-AL: prova 2020, sefaz_al_19), por
    isso o ano anterior também. Sufixos (sefaz_al_19_auditor) não se adivinham:
    ficam para o operador (--slug)."""
    base = re.sub(r"\bpref(?:\.|eitura)?\s*", "pref ", orgao, flags=re.I)
    base = re.sub(r"(\d+)\s*[ªºo°]", r"\1", base)              # TRF 6ª -> TRF 6
    base = slugify(re.sub(r"\b(da|de|do|das|dos)\b|regi[ãa]o", " ", base, flags=re.I))
    variantes = [base, base.replace("_", ""), re.sub(r"_(\d+)$", r"\1", base)]
    return list(dict.fromkeys(f"{v}_{a % 100:02d}" for v in variantes if v
                              for a in (ano, ano - 1)))


def confirmar_slug_cebraspe(slug: str, http_get: HttpGet) -> str | None:
    """O slug canônico (minúsculo) se a API oficial conhece o evento."""
    status, corpo = http_get(CEBRASPE_API.format(slug=slug))
    if status == 200 and isinstance(corpo, dict) and corpo.get("eventoURL"):
        return str(corpo["eventoURL"]).lower()
    return None


def inventario_cebraspe(linhas: list[dict], regras: dict, lido_em: str, *,
                        http_get: HttpGet = http_get_padrao, pausa: float = 0.4) -> list[dict]:
    """Agrupa as provas do PCI por (órgão, ano). Só os concursos com
    carreira sugerida são sondados na API (o resto não vai ser revisado)."""
    import time

    grupos: dict[tuple, dict] = {}
    for l in linhas:
        g = grupos.setdefault((l["orgao_slug"], l["ano"]),
                              {"orgao": l["orgao"], "ano": l["ano"], "cargos": [], "url": l["url"]})
        g["cargos"].append(l["cargo"])
    saida = []
    for (orgao_slug, ano), g in sorted(grupos.items()):
        carreira = sugerir_por_cargos(g["orgao"], g["cargos"], regras)
        slug = None
        if carreira:
            for cand in candidatos_slug(g["orgao"], ano):
                slug = confirmar_slug_cebraspe(cand, http_get)
                if pausa:
                    time.sleep(pausa)
                if slug:
                    break
        e = _entrada("cebraspe", f"pci/{orgao_slug}-{ano}", slug, g["orgao"], g["orgao"],
                     _ano_do_slug(slug) if slug and _ano_do_slug(slug) else ano,
                     "", g["url"], "cebraspe", {"slug": slug} if slug else None, regras, lido_em)
        e.update(carreira_sugerida=carreira, cargos=sorted(set(g["cargos"]))[:20],
                 ano_prova=ano, indice="pci", slug_confirmado=bool(slug))
        saida.append(e)
    return saida


def inventariar(banca: str, *, regras: dict, destino_dir: Path = INVENTARIO_DIR,
                http_get: HttpGet = http_get_padrao,
                fgv_jsonl: Path = INVENTARIO_FGV,
                http_html=None, pausa: float = PCI_PAUSA_S) -> Path:
    lido_em = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if banca == "fgv":
        if not Path(fgv_jsonl).exists():
            raise FileNotFoundError(f"{fgv_jsonl} ausente: rode antes "
                                    "`scrapy crawl fgv -a descoberta=1`")
        linhas = [json.loads(l) for l in Path(fgv_jsonl).read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        entradas = enriquecer_fgv(inventario_fgv(linhas, regras, lido_em), regras,
                                  http_html=http_html or _http_html_padrao, pausa=pausa,
                                  ler_pdf=ano_do_pdf if pausa else None)
    elif banca == "cesgranrio":
        status, corpo = http_get(CESGRANRIO_EVENTOS)
        if status != 200 or not isinstance(corpo, dict):
            raise RuntimeError(f"PortalEventos respondeu {status}")
        entradas = inventario_cesgranrio(corpo, regras, lido_em)
    elif banca == "cebraspe":
        import time

        http_html = http_html or _http_html_padrao
        linhas, vistas, pagina = [], set(), 1
        while True:
            novas = [l for l in ler_pci(http_html(PCI_LISTAGEM.format(banca="cebraspe", pagina=pagina)))
                     if l["url"] not in vistas]
            if not novas:                      # fim da listagem (ou página repetida)
                break
            vistas.update(l["url"] for l in novas)
            linhas += novas
            pagina += 1
            if pausa:
                time.sleep(pausa)
        entradas = inventario_cebraspe(linhas, regras, lido_em, http_get=http_get,
                                       pausa=0.4 if pausa else 0)
    else:
        raise ValueError(f"inventário de {banca!r} não implementado (ADR-0009 §2)")
    destino = Path(destino_dir) / f"{banca}.jsonl"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entradas),
                   encoding="utf-8")
    tmp.replace(destino)
    return destino


def _http_html_padrao(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "LexCorpus/1.0 (+índice de concursos; documentos só de fonte oficial)"})
    return resp.text if resp.status_code == 200 else ""


def carregar_inventario(destino_dir: Path = INVENTARIO_DIR) -> list[dict]:
    entradas = []
    for arq in sorted(Path(destino_dir).glob("*.jsonl")):
        entradas += [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]
    return entradas


# ---------------------------------------------------------------------------
# revisão do operador
# ---------------------------------------------------------------------------

class Revisao:
    """Decisões do operador (state/varredura.yaml) e o watchlist gerado."""

    def __init__(self, *, politica: dict, catalogo: Catalogo,
                 decisoes: Path = DECISOES_PADRAO,
                 watchlist: Path = WATCHLIST_VARREDURA_PADRAO,
                 hoje: date | None = None):
        self.pol = politica["varredura"]
        self.hoje = hoje or date.today()
        self.validadas = set(politica["bancas_validadas"])
        self.catalogo = catalogo
        self.path_decisoes, self.path_watchlist = Path(decisoes), Path(watchlist)
        dados = (yaml.safe_load(self.path_decisoes.read_text(encoding="utf-8"))
                 if self.path_decisoes.exists() else None) or {}
        self.confirmados: dict = dados.get("confirmados") or {}
        self.descartados: dict = dados.get("descartados") or {}

    def pendentes(self, inventario: list[dict], carreira: str) -> list[dict]:
        """Sugeridos para a carreira, de banca validada, ainda sem decisão;
        os mais recentes primeiro."""
        decididos = set(self.confirmados) | set(self.descartados)
        achados = [e for e in inventario
                   if e["carreira_sugerida"] == carreira and e["banca"] in self.validadas
                   and e["chave"] not in decididos]
        return sorted(achados, key=lambda e: (-(e["ano"] or 0), e["chave"]))

    def vagas(self, carreira: str, banca: str) -> int:
        usados = sum(1 for c in self.confirmados.values()
                     if c["carreira"] == carreira and c["banca"] == banca)
        return self.pol["max_por_carreira_e_banca"] - usados

    def confirmar(self, entradas: list[dict], carreira: str) -> list[str]:
        """Confirma em lote. Recusa o lote inteiro se passar do teto ou se
        faltar o ano (o catálogo precisa dele): nada pela metade."""
        if carreira not in self.pol["carreiras"]:
            raise ValueError(f"carreira {carreira!r} fora de varredura.carreiras")
        sem_ano = [e["chave"] for e in entradas if not e["ano"]]
        if sem_ano:
            raise ValueError(f"sem ano no inventário: {sem_ano}")
        sem_provas = [e["chave"] for e in entradas if e.get("sem_provas")]
        if sem_provas:
            raise ValueError(f"sem prova nem gabarito na página oficial: {sem_provas}")
        sem_alvo = [e["chave"] for e in entradas if not e.get("params")]
        if sem_alvo:
            raise ValueError(f"sem slug confirmado na API: {sem_alvo} (use --slug chave=SLUG)")
        por_banca: dict[str, int] = {}
        for e in entradas:
            por_banca[e["banca"]] = por_banca.get(e["banca"], 0) + 1
        for banca, n in por_banca.items():
            if n > self.vagas(carreira, banca):
                raise ValueError(
                    f"{banca}: {n} pedidos, {self.vagas(carreira, banca)} vaga(s) no teto de "
                    f"{self.pol['max_por_carreira_e_banca']} (varredura.max_por_carreira_e_banca)")
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for e in entradas:
            self.catalogo.marcar_carreira(e["concurso"], carreira, organ=e["organ"],
                                          banca=e["banca"], edital_ano=e["ano"])
            self.confirmados[e["chave"]] = {
                "carreira": carreira, "banca": e["banca"], "concurso": e["concurso"],
                "spider": e["spider"], "params": e["params"], "ano": e["ano"],
                "organ": e["organ"], "confirmado_em": agora}
        self._salvar()
        return [e["chave"] for e in entradas]

    def escalonar(self) -> None:
        """Dá a cada confirmado sem data uma `a_partir_de`: no máximo
        `novos_por_semana` liberados por semana (segunda a domingo), nunca
        antes de hoje, na ordem de
        confirmação (e, no lote, os mais recentes primeiro). O LexLearn recebe
        os concursos aos poucos, não todos na primeira passada do scheduler."""
        por_semana = max(1, int(self.pol["novos_por_semana"]))
        segunda = self.hoje - timedelta(days=self.hoje.weekday())

        def semana_de(iso: str) -> date:
            d = date.fromisoformat(iso)
            return d - timedelta(days=d.weekday())

        ocupadas: dict[date, int] = {}
        for c in self.confirmados.values():
            if c.get("a_partir_de") and semana_de(c["a_partir_de"]) >= segunda:
                ocupadas[semana_de(c["a_partir_de"])] = ocupadas.get(semana_de(c["a_partir_de"]), 0) + 1
        sem_data = sorted((c for c in self.confirmados.values() if not c.get("a_partir_de")),
                          key=lambda c: (c["confirmado_em"], -(c["ano"] or 0)))
        semana = segunda
        for c in sem_data:
            while ocupadas.get(semana, 0) >= por_semana:
                semana += timedelta(weeks=1)
            c["a_partir_de"] = max(semana, self.hoje).isoformat()
            ocupadas[semana] = ocupadas.get(semana, 0) + 1

    def reescalonar(self) -> None:
        """Refaz a escala dos que ainda não foram liberados (a_partir_de
        depois de hoje), ex.: quando novos_por_semana muda. O que já foi
        liberado fica como está."""
        for c in self.confirmados.values():
            if c.get("a_partir_de") and c["a_partir_de"] > self.hoje.isoformat():
                del c["a_partir_de"]
        self.escalonar()

    def descartar(self, chaves: list[str], motivo: str) -> None:
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for chave in chaves:
            self.descartados[chave] = {"motivo": motivo, "descartado_em": agora}
        self._salvar()

    def _salvar(self) -> None:
        self.escalonar()
        self.path_decisoes.parent.mkdir(parents=True, exist_ok=True)
        _gravar(self.path_decisoes, "decisões do operador na varredura (ADR-0009)",
                {"version": 1, "confirmados": self.confirmados, "descartados": self.descartados})
        alvos = [{"banca": c["banca"], "spider": c["spider"], "params": c["params"],
                  "concurso": c["concurso"], "cron": self.pol["cron"], "ativo": True,
                  "origem": "varredura", "carreira": c["carreira"],
                  "a_partir_de": c["a_partir_de"]}
                 for c in self.confirmados.values()]
        _gravar(self.path_watchlist, "GERADO pela varredura (ADR-0009). Não edite: o "
                "watchlist.yaml manual é soberano", {"version": 1, "alvos": alvos})


def _gravar(path: Path, cabecalho: str, dados: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"# {cabecalho}\n" + yaml.dump(dados, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Varredura do acervo por carreira (ADR-0009).")
    sub = ap.add_subparsers(dest="comando", required=True)
    pi = sub.add_parser("inventariar", help="lista os concursos da fonte oficial (sem PDF)")
    pi.add_argument("--banca", required=True, choices=["fgv", "cesgranrio", "cebraspe"])
    pr = sub.add_parser("revisar", help="mostra as sugestões e registra a decisão do operador")
    pr.add_argument("--carreira", required=True)
    pr.add_argument("--confirmar", help="chaves separadas por vírgula (ex.: fgv:sefazes22)")
    pr.add_argument("--descartar", help="chaves separadas por vírgula")
    pr.add_argument("--motivo", default="")
    pr.add_argument("--ano", help="chave=AAAA,... para corrigir ou informar o ano "
                                  "(o nome da pasta ganha o ano: tjba -> tjba_2015)")
    pr.add_argument("--slug", help="Cebraspe: chave=SLUG,... quando a sonda não achou; "
                                   "o slug é conferido na API oficial antes de valer")
    pa = sub.add_parser("agenda", help="mostra (e completa) a liberação semanal dos confirmados")
    pa.add_argument("--reescalonar", action="store_true",
                    help="refaz a escala dos ainda não liberados (ex.: novos_por_semana mudou)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # tudo ao lado do watchlist da varredura (no Docker, /state), o mesmo que o scheduler lê
    watchlist = Path(os.environ.get("LEXCORPUS_WATCHLIST_VARREDURA", WATCHLIST_VARREDURA_PADRAO))
    inventario_dir, decisoes = watchlist.parent / "inventario", watchlist.parent / "varredura.yaml"

    if args.comando == "inventariar":
        try:
            destino = inventariar(args.banca, regras=carregar_regras(), destino_dir=inventario_dir)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 2
        entradas = [json.loads(l) for l in destino.read_text(encoding="utf-8").splitlines()]
        sugeridas: dict[str, int] = {}
        for e in entradas:
            if e["carreira_sugerida"]:
                sugeridas[e["carreira_sugerida"]] = sugeridas.get(e["carreira_sugerida"], 0) + 1
        print(f"{destino}: {len(entradas)} concursos; sugestões: {sugeridas or 'nenhuma'}")
        return 0

    rev = Revisao(politica=carregar_politica(os.environ.get("LEXCORPUS_POLITICA", POLITICA_PADRAO)),
                  catalogo=Catalogo.com_semente(os.environ.get("LEXCORPUS_CATALOGO", CATALOGO_PATH)),
                  decisoes=decisoes, watchlist=watchlist)
    if args.comando == "agenda":
        if args.reescalonar:
            rev.reescalonar()
        rev._salvar()                       # escalona quem ainda não tem data
        for chave, c in sorted(rev.confirmados.items(), key=lambda kv: (kv[1]["a_partir_de"], kv[0])):
            print(f"  {c['a_partir_de']}  {c['carreira']:<10} {chave}")
        return 0

    inventario = carregar_inventario(inventario_dir)
    por_chave = {e["chave"]: e for e in inventario}
    try:
        for par in (args.ano or "").split(","):
            if not par.strip():
                continue
            chave, _, ano = par.partition("=")
            e = por_chave.get(chave.strip())
            if e is None or not ano.strip().isdigit() or not 2000 <= int(ano) <= 2100:
                raise ValueError(f"--ano inválido: {par!r}")
            e.update(ano=int(ano), ano_origem="operador")
            if e["banca"] == "fgv" and not _ano_do_slug(e["slug"]):
                nome = f"{slugify(e['slug'])}_{int(ano)}"
                e.update(concurso=nome, params={**e["params"], "concurso": nome})
        for par in (args.slug or "").split(","):
            if not par.strip():
                continue
            chave, _, slug = par.partition("=")
            e = por_chave.get(chave.strip())
            if e is None or e["banca"] != "cebraspe":
                raise ValueError(f"--slug só para concursos Cebraspe do inventário: {chave!r}")
            real = confirmar_slug_cebraspe(slug.strip().lower(), http_get_padrao)
            if not real:
                raise ValueError(f"a API da Cebraspe não conhece o slug {slug!r}")
            e.update(concurso=real, params={"slug": real}, slug_confirmado=True,
                     ano=_ano_do_slug(real) or e["ano"])
        if args.confirmar:
            chaves = [c.strip() for c in args.confirmar.split(",") if c.strip()]
            faltam = [c for c in chaves if c not in por_chave]
            if faltam:
                raise ValueError(f"fora do inventário: {faltam}")
            print("confirmados:", ", ".join(rev.confirmar([por_chave[c] for c in chaves],
                                                         args.carreira)))
        if args.descartar:
            chaves = [c.strip() for c in args.descartar.split(",") if c.strip()]
            rev.descartar(chaves, args.motivo)
            print("descartados:", ", ".join(chaves))
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    pend = rev.pendentes(inventario, args.carreira)
    print(f"\n{args.carreira}: {len(pend)} pendente(s)")
    for banca in sorted({e["banca"] for e in pend}):
        print(f"  [{banca}] vagas no teto: {rev.vagas(args.carreira, banca)}")
    for e in pend:
        marcas = []
        if e.get("ano_origem") in ("pagina", "edital", "pagina_conferir"):
            marcas.append({"pagina": "ano pela página", "edital": "ano pelo edital",
                           "pagina_conferir": "ano pela página: CONFERIR"}[e["ano_origem"]])
        if e.get("sem_provas"):
            marcas.append("SEM PROVAS")
        if e.get("alerta"):
            marcas.append(f"ALERTA {e['alerta']}")
        if e["banca"] == "cebraspe" and not e.get("params"):
            marcas.append("SEM SLUG (--slug)")
        print(f"  {e['chave']:<28} {e['ano'] or '----'}  {e['organ'][:50]}"
              + (f"  [{'; '.join(marcas)}]" if marcas else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
