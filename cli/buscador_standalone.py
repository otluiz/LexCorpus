#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LexCorpus — Buscador de bancas por palavra-chave (CLI PORTÁTIL)
================================================================

Versão standalone: NÃO depende do pacote lexcorpus (nem do Scrapy).
Requisitos mínimos: Python 3.10+ com `requests` e `beautifulsoup4`
(`sudo apt install python3-requests python3-bs4` no Debian).

Uso:

    python3 buscador_standalone.py "Transpetro"
    python3 buscador_standalone.py "PRF" --cargo agente
    python3 buscador_standalone.py "TRT 2" --backend duckduckgo --resumo

A saída em --resumo é legível no terminal; traga-a no prompt do Manus
junto com o nome dos cargos-alvo que o orquestrador/scrapy vai usar.

Tudo é parametrizável por flags/env — nada hardcoded:

    LEXCORPUS_BACKEND      motor padrão (default: bing)
    LEXCORPUS_JANELA_ANOS  quantos anos atrás olhar (default: 12)
    LEXCORPUS_TOP_K        nº de candidatas (default: 8)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date
from urllib.parse import parse_qs, quote_plus, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("dependências ausentes: instale com\n"
             "  sudo apt install python3-requests python3-bs4\n"
             "ou  pip3 install requests beautifulsoup4")

# ---------------------------------------------------------------------------
# Configuração externa (defaults parametrizáveis)
# ---------------------------------------------------------------------------
ANO_ATUAL = date.today().year
ANO_MIN = 2000
JANELA_ANOS = int(os.environ.get("LEXCORPUS_JANELA_ANOS", "12"))
DEFAULT_BACKEND = os.environ.get("LEXCORPUS_BACKEND", "bing")
DEFAULT_TOP_K = int(os.environ.get("LEXCORPUS_TOP_K", "8"))
SESSION_TIMEOUT = 12

BANCAS_CONHECIDAS = {
    "cebraspe": {
        "rotulo": "Cebraspe",
        "aliases": ["cebraspe", "cespe", "cespe/cebraspe", "cebraspe/cespe",
                    "centro brasileiro de pesquisa em avaliação"],
    },
    "cesgranrio": {
        "rotulo": "Cesgranrio",
        "aliases": ["cesgranrio", "fundação cesgranrio", "fundacao cesgranrio",
                    "f. cesgranrio"],
    },
    "fgv": {
        "rotulo": "FGV",
        "aliases": ["fgv", "fundação getulio vargas", "fundacao getulio vargas",
                    "fgv knowledge", "conhecimento.fgv"],
    },
    "fcc": {
        "rotulo": "FCC",
        "aliases": ["fcc", "fundação carlos chagas", "fundacao carlos chagas"],
    },
    "fundatec": {
        "rotulo": "Fundatec",
        "aliases": ["fundatec", "fundação fundatec"],
    },
    "ibfc": {
        "rotulo": "IBFC",
        "aliases": ["ibfc", "instituto brasileiro de formação e capacitação"],
    },
    "quadrix": {
        "rotulo": "Quadrix",
        "aliases": ["quadrix", "instituto quadrix"],
    },
    "vunesp": {
        "rotulo": "Vunesp",
        "aliases": ["vunesp", "vunesp",
                    "fundação para o vestibular da universidade estadual paulista"],
    },
    "nudec": {
        "rotulo": "Nucec",
        "aliases": ["nucec", "núcleo de computadores eletrônicos da ufrj"],
    },
    "consulpam": {
        "rotulo": "Consulpam",
        "aliases": ["consulpam"],
    },
}

AGREGADORES = {
    "pci": {
        "nome": "PCI Concursos",
        "url_catalogo": "https://www.pciconcursos.com.br/provas/{banca_rotulo}",
    },
}


# ---------------------------------------------------------------------------
# Backends de busca
# ---------------------------------------------------------------------------

def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept-Language": "pt-BR,pt;q=0.9",
    })
    return s


def busca_bing(sessao, query, num=10):
    """Bing HTML (sem API key). Default por estabilidade em datacenter."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&cc=br&setlang=pt-BR"
    resp = sessao.get(url, timeout=SESSION_TIMEOUT)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    resultados = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        snippet_el = li.select_one(".b_caption p")
        if not a:
            continue
        href = _resolver_cko(a.get("href", ""), sessao)
        if href:
            resultados.append({
                "titulo": a.get_text(strip=True),
                "url": href,
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            })
    return resultados[:num]


def _resolver_cko(href: str, sessao) -> str:
    """Bing embrulha o destino em /ck/a?...&u=a1<base64url>."""
    if not href.startswith("https://www.bing.com/ck/a"):
        return href
    try:
        qs = parse_qs(urlparse(href).query)
        raw = (qs.get("u") or [None])[0]
        if not raw:
            return href
        if raw.startswith("a1"):
            raw = raw[2:]
        pad = raw + "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(pad).decode("utf-8", errors="replace")
    except Exception:
        return href


def busca_duckduckgo(sessao, query, num=10):
    """DuckDuckGo HTML — pode bloquear IP de datacenter."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=br-pt"
    resp = sessao.get(url, timeout=SESSION_TIMEOUT)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    resultados = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            raw = (qs.get("uddg") or [None])[0]
            if raw:
                try:
                    from urllib.parse import unquote
                    href = unquote(raw)
                except Exception:
                    pass
        if href.startswith("http") and a.get_text(strip=True):
            resultados.append({
                "titulo": a.get_text(strip=True),
                "url": href,
                "snippet": "",
            })
    return resultados[:num]


def busca_google(sessao, query, num=10):
    """Google HTML — último recurso; captcha frequente."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=pt-BR&gl=br"
    resp = sessao.get(url, timeout=SESSION_TIMEOUT)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    resultados = []
    for a in soup.select("a[href^='/url?q='] h3"):
        parent = a.find_parent("a")
        href = parent.get("href", "") if parent else ""
        if href.startswith("/url?q="):
            href = parse_qs(urlparse(href).query).get("q", [""])[0]
        resultados.append({
            "titulo": a.get_text(strip=True),
            "url": href,
            "snippet": "",
        })
    return resultados[:num]


BACKENDS = {"duckduckgo": busca_duckduckgo, "bing": busca_bing,
            "google": busca_google}


# ---------------------------------------------------------------------------
# Queries e extração
# ---------------------------------------------------------------------------

def construir_query(organ: str, cargo: str | None, *, ano: int | None = None,
                    modo: str = "anterior") -> str:
    partes = [organ]
    if cargo:
        partes.append(cargo)
    if ano:
        partes.append(str(ano))
    partes.append("concurso anterior" if modo == "anterior" else "concurso")
    return " ".join(partes)


_RE_ANO = re.compile(r"\b(19|20)\d{2}\b")
_RE_BANCA_CITA = re.compile(
    r"(?:organizad[ao]s?\s+pelo?\s+|organizad[ao]s?\s+pela\s+|banca\s*[:\-]\s*|"
    r"examinador[ao]s?\s*[:\-]\s*|pela\s+fundação\s+|pelo\s+instituto\s+|pela\s+)"
    r"([A-ZÀ-Ü][\wÀ-ü\s.&’'\-]{2,50})",
    re.I,
)
DOMINIOS_CONFIANTES = re.compile(
    r"(pciconcursos|strategyconcursos|qconcursos|teccaconcursos|grancursosonline"
    r"|novaconcursos|acheconcursos|veprovas|concubras|gabarite|ache Provas)",
    re.I,
)


def extrair_banca(texto: str, alias_map: dict):
    m = _RE_BANCA_CITA.search(texto)
    if m:
        trecho = m.group(1).strip().lower()
        for slug, cfg in alias_map.items():
            if trecho in cfg["aliases"] or any(a in trecho for a in cfg["aliases"]):
                return slug, cfg["rotulo"]
    alvo_l = texto.lower()
    for slug, cfg in alias_map.items():
        for a in cfg["aliases"]:
            if a in alvo_l:
                return slug, cfg["rotulo"]
    return None


def coocorrencia(texto: str, organ: str, banca_aliases: list[str]) -> bool:
    alvo = texto.lower()
    idx = alvo.find(organ.lower())
    if idx < 0:
        return False
    janela = alvo[max(0, idx - 250): idx + 250]
    return any(a in janela for a in banca_aliases)


def extrair_anos(texto: str, ano_max: int | None = None) -> list[int]:
    ano_max = ano_max or ANO_ATUAL
    anos = sorted({int(m.group(0)) for m in _RE_ANO.finditer(texto)}, reverse=True)
    return [a for a in anos if ANO_MIN <= a <= ano_max]


def montar_candidatas(resultados, organ, alias_map, ano_max):
    candidatas = []
    for r in resultados:
        texto = f"{r['titulo']} {r['snippet']}"
        banca = extrair_banca(texto, alias_map)
        anos = extrair_anos(texto, ano_max)
        confiavel = bool(DOMINIOS_CONFIANTES.search(r["url"]))
        if not banca:
            continue
        aliases = alias_map[banca[0]]["aliases"]
        eh_forte = coocorrencia(texto, organ, aliases) or confiavel
        for ano in (anos or [ano_max]):
            candidatas.append({
                "banca_slug": banca[0], "banca_rotulo": banca[1], "ano": ano,
                "titulo": r["titulo"], "snippet": r["snippet"], "url": r["url"],
                "dominio_confiante": confiavel,
                "coocorrencia_orgao_banca": eh_forte,
            })
    return candidatas


def validar_em_agregadores(sessao, candidatas, organ):
    ag = AGREGADORES["pci"]
    lista_orgao = []
    try:
        resp = sessao.get(
            ag["url_catalogo"].format(banca_rotulo=quote_plus(organ.lower())),
            timeout=SESSION_TIMEOUT)
        if resp.status_code == 200:
            lista_orgao = _extrair_lista_pci(resp.text)
    except requests.RequestException:
        pass
    for c in candidatas:
        visto_orgao = [e for e in lista_orgao if e["banca_slug"] == c["banca_slug"]]
        url_cat_banca = ag["url_catalogo"].format(
            banca_rotulo=quote_plus(c["banca_rotulo"].lower()))
        confirmado_banca = False
        try:
            resp = sessao.get(url_cat_banca, timeout=SESSION_TIMEOUT)
            if resp.status_code == 200:
                confirmado_banca = True
        except requests.RequestException:
            pass
        c["validacao"] = {
            "agregador": ag["nome"],
            "url_catalogo_orgao": ag["url_catalogo"].format(
                banca_rotulo=quote_plus(organ.lower())),
            "confirmado_por_orgao": bool(visto_orgao),
            "concurso_vistos_no_catalogo": visto_orgao,
            "url_catalogo_banca": url_cat_banca,
            "confirmado": confirmado_banca,
        }
    return candidatas


_RE_PCI_LINHA = re.compile(r"provas/(download|detalhes)/([^\"'\s<>]+)", re.I)


def _extrair_lista_pci(html: str) -> list[dict]:
    vistos = {}
    for m in _RE_PCI_LINHA.finditer(html):
        slug = m.group(2).rstrip("/").lower()
        partes = slug.split("-")
        ano = next((int(p) for p in reversed(partes) if _RE_ANO.fullmatch(p)), None)
        if ano is None:
            continue
        banca_crua = partes[-2] if len(partes) >= 2 else ""
        banca_slug = next(
            (s for s, cfg in BANCAS_CONHECIDAS.items()
             if banca_crua in cfg["aliases"] or banca_crua in s),
            banca_crua,
        )
        certame = slug[:-len(str(ano)) - 1] if slug.endswith(str(ano)) else slug
        visto = vistos.setdefault((banca_slug, ano),
                                  {"banca_slug": banca_slug, "ano": ano,
                                   "certames": set()})
        visto["certames"].add(certame)
    return [{**v, "certames": sorted(v["certames"])} for v in vistos.values()]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def buscar_concursos_anteriores(organ, *, cargo=None, anos_busca=None,
                                backend=None, topo_k=None, queries=None,
                                bancos_adicionais=None):
    sessao = _sessao()
    backend = backend or DEFAULT_BACKEND
    fns_busca = [BACKENDS.get(backend, busca_bing)]
    alias_map = dict(BANCAS_CONHECIDAS)
    if bancos_adicionais:
        alias_map.update(bancos_adicionais)
    anos_busca = anos_busca or JANELA_ANOS
    topo_k = topo_k or DEFAULT_TOP_K
    ano_max = ANO_ATUAL - 1
    if queries is None:
        queries = [
            construir_query(organ, cargo, modo="anterior"),
            construir_query(organ, cargo, ano=ano_max - 2, modo="anterior"),
            f"provas anteriores {organ}{' ' + cargo if cargo else ''}",
            f"concurso {organ} provas gabarito",
            f"provas gabaritos {organ}",
        ]
        queries = [q for q in queries if q]

    resultados_brutos, vistos = [], set()
    for q in queries:
        achou = False
        for fn in fns_busca:
            try:
                rs = fn(sessao, q)
            except Exception:
                rs = []
            for r in rs:
                if r["url"] in vistos:
                    continue
                vistos.add(r["url"])
                resultados_brutos.append(r)
                achou = True
            if achou:
                break

    candidatas = montar_candidatas(resultados_brutos, organ, alias_map, ano_max)
    candidatas = validar_em_agregadores(sessao, candidatas, organ)
    for c in candidatas:
        pontos = 0
        if c["coocorrencia_orgao_banca"]:
            pontos += 4
        if c["dominio_confiante"]:
            pontos += 2
        if c["validacao"]["confirmado_por_orgao"]:
            pontos += 3
        if c["validacao"]["confirmado"]:
            pontos += 1
        c["pontos"] = pontos
    candidatas.sort(key=lambda c: (-c["pontos"], c["ano"]))
    return {
        "organ": organ, "cargo": cargo, "ano_max": ano_max,
        "backend": backend, "queries": queries,
        "total_resultados_analisados": len(resultados_brutos),
        "candidatas": candidatas[:topo_k],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir_resumo(resultado):
    print(f"Busca: {resultado['organ']!r}" +
          (f" cargo={resultado['cargo']!r}" if resultado["cargo"] else "") +
          f"  (ano_max={resultado['ano_max']}, backend={resultado['backend']}, "
          f"{resultado['total_resultados_analisados']} resultados analisados)")
    print("-" * 78)
    for i, c in enumerate(resultado["candidatas"], 1):
        v = c["validacao"]
        print(f"{i:>2}. {c['banca_rotulo']} ({c['ano']}) — "
              f"coocorrência_órgão_banca={c['coocorrencia_orgao_banca']} "
              f"confirmado_por_catálogo_do_órgão={v['confirmado_por_orgao']}")
        print(f"    {c['titulo'][:90]}")
        print(f"    {c['url'][:100]}")
        cert = v.get("concurso_vistos_no_catalogo") or []
        if cert:
            anos_cat = sorted({e["ano"] for e in cert})
            print(f"    catálogo PCI: certames vistos em {anos_cat} "
                  f"({sum(len(e['certames']) for e in cert)} links)")
    if not resultado["candidatas"]:
        print("Nenhuma candidata encontrada. Sugestões:")
        print("  1. confira a grafia do órgão")
        print("  2. troque o motor: --backend google | duckduckgo")
        print("  3. amplie a janela: --anos-busca 20")


def main():
    p = argparse.ArgumentParser(
        description="LexCorpus — busca por palavra-chave a banca dos "
                    "concursos anteriores de um órgão (CLI portátil)")
    p.add_argument("organ", help="órgão/empresa (ex.: Transpetro, PRF, TRT 2)")
    p.add_argument("--cargo", help="cargo-alvo (ex.: analista de sistemas)")
    p.add_argument("--backend", choices=list(BACKENDS), default=DEFAULT_BACKEND,
                   help=f"motor de busca (default {DEFAULT_BACKEND})")
    p.add_argument("--anos-busca", type=int, default=JANELA_ANOS,
                   help=f"anos atrás a olhar (default {JANELA_ANOS})")
    p.add_argument("--topo-k", type=int, default=DEFAULT_TOP_K,
                   help="máximo de candidatas (default 8)")
    p.add_argument("--resumo", action="store_true",
                   help="saída legível no terminal")
    p.add_argument("--saida", default=None, help="gravar JSON neste arquivo")
    args = p.parse_args()

    resultado = buscar_concursos_anteriores(
        args.organ, cargo=args.cargo, anos_busca=args.anos_busca,
        backend=args.backend, topo_k=args.topo_k,
    )
    if args.resumo:
        _imprimir_resumo(resultado)
    else:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    if args.saida:
        Path = __import__("pathlib").Path
        Path(args.saida).write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2),
            encoding="utf-8")


if __name__ == "__main__":
    main()
