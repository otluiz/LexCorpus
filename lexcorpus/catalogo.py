# Arquivo:  lexcorpus/catalogo.py
# Função:   Catálogo de Concursos dinâmico — mantém as listas VIGENTES e
#           PASSADOS (desatualizados), organizadas por ano{mês/dia}/banca/
#           cargos. Concursos entram em `vigentes` quando o edital sai e
#           migram para `passados` quando o certame encerra (a cada
#           ano/semestre — a lista é viva, uns entram e outros saem).
#           (ADR-0006)
# Formato: YAML versionado (`catalogo.yaml` na raiz do projeto), coerente
#          com watchlist.yaml (banca/spider/params/cron).
# CLI: python -m lexcorpus.catalogo {list|add|move|expire|update-busca}
"""Catálogo dinâmico de concursos — vigentes e passados.

Por que existe: quando um concurso concorrido entra "no ar" (ex.: Transpetro
2026), o operador precisa de UM lugar para registrar o alvo de coleta
(vigente) e de OUTRO para consultar o histórico (passados). O catálogo
responde às perguntas: "quais concursos estou coletando agora?", "quem
examinou este órgão antes?" e "quais certames já encerraram e viraram
histórico?".

Estrutura do YAML (versionado):

    version: 1
    vigentes:
      - organ: Transpetro
        organ_slug: transpetro
        edital_ano: 2026
        edital_data: 2026-08-01          # data do edital (opcional: mês/dia)
        banca_rotulo: Cesgranrio
        banca: cesgranrio
        banca_descoberta_em: 2026-08-19  # via buscador
        concurso_rotulo: Transpetro 2026
        concurso: transpetro_2026
        cargos:                          # cargos-alvo do certame atual
          - Analista de Sistemas
          - Administrador
        spider: (a definir | automático)
        params: {}
        cron: "0 */6 * * *"              # frequência de coleta enquanto ativo
        status: coletando                # aguardando_banca | coletando | pausado
        observacao: ""
    passados:
      transpetro_2023:                   # chave = concurso (slug)
        organ: Transpetro
        organ_slug: transpetro
        edital_ano: 2023
        data_prova: 2023-10-29
        encerrado_em: 2024-01-15         # quando migrou para passados
        banca_rotulo: Cesgranrio
        banca: cesgranrio
        concurso_rotulo: Transpetro 2023
        concurso: transpetro_2023
        cargos: [Analista de Sistemas, ...]
        spider: cebraspe (exemplo)
        storage: exams/cesgranrio/transpetro_2023/
        observacao: ""

CLI (tudo parametrizável, nada hardcoded):

    python3 -m lexcorpus.catalogo list                          # visão geral
    python3 -m lexcorpus.catalogo list --grupo vigentes
    python3 -m lexcorpus.catalogo add --organ Transpetro --banca Cesgranrio \\
        --concurso transpetro_2026 --cargos "Analista de Sistemas,Admin"
    python3 -m lexcorpus.catalogo move --chave transpetro_2026 \\
        --de vigentes --para passados
    python3 -m lexcorpus.catalogo expire --hoje 2026-12-31      # migra por data
    python3 -m lexcorpus.catalogo update-busca --organ Transpetro  # consulta
                                                                  # o buscador e
                                                                  # sugere alvos

Uso como biblioteca:

    from lexcorpus.catalogo import Catalogo
    cat = Catalogo()
    cat.adicionar(...)
    cat.mover("transpetro_2026", de="vigentes", para="passados")
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from .util import slugify
from . import buscador

# ---------------------------------------------------------------------------
# Configuração (defaults parametrizáveis)
# ---------------------------------------------------------------------------

CATALOGO_PATH = Path(__file__).resolve().parent.parent / "catalogo.yaml"
VERSION = 1


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

class Catalogo:
    """Catálogo dinâmico de concursos (vigentes/passados)."""

    def __init__(self, path: str | Path = CATALOGO_PATH):
        self.path = Path(path)
        self.dados = self._carregar()

    # --- persistência -------------------------------------------------------

    def _carregar(self) -> dict:
        if yaml is None:
            raise ImportError(
                "PyYAML ausente — sudo pip3 install pyyaml"
            )
        if not self.path.exists():
            return {"version": VERSION, "vigentes": [], "passados": {}}
        with open(self.path, encoding="utf-8") as f:
            dados = yaml.safe_load(f) or {}
        dados.setdefault("version", VERSION)
        dados.setdefault("vigentes", [])
        dados.setdefault("passados", {})
        return dados

    def salvar(self) -> None:
        self.dados["version"] = VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.dados, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False)

    # --- operações ----------------------------------------------------------

    def listar(self, grupo: str | None = None, organ: str | None = None,
               banca: str | None = None) -> dict:
        """Visão geral. Organiza vigentes por organ>banca e passados por
        ano>banca (a hierarquia pedida: ano{mês/dia}/banca/cargos)."""
        saida = {"vigentes": {}, "passados": {}, "estatisticas": {}}

        for v in self.dados["vigentes"]:
            if organ and slugify(v.get("organ", "")) != slugify(organ):
                continue
            if banca and v.get("banca", "") != banca:
                continue
            saida["vigentes"].setdefault(
                v.get("organ", "?"), []).append({
                "concurso": v.get("concurso"),
                "banca": v.get("banca_rotulo") or v.get("banca"),
                "edital_data": v.get("edital_data") or v.get("edital_ano"),
                "cargos": v.get("cargos", []),
                "status": v.get("status"),
            })

        # hierarquia passados: ano -> banca -> [{concurso, cargos, ...}]
        for chave, p in self.dados["passados"].items():
            if organ and slugify(p.get("organ", "")) != slugify(organ):
                continue
            if banca and p.get("banca", "") != banca:
                continue
            ano = p.get("edital_ano") or p.get("data_prova", "?")
            banc = p.get("banca_rotulo") or p.get("banca", "?")
            saida["passados"].setdefault(str(ano), {}).setdefault(
                banc, []).append({
                "concurso": chave,
                "cargos": p.get("cargos", []),
                "encerrado_em": p.get("encerrado_em"),
                "storage": p.get("storage"),
            })

        saida["estatisticas"] = {
            "n_vigentes": len(self.dados["vigentes"]),
            "n_passados": len(self.dados["passados"]),
        }
        return saida

    def adicionar(self, *, organ, organ_slug=None, edital_ano=None,
                  edital_data=None, banca=None, banca_rotulo=None,
                  concurso=None, concurso_rotulo=None, cargos=None,
                  spider=None, params=None, cron=None, status="aguardando_banca",
                  observacao="", substituir=True) -> dict:
        """Adiciona um concurso a `vigentes`. Parametrizável: omita banca
        (None) para registrar o alvo ANTES da descoberta — o buscador
        preenche depois (update-busca)."""
        organ_slug = slugify(organ_slug or organ)
        concurso = slugify(concurso or f"{organ_slug}_{edital_ano or '?'}")
        entry = {
            "organ": organ,
            "organ_slug": organ_slug,
            "concurso": concurso,
            "concurso_rotulo": concurso_rotulo or f"{organ} {edital_ano}",
            "edital_ano": edital_ano,
            "cargos": cargos or [],
            "status": status,
            "observacao": observacao,
        }
        if edital_data:
            entry["edital_data"] = edital_data
        if banca:
            entry["banca"] = slugify(banca)
        if banca_rotulo:
            entry["banca_rotulo"] = banca_rotulo
        if spider is not None:
            entry["spider"] = spider
        if params is not None:
            entry["params"] = params
        if cron is not None:
            entry["cron"] = cron

        vigentes = self.dados["vigentes"]
        idx = next((i for i, v in enumerate(vigentes)
                    if v.get("concurso") == concurso), None)
        if idx is not None and substituir:
            # mantém campos não informados no novo dict (merge)
            existente = vigentes[idx]
            existente.update({k: v for k, v in entry.items() if v})
            entry = existente
        else:
            vigentes.append(entry)
        self.salvar()
        return entry

    def mover(self, chave: str, de: str = "vigentes", para: str = "passados",
              *, encerrado_em: str | None = None, data_prova: str | None = None,
              storage: str | None = None, observacao: str | None = None):
        """Migra um concurso entre grupos (vigentes -> passados e vice-versa)."""
        if de == "vigentes":
            idx = next((i for i, v in enumerate(self.dados["vigentes"])
                        if v.get("concurso") == chave), None)
            if idx is None:
                raise KeyError(f"vigentes: {chave!r} não encontrado")
            entry = self.dados["vigentes"].pop(idx)
            self.dados["passados"][chave] = {
                **entry,
                "encerrado_em": encerrado_em or datetime.date.today().isoformat(),
            }
        elif de == "passados":
            if chave not in self.dados["passados"]:
                raise KeyError(f"passados: {chave!r} não encontrado")
            entry = self.dados["passados"].pop(chave)
            self.dados["vigentes"].append(entry)
        else:
            raise ValueError(f"grupo desconhecido: {de!r}")
        if data_prova:
            self.dados["passados"].setdefault(chave, {})["data_prova"] = data_prova
        if storage:
            self.dados["passados"].setdefault(chave, {})["storage"] = storage
        if observacao is not None:
            self.dados["passados"].setdefault(chave, {})["observacao"] = observacao
        self.salvar()

    def expirar(self, *, hoje: str | None = None,
                max_dias_vigente: int = 365) -> list[str]:
        """Migra para `passados` os vigentes cuja data do edital (ou
        entrada no catálogo) excede `max_dias_vigente`. Roda a cada
        ano/semestre para manter a lista viva."""
        hoje_d = datetime.date.fromisoformat(hoje or str(datetime.date.today()))
        migrados = []
        remaining = []
        for v in self.dados["vigentes"]:
            ref = v.get("edital_data") or v.get("edital_ano")
            try:
                data_ref = datetime.date.fromisoformat(ref) if isinstance(ref, str) else None
            except ValueError:
                data_ref = None
            vencido = (data_ref is not None and
                       (hoje_d - data_ref).days > max_dias_vigente)
            if vencido or v.get("status") == "encerrado":
                chave = v["concurso"]
                self.dados["passados"][chave] = {
                    **v,
                    "encerrado_em": hoje_d.isoformat(),
                }
                migrados.append(chave)
            else:
                remaining.append(v)
        self.dados["vigentes"] = remaining
        self.salvar()
        return migrados

    def atualizar_com_busca(self, organ: str, cargo: str | None = None,
                            backend: str | None = None) -> dict:
        """Consulta o buscador (lexcorpus.buscador) e SUGERE candidatas para
        os vigentes do órgão sem banca definida. NÃO altera o catálogo —
        a promoção é manual (operador decide, coerente com o watchlist).

        Retorna {concurso: [candidatas]} para o operador usar no CLI ou no
        prompt do Manus.
        """
        vigentes_sem_banca = [
            v for v in self.dados["vigentes"]
            if v.get("organ_slug") == slugify(organ) and not v.get("banca")
        ]
        if not vigentes_sem_banca:
            return {"organ": organ, "nota": "nenhum vigente sem banca; "
                                            "adicione com `add` primeiro"}
        kwargs = {"cargo": cargo}
        if backend:
            kwargs["backend"] = backend
        resultado = buscador.buscar_concursos_anteriores(organ, **kwargs)
        return {"organ": organ, "candidatas": resultado["candidatas"],
                "queries": resultado["queries"]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Catálogo dinâmico de concursos — vigentes e passados.",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    # list
    pl = sub.add_parser("list", help="listar catálogo")
    pl.add_argument("--grupo", choices=["vigentes", "passados"])
    pl.add_argument("--organ", help="filtrar por órgão")
    pl.add_argument("--banca", help="filtrar por banca")
    pl.add_argument("--format", choices=["texto", "json"], default="texto",
                    dest="fmt")

    # add
    pa = sub.add_parser("add", help="adicionar concurso vigente")
    pa.add_argument("--organ", required=True)
    pa.add_argument("--edital-ano", type=int)
    pa.add_argument("--edital-data")
    pa.add_argument("--banca")
    pa.add_argument("--banca-rotulo")
    pa.add_argument("--concurso")
    pa.add_argument("--concurso-rotulo")
    pa.add_argument("--cargos", help="lista separada por vírgula")
    pa.add_argument("--spider")
    pa.add_argument("--params", help="JSON de params do spider")
    pa.add_argument("--cron")
    pa.add_argument("--status", default="aguardando_banca")

    # move
    pm = sub.add_parser("move", help="mover entre vigentes/passados")
    pm.add_argument("--chave", required=True)
    pm.add_argument("--de", choices=["vigentes", "passados"], default="vigentes")
    pm.add_argument("--para", choices=["vigentes", "passados"], default="passados")
    pm.add_argument("--encerrado-em")
    pm.add_argument("--data-prova")

    # expire
    pe = sub.add_parser("expire", help="migrar vigentes vencidos para passados")
    pe.add_argument("--hoje", help="data de referência YYYY-MM-DD (default: hoje)")
    pe.add_argument("--max-dias-vigente", type=int, default=365)

    # update-busca
    pu = sub.add_parser("update-busca", help="consultar buscador p/ órgão")
    pu.add_argument("--organ", required=True)
    pu.add_argument("--cargo")
    pu.add_argument("--backend", choices=list(buscador.BACKENDS))

    args = p.parse_args()
    cat = Catalogo()

    if args.comando == "list":
        visao = cat.listar(grupo=args.grupo, organ=args.organ,
                           banca=args.banca)
        if args.fmt == "json":
            print(json.dumps(visao, ensure_ascii=False, indent=2))
        else:
            _imprimir_visao(visao, grupo=args.grupo)

    elif args.comando == "add":
        cargos = [c.strip() for c in args.cargos.split(",")] if args.cargos else []
        params = json.loads(args.params) if args.params else None
        entry = cat.adicionar(
            organ=args.organ, edital_ano=args.edital_ano,
            edital_data=args.edital_data, banca=args.banca,
            banca_rotulo=args.banca_rotulo, concurso=args.concurso,
            concurso_rotulo=args.concurso_rotulo, cargos=cargos,
            spider=args.spider, params=params, cron=args.cron,
            status=args.status,
        )
        print(f"adicionado: {entry['concurso']} ({entry['status']})")
        print(json.dumps(entry, ensure_ascii=False, indent=2))

    elif args.comando == "move":
        try:
            cat.mover(args.chave, de=args.de, para=args.para,
                      encerrado_em=args.encerrado_em, data_prova=args.data_prova)
        except KeyError as exc:
            print(f"AVISO: {exc} — se o concurso já está em {args.para}, "
                  "nenhuma ação necessária")
        else:
            print(f"movido {args.chave}: {args.de} -> {args.para}")

    elif args.comando == "expire":
        migrados = cat.expirar(hoje=args.hoje,
                               max_dias_vigente=args.max_dias_vigente)
        print(f"migrados para passados: {migrados or 'nenhum'}")

    elif args.comando == "update-busca":
        resultado = cat.atualizar_com_busca(args.organ, cargo=args.cargo,
                                            backend=args.backend)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))


def _imprimir_visao(visao, grupo=None):
    def cab(titulo):
        print(f"\n== {titulo} {'=' * (74 - len(titulo))}")

    if grupo in (None, "vigentes"):
        cab("VIGENTES")
        for organ, lista in (visao["vigentes"] or {}).items():
            print(f"\n  {organ}")
            for v in lista:
                cargos = ", ".join(v["cargos"][:5])
                if len(v["cargos"]) > 5:
                    cargos += f" (+{len(v['cargos']) - 5})"
                print(f"    - {v['concurso']} | banca {v['banca']} | "
                      f"edital {v['edital_data']} | status {v['status']}")
                print(f"        cargos: {cargos}")
    if grupo in (None, "passados"):
        cab("PASSADOS (por ano/banca)")
        for ano, bancas in (visao["passados"] or {}).items():
            print(f"\n  {ano}")
            for banca, lista in bancas.items():
                print(f"    {banca}:")
                for p in lista:
                    cargos = ", ".join(p["cargos"][:5])
                    if len(p["cargos"]) > 5:
                        cargos += f" (+{len(p['cargos']) - 5})"
                    print(f"      - {p['concurso']} | {p['encerrado_em']} | "
                          f"{cargos}")
    print()
    e = visao["estatisticas"]
    print(f"estatísticas: {e['n_vigentes']} vigentes, {e['n_passados']} passados")


if __name__ == "__main__":
    main()
