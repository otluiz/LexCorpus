# Arquivo:  lexcorpus/alvos.py
# Função:   resolve "banca X, concurso Y" no ALVO de coleta (spider + params +
#           slug da pasta que o crawl vai produzir). Usado pelo consumidor do
#           LexPubli (ADR-0007). Sem certeza, devolve None: na dúvida, o caso
#           vai para o operador, nunca para um crawl no escuro.
"""Resolvedores de alvo por banca.

    resolver_atual(banca, data, http_get=...)            -> Alvo | None
        o concurso anunciado pelo LexPubli (contest.discovery.created):
        lê as URLs oficiais que vieram no evento (source_url, edital_url)
        ou, na Cesgranrio, a listagem pública de eventos ativos.

    resolver_anterior(banca, organ, ano, http_get=..., inventario_fgv=...)
        um certame ANTERIOR (banca + ano vêm do buscador):
        - fgv: inventário do modo descoberta (eventos_debug/descoberta/fgv.jsonl)
        - cebraspe: sonda a API oficial com o slug provável ({órgão}_{aa})
        - demais: None (operador)

`Alvo.concurso` é o slug da pasta que o spider VAI produzir. É ele que liga a
coleta ao concurso do LexPubli no StateStore (aquisicao_lexpubli), então
precisa ser derivado do mesmo jeito que o spider deriva:
    cebraspe/fgv/fcc -> slug do alvo; cesgranrio -> -a concurso explícito.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .util import slugify

log = logging.getLogger("lexcorpus.alvos")

# GET leve: url -> (status_http, json_ou_None). Injetável nos testes.
HttpGet = Callable[[str], tuple[int, object]]

CEBRASPE_API = "https://apis.cebraspe.org.br/cebraspe/eventos/{slug}"
CESGRANRIO_EVENTOS = "https://concursos.cesgranrio.org.br/api/PortalEventos"
INVENTARIO_FGV = Path("eventos_debug/descoberta/fgv.jsonl")

_RE_URL = {
    "cebraspe": re.compile(r"cebraspe\.org\.br/concursos/([A-Za-z0-9_]+)", re.I),
    "fgv": re.compile(r"conhecimento\.fgv\.br/concursos/([A-Za-z0-9_-]+)", re.I),
    "fcc": re.compile(r"concursosfcc\.com\.br/concursos/([A-Za-z0-9_-]+)", re.I),
}


@dataclass(frozen=True)
class Alvo:
    banca: str
    spider: str
    params: dict = field(hash=False)
    concurso: str            # slug da pasta que o crawl vai produzir
    origem: str              # como foi resolvido (auditoria / log)


def http_get_padrao(url: str) -> tuple[int, object]:
    import requests

    resp = requests.get(url, timeout=10, headers={
        "User-Agent": "LexCorpus/1.0 (+coletor de provas; fonte oficial)",
        "Accept": "application/json",
    })
    try:
        corpo = resp.json()
    except ValueError:
        corpo = None
    return resp.status_code, corpo


# --- concurso ATUAL ----------------------------------------------------------

def resolver_atual(banca: str, data: dict, *, http_get: HttpGet = http_get_padrao) -> Alvo | None:
    urls = [u for u in (data.get("source_url"), data.get("edital_url")) if u]
    if banca in _RE_URL:
        for url in urls:
            m = _RE_URL[banca].search(url)
            if m:
                slug = m.group(1).lower()
                return Alvo(banca, banca, {"slug": slug}, slugify(slug), f"url:{url}")
        return None
    if banca == "cesgranrio":
        return _cesgranrio_atual(data, http_get)
    return None


def _cesgranrio_atual(data: dict, http_get: HttpGet) -> Alvo | None:
    """Casa o órgão com a listagem de eventos ATIVOS do portal geral.

    Só aceita casamento ÚNICO; ambíguo ou nenhum -> None (operador).
    """
    try:
        status, corpo = http_get(CESGRANRIO_EVENTOS)
    except Exception as exc:  # rede: não adivinhar
        log.warning("cesgranrio: listagem indisponível (%s)", exc)
        return None
    itens = ((corpo or {}).get("data") or {}).get("items") or [] if status == 200 else []
    organ = data.get("organ") or {}
    chaves = {c for c in (slugify(organ.get("name", "")), organ.get("slug", "")) if c}
    ano = str(data.get("year") or "")
    achados = []
    for ev in itens:
        cliente = ev.get("cliente") or {}
        texto = slugify(" ".join(str(x or "") for x in (
            cliente.get("razaoSocial"), cliente.get("nome"), ev.get("nomeFantasia"), ev.get("nome"))))
        if not any(c in texto for c in chaves):
            continue
        if ano and ano not in texto and not str(ev.get("dataInicio", "")).startswith(ano):
            continue
        achados.append(ev)
    if len(achados) != 1:
        log.info("cesgranrio: %d eventos casam com %s/%s — operador decide", len(achados), chaves, ano)
        return None
    concurso = data["contest_slug"]
    return Alvo("cesgranrio", "cesgranrio",
                {"evento_id": str(achados[0]["id"]), "concurso": concurso},
                concurso, f"cesgranrio:PortalEventos/{achados[0]['id']}")


# --- certame ANTERIOR --------------------------------------------------------

def resolver_anterior(banca: str, organ: dict, ano: int, *,
                      http_get: HttpGet = http_get_padrao,
                      inventario_fgv: Path = INVENTARIO_FGV) -> Alvo | None:
    if banca == "fgv":
        return _fgv_anterior(organ, ano, inventario_fgv)
    if banca == "cebraspe":
        return _cebraspe_anterior(organ, ano, http_get)
    return None


def _fgv_anterior(organ: dict, ano: int, inventario: Path) -> Alvo | None:
    """O modo descoberta do spider fgv lista {slug, rotulo}; casa órgão + ano."""
    if not Path(inventario).exists():
        return None
    chaves = {c for c in (slugify(organ.get("name", "")), organ.get("slug", "")) if c}
    sufixos = (str(ano), f"{ano % 100:02d}")
    achados = []
    for linha in Path(inventario).read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(linha)
        except json.JSONDecodeError:
            continue
        slug, rotulo = item.get("slug", ""), slugify(item.get("rotulo", ""))
        if not any(c in rotulo or c.replace("_", "") in slug for c in chaves):
            continue
        if not any(slug.endswith(s) for s in sufixos):
            continue
        achados.append(slug)
    if len(set(achados)) != 1:
        return None
    slug = achados[0]
    return Alvo("fgv", "fgv", {"slug": slug}, slugify(slug), f"fgv:inventario/{slug}")


def _cebraspe_anterior(organ: dict, ano: int, http_get: HttpGet) -> Alvo | None:
    """Sonda a API oficial com o padrão de slug da Cebraspe ({órgão}_{aa}).

    No máximo duas requisições leves a uma API pública. Slugs com sufixo
    (ex.: sefaz_al_19_auditor) não são adivinhados: vão para o operador.
    """
    base = organ.get("slug", "")
    if not base:
        return None
    candidatos = dict.fromkeys([f"{base}_{ano % 100:02d}", f"{base.replace('_', '')}_{ano % 100:02d}"])
    for slug in candidatos:
        try:
            status, corpo = http_get(CEBRASPE_API.format(slug=slug))
        except Exception as exc:
            log.warning("cebraspe: sonda %s falhou (%s)", slug, exc)
            continue
        if status == 200 and isinstance(corpo, dict) and corpo.get("eventoURL"):
            real = str(corpo["eventoURL"]).lower()
            return Alvo("cebraspe", "cebraspe", {"slug": real}, slugify(real), f"cebraspe:api/{real}")
    return None
