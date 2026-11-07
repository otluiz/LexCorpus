# Arquivo:  tests/test_ingestao_manual.py
# Função:   ingestão manual por manifesto (lexcorpus/ingestao_manual.py): PDF
#           obtido fora do crawler entra com sidecar, StateStore e evento —
#           pelos mesmos pipelines do crawler. Casos reais: BACEN13 sem
#           metadado; o gabarito do BACEN13 copiado para o SERPRO13.
"""Rodar:  pytest tests/test_ingestao_manual.py -v"""
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from lexcorpus.ingestao_manual import executar, carregar_manifesto
from lexcorpus.statestore import StateStore

SIDECAR = Draft202012Validator(json.loads(Path("schema/sidecar.schema.json").read_text()))
EVENTO = Draft202012Validator(json.loads(Path("schema/evento.schema.json").read_text()))

GAB = b"%PDF-1.4\ngabarito todos os cargos BACEN13\n%%EOF\n"
PROVA = b"%PDF-1.4\n||BACEN13_002_04N800189||\n%%EOF\n"


def ambiente(tmp_path, versao="2.0"):
    return {"FILES_STORE": str(tmp_path / "store"), "RABBIT_ENABLED": False,
            "EVENTOS_OUT_DIR": str(tmp_path / "eventos"),
            "LEXCORPUS_STATE_DB": str(tmp_path / "state.db"),
            "LEXCORPUS_CONTRATO_VERSAO": versao}


def manifesto(tmp_path, concurso="bacen13_analista", arquivos=None, nome="m.yaml"):
    origem = tmp_path / "baixados"
    origem.mkdir(exist_ok=True)
    (origem / "gab_definitivo_todos_cargos-1.pdf").write_bytes(GAB)
    (origem / "bacen13_002_04.pdf").write_bytes(PROVA)
    m = {
        "banca": "cebraspe", "concurso": concurso,
        "fonte_url": "https://www.pciconcursos.com.br/",
        "rotulos": {"banca": "CESPE/UnB", "concurso": "BACEN 2013",
                    "cargos": {"analista_area_2": "Analista - Área 2"}},
        "arquivos": arquivos if arquivos is not None else [
            {"nome": "bacen13_002_04.pdf", "origem": "baixados/bacen13_002_04.pdf",
             "papel": "prova", "cargos": ["analista_area_2"], "caderno": "BACEN13_002_04"},
            {"nome": "gab_definitivo_todos_cargos-1.pdf",
             "origem": "baixados/gab_definitivo_todos_cargos-1.pdf",
             "papel": "gabarito_definitivo", "cargos": ["*"], "multi_cargo": True,
             "segmentos": [{"caderno": "BACEN13_001_01", "pagina_inicio": 1, "pagina_fim": 1},
                           {"caderno": "BACEN13_002_04", "pagina_inicio": 2, "pagina_fim": 2}]},
        ],
    }
    p = tmp_path / nome
    p.write_text(yaml.safe_dump(m, allow_unicode=True))
    return p


def pasta(tmp_path, concurso="bacen13_analista"):
    return tmp_path / "store" / "cebraspe" / concurso


# --- dry-run --------------------------------------------------------------------

def test_dry_run_nao_toca_em_nada(tmp_path):
    r = executar(manifesto(tmp_path), ambiente(tmp_path), gravar=False)
    assert r.erros == []
    assert len(r.plano) == 2
    assert not (tmp_path / "store").exists()
    assert not (tmp_path / "eventos").exists()
    assert not (tmp_path / "state.db").exists()


# --- gravar ---------------------------------------------------------------------

def test_gravar_v21(tmp_path):
    r = executar(manifesto(tmp_path), ambiente(tmp_path, "2.1"), gravar=True)
    assert r.erros == []
    p = pasta(tmp_path)
    assert (p / "bacen13_002_04.pdf").read_bytes() == PROVA
    sc = json.loads((p / "bacen13_002_04.pdf.meta.json").read_text())
    SIDECAR.validate(sc)
    assert sc["caderno"] == "BACEN13_002_04"
    assert sc["origem"]["metodo"] == "ingestao_manual"
    assert sc["rotulos"]["cargos"] == {"analista_area_2": "Analista - Área 2"}
    ev = json.loads((tmp_path / "eventos" / "bacen13_analista.json").read_text())
    EVENTO.validate(ev)
    assert ev["event"] == "concurso.disponivel"
    assert {a["nome"] for a in ev["arquivos"]} == {
        "bacen13_002_04.pdf", "gab_definitivo_todos_cargos-1.pdf"}
    with StateStore(tmp_path / "state.db") as st:
        assert set(st.carregar_concurso("cebraspe", "bacen13_analista")) == {
            "bacen13_002_04.pdf", "gab_definitivo_todos_cargos-1.pdf"}


def test_controle_pdf_ja_na_pasta_sem_origem(tmp_path):
    """Caso BACEN13 hoje: o PDF já está no lugar, só falta o metadado."""
    p = pasta(tmp_path)
    p.mkdir(parents=True)
    (p / "bacen13_002_04.pdf").write_bytes(PROVA)
    m = manifesto(tmp_path, arquivos=[
        {"nome": "bacen13_002_04.pdf", "papel": "prova", "cargos": ["analista_area_2"]}])
    r = executar(m, ambiente(tmp_path), gravar=True)
    assert r.erros == []
    SIDECAR.validate(json.loads((p / "bacen13_002_04.pdf.meta.json").read_text()))


def test_reexecucao_nao_publica_de_novo(tmp_path):
    amb = ambiente(tmp_path)
    executar(manifesto(tmp_path), amb, gravar=True)
    ev = tmp_path / "eventos" / "bacen13_analista.json"
    ev.unlink()
    r = executar(manifesto(tmp_path), amb, gravar=True)
    assert r.erros == []
    assert not ev.exists()


# --- recusas --------------------------------------------------------------------

def test_mesmo_arquivo_em_outro_concurso_recusado(tmp_path):
    """O caso real: o gabarito do BACEN13 posto também no SERPRO13."""
    amb = ambiente(tmp_path)
    assert executar(manifesto(tmp_path), amb, gravar=True).erros == []
    m = manifesto(tmp_path, concurso="serpro13_analista_ti", nome="s.yaml", arquivos=[
        {"nome": "gab_definitivo_todos_cargos-1.pdf",
         "origem": "baixados/gab_definitivo_todos_cargos-1.pdf",
         "papel": "gabarito_definitivo", "cargos": ["*"]}])
    r = executar(m, amb, gravar=True)
    assert any("bacen13_analista" in e for e in r.erros), r.erros
    assert not pasta(tmp_path, "serpro13_analista_ti").exists()


def test_nao_sobrescreve_conteudo_diferente(tmp_path):
    p = pasta(tmp_path)
    p.mkdir(parents=True)
    (p / "bacen13_002_04.pdf").write_bytes(b"%PDF outro conteudo")
    r = executar(manifesto(tmp_path), ambiente(tmp_path), gravar=True)
    assert any("bacen13_002_04.pdf" in e and "diferente" in e for e in r.erros), r.erros
    assert (p / "bacen13_002_04.pdf").read_bytes() == b"%PDF outro conteudo"
    assert not (p / "bacen13_002_04.pdf.meta.json").exists()


def test_duplicata_dentro_do_manifesto(tmp_path):
    m = manifesto(tmp_path, arquivos=[
        {"nome": "a.pdf", "origem": "baixados/bacen13_002_04.pdf", "papel": "prova", "cargos": ["x"]},
        {"nome": "b.pdf", "origem": "baixados/bacen13_002_04.pdf", "papel": "prova", "cargos": ["x"]}])
    r = executar(m, ambiente(tmp_path), gravar=True)
    assert any("a.pdf" in e and "b.pdf" in e for e in r.erros), r.erros
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize("arq, trecho", [
    ({"nome": "../x.pdf", "papel": "prova", "cargos": ["x"]}, "nome"),
    ({"nome": "x.pdf", "papel": "resposta", "cargos": ["x"]}, "papel"),
    ({"nome": "x.pdf", "papel": "prova", "cargos": ["Analista Área 2"]}, "cargos"),
    ({"nome": "x.pdf", "papel": "prova", "cargos": []}, "cargos"),
    ({"nome": "x.pdf", "papel": "prova", "cargos": ["x"], "caderno": "A B"}, "caderno"),
    ({"nome": "x.pdf", "papel": "prova", "cargos": ["x"], "desconhecido": 1}, "desconhecido"),
])
def test_manifesto_invalido(tmp_path, arq, trecho):
    r = executar(manifesto(tmp_path, arquivos=[arq]), ambiente(tmp_path), gravar=True)
    assert any(trecho in e for e in r.erros), r.erros
    assert not (tmp_path / "store").exists()


def test_origem_inexistente(tmp_path):
    m = manifesto(tmp_path, arquivos=[
        {"nome": "x.pdf", "origem": "baixados/nao_existe.pdf", "papel": "prova", "cargos": ["x"]}])
    r = executar(m, ambiente(tmp_path), gravar=False)
    assert any("nao_existe.pdf" in e for e in r.erros), r.erros


def test_manifesto_bacen13_do_repo_e_valido():
    """O manifesto real versionado passa na validação de forma."""
    m = carregar_manifesto(Path("manifestos/cebraspe/bacen13_analista.yaml"))
    assert m.erros == []
    assert m.concurso == "bacen13_analista"
