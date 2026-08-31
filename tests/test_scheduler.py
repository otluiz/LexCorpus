# Arquivo:  tests/test_scheduler.py
# Função:   testes unitários de lexcorpus/scheduler.py — watchlist fake em
#           tmp_path, vencimento por cron (croniter), estado JSON, montagem
#           do comando e disparo com subprocess mockado (nada de crawl real).
"""Rodar:  pytest tests/test_scheduler.py -v"""
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lexcorpus import scheduler as sch


# --- fixtures ------------------------------------------------------------------

WATCHLIST = """\
version: 1

alvos:
  - banca: cebraspe
    spider: cebraspe
    params: {slug: prf_21}
    cron: "0 6 * * *"
    ativo: true
  - banca: fgv
    spider: fgv
    params: {slug: dataprev26}
    cron: "0 */6 * * *"
    ativo: true
  - banca: fcc
    spider: fcc
    params: {slug: dpeba125}
    cron: "0 6,18 * * *"
    ativo: false          # pausado: nunca vence

descoberta:
  - spider: fgv
    params: {descoberta: 1}
    cron: "5 5 * * *"
    ativo: true
"""

AGORA = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)  # terça 13h UTC


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "watchlist.yaml"
    p.write_text(WATCHLIST, encoding="utf-8")
    return p


@pytest.fixture
def alvos(watchlist):
    return sch.carregar_watchlist(watchlist)


# --- carregar_watchlist --------------------------------------------------------

def test_carrega_so_ativos(alvos):
    assert {a["spider"] for a in alvos} == {"cebraspe", "fgv"}
    assert len(alvos) == 3  # fcc está ativo: false


def test_grupo_e_chave(alvos):
    cebraspe = next(a for a in alvos if a["spider"] == "cebraspe")
    assert cebraspe["grupo"] == "cebraspe"            # serializa por banca
    assert cebraspe["chave"] == "alvos:cebraspe:slug=prf_21"
    desc = next(a for a in alvos if a["secao"] == "descoberta")
    assert desc["grupo"] == "fgv"                     # sem banca: usa spider
    assert desc["chave"] == "descoberta:fgv:descoberta=1"


def test_alvo_sem_spider_e_ignorado(tmp_path, caplog):
    p = tmp_path / "w.yaml"
    p.write_text('alvos:\n  - {banca: x, cron: "0 6 * * *", ativo: true}\n',
                 encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert sch.carregar_watchlist(p) == []
    assert "sem spider" in caplog.text


# --- vencimento ----------------------------------------------------------------

def test_sem_estado_tudo_vence(alvos):
    vencidos = sch.alvos_vencidos(alvos, {}, AGORA)
    assert len(vencidos) == 3
    for a in vencidos:
        assert a["tick"].tzinfo is not None


def test_estado_no_tick_nao_vence(alvos):
    # dispara tudo; na mesma hora nada vence de novo
    estado = {}
    for a in sch.alvos_vencidos(alvos, estado, AGORA):
        estado[a["chave"]] = a["tick"].isoformat()
    assert sch.alvos_vencidos(alvos, estado, AGORA) == []


def test_vence_quando_passa_o_proximo_tick(alvos):
    estado = {}
    for a in sch.alvos_vencidos(alvos, estado, AGORA):
        estado[a["chave"]] = a["tick"].isoformat()
    depois = AGORA + timedelta(hours=6, minutes=1)
    vencidos = sch.alvos_vencidos(alvos, estado, depois)
    # fgv (*/6) venceu de novo; cebraspe (só 6h) e descoberta (5h05) não
    assert [a["chave"] for a in vencidos] == ["alvos:fgv:slug=dataprev26"]


def test_cron_invalido_ou_ausente(alvos):
    alvos[0]["cron"] = None
    vencidos = sch.alvos_vencidos(alvos, {}, AGORA)
    assert alvos[0]["chave"] not in [a["chave"] for a in vencidos]


# --- estado JSON ---------------------------------------------------------------

def test_estado_roundtrip(tmp_path):
    p = tmp_path / "state" / "scheduler_state.json"
    sch.salvar_estado(p, {"a": "2026-08-25T06:00:00-03:00"})
    assert sch.carregar_estado(p) == {"a": "2026-08-25T06:00:00-03:00"}


def test_estado_inexistente_ou_corrompido(tmp_path):
    assert sch.carregar_estado(tmp_path / "nada.json") == {}
    p = tmp_path / "ruim.json"
    p.write_text("{não é json", encoding="utf-8")
    assert sch.carregar_estado(p) == {}


# --- disparo -------------------------------------------------------------------

def test_montar_comando(alvos):
    cebraspe = next(a for a in alvos if a["spider"] == "cebraspe")
    assert sch.montar_comando(cebraspe) == [
        "scrapy", "crawl", "cebraspe", "-a", "slug=prf_21"]


def _vencidos_de(alvos, *spiders):
    return [dict(a, tick=AGORA) for a in alvos if a["spider"] in spiders]


def test_sucesso_grava_estado(alvos, tmp_path):
    estado, running = {}, set()
    state = tmp_path / "s.json"
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        sch.disparar_vencidos(_vencidos_de(alvos, "cebraspe"), estado, state,
                              running)
    run.assert_called_once_with(
        ["scrapy", "crawl", "cebraspe", "-a", "slug=prf_21"])
    assert estado["alvos:cebraspe:slug=prf_21"] == AGORA.isoformat()
    assert json.loads(state.read_text()) == estado
    assert running == set()  # trava liberada


def test_falha_nao_grava_estado(alvos, tmp_path):
    estado, running = {}, set()
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        sch.disparar_vencidos(_vencidos_de(alvos, "cebraspe"), estado,
                              tmp_path / "s.json", running)
    assert estado == {}
    assert running == set()


def test_alvo_em_execucao_nao_dispara_de_novo(alvos, tmp_path):
    estado = {}
    running = {"alvos:cebraspe:slug=prf_21"}  # trava posta por outra passada
    with patch("subprocess.run") as run:
        sch.disparar_vencidos(_vencidos_de(alvos, "cebraspe"), estado,
                              tmp_path / "s.json", running)
    run.assert_not_called()


def test_dry_run_nao_executa_nem_grava(alvos, tmp_path, capsys):
    estado, running = {}, set()
    with patch("subprocess.run") as run:
        sch.disparar_vencidos(_vencidos_de(alvos, "cebraspe"), estado,
                              tmp_path / "s.json", running, dry_run=True)
    run.assert_not_called()
    assert estado == {}
    assert "scrapy crawl cebraspe -a slug=prf_21" in capsys.readouterr().out


def test_grupos_disparam_em_threads_separadas(alvos, tmp_path):
    # cebraspe e fgv são bancas distintas: cada uma vira uma thread
    threads_criadas = []
    real_thread = threading.Thread

    class Espiao(real_thread):
        def __init__(self, *a, **kw):
            threads_criadas.append(kw.get("name"))
            super().__init__(*a, **kw)

    with patch("subprocess.run") as run, \
         patch.object(sch.threading, "Thread", Espiao):
        run.return_value.returncode = 0
        sch.disparar_vencidos(_vencidos_de(alvos, "cebraspe", "fgv"), {},
                              tmp_path / "s.json", set())
    assert sorted(threads_criadas) == ["crawl-cebraspe", "crawl-fgv"]
    # 3 crawls: cebraspe na sua thread; fgv alvo + fgv descoberta seriados
    # na thread da fgv (mesmo grupo)
    assert run.call_count == 3
