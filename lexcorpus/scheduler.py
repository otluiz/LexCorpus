# Arquivo:  lexcorpus/scheduler.py
# Função:   a automação do LexCorpus (BACKLOG [#A]). Lê o watchlist.yaml,
#           calcula os alvos vencidos pelo cron de cada um e dispara
#           `scrapy crawl <spider> -a ...` — sequencial DENTRO de cada
#           banca, bancas distintas em paralelo. Registra o último disparo
#           por alvo num arquivo de estado JSON (o StateStore completo, com
#           ciclo preliminar→definitivo, é outro item do backlog).
"""Scheduler de coletas do LexCorpus.

USO:
    python -m lexcorpus.scheduler --once          # roda os vencidos e sai
    python -m lexcorpus.scheduler --loop 60       # verifica a cada 60s
    python -m lexcorpus.scheduler --once --dry-run  # só imprime os comandos

Decisões de desenho:
  - Disparo via SUBPROCESSO (`scrapy crawl`), não CrawlerProcess em-processo:
    um crawl que trava ou explode não derruba o scheduler nem os das outras
    bancas, e cada crawl nasce com reactor limpo. É o mesmo comando que o
    operador rodaria na mão — nada escondido.
  - Vencimento: compara o tick de cron mais recente (croniter) com o último
    tick disparado, gravado no estado JSON. Alvo que falha (exit != 0) NÃO
    atualiza o estado → é tentado de novo na próxima verificação.
  - Overlap: um alvo ainda em execução não é disparado de novo (trava por
    chave de alvo); bancas distintas rodam em threads separadas.
  - Estado: --state-file > LEXCORPUS_STATE_FILE > ./state/scheduler_state.json
    (no Docker, apontar para o volume /state — ver backlog Docker).

O watchlist é a config; este módulo é só o relógio + gatilho.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from croniter import croniter

log = logging.getLogger("lexcorpus.scheduler")

WATCHLIST_DEFAULT = Path(__file__).resolve().parent.parent / "watchlist.yaml"
STATE_DEFAULT = Path("state/scheduler_state.json")

# ---------------------------------------------------------------------------
# watchlist + estado
# ---------------------------------------------------------------------------


def carregar_watchlist(path: Path) -> list[dict]:
    """Devolve a lista plana de alvos ATIVOS (seções alvos + descoberta).

    Cada alvo ganha: secao ("alvos"|"descoberta"), grupo (banca ou spider —
    define a serialização) e chave (identidade estável para o estado).
    """
    dados = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    alvos = []
    for secao in ("alvos", "descoberta"):
        for entrada in dados.get(secao) or []:
            if not entrada.get("ativo"):
                continue
            spider = entrada.get("spider")
            if not spider:
                log.warning("alvo sem spider ignorado: %s", entrada)
                continue
            params = dict(entrada.get("params") or {})
            grupo = entrada.get("banca") or spider
            chave = _chave(secao, spider, params)
            alvos.append({
                "secao": secao, "grupo": grupo, "chave": chave,
                "spider": spider, "params": params,
                "cron": entrada.get("cron"),
                "rotulo": _rotulo(entrada),
            })
    return alvos


def _chave(secao: str, spider: str, params: dict) -> str:
    pares = ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{secao}:{spider}:{pares}"


def _rotulo(entrada: dict) -> str:
    partes = [entrada.get("banca"), entrada.get("concurso")]
    return "/".join(p for p in partes if p) or entrada.get("spider", "?")


def carregar_estado(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("estado ilegível (%s): %s — recomeçando vazio", path, exc)
        return {}


def salvar_estado(path: Path, estado: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)  # escrita atômica (mesmo padrão do util.py)


# ---------------------------------------------------------------------------
# vencimento
# ---------------------------------------------------------------------------


def tick_vencido(cron: str, agora: datetime) -> datetime | None:
    """Tick de cron mais recente até `agora` (inclusive)."""
    if not cron:
        return None
    return croniter(cron, agora).get_prev(datetime)


def alvos_vencidos(alvos: list[dict], estado: dict,
                   agora: datetime) -> list[dict]:
    """Alvos cujo último tick de cron ainda não foi disparado com sucesso."""
    vencidos = []
    for alvo in alvos:
        tick = tick_vencido(alvo["cron"], agora)
        if tick is None:
            log.warning("alvo %s sem cron válido — ignorado", alvo["chave"])
            continue
        ultimo = estado.get(alvo["chave"])
        if ultimo is None or datetime.fromisoformat(ultimo) < tick:
            alvo = dict(alvo, tick=tick)
            vencidos.append(alvo)
    return vencidos


# ---------------------------------------------------------------------------
# disparo
# ---------------------------------------------------------------------------


def montar_comando(alvo: dict) -> list[str]:
    cmd = ["scrapy", "crawl", alvo["spider"]]
    for chave, valor in sorted(alvo["params"].items()):
        cmd += ["-a", f"{chave}={valor}"]
    return cmd


def disparar_grupo(grupo: str, alvos: list[dict], estado: dict,
                   state_path: Path, running: set, lock: threading.Lock,
                   dry_run: bool = False) -> None:
    """Roda os alvos de UM grupo (banca) em sequência."""
    for alvo in alvos:
        cmd = montar_comando(alvo)
        if dry_run:
            print(f"[dry-run] {alvo['rotulo']}: {' '.join(cmd)}")
            continue
        log.info("disparando %s: %s", alvo["rotulo"], " ".join(cmd))
        try:
            rc = subprocess.run(cmd).returncode
        except OSError as exc:
            log.error("falha ao lançar %s: %s", alvo["rotulo"], exc)
            rc = 127
        with lock:
            running.discard(alvo["chave"])
            if rc == 0:
                estado[alvo["chave"]] = alvo["tick"].isoformat()
                salvar_estado(state_path, estado)
                log.info("ok: %s", alvo["rotulo"])
            else:
                log.error("falhou (exit %d): %s — nova tentativa na próxima "
                          "verificação", rc, alvo["rotulo"])


def disparar_vencidos(vencidos: list[dict], estado: dict, state_path: Path,
                      running: set, dry_run: bool = False) -> None:
    """Agrupa por banca e dispara: grupos em paralelo, alvos em sequência."""
    grupos: dict[str, list[dict]] = {}
    for alvo in vencidos:
        if alvo["chave"] in running:
            log.info("já em execução, pulando: %s", alvo["rotulo"])
            continue
        running.add(alvo["chave"])
        grupos.setdefault(alvo["grupo"], []).append(alvo)
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=disparar_grupo,
            args=(grupo, alvos, estado, state_path, running, lock, dry_run),
            name=f"crawl-{grupo}", daemon=True,
        )
        for grupo, alvos in grupos.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def verificar_spiders(alvos: list[dict]) -> None:
    """Avisa (uma vez) se algum alvo ativo aponta para spider inexistente."""
    try:
        out = subprocess.run(["scrapy", "list"], capture_output=True,
                             text=True, timeout=30)
        existentes = set(out.stdout.split())
    except (OSError, subprocess.TimeoutExpired):
        return  # sem scrapy no PATH — o erro aparecerá no disparo
    for alvo in alvos:
        if alvo["spider"] not in existentes:
            log.warning("alvo ativo com spider inexistente: %s (%s)",
                        alvo["spider"], alvo["rotulo"])


def uma_passada(args, running: set) -> int:
    alvos = carregar_watchlist(args.watchlist)
    estado = carregar_estado(args.state_file)
    agora = datetime.now(timezone.utc).astimezone()  # tz local do container
    vencidos = alvos_vencidos(alvos, estado, agora)
    if not vencidos:
        log.info("nenhum alvo vencido (%d ativos)", len(alvos))
        return 0
    log.info("%d alvo(s) vencido(s): %s", len(vencidos),
             ", ".join(a["rotulo"] for a in vencidos))
    disparar_vencidos(vencidos, estado, args.state_file, running,
                      dry_run=args.dry_run)
    return len(vencidos)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lexcorpus.scheduler",
        description="Dispara `scrapy crawl` para os alvos do watchlist "
                    "cujo cron venceu.",
    )
    ap.add_argument("--watchlist", type=Path, default=Path(
        os.environ.get("LEXCORPUS_WATCHLIST", WATCHLIST_DEFAULT)))
    ap.add_argument("--state-file", type=Path, default=Path(
        os.environ.get("LEXCORPUS_STATE_FILE", STATE_DEFAULT)))
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--once", action="store_true",
                      help="roda os vencidos uma vez e sai (default; ideal "
                           "para cron/supercronic no container)")
    modo.add_argument("--loop", type=int, metavar="SEG",
                      help="verifica a cada SEG segundos, sem sair")
    ap.add_argument("--dry-run", action="store_true",
                    help="só imprime os comandos, sem disparar nem gravar estado")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    running: set = set()
    alvos = carregar_watchlist(args.watchlist)
    verificar_spiders(alvos)

    if not args.loop:
        uma_passada(args, running)
        return 0

    log.info("loop a cada %ds — Ctrl+C para sair", args.loop)
    import time
    try:
        while True:
            uma_passada(args, running)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        log.info("interrompido pelo operador")
        return 0


if __name__ == "__main__":
    sys.exit(main())
