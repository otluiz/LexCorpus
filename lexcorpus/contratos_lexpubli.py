# Arquivo:  lexcorpus/contratos_lexpubli.py
# Função:   validação e montagem dos eventos dos contratos com o LexPubli
#           (ADR-0007): o envelope v1 e os schemas copiados em
#           schema/lexpubli/v1/. O dono dos schemas é o LexPubli.
"""Contratos LexPubli × LexCorpus — validar o que entra e o que sai.

    validar(envelope)                 -> levanta ValidationError/ValueError
    montar_envelope(tipo, data, ...)  -> dict pronto para publicar

Os schemas se referenciam por `$id` (https://lex/lexpubli/events/v1/...),
não por caminho de arquivo: o registro é montado a partir dos `$id`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .util import uuid7

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schema" / "lexpubli" / "v1"
PRODUCER = "lexcorpus/consumidor"

TIPO_DESCOBERTA = "contest.discovery.created"
TIPO_ATUALIZACAO = "contest.discovery.updated"
TIPO_COMPLETED = "document.acquisition.completed"
TIPO_AWAITING = "document.acquisition.awaiting_operator"
TIPO_FAILED = "document.acquisition.failed"


@lru_cache(maxsize=1)
def _registro() -> tuple[Registry, dict[str, dict]]:
    """Todos os schemas da pasta, indexados por $id e por event_type."""
    registro = Registry()
    por_tipo: dict[str, dict] = {}
    for arq in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        schema = json.loads(arq.read_text(encoding="utf-8"))
        registro = registro.with_resource(schema["$id"], Resource.from_contents(schema))
        nome = arq.name.removesuffix(".schema.json")
        if "." in nome:  # event_type tem ponto; envelope e common são só peças
            por_tipo[nome] = schema
    return registro, por_tipo


def carreiras() -> list[str]:
    """A lista fixa de carreiras da curadoria (ADR-0004 §2), lida do próprio
    contrato: a cópia do schema é a fonte, não uma constante daqui."""
    schema = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
    return list(schema["$defs"]["career"]["enum"])


def validar(envelope: dict) -> None:
    """Levanta jsonschema.ValidationError (contrato violado) ou ValueError
    (event_type sem contrato)."""
    registro, por_tipo = _registro()
    tipo = envelope.get("event_type") if isinstance(envelope, dict) else None
    if tipo not in por_tipo:
        raise ValueError(f"event_type sem contrato em {SCHEMAS_DIR}: {tipo!r}")
    validador = Draft202012Validator(por_tipo[tipo], registry=registro,
                                     format_checker=FormatChecker())
    erro = next(iter(sorted(validador.iter_errors(envelope), key=lambda e: e.path)), None)
    if erro is not None:
        raise erro


def montar_envelope(event_type: str, data: dict, *, correlation_id: str | None,
                    event_version: str = "1.0", producer: str = PRODUCER) -> dict:
    agora = datetime.now(timezone.utc)
    return {
        "event_id": str(uuid7()),
        "event_type": event_type,
        "event_version": event_version,
        "occurred_at": agora.strftime("%Y-%m-%dT%H:%M:%S.") + f"{agora.microsecond // 1000:03d}Z",
        "producer": producer,
        "correlation_id": correlation_id,
        "data": data,
    }
