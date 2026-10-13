# Arquivo:  lexcorpus/consumidor.py
# Função:   consumidor dos concursos aprovados no LexPubli (ADR-0007/0008).
#           Recebe contest.discovery.created e .updated, aplica a política de
#           coleta (politica_coleta.yaml) e responde com document.acquisition.*.
"""Consumidor do LexPubli: "o LexPubli avisa, o LexCorpus decide".

    python -m lexcorpus.consumidor                 # escuta a fila (longa duração)
    python -m lexcorpus.consumidor --arquivo ev.json   # processa um evento salvo
                                                       # (dev; sem broker)

Para cada concurso aprovado:
  1. idempotência: event_id já visto -> ignora
  2. catálogo: upsert em `vigentes` (chave contest_slug; guarda o contest_id)
  3. concurso ATUAL: banca validada + alvo resolvido -> watchlist automático
     (cron conforme a fase); senão -> awaiting_operator
  4. ACERVO (ADR-0008): concursos já coletados da mesma banca e carreira.
     Mesmo órgão e ano anterior -> previous; outro órgão -> related (até
     relacionados.max_por_alvo). completed na hora, sem coleta nova.
  5. certames ANTERIORES: buscador (ADR-0006) -> política -> resolvedor ->
     watchlist automático; o que sobrar -> awaiting_operator
  6. concurso já coletado antes -> completed na hora (um recrawl sem
     novidade não publicaria nada). Só na PRIMEIRA ligação ao concurso do
     LexPubli: repetir a ligação não repete a resposta.

contest.discovery.updated (a curadoria mudou o concurso; `changed` diz o quê):
  catálogo sempre; banca nova -> passo 3; status novo -> cron do atual;
  banca ou carreira nova -> passo 4. O buscador não roda de novo.

O completed das coletas novas sai do EventoRabbitPipeline, ao publicar o
concurso para o LexLearn (consulta aquisicao_lexpubli no StateStore).

Fila: lexcorpus.lexpubli.contests, ligada a lexpubli.events com
contest.discovery.*. Mensagem fora do contrato -> dead-letter
(lexcorpus.lexpubli.contests.dlq), nunca processada pela metade.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from . import contratos_lexpubli as contratos
from .alvos import INVENTARIO_FGV, Alvo, HttpGet, http_get_padrao, resolver_anterior, resolver_atual
from .catalogo import CATALOGO_PATH, Catalogo
from .respostas import Respondedor
from .statestore import StateStore

log = logging.getLogger("lexcorpus.consumidor")

RAIZ = Path(__file__).resolve().parent.parent
POLITICA_PADRAO = RAIZ / "politica_coleta.yaml"
WATCHLIST_AUTO_PADRAO = Path("state/watchlist.auto.yaml")
FILA = "lexcorpus.lexpubli.contests"
FILA_DLQ = "lexcorpus.lexpubli.contests.dlq"
DLX = "lexcorpus.lexpubli.dlx"


class ContratoInvalido(Exception):
    """Mensagem fora do contrato: vai para a dead-letter."""


@dataclass
class Contexto:
    store: StateStore
    catalogo: Catalogo
    politica: dict
    respondedor: Respondedor
    watchlist_auto: Path
    buscar: Callable[..., dict] | None = None      # buscador.buscar_concursos_anteriores
    http_get: HttpGet = http_get_padrao
    inventario_fgv: Path = INVENTARIO_FGV
    resumo: list[str] = field(default_factory=list)

    def anotar(self, msg: str) -> None:
        log.info(msg)
        self.resumo.append(msg)


def carregar_politica(path: str | Path = POLITICA_PADRAO) -> dict:
    dados = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    dados.setdefault("bancas_validadas", [])
    dados.setdefault("cron_por_status", {})
    ant = dados.setdefault("anteriores", {})
    ant.setdefault("ativo", False)
    ant.setdefault("max_certames", 3)
    ant.setdefault("exige_confirmacao_orgao", True)
    ant.setdefault("score_minimo", 4)
    ant.setdefault("cron", "30 6 * * 1")
    rel = dados.setdefault("relacionados", {})
    rel.setdefault("ativo", False)
    rel.setdefault("max_por_alvo", 5)
    var = dados.setdefault("varredura", {})       # ADR-0009 (lexcorpus.varredura)
    var.setdefault("ativo", False)
    var.setdefault("carreiras", [])
    var.setdefault("max_por_carreira_e_banca", 5)
    var.setdefault("cron", "45 5 * * 2")
    return dados


# ---------------------------------------------------------------------------
# processamento (puro: sem broker)
# ---------------------------------------------------------------------------

def processar(envelope: dict, ctx: Contexto) -> str:
    """Aplica a política a um evento. Devolve o resumo do que foi feito."""
    try:
        contratos.validar(envelope)
    except Exception as exc:
        raise ContratoInvalido(str(exc)) from exc
    tipo = envelope["event_type"]
    if tipo not in (contratos.TIPO_DESCOBERTA, contratos.TIPO_ATUALIZACAO):
        return f"ignorado: {tipo}"
    if ctx.store.evento_ja_recebido(envelope["event_id"]):
        return f"duplicado: {envelope['event_id']} já processado"

    ctx.resumo = []
    data = envelope["data"]
    corr = envelope["event_id"]
    banca = (data.get("exam_board") or {}).get("slug")

    _catalogar(data, envelope, ctx)
    if tipo == contratos.TIPO_DESCOBERTA:
        _tratar_atual(data, corr, banca, ctx)
        _tratar_acervo(data, corr, banca, ctx)
        _tratar_anteriores(data, corr, ctx)
    else:
        mudou = set(data["changed"])
        ctx.anotar("atualização: " + ", ".join(sorted(mudou)))
        if "exam_board" in mudou:
            _tratar_atual(data, corr, banca, ctx)
        elif "status" in mudou:
            _atualizar_cron(data, ctx)
        if mudou & {"exam_board", "career"}:
            _tratar_acervo(data, corr, banca, ctx)

    resumo = "; ".join(ctx.resumo)
    ctx.store.registrar_evento_recebido(envelope["event_id"], envelope["event_type"], resumo)
    return resumo


def _catalogar(data: dict, envelope: dict, ctx: Contexto) -> None:
    banca = data.get("exam_board") or {}
    ctx.catalogo.adicionar(
        organ=data["organ"]["name"],
        organ_slug=data["organ"]["slug"],
        edital_ano=data["year"],
        concurso=data["contest_slug"],
        concurso_rotulo=data["title"],
        banca=banca.get("slug"),
        banca_rotulo=banca.get("name"),
        cargos=[p["name"] for p in data.get("positions", [])],
        status="coletando" if banca else "aguardando_banca",
        observacao="via LexPubli (ADR-0007)",
        carreira=data.get("career"),
        data_prova=data.get("exam_date"),
        lexpubli={"contest_id": data["contest_id"], "event_id": envelope["event_id"],
                  "page_url": data["page_url"]},
    )
    ctx.anotar(f"catálogo: {data['contest_slug']}")


def _tratar_atual(data: dict, corr: str, banca: str | None, ctx: Contexto) -> None:
    contest_id = data["contest_id"]
    base = {"contest_id": contest_id, "correlation_id": corr, "relation": "current"}
    if not banca:
        ctx.respondedor.awaiting_operator(**base, reason="unknown_exam_board",
                                          detail="Concurso aprovado sem banca definida.")
        ctx.anotar("atual: sem banca -> operador")
        return
    if banca not in ctx.politica["bancas_validadas"]:
        ctx.respondedor.awaiting_operator(**base, reason="no_spider", banca=banca,
                                          detail=f"Banca {banca!r} fora de bancas_validadas.")
        ctx.anotar(f"atual: {banca} não validada -> operador")
        return
    alvo = resolver_atual(banca, data, http_get=ctx.http_get)
    if alvo is None:
        ctx.respondedor.awaiting_operator(
            **base, reason="unresolved_target", banca=banca,
            detail="Não foi possível achar o alvo do spider nas URLs oficiais do evento.")
        ctx.anotar(f"atual: alvo {banca} não resolvido -> operador")
        return
    cron = ctx.politica["cron_por_status"].get(data["status"], "0 7 * * *")
    _agendar(alvo, cron, "current", data, corr, data["year"], ctx)


def _tratar_acervo(data: dict, corr: str, banca: str | None, ctx: Contexto) -> None:
    """ADR-0008: o que já foi coletado serve ao alvo sem coleta nova. Mesma
    banca obrigatória (o DNA é por banca) e mesma carreira."""
    pol = ctx.politica["relacionados"]
    carreira = data.get("career")
    if not pol["ativo"]:
        ctx.anotar("acervo: desligado")
        return
    if not (banca and carreira):
        ctx.anotar("acervo: sem banca ou carreira")
        return
    organ = data["organ"]["slug"]
    relacionados = 0
    for e in ctx.catalogo.por_banca_e_carreira(banca, carreira):
        concurso, ano = e["concurso"], e.get("edital_ano")
        if ctx.store.contagem_vigentes(banca, concurso) is None:
            continue                                  # no catálogo, mas não coletado
        if e.get("organ_slug") == organ:
            if not (isinstance(ano, int) and ano < data["year"]):
                continue                              # o próprio alvo, ou um futuro
            relation = "previous"
        else:
            if relacionados >= pol["max_por_alvo"]:
                continue
            relacionados += 1
            relation = "related"
        _ligar_acervo(banca, concurso, relation, ano, data, corr, ctx)


def _ligar_acervo(banca: str, concurso: str, relation: str, ano: int | None,
                  data: dict, corr: str, ctx: Contexto) -> None:
    contest_id = data["contest_id"]
    if ctx.store.aquisicao_existe(banca, concurso, contest_id):
        return
    ctx.store.registrar_aquisicao(banca, concurso, contest_id, corr, relation, ano=ano)
    ctx.respondedor.completed(contest_id=contest_id, correlation_id=corr, relation=relation,
                              banca=banca, concurso=concurso,
                              contagem=ctx.store.contagem_vigentes(banca, concurso), ano=ano)
    ctx.anotar(f"{relation}: {banca}/{concurso} do acervo -> completed")


def _atualizar_cron(data: dict, ctx: Contexto) -> None:
    """O status mudou: o cron do concurso atual no watchlist automático
    segue a fase (cron_por_status)."""
    path = Path(ctx.watchlist_auto)
    if not path.exists():
        return
    cron = ctx.politica["cron_por_status"].get(data["status"], "0 7 * * *")
    dados = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = {"contest_id": data["contest_id"], "relation": "current"}
    alterados = [a for a in dados.get("alvos", []) if meta in a.get("lexpubli", [])]
    for a in alterados:
        a["cron"] = cron
    if alterados:
        _gravar_watchlist_auto(path, dados)
        ctx.anotar(f"atual: cron {cron!r} (status {data['status']})")


def _tratar_anteriores(data: dict, corr: str, ctx: Contexto) -> None:
    pol = ctx.politica["anteriores"]
    if not pol["ativo"] or ctx.buscar is None:
        ctx.anotar("anteriores: desligado")
        return
    contest_id = data["contest_id"]
    base = {"contest_id": contest_id, "correlation_id": corr, "relation": "previous"}
    cargo = (data.get("positions") or [{}])[0].get("name")
    try:
        resultado = ctx.buscar(data["organ"]["name"], cargo=cargo)
    except Exception as exc:  # motor de busca fora, captcha...: operador decide
        ctx.respondedor.awaiting_operator(**base, reason="below_threshold",
                                          detail=f"Buscador indisponível: {type(exc).__name__}: {exc}")
        ctx.anotar("anteriores: buscador falhou -> operador")
        return

    candidatas = _agrupar(resultado.get("candidatas") or [], ano_limite=data["year"])
    if not candidatas:
        ctx.respondedor.awaiting_operator(**base, reason="below_threshold", candidatas=[],
                                          detail="O buscador não achou certames anteriores.")
        ctx.anotar("anteriores: nenhuma candidata -> operador")
        return

    validadas = set(ctx.politica["bancas_validadas"])
    elegiveis = [c for c in candidatas
                 if c["exam_board_slug"] in validadas
                 and c["score"] >= pol["score_minimo"]
                 and (c["confirmed_by_organ_catalog"] or not pol["exige_confirmacao_orgao"])]
    if not elegiveis:
        motivo = ("no_spider" if all(c["exam_board_slug"] not in validadas for c in candidatas)
                  else "below_threshold")
        ctx.respondedor.awaiting_operator(**base, reason=motivo, candidatas=candidatas[:10],
                                          detail="Nenhuma candidata passou pela política.")
        ctx.anotar(f"anteriores: {len(candidatas)} candidata(s), nenhuma elegível ({motivo}) -> operador")
        return

    escolhidas, excedentes = elegiveis[:pol["max_certames"]], elegiveis[pol["max_certames"]:]
    nao_resolvidas = []
    for c in escolhidas:
        alvo = resolver_anterior(c["exam_board_slug"], data["organ"], c["year"],
                                 http_get=ctx.http_get, inventario_fgv=ctx.inventario_fgv)
        if alvo is None:
            nao_resolvidas.append(c)
            continue
        _agendar(alvo, pol["cron"], "previous", data, corr, c["year"], ctx)
    if nao_resolvidas:
        ctx.respondedor.awaiting_operator(
            **base, reason="unresolved_target", candidatas=nao_resolvidas,
            detail="Candidatas aprovadas pela política, mas sem alvo de spider identificado.")
        ctx.anotar(f"anteriores: {len(nao_resolvidas)} sem alvo -> operador")
    if excedentes:
        ctx.respondedor.awaiting_operator(
            **base, reason="volume_limit", candidatas=excedentes[:10],
            detail=f"Acima do limite de {pol['max_certames']} certames anteriores.")
        ctx.anotar(f"anteriores: {len(excedentes)} acima do limite -> operador")


def _agrupar(candidatas: list[dict], *, ano_limite: int) -> list[dict]:
    """Uma entrada por (banca, ano), só certames ANTERIORES ao anunciado,
    mais recentes primeiro. Formato do contrato (candidates)."""
    por_chave: dict[tuple, dict] = {}
    for c in candidatas:
        ano, banca = c.get("ano"), c.get("banca_slug")
        if not banca or not isinstance(ano, int) or ano >= ano_limite:
            continue
        atual = por_chave.setdefault((banca, ano), {
            "exam_board_slug": banca, "year": ano, "score": 0,
            "confirmed_by_organ_catalog": False})
        atual["score"] = max(atual["score"], c.get("score", 0))
        if (c.get("validacao") or {}).get("confirmado_por_orgao"):
            atual["confirmed_by_organ_catalog"] = True
    return sorted(por_chave.values(), key=lambda c: (-c["year"], -c["score"]))


def _agendar(alvo: Alvo, cron: str, relation: str, data: dict, corr: str,
             ano: int | None, ctx: Contexto) -> None:
    """Liga a coleta ao concurso do LexPubli e a coloca no watchlist automático.

    Já coletado antes -> completed na hora: um recrawl sem novidade não
    publicaria nada, e o LexPubli ficaria sem resposta.
    """
    nova = not ctx.store.aquisicao_existe(alvo.banca, alvo.concurso, data["contest_id"])
    ctx.store.registrar_aquisicao(alvo.banca, alvo.concurso, data["contest_id"], corr,
                                  relation, ano=ano)
    contagem = ctx.store.contagem_vigentes(alvo.banca, alvo.concurso)
    if contagem is not None and nova:
        ctx.respondedor.completed(contest_id=data["contest_id"], correlation_id=corr,
                                  relation=relation, banca=alvo.banca, concurso=alvo.concurso,
                                  contagem=contagem, ano=ano)
        ctx.anotar(f"{relation}: {alvo.banca}/{alvo.concurso} já no acervo -> completed")
    registrar_alvo_auto(ctx.watchlist_auto, alvo, cron,
                        {"contest_id": data["contest_id"], "relation": relation})
    ctx.anotar(f"{relation}: {alvo.banca}/{alvo.concurso} no watchlist automático ({alvo.origem})")


# ---------------------------------------------------------------------------
# watchlist automático
# ---------------------------------------------------------------------------

def registrar_alvo_auto(path: Path, alvo: Alvo, cron: str, meta: dict) -> None:
    """Upsert no watchlist automático (mesmo formato do watchlist.yaml).

    Arquivo GERADO, separado do manual: pode ser apagado e reconstruído a
    partir dos eventos. O manual continua soberano (o scheduler ignora o
    alvo automático que repete um manual).
    """
    path = Path(path)
    dados = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    dados.setdefault("version", 1)
    alvos = dados.setdefault("alvos", [])
    existente = next((a for a in alvos
                      if a.get("spider") == alvo.spider and a.get("params") == alvo.params), None)
    if existente is None:
        existente = {"banca": alvo.banca, "spider": alvo.spider, "params": dict(alvo.params),
                     "concurso": alvo.concurso, "cron": cron, "ativo": True,
                     "origem": "lexpubli", "lexpubli": []}
        alvos.append(existente)
    if meta not in existente.setdefault("lexpubli", []):
        existente["lexpubli"].append(meta)
    _gravar_watchlist_auto(path, dados)


def _gravar_watchlist_auto(path: Path, dados: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# GERADO pelo lexcorpus.consumidor (ADR-0007). Não edite: o\n"
                   "# watchlist.yaml manual é o lugar das decisões do operador.\n"
                   + yaml.dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# broker
# ---------------------------------------------------------------------------

def escutar(ctx_fabrica: Callable[[object], Contexto], url: str,
            exchange_lexpubli: str, exchange_resposta: str) -> None:
    """Loop de consumo. ack só depois de processar; contrato violado -> DLQ."""
    import pika

    conexao = pika.BlockingConnection(pika.URLParameters(url))
    canal = conexao.channel()
    canal.exchange_declare(exchange=exchange_lexpubli, exchange_type="topic", durable=True)
    canal.exchange_declare(exchange=exchange_resposta, exchange_type="topic", durable=True)
    # DLX próprio, não o exchange padrão: o usuário lexcorpus do broker do
    # LexPubli não tem escrita em amq.default (que alcançaria qualquer fila).
    canal.exchange_declare(exchange=DLX, exchange_type="direct", durable=True)
    canal.queue_declare(queue=FILA_DLQ, durable=True)
    canal.queue_bind(queue=FILA_DLQ, exchange=DLX, routing_key=FILA_DLQ)
    canal.queue_declare(queue=FILA, durable=True, arguments={
        "x-dead-letter-exchange": DLX, "x-dead-letter-routing-key": FILA_DLQ})
    canal.queue_bind(queue=FILA, exchange=exchange_lexpubli, routing_key="contest.discovery.*")
    canal.basic_qos(prefetch_count=1)  # um concurso por vez: o buscador é lento
    ctx = ctx_fabrica(canal)

    def ao_receber(ch, metodo, props, corpo):
        try:
            envelope = json.loads(corpo)
            log.info("recebido %s: %s", envelope.get("event_type"), processar(envelope, ctx))
            ch.basic_ack(metodo.delivery_tag)
        except (ContratoInvalido, json.JSONDecodeError) as exc:
            log.error("mensagem fora do contrato -> DLQ: %s", exc)
            ch.basic_nack(metodo.delivery_tag, requeue=False)
        except Exception:
            log.exception("falha ao processar -> DLQ (reprocessar a partir de lá)")
            ch.basic_nack(metodo.delivery_tag, requeue=False)

    canal.basic_consume(queue=FILA, on_message_callback=ao_receber)
    log.info("escutando %s (%s / contest.discovery.*)", FILA, exchange_lexpubli)
    try:
        canal.start_consuming()
    finally:
        if conexao.is_open:
            conexao.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arquivo", help="processa um evento JSON salvo (sem broker) e sai")
    ap.add_argument("--politica", default=os.environ.get("LEXCORPUS_POLITICA", POLITICA_PADRAO))
    ap.add_argument("--catalogo", default=os.environ.get("LEXCORPUS_CATALOGO", CATALOGO_PATH))
    ap.add_argument("--watchlist-auto", default=os.environ.get(
        "LEXCORPUS_WATCHLIST_AUTO", WATCHLIST_AUTO_PADRAO))
    ap.add_argument("--state-db", default=os.environ.get(
        "LEXCORPUS_STATE_DB", "state/lexcorpus_state.db"))
    ap.add_argument("--sem-buscador", action="store_true",
                    help="não busca certames anteriores (só o concurso anunciado)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)

    from . import buscador
    out_dir = os.environ.get("EVENTOS_OUT_DIR", "eventos_debug")
    exchange_resposta = os.environ.get("LEXPUBLI_RESPOSTA_EXCHANGE", "lexcorpus.events")

    def fabrica(canal) -> Contexto:
        return Contexto(
            store=StateStore(args.state_db),
            catalogo=Catalogo.com_semente(args.catalogo),
            politica=carregar_politica(args.politica),
            respondedor=Respondedor(channel=canal, exchange=exchange_resposta, out_dir=out_dir),
            watchlist_auto=Path(args.watchlist_auto),
            buscar=None if args.sem_buscador else buscador.buscar_concursos_anteriores,
        )

    if args.arquivo:
        envelope = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
        print(processar(envelope, fabrica(None)))
        return 0
    # O broker é o do LexPubli (LexPubli ADR-0005 §2a), nunca o do LexLearn:
    # por isso não há fallback para RABBIT_URL.
    url = os.environ.get("LEXPUBLI_RABBIT_URL", "")
    if not url:
        print("LEXPUBLI_RABBIT_URL vazia: use --arquivo para processar um evento salvo.",
              file=sys.stderr)
        return 2
    escutar(fabrica, url, os.environ.get("LEXPUBLI_EXCHANGE", "lexpubli.events"),
            exchange_resposta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
