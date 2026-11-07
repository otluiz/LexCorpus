# Arquivo:  tests/test_consulta.py
# Função:   testes unitários de lexcorpus/consulta.py — CLI de consulta ao
#           storage. Monta um storage fake em tmp_path (sidecars + PDFs)
#           e valida os filtros (banca/concurso/cargo/papel, incluindo o
#           consolidado multi-cargo "*") e a cópia de PDF + sidecar.
"""Rodar:  pytest tests/test_consulta.py -v"""
import json
from types import SimpleNamespace

import pytest

from lexcorpus import consulta


# --- storage fake --------------------------------------------------------------

def _grava(store, banca, concurso, nome, papel, cargos, tipo_prova=None):
    """Grava um PDF mínimo + sidecar no layout do contrato ({banca}/{concurso}/)."""
    pasta = store / banca / concurso
    pasta.mkdir(parents=True, exist_ok=True)
    pdf = pasta / nome
    pdf.write_bytes(b"%PDF-fake")
    sidecar = {
        "schema_version": "2.0",
        "arquivo": nome,
        "papel": papel,
        "cargos": cargos,
        "banca": banca,
        "concurso": concurso,
    }
    if tipo_prova:
        sidecar["tipo_prova"] = tipo_prova
    (pasta / f"{nome}.meta.json").write_text(
        json.dumps(sidecar), encoding="utf-8")
    return pdf


@pytest.fixture
def store(tmp_path):
    s = tmp_path / "exams"
    _grava(s, "cesgranrio", "bnb0124", "bnb_prova_cargo1.pdf",
           "prova", ["analista_bancario_1"], "gabarito_1")
    _grava(s, "cesgranrio", "bnb0124", "bnb_gab_pre_cargo1.pdf",
           "gabarito_preliminar", ["analista_bancario_1"])
    _grava(s, "fgv", "cnu_2025", "cnu_gab_def.pdf",
           "gabarito_definitivo", ["*"])
    _grava(s, "fcc", "dpeba125", "dpeba_gab_def.pdf",
           "gabarito_definitivo", ["*"])
    return s


def _args(**kw):
    base = dict(banca=None, concurso=None, cargo=None, papel=None, copiar=None)
    return SimpleNamespace(**{**base, **kw})


def _nomes(store, args):
    return sorted(sc["arquivo"] for sc, _ in consulta.iter_arquivos(store)
                  if consulta.casa(sc, args))


# --- iter_arquivos -------------------------------------------------------------

def test_iter_arquivos_pareia_sidecar_com_pdf(store):
    pares = list(consulta.iter_arquivos(store))
    assert len(pares) == 4
    for sc, pdf in pares:
        assert pdf.suffix == ".pdf"
        assert pdf.is_file()
        assert sc["arquivo"] == pdf.name


# --- filtros -------------------------------------------------------------------

def test_sem_filtro_lista_tudo(store):
    assert len(_nomes(store, _args())) == 4


def test_filtro_banca_exato(store):
    assert _nomes(store, _args(banca="cesgranrio")) == [
        "bnb_gab_pre_cargo1.pdf", "bnb_prova_cargo1.pdf"]
    assert _nomes(store, _args(banca="cebraspe")) == []


def test_filtro_concurso_por_trecho(store):
    assert _nomes(store, _args(concurso="bnb")) == [
        "bnb_gab_pre_cargo1.pdf", "bnb_prova_cargo1.pdf"]


def test_filtro_papel_exato(store):
    assert _nomes(store, _args(papel="prova")) == ["bnb_prova_cargo1.pdf"]
    assert _nomes(store, _args(papel="gabarito_preliminar")) == [
        "bnb_gab_pre_cargo1.pdf"]


def test_papel_gabarito_abrange_preliminar_e_definitivo(store):
    assert _nomes(store, _args(papel="gabarito")) == [
        "bnb_gab_pre_cargo1.pdf", "cnu_gab_def.pdf", "dpeba_gab_def.pdf"]


def test_cargo_inclui_consolidado_multi_cargo(store):
    # "*" cobre qualquer cargo (contrato §5, caso C)
    assert _nomes(store, _args(cargo="analista_bancario_1")) == [
        "bnb_gab_pre_cargo1.pdf", "bnb_prova_cargo1.pdf",
        "cnu_gab_def.pdf", "dpeba_gab_def.pdf"]
    assert _nomes(store, _args(cargo="delegado")) == [
        "cnu_gab_def.pdf", "dpeba_gab_def.pdf"]


def test_filtros_combinam(store):
    assert _nomes(store, _args(banca="cesgranrio", papel="gabarito",
                               cargo="analista_bancario_1")) == [
        "bnb_gab_pre_cargo1.pdf"]


# --- main: storage ausente e cópia ---------------------------------------------

def test_main_retorna_2_quando_storage_nao_existe(tmp_path, capsys):
    rc = consulta.main(["--store", str(tmp_path / "inexistente")])
    assert rc == 2
    assert "storage não encontrado" in capsys.readouterr().err


def test_main_copia_pdf_e_sidecar(store, tmp_path):
    destino = tmp_path / "selecao"
    rc = consulta.main([
        "--store", str(store),
        "--banca", "cesgranrio", "--papel", "prova",
        "--copiar", str(destino),
    ])
    assert rc == 0
    assert (destino / "bnb_prova_cargo1.pdf").is_file()
    assert (destino / "bnb_prova_cargo1.pdf.meta.json").is_file()
    assert len(list(destino.iterdir())) == 2
