# Arquivo:  lexcorpus/buscador.py
# Função:   cliente de busca por palavra-chave para DESCOBRIR bancas examinadoras
#           de concursos anteriores. Consulta a Internet (backends de busca),
#           extrai candidatos {banca, ano, concurso, cargos}, valida em
#           agregadores de índice (ADR-0001: índice, nunca fonte de PDF) e
#           ranqueia por triangulação de confiança.
#           (ADR-0006: descoberta de bancas por busca de palavra-chave)
# Interface: buscar_concursos_anteriores(organ, *, cargo=None, ano_max=None,
#                                        anos_busca=12, topo_k=8) -> dict
# CLI: python -m lexcorpus.buscador "Transpetro" "analista de sistemas"
"""Descoberta de banca examinadora de concursos anteriores por busca de
palavra-chave.

Por que este módulo existe: quando um concurso concorrido (ex.: Transpetro)
entra "no ar", a banca examinadora do certame atual pode NÃO ser a mesma dos
concursos anteriores. Para colher provas anteriores (alimento do LexLearn),
o LexCorpus precisa descobrir na Internet QUEM examinou antes — e este
módulo faz exatamente isso: busca, extrai, valida e ranqueia candidatas.

Princípios (não negociáveis):
  1. Agregadores (PCI, Estratégia, QConcursos...) são ÍNDICE de descoberta —
     nunca fonte de PDF (ADR-0001). O Spider da banca descoberta vai buscar
     o PDF na fonte primária.
  2. Toda decisão de confiança é explícita no JSON de saída (`confianca`,
     `evidencias`), para o operador validar antes de disparar o spider.
  3. Sem API key: backends HTTP puros (DuckDuckGo HTML, Bing, Google),
     selecionáveis por parâmetro — nada hardcoded.

Uso como biblioteca:

    from lexcorpus.buscador import buscar_concursos_anteriores

    resultado = buscar_concursos_anteriores(
        "Transpetro", cargo="analista de sistemas", anos_busca=12,
    )
    print(resultado["candidatas"][0])   # {banca, ano, concurso_rotulo, ...}

Uso como CLI:

    python3 -m lexcorpus.buscador "Transpetro" --cargo "analista de sistemas" \
        --anos-busca 12 --backend duckduckgo
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import quote_plus

try:
    import base64
    import requests
    from bs4 import BeautifulSoup
except ImportError as _exc:  # CLI standalone carrega requests/bs4 à parte
    base64 = requests = BeautifulSoup = None
    _IMPORT_ERR = _exc
else:
    _IMPORT_ERR = None


# ---------------------------------------------------------------------------
# Configuração externa (defaults parametrizáveis, não hardcoded)
# ---------------------------------------------------------------------------

ANO_MIN = 2000                    # ano mínimo de concurso a considerar
ANO_ATUAL = date.today().year     # recalculado no import

# Bancas conhecidas com slug canônico + nomes alternativos. Ranqueamento
# de triangulação usa este mapa — adicionar novas bancas aqui (ou via
# `BANCAS_CONHECIDAS` adicional passado a buscar_concursos_anteriores).
BANCAS_CONHECIDAS = {
    "cebraspe":  {"rotulo": "CEBRASPE",  "aliases": ["cespe", "unb", "cespe/unb", "cebraspe"]},
    "cesgranrio": {"rotulo": "Cesgranrio", "aliases": ["cesgranrio", "fundação cesgranrio"]},
    "fgv":      {"rotulo": "FGV",      "aliases": ["fgv", "fundação getúlio vargas", "fgv conhecimento", "fgv projetos"]},
    "fcc":      {"rotulo": "FCC",      "aliases": ["fcc", "fundação carlos chagas"]},
    "fundatec": {"rotulo": "Fundatec", "aliases": ["fundatec", "fundação fundatec"]},
    "ibfc":     {"rotulo": "IBFC",     "aliases": ["ibfc"]},
    "quadrix":  {"rotulo": "Quadrix",  "aliases": ["quadrix"]},
    "vunesp":   {"rotulo": "VUNESP",   "aliases": ["vunesp", "vunesp", "fundação para o vestibular da unesp"]},
    "instituto_ao": {"rotulo": "Instituto AOCP", "aliases": ["aocp", "instituto aocp"]},
    "consulplan": {"rotulo": "Consulplan", "aliases": ["consulplan", "consulpam"]},
    "fmp":      {"rotulo": "FMP",      "aliases": ["fmp", "fmp concursos"]},
    "institutoc&b": {"rotulo": "Instituto C&B", "aliases": ["instituto c&b", "c&b", "cb"]},
    "ms_concursos": {"rotulo": "MS Concursos", "aliases": ["ms concursos"]},
    "riogrande_do_sul": {"rotulo": "FDRH", "aliases": ["fdrh", "fundação para o desenvolvimento de recursos humanos"]},
    "fgv_concursos": {"rotulo": "FGV Concursos", "aliases": ["fgv concursos"]},
}

# Agregadores usados como ÍNDICE de validação (ADR-0001). Cada um tem o
# padrão de URL do catálogo da banca — o validador tenta montar a URL e
# confirma com um GET leve (HEAD quando possível).
AGREGADORES = {
    "pci": {
        "nome": "PCI Concursos",
        "url_catalogo": "https://www.pciconcursos.com.br/provas/{banca_rotulo}",
        "url_concurso": "https://www.pciconcursos.com.br/provas/download/{slug}",
        "dominio": "pciconcursos.com.br",
    },
    "estrategia": {
        "nome": "Estratégia Concursos",
        "url_catalogo": "https://info.strategyconcursos.com.br/search?q={banca_rotulo}",
        "dominio": "strategyconcursos.com.br",
    },
    "qconcursos": {
        "nome": "QConcursos",
        "url_catalogo": "https://www.qconcursos.com/provas-de-concursos?banca={slug}",
        "dominio": "qconcursos.com",
    },
}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

SESSION_TIMEOUT = 12  # segundos por requisição


# ---------------------------------------------------------------------------
# Backends de busca (pluggable — adicione o seu sem tocar no resto)
# ---------------------------------------------------------------------------

def _sessao():
    if requests is None:
        raise ImportError(
            "requests/bs4 ausentes — instale com: sudo pip3 install requests beautifulsoup4"
        ) from _IMPORT_ERR
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    return s


def busca_duckduckgo(sessao, query, num=10):
    """DuckDuckGo HTML (sem API key). Rate-limit alto; sem captcha p/ uso leve."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=br-pt"
    resp = sessao.get(url, timeout=SESSION_TIMEOUT)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    resultados = []
    for r in soup.select("div.result"):
        a = r.select_one("a.result__a")
        snippet = " ".join(
            t.strip() for t in r.select_one("a.result__snippet, .result__snippet")
            .stripped_strings if r.select_one("a.result__snippet, .result__snippet")
        )
        if a and a.get("href"):
            resultados.append({"titulo": a.get_text(strip=True), "url": a["href"],
                               "snippet": snippet})
    return resultados[:num]


def busca_bing(sessao, query, num=10):
    """Bing HTML (sem API key). Backend padrão — o mais estável em IP de
    datacenter (DuckDuckGo costuma bloquear)."""
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
    """Bing embrulha URLs reais em /ck/a?...&u=a1<base64url>. Extrai o parâmetro
    `u` e decodifica o destino em base64url (sem requisição extra). Erros →
    href cru."""
    if not href.startswith("https://www.bing.com/ck/a"):
        return href
    try:
        from urllib.parse import parse_qs, urlparse
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


def busca_google(sessao, query, num=10):
    """Google HTML (sem API key) — último recurso; captcha frequente."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=pt-BR&gl=br"
    resp = sessao.get(url, timeout=SESSION_TIMEOUT)
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    resultados = []
    for div in soup.select("div.g"):
        a = div.select_one("a[href]")
        snippet_el = div.select_one("div.VwiC3b, span.aCOpRe")
        if a and a.get("href").startswith("http"):
            resultados.append({
                "titulo": a.get_text(strip=True),
                "url": a["href"],
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            })
    return resultados[:num]


BACKENDS = {"duckduckgo": busca_duckduckgo, "bing": busca_bing, "google": busca_google}

DEFAULT_BACKEND = "bing"  # DuckDuckGo bloqueia IP de datacenter (verificado)


# ---------------------------------------------------------------------------
# Extração de candidatos a partir dos resultados de busca
# ---------------------------------------------------------------------------

_RE_ANO = re.compile(r"\b(19|20)\d{2}\b")
_RE_GAB_DEF = re.compile(r"gabarito.*(definitiv|final|oficial|p[óo]s.?recurs)", re.I)
_RE_GAB_PRE = re.compile(r"gabarito.*(preliminar|provis[óo]ri)", re.I)
_RE_GAB = re.compile(r"gabarito|gab[\W_]", re.I)
_RE_PROVA = re.compile(r"prova|caderno|padr[ãa]o\s+de\s+resposta", re.I)
_RE_BANCA_CITA = re.compile(
    r"(?:organizad[ao]s?\s+pelo?\s+|organizad[ao]s?\s+pela\s+|banca\s*[:\-]\s*|"
    r"examinador[ao]s?\s*[:\-]\s*|pela\s+fundação\s+|pelo\s+instituto\s+|pela\s+)"
    r"([A-ZÀ-Ü][\wÀ-ü\s.&’'\-]{2,50})",
    re.I,
)

# contexto de co-ocorrência: a banca é citada PERTO do órgão? (janela de
# ~250 caracteres). Co-ocorrência forte = a banca examinou ESTE órgão.
# Nota: a checagem em si é feita por coocorrencia() (substring em janela);
# este regex documenta o padrão canônico quando se quiser usar via .format.
_COOCORRENCIA_PADRAO = r"{organ}[^{{]{{0,250}}?{{banca}}|{{banca}}[^{{]{{0,250}}?{{organ}}"

# Padrões de bancos de questões / editais que quase sempre citam a banca
DOMINIOS_CONFIANTES = re.compile(
    r"(pciconcursos|strategyconcursos|qconcursos|teccaconcursos|grancursosonline"
    r"|estuda\.com|pontodosconcursos|folhadirigida|jcconcursos|acheconcursos)",
    re.I,
)


def extrair_banca(texto: str, alias_map: dict) -> tuple[str, str] | None:
    """Tenta localizar o nome de uma banca conhecida dentro do texto.

    Retorna (slug, rotulo) ou None. Prioriza menções explícitas da banca
    (ex.: "organizada pela FGV") sobre substrings ambíguas.
    """
    alvo = texto
    m = _RE_BANCA_CITA.search(alvo)
    if m:
        trecho = m.group(1).strip().lower()
        for slug, cfg in alias_map.items():
            if trecho in cfg["aliases"] or any(a in trecho for a in cfg["aliases"]):
                return slug, cfg["rotulo"]
    # varredura por aliases (menos precisa — confiança menor)
    alvo_l = alvo.lower()
    for slug, cfg in alias_map.items():
        for a in cfg["aliases"]:
            if a in alvo_l:
                return slug, cfg["rotulo"]
    return None


def coocorrencia(texto: str, organ: str, banca_aliases: list[str]) -> bool:
    """True se órgão e banca aparecem próximos (<=250 chars) — evidência de
    que a banca examinou ESTE órgão, não só apareceu na SERP."""
    alvo = texto.lower()
    organ_l = organ.lower()
    idx = alvo.find(organ_l)
    if idx < 0:
        return False
    janela = alvo[max(0, idx - 250): idx + 250]
    return any(a in janela for a in banca_aliases)  # busca literal (casefold)


def extrair_anos(texto: str, ano_max: int | None = None) -> list[int]:
    """Anos citados no texto, filtrados por janela [ANO_MIN, ano_max]."""
    ano_max = ano_max or ANO_ATUAL
    anos = sorted({int(m.group(0)) for m in _RE_ANO.finditer(texto)}, reverse=True)
    return [a for a in anos if ANO_MIN <= a <= ano_max]


def classificar_achado(texto: str) -> str | None:
    """O resultado de busca menciona prova/gabarito? (usa heuristics do projeto)."""
    try:
        from . import heuristics
        return heuristics.classificar_papel(texto)
    except ImportError:  # CLI standalone
        alvo = texto.lower()
        if _RE_GAB_DEF.search(alvo):
            return "gabarito_definitivo"
        if _RE_GAB_PRE.search(alvo):
            return "gabarito_preliminar"
        if _RE_GAB.search(alvo):
            return "gabarito_definitivo"
        if _RE_PROVA.search(alvo):
            return "prova"
        return None


def montar_candidatas(resultados, organ, alias_map, ano_max):
    """Transforma resultados brutos de busca em candidatas ranqueáveis."""
    candidatas = []
    for r in resultados:
        texto = f"{r['titulo']} {r['snippet']}"
        banca = extrair_banca(texto, alias_map)
        anos = extrair_anos(texto, ano_max)
        papel = classificar_achado(texto)
        confiavel = bool(DOMINIOS_CONFIANTES.search(r["url"]))
        if not banca:
            continue
        # triangulação básica na extração: órgão + banca próximos no texto?
        aliases = alias_map[banca[0]]["aliases"]
        eh_candidata_forte = coocorrencia(texto, organ, aliases) or confiavel
        for ano in (anos or [ano_max]):
            candidatas.append({
                "banca_slug": banca[0],
                "banca_rotulo": banca[1],
                "ano": ano,
                "titulo": r["titulo"],
                "snippet": r["snippet"],
                "url": r["url"],
                "papel_mencionado": papel,
                "dominio_confiante": confiavel,
                "coocorrencia_orgao_banca": eh_candidata_forte,
            })
    return candidatas


# ---------------------------------------------------------------------------
# Validação em agregadores-índice (ADR-0001: nunca fonte de PDF)
# ---------------------------------------------------------------------------

def validar_em_agregadores(sessao, candidatas, organ):
    """Confirma candidatas consultando catálogos de agregadores.

    Duas frentes de validação (ADR-0001: índice, nunca fonte de PDF):
      1. Página PCI do ÓRGÃO (/provas/{organ}) — lista TODAS as provas do
         órgão com a banca e o ano de cada certame. É a fonte mais rica para
         responder "quem fez o concurso anterior".
      2. Página PCI da BANCA (/provas/{banca}) — confirma que o catálogo da
         banca existe (validação mais fraca).
    Marca `validacao` em cada candidata.
    """
    ag = AGREGADORES["pci"]
    # frente 1: catálogo por órgão — extrai lista {banca, ano, concurso}
    lista_orgao = []
    try:
        resp = sessao.get(
            ag["url_catalogo"].format(banca_rotulo=quote_plus(organ.lower())),
            timeout=SESSION_TIMEOUT,
        )
        if resp.status_code == 200:
            lista_orgao = _extrair_lista_pci(resp.text)
    except requests.RequestException:
        pass

    for c in candidatas:
        visto_orgao = [e for e in lista_orgao if e["banca_slug"] == c["banca_slug"]]
        confirmado_orgao = bool(visto_orgao)  # catálogo do órgão lista certames
                                            # desta banca — vínculo real
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
            "confirmado_por_orgao": confirmado_orgao,
            "concurso_vistos_no_catalogo": [
                e for e in lista_orgao if e["banca_slug"] == c["banca_slug"]
            ],
            "url_catalogo_banca": url_cat_banca,
            "confirmado": confirmado_banca,
        }
    return candidatas


_RE_PCI_LINHA = re.compile(
    r"provas/(download|detalhes)/([^\"'\s<>]+)" , re.I)
_RE_PCI_ANO = re.compile(r"\b(19|20)\d{2}\b")


def _extrair_lista_pci(html: str) -> list[dict]:
    """Varre o HTML da página /provas/{orgao} do PCI e agrupa links de
    download por certame. O slug do PCI carrega '{concurso}-{banca}-{ano}'
    — extrai-se o ano e tenta-se mapear a banca pelo nome do certame.

    Ex.: /provas/download/transpetro-cesgranrio-2026 ->
         {orgao: transpetro, ano: 2026, banca_slug: cesgranrio}
    """
    vistos = {}
    for m in _RE_PCI_LINHA.finditer(html):
        slug = m.group(2).rstrip("/").lower()
        partes = slug.split("-")
        ano = next((int(p) for p in reversed(partes) if _RE_PCI_ANO.fullmatch(p)), None)
        if ano is None:
            continue
        # último segmento numérico = ano; o penúltimo costuma ser a banca
        banca_crua = partes[-2] if len(partes) >= 2 else ""
        banca_slug = next(
            (s for s, cfg in BANCAS_CONHECIDAS.items()
             if banca_crua in cfg["aliases"] or banca_crua in s),
            banca_crua,
        )
        certame = slug[: -len(str(ano)) - 1] if slug.endswith(str(ano)) else slug
        visto = vistos.setdefault(
            (banca_slug, ano),
            {"banca_slug": banca_slug, "ano": ano, "certames": set()},
        )
        visto["certames"].add(certame)
    return [
        {**v, "certames": sorted(v["certames"])}
        for v in vistos.values()
    ]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def construir_query(organ, cargo=None, ano=None, modo="anterior"):
    """Monta a string de busca parametrizável (override via -a query se quiser)."""
    partes = [organ]
    if cargo:
        partes.append(cargo)
    if modo == "anterior":
        partes.append("concurso anterior")
    else:
        partes.append("concurso provas gabarito")
    if ano:
        partes.append(str(ano))
    return " ".join(partes)


def buscar_concursos_anteriores(
    organ,
    *,
    cargo=None,
    anos_busca=12,
    backend=None,
    topo_k=8,
    queries=None,
    bancos_adicionais=None,
):
    """Descobre bancas examinadoras de concursos anteriores do órgão.

    Parâmetros (tudo parametrizável, nada hardcoded):
      organ        — órgão/empresa (ex.: "Transpetro")
      cargo        — cargo de interesse (opcional; restringe a busca)
      anos_busca   — janela de anos atrás de hoje (default 12)
      backend      — "duckduckgo" | "bing" | "google"
      topo_k       — quantas candidatas retornar
      queries      — override manual da lista de queries (lista de strings)
      bancos_adicionais — dict extra de bancas {slug: {rotulo, aliases}}
    """
    sessao = _sessao()
    if backend is None:
        backend = DEFAULT_BACKEND
    fns_busca = [BACKENDS.get(backend, busca_bing)]
    alias_map = dict(BANCAS_CONHECIDAS)
    if bancos_adicionais:
        alias_map.update(bancos_adicionais)

    ano_max = ANO_ATUAL - 1  # "anterior" = antes do ano corrente
    if queries is None:
        queries = [
            construir_query(organ, cargo, modo="anterior"),
            construir_query(organ, cargo, ano=ano_max - 2, modo="anterior"),
            f"provas anteriores {organ}{' ' + cargo if cargo else ''}",
            f"concurso {organ} provas gabarito",  # sem âncora de ano — pega histórico
            f"provas gabaritos {organ}",
        ]
        queries = [q for q in queries if q]

    resultados_brutos = []
    vistos = set()
    for q in queries:
        achou = False
        for fn in fns_busca:
            try:
                rs = fn(sessao, q)
            except Exception:  # backend falhou (captcha/bloqueio) — silencioso
                rs = []
            for r in rs:
                if r["url"] in vistos:
                    continue
                vistos.add(r["url"])
                resultados_brutos.append(r)
                achou = True
            if achou or len(fns_busca) <= 1:
                break
        if not achou and len(fns_busca) <= 1:
            continue

    candidatas = montar_candidatas(resultados_brutos, organ, alias_map, ano_max)
    candidatas = validar_em_agregadores(sessao, candidatas, organ)

    # ranqueamento por confiança (triangulação)
    for c in candidatas:
        pontos = 0
        if c["coocorrencia_orgao_banca"]:
            pontos += 4   # órgão + banca próximos no mesmo texto = evidência forte
        if c["dominio_confiante"]:
            pontos += 2
        if c["validacao"]["confirmado"]:
            pontos += 1   # catálogo PCI existe, mas não prova vínculo com o órgão
        if c["papel_mencionado"]:
            pontos += 1
        c["pontos"] = pontos

    candidatas.sort(key=lambda c: (-c["pontos"], -c["ano"]))
    candidatas = candidatas[:topo_k]
    for c in candidatas:
        del c["pontos"]

    return {
        "organ": organ,
        "cargo": cargo,
        "ano_max": ano_max,
        "backend": backend,
        "queries": queries,
        "total_resultados_analisados": len(resultados_brutos),
        "candidatas": candidatas,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    if _IMPORT_ERR:
        raise SystemExit(
            f"dependências ausentes: {_IMPORT_ERR}\n"
            "sudo pip3 install requests beautifulsoup4"
        )
    p = argparse.ArgumentParser(
        description="Descobre a banca examinadora de concursos anteriores "
                    "de um órgão, por busca de palavra-chave na Internet.",
    )
    p.add_argument("organ", help="órgão/empresa, ex.: Transpetro")
    p.add_argument("--cargo", default=None, help="cargo de interesse (opcional)")
    p.add_argument("--anos-busca", type=int, default=12,
                   help="janela de anos a procurar atrás (default 12)")
    p.add_argument("--backend", choices=list(BACKENDS), default=DEFAULT_BACKEND,
                   help=f"motor de busca (default {DEFAULT_BACKEND})")
    p.add_argument("--topo-k", type=int, default=8,
                   help="número máximo de candidatas (default 8)")
    p.add_argument("--saida", default=None,
                   help="gravar JSON de saída neste arquivo (opcional)")
    p.add_argument("--resumo", action="store_true",
                   help="imprimir só o resumo ranqueado (legível), sem JSON")
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
        from pathlib import Path
        Path(args.saida).write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _imprimir_resumo(resultado):
    """Resumo legível para uso humano no terminal (não quebra o JSON da API)."""
    print(f"Busca: {resultado['organ']!r}" +
          (f" cargo={resultado['cargo']!r}" if resultado['cargo'] else "") +
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
        print("  1. confira a grafia do órgão (--organ 'Petrobras' em vez de "
              "'Transpetro'?)")
        print("  2. troque o motor: --backend google | duckduckgo")
        print("  3. amplie a janela: --anos-busca 20")


if __name__ == "__main__":
    main()
