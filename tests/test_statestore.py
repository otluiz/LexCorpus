# Arquivo:  tests/test_statestore.py
# Função:   testes do StateStore (lexcorpus/statestore.py) e do ciclo de vida
#           preliminar→definitivo (contrato §6.11 / Caso D) através do
#           EventoRabbitPipeline. Sem rede, sem RabbitMQ: PDFs fake em
#           tmp_path, eventos lidos do disco (RABBIT_ENABLED=False).
"""Rodar:  pytest tests/test_statestore.py -v"""
import json
import logging
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from lexcorpus.items import ArquivoItem
from lexcorpus.pipelines import EventoRabbitPipeline, SidecarPipeline
from lexcorpus.statestore import StateStore


# --- fakes (mesmo padrão do test_e2e.py) --------------------------------------

class FakeSettings(dict):
    def get(self, k, d=None): return super().get(k, d)
    def getbool(self, k, d=False): return bool(super().get(k, d))


class FakeSpider:
    logger = logging.getLogger("fake")


def build_downloaded_item(store_root: Path, nome, papel, cargos, **extra):
    """Cria um PDF de verdade no storage e devolve um item pós-FilesPipeline."""
    pdf_dir = store_root / "fgv" / "ifs_2024"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / nome).write_bytes(b"%PDF-1.4\nfake\n%%EOF\n")

    it = ArquivoItem()
    it["files"] = [{"path": f"fgv/ifs_2024/{nome}", "checksum": "x",
                    "url": f"https://ex/{nome}"}]
    it["fonte_url"] = f"https://exemplo.br/{nome}"
    it["banca"] = "fgv"
    it["concurso"] = "ifs_2024"
    it["banca_rotulo"] = "FGV"
    it["concurso_rotulo"] = "IF-Sergipe 2024"
    it["cargos_rotulo"] = {c: c.upper() for c in cargos}
    it["papel"] = papel
    it["cargos"] = cargos
    it["tipo_prova"] = extra.get("tipo_prova")
    it["multi_cargo"] = False
    it["vigente"] = extra.get("vigente", True)
    return it


def rodar_crawl(tmp_path: Path, itens):
    """Simula um crawl: sidecar + evento, com StateStore em tmp_path."""
    settings = FakeSettings({
        "FILES_STORE": str(tmp_path),
        "RABBIT_ENABLED": False,
        "EVENTOS_OUT_DIR": str(tmp_path / "eventos"),
        "LEXCORPUS_STATE_DB": str(tmp_path / "state" / "lexcorpus_state.db"),
    })
    spider = FakeSpider()
    sidecar_pipe = SidecarPipeline(store_root=str(tmp_path))
    evento_pipe = EventoRabbitPipeline(settings)
    evento_pipe.open_spider(spider)
    for it in itens:
        sidecar_pipe.process_item(it, spider)
        evento_pipe.process_item(it, spider)
    evento_pipe.close_spider(spider)


def evento_gravado(tmp_path: Path) -> dict | None:
    arq = tmp_path / "eventos" / "ifs_2024.json"
    return json.loads(arq.read_text()) if arq.exists() else None


EVENTO_SCHEMA = Draft202012Validator(
    json.loads(Path("schema/evento.schema.json").read_text()))


# --- StateStore puro -----------------------------------------------------------

def test_upsert_e_recarga(tmp_path):
    db = tmp_path / "s.db"
    with StateStore(db) as st:
        assert st.carregar_concurso("fgv", "ifs_2024") == {}
        st.upsert_arquivos([{
            "banca": "fgv", "concurso": "ifs_2024", "nome": "gab_pre.pdf",
            "papel": "gabarito_preliminar", "cargos": ["geografia"],
            "tipo_prova": "1", "checksum_sha256": "a" * 64,
            "tamanho_bytes": 100,
        }])

    # reabre do disco: outra conexão vê o que foi gravado
    with StateStore(db) as st:
        rows = st.carregar_concurso("fgv", "ifs_2024")
        assert list(rows) == ["gab_pre.pdf"]
        row = rows["gab_pre.pdf"]
        assert row["cargos"] == ["geografia"]
        assert row["vigente"] is True
        assert row["tipo_prova"] == "1"
        assert row["first_seen"] and row["last_seen"]

        st.arquivar_preliminar("fgv", "ifs_2024", "gab_pre.pdf", "gab_def.pdf")
        row = st.carregar_concurso("fgv", "ifs_2024")["gab_pre.pdf"]
        assert row["vigente"] is False
        assert row["substituido_por"] == "gab_def.pdf"


def test_upsert_nao_desfaz_arquivamento(tmp_path):
    """Re-run que vê o preliminar de novo não pode ressuscitar vigente=true."""
    with StateStore(tmp_path / "s.db") as st:
        base = {"banca": "fgv", "concurso": "ifs_2024", "nome": "gab_pre.pdf",
                "papel": "gabarito_preliminar", "cargos": ["geografia"],
                "checksum_sha256": "a" * 64, "tamanho_bytes": 100}
        st.upsert_arquivos([base])
        st.arquivar_preliminar("fgv", "ifs_2024", "gab_pre.pdf", "gab_def.pdf")
        st.upsert_arquivos([base])  # crawl seguinte vê o preliminar de novo
        row = st.carregar_concurso("fgv", "ifs_2024")["gab_pre.pdf"]
        assert row["vigente"] is False
        assert row["substituido_por"] == "gab_def.pdf"


def test_matching_cargos_e_tipo_prova():
    """_mesma_prova: interseção de cargos, '*' cobre tudo, tipo_prova desempata."""
    m = EventoRabbitPipeline._mesma_prova
    pre = {"cargos": ["geografia"], "tipo_prova": "1"}
    assert m(pre, {"cargos": ["geografia"], "tipo_prova": "1"})
    assert m(pre, {"cargos": ["geografia", "ingles"], "tipo_prova": "1"})
    assert m(pre, {"cargos": ["*"], "tipo_prova": None})
    assert not m(pre, {"cargos": ["ingles"], "tipo_prova": "1"})
    assert not m(pre, {"cargos": ["geografia"], "tipo_prova": "2"})
    # tipo_prova ausente de um lado não bloqueia
    assert m({**pre, "tipo_prova": None}, {"cargos": ["geografia"],
                                           "tipo_prova": "1"})


# --- ciclo de vida ponta a ponta (pipeline + estado) ----------------------------

def test_ciclo_preliminar_definitivo(tmp_path):
    pre = build_downloaded_item(tmp_path, "gab_pre_t1.pdf",
                                "gabarito_preliminar", ["geografia"],
                                tipo_prova="1")
    prova = build_downloaded_item(tmp_path, "prova_t1.pdf", "prova",
                                  ["geografia"], tipo_prova="1")

    # crawl 1: concurso nunca visto -> disponivel
    rodar_crawl(tmp_path, [prova, pre])
    ev = evento_gravado(tmp_path)
    assert ev["event"] == "concurso.disponivel"
    assert not list(EVENTO_SCHEMA.iter_errors(ev))
    assert {a["nome"] for a in ev["arquivos"]} == {"prova_t1.pdf",
                                                   "gab_pre_t1.pdf"}
    assert all(a.get("vigente", True) for a in ev["arquivos"])

    # crawl 2: idêntico -> NADA é publicado (evento anterior fica intacto)
    antes = (tmp_path / "eventos" / "ifs_2024.json").read_bytes()
    rodar_crawl(tmp_path, [prova, pre])
    assert (tmp_path / "eventos" / "ifs_2024.json").read_bytes() == antes

    # crawl 3: sai o definitivo -> atualizado, preliminar arquivado
    defi = build_downloaded_item(tmp_path, "gab_def_t1.pdf",
                                 "gabarito_definitivo", ["geografia"],
                                 tipo_prova="1")
    rodar_crawl(tmp_path, [prova, pre, defi])
    ev = evento_gravado(tmp_path)
    assert ev["event"] == "concurso.atualizado"
    assert not list(EVENTO_SCHEMA.iter_errors(ev))
    por_nome = {a["nome"]: a for a in ev["arquivos"]}
    assert set(por_nome) == {"prova_t1.pdf", "gab_pre_t1.pdf", "gab_def_t1.pdf"}
    assert por_nome["gab_pre_t1.pdf"]["vigente"] is False
    assert por_nome["gab_pre_t1.pdf"]["substituido_por"] == "gab_def_t1.pdf"
    assert por_nome["gab_def_t1.pdf"]["vigente"] is True

    # sidecar do preliminar no storage foi regravado (PDF intacto)
    sc = json.loads((tmp_path / "fgv" / "ifs_2024"
                     / "gab_pre_t1.pdf.meta.json").read_text())
    assert sc["vigente"] is False
    assert sc["substituido_por"] == "gab_def_t1.pdf"

    # crawl 4: tudo igual de novo -> silêncio (o arquivamento não repete)
    antes = (tmp_path / "eventos" / "ifs_2024.json").read_bytes()
    rodar_crawl(tmp_path, [prova, pre, defi])
    assert (tmp_path / "eventos" / "ifs_2024.json").read_bytes() == antes


def test_arquivo_novo_sem_transicao_vira_atualizado(tmp_path):
    prova = build_downloaded_item(tmp_path, "prova_t1.pdf", "prova",
                                  ["geografia"], tipo_prova="1")
    rodar_crawl(tmp_path, [prova])
    assert evento_gravado(tmp_path)["event"] == "concurso.disponivel"

    # reaplica a prova + uma prova tipo 2 que a banca publicou depois
    prova2 = build_downloaded_item(tmp_path, "prova_t2.pdf", "prova",
                                   ["geografia"], tipo_prova="2")
    rodar_crawl(tmp_path, [prova, prova2])
    ev = evento_gravado(tmp_path)
    assert ev["event"] == "concurso.atualizado"
    assert not list(EVENTO_SCHEMA.iter_errors(ev))
    assert {a["nome"] for a in ev["arquivos"]} == {"prova_t1.pdf",
                                                   "prova_t2.pdf"}
