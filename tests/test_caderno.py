# Arquivo:  tests/test_caderno.py
# Função:   contrato 2.1 — campo `caderno` (código que a banca imprime no PDF,
#           ex.: BACEN13_002_04) no arquivo e nos segmentos. Emitido só com
#           LEXCORPUS_CONTRATO_VERSAO=2.1; em 2.0 (default, enquanto o LexLearn
#           não tem o schema novo) sai limpo e válido no schema antigo.
"""Rodar:  pytest tests/test_caderno.py -v"""
import copy
import json
import logging
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from lexcorpus.items import ArquivoItem
from lexcorpus.pipelines import EventoRabbitPipeline, SidecarPipeline

EVENTO = Draft202012Validator(json.loads(Path("schema/evento.schema.json").read_text()))
SIDECAR = Draft202012Validator(json.loads(Path("schema/sidecar.schema.json").read_text()))
# o que o LexLearn valida hoje: evento v2.0 anterior ao `caderno`
LEGADO = Draft202012Validator(json.loads(Path("legado/evento.schema.json").read_text()))


class FakeSettings(dict):
    def get(self, k, d=None): return super().get(k, d)
    def getbool(self, k, d=False): return bool(super().get(k, d))


class FakeSpider:
    logger = logging.getLogger("fake")


def itens(root: Path):
    pasta = root / "cebraspe" / "bacen13"
    pasta.mkdir(parents=True)
    prova, gab = "bacen13_002_04.pdf", "gab_definitivo_todos_cargos-1.pdf"
    for nome in (prova, gab):
        (pasta / nome).write_bytes(b"%PDF-1.4\n" + nome.encode() + b"\n%%EOF\n")
    base = dict(banca="cebraspe", concurso="bacen13", fonte_url="https://ex/x.pdf",
                tipo_prova=None, vigente=True)
    p = ArquivoItem(**base, files=[{"path": f"cebraspe/bacen13/{prova}"}],
                    papel="prova", cargos=["analista_area_2"], multi_cargo=False,
                    caderno="BACEN13_002_04")
    g = ArquivoItem(**base, files=[{"path": f"cebraspe/bacen13/{gab}"}],
                    papel="gabarito_definitivo", cargos=["*"], multi_cargo=True,
                    segmentos=[
                        {"caderno": "BACEN13_001_01", "pagina_inicio": 1, "pagina_fim": 1},
                        {"caderno": "BACEN13_002_04", "pagina_inicio": 2, "pagina_fim": 2},
                        {"cargo": "analista_area_3", "caderno": "BACEN13_003_07",
                         "pagina_inicio": 3, "pagina_fim": 3},
                    ])
    return p, g


def rodar(tmp_path, versao=None):
    s = {"FILES_STORE": str(tmp_path), "RABBIT_ENABLED": False,
         "EVENTOS_OUT_DIR": str(tmp_path / "eventos"),
         "LEXCORPUS_STATE_DB": str(tmp_path / "state.db")}
    if versao:
        s["LEXCORPUS_CONTRATO_VERSAO"] = versao
    settings = FakeSettings(s)
    sidecar = SidecarPipeline.from_crawler(type("C", (), {"settings": settings})())
    evento = EventoRabbitPipeline(settings)
    evento.open_spider(FakeSpider())
    for it in itens(tmp_path):
        evento.process_item(sidecar.process_item(it, FakeSpider()), FakeSpider())
    evento.close_spider(FakeSpider())
    ev = json.loads((tmp_path / "eventos" / "bacen13.json").read_text())
    pasta = tmp_path / "cebraspe" / "bacen13"
    scs = {p.name: json.loads(p.read_text()) for p in pasta.glob("*.meta.json")}
    return ev, scs


def test_v21_emite_caderno(tmp_path):
    ev, scs = rodar(tmp_path, "2.1")
    EVENTO.validate(ev)
    assert ev["schema_version"] == "2.1"
    arqs = {a["nome"]: a for a in ev["arquivos"]}
    assert arqs["bacen13_002_04.pdf"]["caderno"] == "BACEN13_002_04"
    segs = arqs["gab_definitivo_todos_cargos-1.pdf"]["segmentos"]
    assert [s["caderno"] for s in segs] == ["BACEN13_001_01", "BACEN13_002_04", "BACEN13_003_07"]
    for sc in scs.values():
        SIDECAR.validate(sc)
        assert sc["schema_version"] == "2.1"
    assert scs["bacen13_002_04.pdf.meta.json"]["caderno"] == "BACEN13_002_04"


def test_controle_default_20_sem_caderno(tmp_path):
    """Default 2.0: nada de caderno; segmento só com caderno sai; o evento
    passa no schema que o LexLearn tem HOJE."""
    ev, scs = rodar(tmp_path)
    assert ev["schema_version"] == "2.0"
    LEGADO.validate(ev)
    EVENTO.validate(ev)
    assert "caderno" not in json.dumps(ev)
    arqs = {a["nome"]: a for a in ev["arquivos"]}
    assert arqs["gab_definitivo_todos_cargos-1.pdf"]["segmentos"] == [
        {"cargo": "analista_area_3", "pagina_inicio": 3, "pagina_fim": 3}]
    for sc in scs.values():
        SIDECAR.validate(sc)
        assert sc["schema_version"] == "2.0"
        assert "caderno" not in json.dumps(sc)


def test_versao_desconhecida_recusada(tmp_path):
    with pytest.raises(ValueError, match="LEXCORPUS_CONTRATO_VERSAO"):
        rodar(tmp_path, "3.0")


# --- schema --------------------------------------------------------------------

def _evento_min(**arq):
    return {"schema_version": "2.1", "event": "concurso.disponivel",
            "event_id": "7f1c2a4e-0000-4000-8000-000000000000",
            "produced_at": "2026-09-26T12:00:00+00:00",
            "banca": "cebraspe", "concurso": "bacen13",
            "pasta_uri": "file:///data/raw/exams/cebraspe/bacen13/",
            "arquivos": [{"nome": "x.pdf", "papel": "prova", "cargos": ["*"],
                          "checksum_sha256": "a" * 64, "tamanho_bytes": 1, **arq}]}


def test_schema_segmento_precisa_de_cargo_ou_caderno():
    assert EVENTO.is_valid(_evento_min(segmentos=[{"caderno": "BACEN13_001_01"}]))
    assert EVENTO.is_valid(_evento_min(segmentos=[{"cargo": "x"}]))
    assert not EVENTO.is_valid(_evento_min(segmentos=[{"pagina_inicio": 1}]))


def test_schema_caderno_sem_barra_nem_espaco():
    assert EVENTO.is_valid(_evento_min(caderno="578_PRF_001_01"))
    assert not EVENTO.is_valid(_evento_min(caderno="../x"))
    assert not EVENTO.is_valid(_evento_min(caderno="BACEN13 002"))
    assert not EVENTO.is_valid(_evento_min(caderno=""))
