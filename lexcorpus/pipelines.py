"""Pipelines do LexCorpus — a camada de saída que materializa o contrato v2.0.

Cadeia (ordem em ITEM_PIPELINES):
  100  LexCorpusFilesPipeline  -> baixa o PDF para a pasta plana {banca}/{concurso}/
  200  SidecarPipeline         -> calcula SHA-256, escreve {nome}.meta.json atômico
  300  EventoRabbitPipeline    -> agrega por concurso, compara com o StateStore
                                 (§6.11) e publica concurso.disponivel /
                                 concurso.atualizado — ou nada, se nada mudou

Princípios do contrato honrados aqui:
  §6.1  salvar PDF + sidecar ANTES de publicar o evento
  §6.2  escrita atômica (.tmp -> rename)
  §6.3  checksum SHA-256 do arquivo final
  §6.9  pasta plana, sem subpastas
  §6.11 ciclo preliminar->definitivo via StateStore (lexcorpus/statestore.py)
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

from .statestore import StateStore
from .util import sha256_file, atomic_write_bytes


# ---------------------------------------------------------------------------
# 1. Download — FilesPipeline customizado
# ---------------------------------------------------------------------------
class LexCorpusFilesPipeline(FilesPipeline):
    """Baixa o PDF preservando o nome original numa pasta plana por concurso.

    GATE ADMINISTRATIVO DE EXPIRAÇÃO: arquivo já baixado cuja idade passa de
    FILES_EXPIRES NÃO é rebaixado automaticamente (o default do Scrapy seria
    sobrescrever em cima). Provas são o acervo do LexLearn — re-download que
    substitui arquivo existente é decisão do administrador. O pipeline mantém
    o arquivo antigo, trata como "uptodate" e loga WARNING com a instrução.
    Para autorizar o re-download dos expirados num crawl:
        scrapy crawl X -s LEXCORPUS_REBAIXAR_EXPIRADOS=True
    """

    def file_path(self, request, response=None, info=None, *, item=None):
        adapter = ItemAdapter(item)
        banca = adapter["banca"]
        concurso = adapter["concurso"]
        nome = adapter.get("nome") or request.url.rsplit("/", 1)[-1]
        return f"{banca}/{concurso}/{nome}"

    def _onsuccess(self, result, request, info, path):
        file_info = super()._onsuccess(result, request, info, path)
        if file_info is not None or not result or not result.get("last_modified"):
            # uptodate normal, ou arquivo inexistente (download legítimo)
            return file_info
        # chegou aqui: o arquivo EXISTE mas está "expirado" — o Scrapy
        # rebaixaria e sobrescreveria. Segurar para o administrador decidir.
        if info.spider.settings.getbool("LEXCORPUS_REBAIXAR_EXPIRADOS", False):
            info.spider.logger.warning(
                "re-download autorizado (LEXCORPUS_REBAIXAR_EXPIRADOS): %s", path
            )
            return None
        info.spider.logger.warning(
            "arquivo expirado MANTIDO (decisão pendente do administrador): %s "
            "— para rebaixar e substituir, rode o crawl com "
            "-s LEXCORPUS_REBAIXAR_EXPIRADOS=True",
            path,
        )
        self.inc_stats("expirado_mantido")
        return {
            "url": request.url,
            "path": path,
            "checksum": result.get("checksum"),
            "status": "uptodate",
        }


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
        self.routing_disponivel = settings.get(
            "RABBIT_ROUTING_DISPONIVEL", "concurso.disponivel")
        self.routing_atualizado = settings.get(
            "RABBIT_ROUTING_ATUALIZADO", "concurso.atualizado")
        self.rabbit_url = settings.get("RABBIT_URL", "amqp://guest:guest@localhost:5672/")
        self.out_dir = settings.get("EVENTOS_OUT_DIR", "eventos_debug")
        self.state_db = settings.get("LEXCORPUS_STATE_DB", "state/lexcorpus_state.db")
        self._por_concurso = {}
        self._store = None        # StateStore, aberto lazy em open_spider
        self._conn = None
        self._channel = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def open_spider(self, spider):
        self._store = StateStore(self.state_db)
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
        try:
            for chave, dados in self._por_concurso.items():
                self._fechar_concurso(dados, spider)
        finally:
            if self._store is not None:
                self._store.close()
            if self._conn is not None:
                self._conn.close()

    # -- diff contra o StateStore (§6.11) -------------------------------------

    def _fechar_concurso(self, dados, spider):
        """Decide o destino do concurso: disponivel / atualizado / silêncio.

        - concurso nunca visto      -> concurso.disponivel (como sempre foi)
        - arquivos novos/mudados ou
          preliminar arquivado       -> concurso.atualizado (lista completa)
        - nada mudou                 -> não publica nada (idempotência do
                                        produtor: re-run diário é gratuito)
        """
        banca, concurso = dados["banca"], dados["concurso"]
        atuais = {a["nome"]: a for a in dados["arquivos"]}
        anteriores = self._store.carregar_concurso(banca, concurso)

        if not anteriores:
            self._store.upsert_arquivos(
                [self._row(banca, concurso, a) for a in dados["arquivos"]])
            evento = self._build_evento(dados, "concurso.disponivel",
                                        dados["arquivos"])
            self._publish(evento, spider)
            return

        # arquivos novos ou com conteúdo alterado (checksum mudou)
        novos = [n for n, a in atuais.items()
                 if n not in anteriores
                 or anteriores[n]["checksum_sha256"] != a["checksum_sha256"]]

        # transições preliminar -> definitivo (Caso D do contrato)
        transicoes = self._detectar_transicoes(atuais, anteriores)

        if not novos and not transicoes:
            self._store.upsert_arquivos(
                [self._row(banca, concurso, a) for a in dados["arquivos"]])
            spider.logger.info("sem mudanças, nada publicado: %s", concurso)
            return

        # arquivar os preliminares substituídos (estado + sidecar no storage)
        for nome_pre, nome_def in transicoes.items():
            self._store.arquivar_preliminar(banca, concurso, nome_pre, nome_def)
            self._regravar_sidecar_arquivado(banca, concurso, nome_pre,
                                             nome_def, spider)
        self._store.upsert_arquivos(
            [self._row(banca, concurso, a) for a in dados["arquivos"]])

        # evento carrega a lista COMPLETA do concurso (schema, linha 50):
        # crawl atual ∪ rows do estado ausentes deste crawl (ex.: preliminar
        # que a banca tirou do ar), com os overrides de vigente aplicados
        completa = list(dados["arquivos"])
        for a in completa:
            if a["nome"] in transicoes:
                a["vigente"] = False
                a["substituido_por"] = transicoes[a["nome"]]
        for nome, row in anteriores.items():
            if nome in atuais:
                continue
            completa.append({
                "nome": nome,
                "papel": row["papel"],
                "cargos": row["cargos"],
                "checksum_sha256": row["checksum_sha256"],
                "tamanho_bytes": row["tamanho_bytes"],
                "vigente": False if nome in transicoes else row["vigente"],
                "substituido_por": transicoes.get(nome) or row["substituido_por"],
            })
        evento = self._build_evento(dados, "concurso.atualizado", completa)
        self._publish(evento, spider)
        spider.logger.info(
            "concurso atualizado: %s (%d novos, %d preliminares arquivados)",
            concurso, len(novos), len(transicoes))

    @staticmethod
    def _row(banca, concurso, arq):
        """Arquivo agregado pelo pipeline -> row do StateStore."""
        return {
            "banca": banca, "concurso": concurso, "nome": arq["nome"],
            "papel": arq["papel"], "cargos": arq["cargos"],
            "tipo_prova": arq.get("tipo_prova"),
            "checksum_sha256": arq["checksum_sha256"],
            "tamanho_bytes": arq["tamanho_bytes"],
            "vigente": arq.get("vigente", True),
            "substituido_por": arq.get("substituido_por"),
        }

    @staticmethod
    def _mesma_prova(pre_row, def_arq) -> bool:
        """O definitivo cobre o preliminar? Interseção de cargos (["*"] cobre
        tudo) + tipo_prova igual quando ambos declaram."""
        cargos_pre, cargos_def = set(pre_row["cargos"]), set(def_arq["cargos"])
        if "*" not in cargos_pre and "*" not in cargos_def \
                and not (cargos_pre & cargos_def):
            return False
        tp_pre, tp_def = pre_row["tipo_prova"], def_arq.get("tipo_prova")
        return tp_pre is None or tp_def is None or tp_pre == tp_def

    def _detectar_transicoes(self, atuais, anteriores) -> dict:
        """{nome do preliminar: nome do definitivo} a arquivar neste crawl."""
        definitivos = [a for a in atuais.values()
                       if a["papel"] == "gabarito_definitivo"]
        transicoes = {}
        for nome, row in anteriores.items():
            if row["papel"] != "gabarito_preliminar" or not row["vigente"]:
                continue
            for d in definitivos:
                if self._mesma_prova(row, d):
                    transicoes[nome] = d["nome"]
                    break
        return transicoes

    def _regravar_sidecar_arquivado(self, banca, concurso, nome_pre,
                                    nome_def, spider):
        """Atualiza o .meta.json do preliminar no storage (só metadado — o
        PDF nunca é tocado; histórico é propriedade, não lugar — Caso D)."""
        sidecar = (Path(self.files_store) / banca / concurso
                   / f"{nome_pre}.meta.json")
        try:
            dados = json.loads(sidecar.read_text(encoding="utf-8"))
        except OSError as exc:
            spider.logger.warning("sidecar do preliminar ilegível (%s): %s",
                                  sidecar, exc)
            return
        dados["vigente"] = False
        dados["substituido_por"] = nome_def
        atomic_write_bytes(
            sidecar, json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8"))

    def _build_evento(self, dados, tipo, arquivos):
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
            "event": tipo,
            "event_id": str(uuid.uuid4()),
            "produced_at": datetime.now(timezone.utc).isoformat(),
            "banca": dados["banca"],
            "concurso": dados["concurso"],
            "pasta_uri": pasta_uri,
            "arquivos": arquivos,
        }
        if dados.get("fonte_url"):
            evento["fonte_url"] = dados["fonte_url"]
        if rot:
            evento["extra"] = {"rotulos": rot}
        return evento

    def _publish(self, evento, spider):
        payload = json.dumps(evento, ensure_ascii=False).encode("utf-8")
        routing_key = (self.routing_disponivel
                       if evento["event"] == "concurso.disponivel"
                       else self.routing_atualizado)
        if self.rabbit_enabled and self._channel is not None:
            import pika
            self._channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=payload,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                ),
            )
            spider.logger.info(
                "evento publicado: %s %s (%s arquivos)",
                evento["event"], evento["concurso"], len(evento["arquivos"]),
            )
        else:
            out = Path(self.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{evento['concurso']}.json").write_bytes(payload)
            spider.logger.info("evento (dev) gravado: %s (%s)",
                               evento["concurso"], evento["event"])
