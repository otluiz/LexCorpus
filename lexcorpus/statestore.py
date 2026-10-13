# Arquivo:  lexcorpus/statestore.py
# Função:   memória do coletor entre crawls (BACKLOG [#A] "StateStore + ciclo
#           preliminar→definitivo", contrato §6.11/Caso D). SQLite pequeno no
#           volume de estado: registra o que cada concurso já publicou, para
#           que o EventoRabbitPipeline saiba distinguir "novo" (disponivel),
#           "mudou" (atualizado) e "nada mudou" (não publicar — idempotência
#           do lado produtor).
"""StateStore do LexCorpus — SQLite.

    arquivo_visto(banca, concurso, nome, papel, cargos, tipo_prova,
                  checksum_sha256, tamanho_bytes, vigente, substituido_por,
                  first_seen, last_seen)
    PK: (banca, concurso, nome)   -- cargos como JSON da lista de slugs

    Integração com o LexPubli (ADR-0007):
    evento_recebido(event_id, event_type, resultado, recebido_em)
        idempotência do consumidor: event_id já visto = ignorar
    aquisicao_lexpubli(banca, concurso, contest_id, correlation_id, relation,
                       ano, registrada_em)
        "este concurso do LexCorpus atende àquele concurso do LexPubli":
        o EventoRabbitPipeline consulta para emitir
        document.acquisition.completed ao publicar o concurso

Concorrência: o scheduler dispara crawls de bancas distintas em paralelo
(subprocessos). WAL + busy_timeout cobrem escritores concorrentes no mesmo
arquivo; rows de bancas diferentes nunca colidem na PK.

Caminho: LEXCORPUS_STATE_DB (default state/lexcorpus_state.db; no Docker o
volume /state). O arquivo e o diretório-pai são criados sob demanda.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS arquivo_visto (
    banca           TEXT NOT NULL,
    concurso        TEXT NOT NULL,
    nome            TEXT NOT NULL,
    papel           TEXT NOT NULL,
    cargos          TEXT NOT NULL,           -- JSON: ["geografia"] ou ["*"]
    tipo_prova      TEXT,
    checksum_sha256 TEXT NOT NULL,
    tamanho_bytes   INTEGER NOT NULL,
    vigente         INTEGER NOT NULL,        -- 1/0 (sqlite não tem bool)
    substituido_por TEXT,
    first_seen      TEXT NOT NULL,           -- ISO 8601 UTC
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (banca, concurso, nome)
);

CREATE TABLE IF NOT EXISTS evento_recebido (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    resultado   TEXT NOT NULL,               -- resumo do que o consumidor fez
    recebido_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aquisicao_lexpubli (
    banca          TEXT NOT NULL,
    concurso       TEXT NOT NULL,            -- slug da pasta no LexCorpus
    contest_id     TEXT NOT NULL,            -- id do concurso no LexPubli
    correlation_id TEXT NOT NULL,            -- event_id do contest.discovery.created
    relation       TEXT NOT NULL,            -- current | previous
    ano            INTEGER,                  -- ano do certame, se conhecido
    registrada_em  TEXT NOT NULL,
    PRIMARY KEY (banca, concurso, contest_id)
);
"""

PAPEIS = ("prova", "gabarito_preliminar", "gabarito_definitivo")

_COLS = ("banca", "concurso", "nome", "papel", "cargos", "tipo_prova",
         "checksum_sha256", "tamanho_bytes", "vigente", "substituido_por",
         "first_seen", "last_seen")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Wrapper fino de sqlite3 para o estado de arquivos vistos."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- leitura -------------------------------------------------------------

    def carregar_concurso(self, banca: str, concurso: str) -> dict[str, dict]:
        """Rows de um concurso, indexadas por nome. {} se nunca visto."""
        cur = self._conn.execute(
            f"SELECT {', '.join(_COLS)} FROM arquivo_visto"
            " WHERE banca = ? AND concurso = ?",
            (banca, concurso),
        )
        rows = {}
        for r in cur.fetchall():
            row = {c: r[c] for c in _COLS}
            row["cargos"] = json.loads(row["cargos"])
            row["vigente"] = bool(row["vigente"])
            rows[row["nome"]] = row
        return rows

    def buscar_por_checksum(self, checksum_sha256: str) -> list[dict]:
        """Onde este conteúdo já foi visto: [{banca, concurso, nome}, ...]."""
        cur = self._conn.execute(
            "SELECT banca, concurso, nome FROM arquivo_visto"
            " WHERE checksum_sha256 = ? ORDER BY banca, concurso, nome",
            (checksum_sha256,),
        )
        return [dict(r) for r in cur.fetchall()]

    # -- escrita ---------------------------------------------------------------

    def upsert_arquivos(self, arquivos: list[dict]) -> None:
        """Insere/atualiza rows vistas neste crawl (last_seen sempre avança).

        Cada dict precisa de: banca, concurso, nome, papel, cargos (lista),
        checksum_sha256, tamanho_bytes. Opcionais: tipo_prova, vigente
        (default True), substituido_por. O upsert NÃO sobrescreve
        vigente/substituido_por de um preliminar já arquivado — o
        arquivamento é via arquivar_preliminar.
        """
        agora = _agora()
        for a in arquivos:
            self._conn.execute(
                """
                INSERT INTO arquivo_visto
                    (banca, concurso, nome, papel, cargos, tipo_prova,
                     checksum_sha256, tamanho_bytes, vigente, substituido_por,
                     first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (banca, concurso, nome) DO UPDATE SET
                    papel           = excluded.papel,
                    cargos          = excluded.cargos,
                    tipo_prova      = excluded.tipo_prova,
                    checksum_sha256 = excluded.checksum_sha256,
                    tamanho_bytes   = excluded.tamanho_bytes,
                    last_seen       = excluded.last_seen
                """,
                (
                    a["banca"], a["concurso"], a["nome"], a["papel"],
                    json.dumps(a["cargos"], ensure_ascii=False),
                    a.get("tipo_prova"), a["checksum_sha256"],
                    a["tamanho_bytes"],
                    int(a.get("vigente", True)), a.get("substituido_por"),
                    agora, agora,
                ),
            )
        self._conn.commit()

    def arquivar_preliminar(self, banca: str, concurso: str, nome: str,
                            substituido_por: str) -> None:
        """Marca um preliminar como histórico (contrato §6.11)."""
        self._conn.execute(
            "UPDATE arquivo_visto SET vigente = 0, substituido_por = ?,"
            " last_seen = ? WHERE banca = ? AND concurso = ? AND nome = ?",
            (substituido_por, _agora(), banca, concurso, nome),
        )
        self._conn.commit()

    # -- integração LexPubli (ADR-0007) --------------------------------------

    def evento_ja_recebido(self, event_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM evento_recebido WHERE event_id = ?", (event_id,))
        return cur.fetchone() is not None

    def registrar_evento_recebido(self, event_id: str, event_type: str,
                                  resultado: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO evento_recebido"
            " (event_id, event_type, resultado, recebido_em) VALUES (?, ?, ?, ?)",
            (event_id, event_type, resultado, _agora()),
        )
        self._conn.commit()

    def registrar_aquisicao(self, banca: str, concurso: str, contest_id: str,
                            correlation_id: str, relation: str,
                            ano: int | None = None) -> None:
        """Liga um concurso do LexCorpus a um concurso do LexPubli. Idempotente:
        repetir só mantém a ligação (a primeira correlação vale)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO aquisicao_lexpubli"
            " (banca, concurso, contest_id, correlation_id, relation, ano, registrada_em)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (banca, concurso, contest_id, correlation_id, relation, ano, _agora()),
        )
        self._conn.commit()

    def aquisicao_existe(self, banca: str, concurso: str, contest_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM aquisicao_lexpubli WHERE banca = ? AND concurso = ?"
            " AND contest_id = ?", (banca, concurso, contest_id))
        return cur.fetchone() is not None

    def aquisicoes_de(self, banca: str, concurso: str) -> list[dict]:
        """Os concursos do LexPubli atendidos por este concurso do LexCorpus."""
        cur = self._conn.execute(
            "SELECT banca, concurso, contest_id, correlation_id, relation, ano"
            " FROM aquisicao_lexpubli WHERE banca = ? AND concurso = ?"
            " ORDER BY registrada_em",
            (banca, concurso),
        )
        return [dict(r) for r in cur.fetchall()]

    def contagem_vigentes(self, banca: str, concurso: str) -> dict[str, int] | None:
        """Arquivos VIGENTES por papel, ou None se o concurso nunca foi visto."""
        rows = self.carregar_concurso(banca, concurso)
        if not rows:
            return None
        contagem = dict.fromkeys(PAPEIS, 0)
        for row in rows.values():
            if row["vigente"] and row["papel"] in contagem:
                contagem[row["papel"]] += 1
        return contagem
