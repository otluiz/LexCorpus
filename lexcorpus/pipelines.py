"""Pipelines do LexCorpus — a camada de saída que materializa o contrato v2.0.

Cadeia (ordem em ITEM_PIPELINES):
  100  LexCorpusFilesPipeline  -> baixa o PDF para a pasta plana {banca}/{concurso}/
  200  SidecarPipeline         -> calcula SHA-256, escreve {nome}.meta.json atômico
  300  EventoRabbitPipeline    -> agrega por concurso e publica concurso.disponivel

Princípios do contrato honrados aqui:
  §6.1  salvar PDF + sidecar ANTES de publicar o evento
  §6.2  escrita atômica (.tmp -> rename)
  §6.3  checksum SHA-256 do arquivo final
  §6.9  pasta plana, sem subpastas
  §7    ponteiro é URI com esquema (file://)
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.pipelines.files import FilesPipeline

from .util import sha256_file, atomic_write_bytes


# ---------------------------------------------------------------------------
# 1. Download — FilesPipeline customizado
# ---------------------------------------------------------------------------
class LexCorpusFilesPipeline(FilesPipeline):
    """Baixa o PDF preservando o nome original numa pasta plana por concurso."""

    def file_path(self, request, response=None, info=None, *, item=None):
        adapter = ItemAdapter(item)
        banca = adapter["banca"]
        concurso = adapter["concurso"]
        nome = adapter.get("nome") or request.url.rsplit("/", 1)[-1]
        return f"{banca}/{concurso}/{nome}"


# ---------------------------------------------------------------------------
# 2. Sidecar — checksum + .meta.json atômico
# ---------------------------------------------------------------------------
class SidecarPipeline:
    def __init__(self, store_root: str):
        self.store_root = Path(store_root)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(store_root=crawler.settings.get("FILES_STORE"))

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if not adapter.get("files"):
            raise DropItem("sem arquivo baixado (FilesPipeline nao preencheu 'files')")

        rel_path = adapter["files"][0]["path"]
        abs_path = self.store_root / rel_path
        nome = Path(rel_path).name

        checksum = sha256_file(abs_path)
        tamanho = abs_path.stat().st_size

        adapter["nome"] = nome
        adapter["checksum_sha256"] = checksum
        adapter["tamanho_bytes"] = tamanho
        adapter["caminho_local"] = str(abs_path)

        sidecar = self._build_sidecar(adapter)
        destino = abs_path.parent / f"{nome}.meta.json"
        atomic_write_bytes(
            destino, json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8")
        )
        return item

    @staticmethod
    def _build_sidecar(adapter) -> dict:
        rotulos = {}
        if adapter.get("banca_rotulo"):
            rotulos["banca"] = adapter["banca_rotulo"]
        if adapter.get("concurso_rotulo"):
            rotulos["concurso"] = adapter["concurso_rotulo"]
        if adapter.get("cargos_rotulo"):
            rotulos["cargos"] = adapter["cargos_rotulo"]

        sc = {
            "schema_version": "2.0",
            "arquivo": adapter["nome"],
            "papel": adapter["papel"],
            "cargos": adapter["cargos"],
            "checksum_sha256": adapter["checksum_sha256"],
            "banca": adapter["banca"],
            "concurso": adapter["concurso"],
            "origem": {
                "fonte_url": adapter["fonte_url"],
                "raspado_em": datetime.now(timezone.utc).isoformat(),
                "scraper_versao": "lexcorpus/0.1",
            },
        }
        if adapter.get("tipo_prova") is not None:
            sc["tipo_prova"] = adapter["tipo_prova"]
        if adapter.get("multi_cargo"):
            sc["multi_cargo"] = True
        if adapter.get("segmentos"):
            sc["segmentos"] = adapter["segmentos"]
        if adapter.get("vigente") is not None:
            sc["vigente"] = adapter["vigente"]
        if adapter.get("substituido_por"):
            sc["substituido_por"] = adapter["substituido_por"]
        if rotulos:
            sc["rotulos"] = rotulos
        return sc


# ---------------------------------------------------------------------------
# 3. Evento — agrega por concurso e publica no RabbitMQ
# ---------------------------------------------------------------------------
class EventoRabbitPipeline:
    def __init__(self, settings):
        self.files_store = settings.get("FILES_STORE")
        self.uri_scheme = settings.get("STORAGE_URI_SCHEME", "file://")
        self.rabbit_enabled = settings.getbool("RABBIT_ENABLED", False)
        self.exchange = settings.get("RABBIT_EXCHANGE", "lexcorpus.events")
        self.routing_key = settings.get("RABBIT_ROUTING_DISPONIVEL", "concurso.disponivel")
        self.rabbit_url = settings.get("RABBIT_URL", "amqp://guest:guest@localhost:5672/")
        self.out_dir = settings.get("EVENTOS_OUT_DIR", "eventos_debug")
        self._por_concurso = {}
        self._conn = None
        self._channel = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def open_spider(self, spider):
        if not self.rabbit_enabled:
            return
        import pika
        params = pika.URLParameters(self.rabbit_url)
        self._conn = pika.BlockingConnection(params)
        self._channel = self._conn.channel()
        self._channel.exchange_declare(
            exchange=self.exchange, exchange_type="topic", durable=True
        )

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        chave = (adapter["banca"], adapter["concurso"])
        entrada = self._por_concurso.setdefault(
            chave,
            {
                "banca": adapter["banca"],
                "concurso": adapter["concurso"],
                "fonte_url": adapter.get("fonte_url"),
                "rotulos": {"cargos": {}},
                "arquivos": [],
            },
        )
        if adapter.get("banca_rotulo"):
            entrada["rotulos"]["banca"] = adapter["banca_rotulo"]
        if adapter.get("concurso_rotulo"):
            entrada["rotulos"]["concurso"] = adapter["concurso_rotulo"]
        if adapter.get("cargos_rotulo"):
            entrada["rotulos"]["cargos"].update(adapter["cargos_rotulo"])

        arq = {
            "nome": adapter["nome"],
            "papel": adapter["papel"],
            "cargos": adapter["cargos"],
            "checksum_sha256": adapter["checksum_sha256"],
            "tamanho_bytes": adapter["tamanho_bytes"],
        }
        for opc in ("tipo_prova", "multi_cargo", "segmentos", "vigente", "substituido_por"):
            if adapter.get(opc) is not None:
                arq[opc] = adapter[opc]
        entrada["arquivos"].append(arq)
        return item

    def close_spider(self, spider):
        for chave, dados in self._por_concurso.items():
            evento = self._build_evento(dados)
            self._publish(evento, spider)
        if self._conn is not None:
            self._conn.close()

    def _build_evento(self, dados):
       # pasta_uri = f"{self.uri_scheme}{Path(self.files_store).resolve()}/{dados['banca']}/{dados['concurso']}/"
        pasta_uri = f"{self.uri_scheme}{os.path.abspath(self.files_store)}/{dados['banca']}/{dados['concurso']}/"
        rot = {}
        if dados["rotulos"].get("banca"):
            rot["banca"] = dados["rotulos"]["banca"]
        if dados["rotulos"].get("concurso"):
            rot["concurso"] = dados["rotulos"]["concurso"]
        if dados["rotulos"].get("cargos"):
            rot["cargos"] = dados["rotulos"]["cargos"]
        evento = {
            "schema_version": "2.0",
            "event": "concurso.disponivel",
            "event_id": str(uuid.uuid4()),
            "produced_at": datetime.now(timezone.utc).isoformat(),
            "banca": dados["banca"],
            "concurso": dados["concurso"],
            "pasta_uri": pasta_uri,
            "arquivos": dados["arquivos"],
        }
        if dados.get("fonte_url"):
            evento["fonte_url"] = dados["fonte_url"]
        if rot:
            evento["extra"] = {"rotulos": rot}
        return evento

    def _publish(self, evento, spider):
        payload = json.dumps(evento, ensure_ascii=False).encode("utf-8")
        if self.rabbit_enabled and self._channel is not None:
            import pika
            self._channel.basic_publish(
                exchange=self.exchange,
                routing_key=self.routing_key,
                body=payload,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                ),
            )
            spider.logger.info(
                "evento publicado: %s (%s arquivos)",
                evento["concurso"], len(evento["arquivos"]),
            )
        else:
            out = Path(self.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{evento['concurso']}.json").write_bytes(payload)
            spider.logger.info("evento (dev) gravado: %s", evento["concurso"])
