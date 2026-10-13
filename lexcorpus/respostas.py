# Arquivo:  lexcorpus/respostas.py
# Função:   respostas do LexCorpus ao LexPubli (ADR-0007):
#           document.acquisition.{completed,awaiting_operator,failed}, no
#           envelope v1, validadas contra schema/lexpubli/v1/ antes de sair.
"""Respondedor: monta, valida e publica as respostas ao LexPubli.

Destino: o exchange `lexcorpus.events` (o mesmo do contrato v2.1), com
routing key = event_type. O LexLearn se liga com `concurso.#`, então não
recebe estas mensagens. Sem broker (RABBIT_ENABLED desligado), grava em
{EVENTOS_OUT_DIR}/respostas/ como os eventos de dev do pipeline.

As respostas NUNCA carregam caminho de storage (ADR-0002 §3).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import contratos_lexpubli as contratos
from .util import atomic_write_bytes

log = logging.getLogger("lexcorpus.respostas")


class Respondedor:
    def __init__(self, *, channel=None, exchange: str = "lexcorpus.events",
                 out_dir: str | Path = "eventos_debug"):
        self.channel = channel          # pika BlockingChannel ou None (dev)
        self.exchange = exchange
        self.out_dir = Path(out_dir) / "respostas"

    # -- montagem ------------------------------------------------------------

    def completed(self, *, contest_id: str, correlation_id: str, relation: str,
                  banca: str, concurso: str, contagem: dict[str, int],
                  ano: int | None = None, banca_rotulo: str | None = None,
                  cargos: list[str] | None = None,
                  fonte_url: str | None = None) -> dict:
        data = {
            "contest_id": contest_id,
            "relation": relation,
            "exam_board": _banca(banca, banca_rotulo),
            "lexcorpus_contest": concurso,
            "year": ano,
            "files": {p: int(contagem.get(p, 0)) for p in
                      ("prova", "gabarito_preliminar", "gabarito_definitivo")},
        }
        if cargos:
            data["positions"] = sorted({c for c in cargos if c != "*"})
            if not data["positions"]:
                del data["positions"]
        if fonte_url:
            data["source_url"] = fonte_url
        return self.publicar(contratos.TIPO_COMPLETED, data, correlation_id)

    def awaiting_operator(self, *, contest_id: str, correlation_id: str,
                          relation: str, reason: str, banca: str | None = None,
                          detail: str | None = None,
                          candidatas: list[dict] | None = None) -> dict:
        data = {"contest_id": contest_id, "relation": relation, "reason": reason,
                "exam_board": _banca(banca) if banca else None}
        if detail:
            data["detail"] = detail[:2000]
        if candidatas is not None:
            data["candidates"] = candidatas[:10]
        return self.publicar(contratos.TIPO_AWAITING, data, correlation_id)

    def failed(self, *, contest_id: str, correlation_id: str, relation: str,
               reason: str, retryable: bool, banca: str | None = None,
               concurso: str | None = None, detail: str | None = None) -> dict:
        data = {"contest_id": contest_id, "relation": relation, "reason": reason,
                "retryable": retryable,
                "exam_board": _banca(banca) if banca else None,
                "lexcorpus_contest": concurso}
        if detail:
            data["detail"] = detail[:2000]
        return self.publicar(contratos.TIPO_FAILED, data, correlation_id)

    # -- saída ---------------------------------------------------------------

    def publicar(self, event_type: str, data: dict, correlation_id: str) -> dict:
        envelope = contratos.montar_envelope(event_type, data, correlation_id=correlation_id)
        contratos.validar(envelope)  # nada sai fora do contrato
        corpo = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if self.channel is not None:
            import pika
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=event_type,
                body=corpo,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                    message_id=envelope["event_id"],
                    type=event_type,
                    app_id="lexcorpus",
                ),
            )
            log.info("resposta publicada: %s (contest %s)", event_type, data["contest_id"])
        else:
            destino = self.out_dir / f"{event_type}_{envelope['event_id']}.json"
            atomic_write_bytes(destino, corpo)
            log.info("resposta (dev) gravada: %s", destino)
        return envelope


def _banca(slug: str, rotulo: str | None = None) -> dict:
    banca = {"slug": slug}
    if rotulo:
        banca["name"] = rotulo
    return banca
