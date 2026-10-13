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

from .alvos import CESGRANRIO_EVENTOS, INVENTARIO_FGV, HttpGet, http_get_padrao
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


def inventariar(banca: str, *, regras: dict, destino_dir: Path = INVENTARIO_DIR,
                http_get: HttpGet = http_get_padrao,
                fgv_jsonl: Path = INVENTARIO_FGV) -> Path:
    lido_em = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if banca == "fgv":
        if not Path(fgv_jsonl).exists():
            raise FileNotFoundError(f"{fgv_jsonl} ausente: rode antes "
                                    "`scrapy crawl fgv -a descoberta=1`")
        linhas = [json.loads(l) for l in Path(fgv_jsonl).read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        entradas = inventario_fgv(linhas, regras, lido_em)
    elif banca == "cesgranrio":
        status, corpo = http_get(CESGRANRIO_EVENTOS)
        if status != 200 or not isinstance(corpo, dict):
            raise RuntimeError(f"PortalEventos respondeu {status}")
        entradas = inventario_cesgranrio(corpo, regras, lido_em)
    else:
        # Cebraspe: a API não lista eventos (exige o identificador) e o sitemap
        # não tem concursos. Candidatos virão do índice PCI + confirmação na
        # API por slug (ADR-0009 §2), em passo próprio.
        raise ValueError(f"inventário de {banca!r} ainda não implementado (ADR-0009 §2)")
    destino = Path(destino_dir) / f"{banca}.jsonl"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entradas),
                   encoding="utf-8")
    tmp.replace(destino)
    return destino


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
    sub.add_parser("agenda", help="mostra (e completa) a liberação semanal dos confirmados")
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
        rev._salvar()                       # escalona quem ainda não tem data
        for chave, c in sorted(rev.confirmados.items(), key=lambda kv: (kv[1]["a_partir_de"], kv[0])):
            print(f"  {c['a_partir_de']}  {c['carreira']:<10} {chave}")
        return 0

    inventario = carregar_inventario(inventario_dir)
    por_chave = {e["chave"]: e for e in inventario}
    try:
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
        print(f"  {e['chave']:<28} {e['ano'] or '----'}  {e['organ'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
