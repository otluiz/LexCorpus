# Arquivo:  tests/test_consumidor.py
# Função:   testes do consumidor do LexPubli (ADR-0007): política de coleta,
#           resolvedores de alvo, watchlist automático, respostas
#           document.acquisition.* e o gancho no EventoRabbitPipeline. Sem
#           rede e sem RabbitMQ: buscador e GETs são fakes; as respostas vão
#           para arquivos e são validadas contra schema/lexpubli/v1/.
"""Rodar:  pytest tests/test_consumidor.py -v"""
import copy
import json
import logging
from pathlib import Path

import pytest
import yaml

from lexcorpus import consumidor as cons
from lexcorpus import scheduler as sch
from lexcorpus.alvos import resolver_anterior, resolver_atual
from lexcorpus.catalogo import Catalogo
from lexcorpus.contratos_lexpubli import validar
from lexcorpus.items import ArquivoItem
from lexcorpus.pipelines import EventoRabbitPipeline, SidecarPipeline
from lexcorpus.respostas import Respondedor
from lexcorpus.statestore import StateStore

EXEMPLO = json.loads(Path("schema/lexpubli/v1/examples/contest.discovery.created.json")
                     .read_text(encoding="utf-8"))


# --- fakes -------------------------------------------------------------------

def sem_rede(url):
    raise AssertionError(f"GET inesperado: {url}")


def buscar_vazio(organ, cargo=None):
    return {"candidatas": []}


def candidata(banca, ano, score=5, confirmado=True):
    return {"banca_slug": banca, "banca_rotulo": banca.upper(), "ano": ano, "score": score,
            "validacao": {"confirmado_por_orgao": confirmado}}


def api_cebraspe(existentes: dict):
    """Fake da API da Cebraspe: slug sondado -> eventoURL real."""
    def get(url):
        slug = url.rsplit("/", 1)[-1]
        if slug in existentes:
            return 200, {"eventoURL": existentes[slug]}
        return 400, {"errors": {"identificador": ["not found"]}}
    return get


@pytest.fixture
def ctx(tmp_path):
    def fabricar(buscar=buscar_vazio, http_get=sem_rede, **politica):
        pol = cons.carregar_politica()
        for chave, valor in politica.items():
            if isinstance(valor, dict):
                pol[chave].update(valor)
            else:
                pol[chave] = valor
        return cons.Contexto(
            store=StateStore(tmp_path / "state" / "s.db"),
            catalogo=Catalogo(tmp_path / "catalogo.yaml"),
            politica=pol,
            respondedor=Respondedor(channel=None, out_dir=tmp_path / "eventos"),
            watchlist_auto=tmp_path / "state" / "watchlist.auto.yaml",
            buscar=buscar, http_get=http_get,
            inventario_fgv=tmp_path / "fgv.jsonl",
        )
    return fabricar


def respostas(tmp_path) -> list[dict]:
    pasta = tmp_path / "eventos" / "respostas"
    envs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pasta.glob("*.json"))] \
        if pasta.exists() else []
    for env in envs:
        validar(env)  # toda resposta emitida respeita o contrato
    return envs


def por_motivo(envs, relation):
    return {e["data"]["reason"]: e for e in envs
            if e["event_type"] == "document.acquisition.awaiting_operator"
            and e["data"]["relation"] == relation}


def watchlist_auto(tmp_path) -> list[dict]:
    arq = tmp_path / "state" / "watchlist.auto.yaml"
    return yaml.safe_load(arq.read_text(encoding="utf-8"))["alvos"] if arq.exists() else []


def evento(**data):
    env = copy.deepcopy(EXEMPLO)
    env["data"].update(data)
    return env


# --- contrato e idempotência ---------------------------------------------------

def test_mensagem_fora_do_contrato_vai_para_dlq(ctx):
    env = evento()
    env["producer"] = "lexcorpus"  # descoberta só pode vir do LexPubli
    with pytest.raises(cons.ContratoInvalido):
        cons.processar(env, ctx())


def test_mesmo_evento_duas_vezes_nao_reprocessa(ctx, tmp_path):
    c = ctx()
    cons.processar(evento(), c)
    n = len(respostas(tmp_path))
    assert cons.processar(evento(), c).startswith("duplicado")
    assert len(respostas(tmp_path)) == n


def test_catalogo_recebe_o_concurso_com_origem(ctx, tmp_path):
    cons.processar(evento(), ctx())
    vig = Catalogo(tmp_path / "catalogo.yaml").dados["vigentes"]
    assert len(vig) == 1
    assert vig[0]["concurso"] == "sefaz_al_2026"
    assert vig[0]["banca"] == "cebraspe" and vig[0]["status"] == "coletando"
    assert vig[0]["lexpubli"]["contest_id"] == EXEMPLO["data"]["contest_id"]


# --- concurso ATUAL ------------------------------------------------------------

def test_atual_cebraspe_vai_para_o_watchlist_automatico(ctx, tmp_path):
    cons.processar(evento(), ctx(anteriores={"ativo": False}))
    alvos = watchlist_auto(tmp_path)
    assert len(alvos) == 1
    a = alvos[0]
    assert (a["spider"], a["params"], a["concurso"]) == ("cebraspe", {"slug": "sefaz_al_26"}, "sefaz_al_26")
    assert a["cron"] == "0 7 * * *"  # edital_aberto na política
    assert a["lexpubli"] == [{"contest_id": EXEMPLO["data"]["contest_id"], "relation": "current"}]
    assert respostas(tmp_path) == []  # nada coletado ainda: nada a responder


def test_atual_ja_no_acervo_responde_completed_na_hora(ctx, tmp_path):
    c = ctx(anteriores={"ativo": False})
    c.store.upsert_arquivos([
        {"banca": "cebraspe", "concurso": "sefaz_al_26", "nome": n, "papel": p,
         "cargos": ["afte"], "checksum_sha256": h * 64, "tamanho_bytes": 10}
        for n, p, h in [("p.pdf", "prova", "a"), ("g.pdf", "gabarito_preliminar", "b")]])
    cons.processar(evento(), c)
    [env] = respostas(tmp_path)
    assert env["event_type"] == "document.acquisition.completed"
    assert env["correlation_id"] == EXEMPLO["event_id"]
    assert env["data"]["files"] == {"prova": 1, "gabarito_preliminar": 1, "gabarito_definitivo": 0}
    assert env["data"]["relation"] == "current" and env["data"]["year"] == 2026


def test_atual_sem_banca_vai_para_o_operador(ctx, tmp_path):
    cons.processar(evento(exam_board=None), ctx())
    assert "unknown_exam_board" in por_motivo(respostas(tmp_path), "current")
    assert watchlist_auto(tmp_path) == []
    vig = Catalogo(tmp_path / "catalogo.yaml").dados["vigentes"][0]
    assert vig["status"] == "aguardando_banca"


def test_atual_banca_fora_da_politica_vai_para_o_operador(ctx, tmp_path):
    cons.processar(evento(exam_board={"slug": "fcc", "name": "FCC"}), ctx())
    assert "no_spider" in por_motivo(respostas(tmp_path), "current")
    assert watchlist_auto(tmp_path) == []


def test_atual_sem_url_oficial_reconhecivel_vai_para_o_operador(ctx, tmp_path):
    cons.processar(evento(source_url="https://www.sefaz.al.gov.br/concurso", edital_url=None), ctx())
    assert "unresolved_target" in por_motivo(respostas(tmp_path), "current")


PORTAL_CESGRANRIO = {"success": True, "data": {"items": [
    {"id": 22, "nome": "TRANSPETRO 2026", "nomeFantasia": "PSP Transpetro 2026",
     "dataInicio": "2026-08-01T00:00:00",
     "cliente": {"razaoSocial": "PETROBRAS TRANSPORTE S.A. - TRANSPETRO"}},
    {"id": 26, "nome": "PC-AP 2026", "nomeFantasia": "Polícia Civil do Amapá",
     "dataInicio": "2026-09-01T00:00:00", "cliente": {"razaoSocial": "ESTADO DO AMAPA"}},
]}}


def dados_transpetro(**extra):
    d = dict(EXEMPLO["data"], contest_slug="transpetro_2026", year=2026,
             organ={"slug": "transpetro", "name": "Transpetro"},
             exam_board={"slug": "cesgranrio", "name": "Cesgranrio"},
             source_url="https://www.cesgranrio.org.br/concurso/transpetro", edital_url=None)
    d.update(extra)
    return d


def test_cesgranrio_casa_o_orgao_na_listagem_publica():
    alvo = resolver_atual("cesgranrio", dados_transpetro(),
                          http_get=lambda url: (200, PORTAL_CESGRANRIO))
    assert alvo.params == {"evento_id": "22", "concurso": "transpetro_2026"}
    assert alvo.concurso == "transpetro_2026"


def test_cesgranrio_ambiguo_nao_adivinha():
    duplicado = copy.deepcopy(PORTAL_CESGRANRIO)
    duplicado["data"]["items"].append(dict(duplicado["data"]["items"][0], id=23))
    assert resolver_atual("cesgranrio", dados_transpetro(),
                          http_get=lambda url: (200, duplicado)) is None


def test_cesgranrio_listagem_fora_do_ar_nao_adivinha():
    def falha(url):
        raise ConnectionError("timeout")
    assert resolver_atual("cesgranrio", dados_transpetro(), http_get=falha) is None


def test_url_do_cdn_tambem_resolve_cebraspe():
    alvo = resolver_atual("cebraspe", {"source_url": None, "edital_url":
                          "https://cdn.cebraspe.org.br/concursos/sefaz_al_26/arquivos/X.pdf"})
    assert alvo.params == {"slug": "sefaz_al_26"}


# --- certames ANTERIORES -------------------------------------------------------

def test_anterior_confirmado_e_resolvido_vai_para_o_watchlist(ctx, tmp_path):
    busca = lambda organ, cargo=None: {"candidatas": [
        candidata("cebraspe", 2019), candidata("cebraspe", 2026),  # 2026 = o próprio: fora
        candidata("fgv", 2013, score=2)]}                           # abaixo do limiar: ignorada
    c = ctx(buscar=busca, http_get=api_cebraspe({"sefaz_al_19": "SEFAZ_AL_19"}))
    resumo = cons.processar(evento(), c)
    previous = [a for a in watchlist_auto(tmp_path)
                if a["lexpubli"][0]["relation"] == "previous"]
    assert [a["params"] for a in previous] == [{"slug": "sefaz_al_19"}]
    assert previous[0]["cron"] == "30 6 * * 1"
    assert c.store.aquisicoes_de("cebraspe", "sefaz_al_19")[0]["ano"] == 2019
    assert por_motivo(respostas(tmp_path), "previous") == {}, resumo


def test_anterior_sem_alvo_vai_para_o_operador_com_as_candidatas(ctx, tmp_path):
    busca = lambda organ, cargo=None: {"candidatas": [candidata("cebraspe", 2019)]}
    cons.processar(evento(), ctx(buscar=busca, http_get=api_cebraspe({})))
    env = por_motivo(respostas(tmp_path), "previous")["unresolved_target"]
    assert env["data"]["candidates"] == [{"exam_board_slug": "cebraspe", "year": 2019,
                                          "score": 5, "confirmed_by_organ_catalog": True}]


def test_limite_de_volume(ctx, tmp_path):
    anos = [2023, 2021, 2019, 2017, 2015]
    busca = lambda organ, cargo=None: {"candidatas": [candidata("cebraspe", a) for a in anos]}
    existentes = {f"sefaz_al_{a % 100:02d}": f"SEFAZ_AL_{a % 100:02d}" for a in anos}
    cons.processar(evento(), ctx(buscar=busca, http_get=api_cebraspe(existentes)))
    previous = [a["params"]["slug"] for a in watchlist_auto(tmp_path)
                if a["lexpubli"][0]["relation"] == "previous"]
    assert previous == ["sefaz_al_23", "sefaz_al_21", "sefaz_al_19"]  # os 3 mais recentes
    env = por_motivo(respostas(tmp_path), "previous")["volume_limit"]
    assert [c["year"] for c in env["data"]["candidates"]] == [2017, 2015]


def test_sem_confirmacao_no_catalogo_do_orgao_nao_coleta(ctx, tmp_path):
    busca = lambda organ, cargo=None: {"candidatas": [candidata("cebraspe", 2019, confirmado=False)]}
    cons.processar(evento(), ctx(buscar=busca))
    assert "below_threshold" in por_motivo(respostas(tmp_path), "previous")


def test_so_bancas_sem_spider_validado(ctx, tmp_path):
    busca = lambda organ, cargo=None: {"candidatas": [candidata("fcc", 2013), candidata("vunesp", 2010)]}
    cons.processar(evento(), ctx(buscar=busca))
    assert "no_spider" in por_motivo(respostas(tmp_path), "previous")


def test_buscador_fora_do_ar_vai_para_o_operador(ctx, tmp_path):
    def quebrado(organ, cargo=None):
        raise TimeoutError("bing não respondeu")
    cons.processar(evento(), ctx(buscar=quebrado))
    env = por_motivo(respostas(tmp_path), "previous")["below_threshold"]
    assert "bing não respondeu" in env["data"]["detail"]


def test_anteriores_desligados_pela_politica(ctx, tmp_path):
    def nao_chamar(organ, cargo=None):
        raise AssertionError("buscador não deveria rodar")
    cons.processar(evento(), ctx(buscar=nao_chamar, anteriores={"ativo": False}))
    assert por_motivo(respostas(tmp_path), "previous") == {}


def test_fgv_anterior_pelo_inventario_do_modo_descoberta(tmp_path):
    inv = tmp_path / "fgv.jsonl"
    inv.write_text("\n".join(json.dumps(x) for x in [
        {"slug": "sefazal21", "rotulo": "Concurso SEFAZ-AL"},
        {"slug": "tjal23", "rotulo": "Tribunal de Justiça de Alagoas"},
    ]), encoding="utf-8")
    organ = {"slug": "sefaz_al", "name": "Secretaria de Estado da Fazenda de Alagoas"}
    alvo = resolver_anterior("fgv", organ, 2021, inventario_fgv=inv)
    assert alvo.params == {"slug": "sefazal21"} and alvo.concurso == "sefazal21"
    assert resolver_anterior("fgv", organ, 2019, inventario_fgv=inv) is None


# --- scheduler: manual é soberano ----------------------------------------------

def test_scheduler_junta_manual_e_auto_sem_duplicar(tmp_path):
    manual = tmp_path / "watchlist.yaml"
    manual.write_text(yaml.dump({"alvos": [
        {"banca": "cebraspe", "spider": "cebraspe", "params": {"slug": "sefaz_al_26"},
         "cron": "40 6 * * *", "ativo": True}]}), encoding="utf-8")
    auto = tmp_path / "watchlist.auto.yaml"
    auto.write_text(yaml.dump({"alvos": [
        {"banca": "cebraspe", "spider": "cebraspe", "params": {"slug": "sefaz_al_26"},
         "cron": "0 7 * * *", "ativo": True},
        {"banca": "cebraspe", "spider": "cebraspe", "params": {"slug": "sefaz_al_19"},
         "cron": "30 6 * * 1", "ativo": True}]}), encoding="utf-8")
    alvos = sch.carregar_alvos(manual, auto)
    assert [(a["params"]["slug"], a["cron"]) for a in alvos] == [
        ("sefaz_al_26", "40 6 * * *"), ("sefaz_al_19", "30 6 * * 1")]
    assert alvos[1]["rotulo"].endswith("(auto)")
    assert sch.carregar_alvos(manual, tmp_path / "nao_existe.yaml") == alvos[:1]


# --- pipeline: completed ao publicar o concurso --------------------------------

class FakeSettings(dict):
    def get(self, k, d=None): return super().get(k, d)
    def getbool(self, k, d=False): return bool(super().get(k, d))


class FakeSpider:
    logger = logging.getLogger("fake")


def item(store_root, nome, papel):
    pasta = store_root / "cebraspe" / "sefaz_al_19"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / nome).write_bytes(b"%PDF-1.4\n" + nome.encode() + b"\n%%EOF\n")
    it = ArquivoItem()
    it["files"] = [{"path": f"cebraspe/sefaz_al_19/{nome}", "checksum": "x", "url": f"https://ex/{nome}"}]
    it["fonte_url"] = "https://apis.cebraspe.org.br/cebraspe/eventos/sefaz_al_19"
    it["banca"], it["concurso"] = "cebraspe", "sefaz_al_19"
    it["banca_rotulo"], it["concurso_rotulo"] = "CEBRASPE", "SEFAZ AL 19"
    it["cargos_rotulo"] = {"auditor_fiscal": "Auditor Fiscal"}
    it["papel"], it["cargos"] = papel, ["auditor_fiscal"]
    it["tipo_prova"], it["multi_cargo"], it["vigente"] = None, False, True
    return it


def crawl(tmp_path, itens, **extra):
    settings = FakeSettings({
        "FILES_STORE": str(tmp_path), "RABBIT_ENABLED": False,
        "EVENTOS_OUT_DIR": str(tmp_path / "eventos"),
        "LEXCORPUS_STATE_DB": str(tmp_path / "state" / "s.db"), **extra})
    sidecar, pipe, spider = SidecarPipeline(store_root=str(tmp_path)), EventoRabbitPipeline(settings), FakeSpider()
    pipe.open_spider(spider)
    for it in itens:
        sidecar.process_item(it, spider)
        pipe.process_item(it, spider)
    pipe.close_spider(spider)


def test_pipeline_responde_completed_ao_publicar_concurso_ligado(tmp_path):
    with StateStore(tmp_path / "state" / "s.db") as st:
        st.registrar_aquisicao("cebraspe", "sefaz_al_19", EXEMPLO["data"]["contest_id"],
                               EXEMPLO["event_id"], "previous", ano=2019)

    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova")])
    [env] = respostas(tmp_path)
    assert env["event_type"] == "document.acquisition.completed"
    assert env["correlation_id"] == EXEMPLO["event_id"]
    assert env["data"]["lexcorpus_contest"] == "sefaz_al_19"
    assert env["data"]["files"] == {"prova": 1, "gabarito_preliminar": 0, "gabarito_definitivo": 0}
    assert env["data"]["positions"] == ["auditor_fiscal"]
    assert env["data"]["exam_board"] == {"slug": "cebraspe", "name": "CEBRASPE"}
    assert "pasta_uri" not in json.dumps(env)  # sem caminho de storage

    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova")])  # nada mudou
    assert len(respostas(tmp_path)) == 1

    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova"),
                     item(tmp_path, "gab_def.pdf", "gabarito_definitivo")])
    ultima = max(respostas(tmp_path), key=lambda e: e["occurred_at"])
    assert ultima["data"]["files"]["gabarito_definitivo"] == 1


def test_pipeline_sem_ligacao_nao_responde(tmp_path):
    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova")])
    assert respostas(tmp_path) == []


# -- broker do LexPubli separado (LexPubli ADR-0005 §2a) ---------------------

class CanalFalso:
    def __init__(self):
        self.publicadas = []

    def basic_publish(self, exchange, routing_key, body, properties=None):
        self.publicadas.append((exchange, routing_key, json.loads(body)))


class ConexaoFalsa:
    urls = []

    def __init__(self, params):
        ConexaoFalsa.urls.append(params)
        self.canal, self.is_open = CanalFalso(), True

    def channel(self):
        return self.canal

    def close(self):
        self.is_open = False


def test_respostas_vao_para_o_broker_do_lexpubli(tmp_path, monkeypatch):
    import pika
    criadas = []

    def fabrica(params):
        criadas.append(ConexaoFalsa(params))
        return criadas[-1]

    monkeypatch.setattr(pika, "BlockingConnection", fabrica)
    monkeypatch.setattr(pika, "URLParameters", lambda url: url)
    with StateStore(tmp_path / "state" / "s.db") as st:
        st.registrar_aquisicao("cebraspe", "sefaz_al_19", EXEMPLO["data"]["contest_id"],
                               EXEMPLO["event_id"], "previous", ano=2019)

    url = "amqp://lexcorpus:x@lexpubli-rabbitmq:5672/lexpubli"
    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova")], LEXPUBLI_RABBIT_URL=url)

    # uma conexão só, a do LexPubli (o v2.1 está desligado neste teste)
    assert [c.is_open for c in criadas] == [False]
    assert ConexaoFalsa.urls[-1] == url
    [(exchange, rk, env)] = criadas[0].canal.publicadas
    assert (exchange, rk) == ("lexcorpus.events", "document.acquisition.completed")
    assert env["correlation_id"] == EXEMPLO["event_id"]
    assert respostas(tmp_path) == []          # nada em disco


def test_sem_ligacao_nao_abre_o_broker_do_lexpubli(tmp_path, monkeypatch):
    import pika
    monkeypatch.setattr(pika, "BlockingConnection", lambda p: pytest.fail("conectou"))
    crawl(tmp_path, [item(tmp_path, "prova.pdf", "prova")],
          LEXPUBLI_RABBIT_URL="amqp://x@lexpubli-rabbitmq/lexpubli")


def test_consumidor_nao_usa_o_broker_do_lexlearn(monkeypatch, capsys):
    from lexcorpus import consumidor
    monkeypatch.delenv("LEXPUBLI_RABBIT_URL", raising=False)
    monkeypatch.setenv("RABBIT_ENABLED", "true")
    monkeypatch.setenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/")
    monkeypatch.setattr(consumidor, "escutar", lambda *a: pytest.fail("escutou no broker do LexLearn"))
    assert consumidor.main([]) == 2
    assert "LEXPUBLI_RABBIT_URL" in capsys.readouterr().err


# -- ADR-0008: acervo (previous/related) e contest.discovery.updated ---------

ATUALIZACAO = json.loads(Path("schema/lexpubli/v1/examples/contest.discovery.updated.json")
                         .read_text(encoding="utf-8"))


def atualizacao(event_id, changed, **data):
    env = copy.deepcopy(ATUALIZACAO)
    env["event_id"] = event_id
    env["data"].update(data, changed=changed)
    return env


def no_acervo(c, banca, concurso, *, organ, ano, carreira=None, n_provas=2):
    """Concurso já coletado: arquivos no StateStore + entrada no catálogo."""
    c.store.upsert_arquivos([
        {"banca": banca, "concurso": concurso, "nome": f"p{i}.pdf", "papel": "prova",
         "cargos": ["auditor_fiscal"], "checksum_sha256": f"{concurso}{i}", "tamanho_bytes": 1}
        for i in range(n_provas)])
    if carreira:
        c.catalogo.marcar_carreira(concurso, carreira, organ=organ, banca=banca, edital_ano=ano)


def completed(envs):
    return {(e["data"]["lexcorpus_contest"], e["data"]["relation"]): e for e in envs
            if e["event_type"] == "document.acquisition.completed"}


def acervo_fiscal(c):
    no_acervo(c, "cebraspe", "sefaz_ce_21", organ="Sefaz-CE", ano=2021, carreira="fiscal")
    no_acervo(c, "cebraspe", "seec_auditor_19", organ="SEEC-DF", ano=2019, carreira="fiscal")
    no_acervo(c, "cebraspe", "sefaz_al_19_auditor", organ="Sefaz-AL", ano=2019, carreira="fiscal")
    no_acervo(c, "cebraspe", "prf_21", organ="PRF", ano=2021, carreira="policial")
    no_acervo(c, "fgv", "sefaz_mt_23", organ="Sefaz-MT", ano=2023, carreira="fiscal")
    # no catálogo como fiscal, mas nunca coletado
    c.catalogo.marcar_carreira("sefaz_rs_25", "fiscal", organ="Sefaz-RS", banca="cebraspe",
                               edital_ano=2025)


def test_aceite_sefaz_al_2026_recebe_o_acervo_fiscal_sem_coleta_nova(ctx, tmp_path):
    """O teste de aceite da Fase 1 (LexPubli ADR-0004): sem coleta nova."""
    c = ctx()
    acervo_fiscal(c)
    env = evento()
    cons.processar(env, c)

    resp = completed(respostas(tmp_path))
    assert set(resp) == {("sefaz_ce_21", "related"), ("seec_auditor_19", "related"),
                         ("sefaz_al_19_auditor", "previous")}
    assert all(e["correlation_id"] == env["event_id"] for e in resp.values())
    assert resp[("sefaz_ce_21", "related")]["data"]["files"]["prova"] == 2
    assert resp[("sefaz_ce_21", "related")]["data"]["year"] == 2021
    # nada do acervo foi para coleta: só o concurso atual está no watchlist
    assert [a["params"] for a in watchlist_auto(tmp_path)] == [{"slug": "sefaz_al_26"}]
    # e o pipeline responde de novo quando o concurso for recoletado
    assert c.store.aquisicoes_de("cebraspe", "sefaz_ce_21")[0]["relation"] == "related"


def test_relacionados_respeitam_o_limite_mais_recentes_primeiro(ctx, tmp_path):
    c = ctx(relacionados={"max_por_alvo": 1})
    acervo_fiscal(c)
    cons.processar(evento(), c)
    rel = [k for k in completed(respostas(tmp_path)) if k[1] == "related"]
    assert rel == [("sefaz_ce_21", "related")]       # 2021 antes de 2019


def test_sem_carreira_nao_liga_o_acervo(ctx, tmp_path):
    c = ctx()
    acervo_fiscal(c)
    cons.processar(evento(career=None), c)
    assert completed(respostas(tmp_path)) == {}


def test_politica_desligada_nao_liga_o_acervo(ctx, tmp_path):
    c = ctx(relacionados={"ativo": False})
    acervo_fiscal(c)
    cons.processar(evento(), c)
    assert completed(respostas(tmp_path)) == {}


def test_carreira_definida_depois_liga_o_acervo_uma_vez(ctx, tmp_path):
    c = ctx()
    acervo_fiscal(c)
    cons.processar(evento(career=None), c)
    up = atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f31", ["career"], career="fiscal")
    cons.processar(up, c)
    resp = completed(respostas(tmp_path))
    assert len(resp) == 3
    assert all(e["correlation_id"] == up["event_id"] for e in resp.values())

    # outra atualização da carreira: a ligação já existe, nada se repete
    cons.processar(atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f32", ["career"]), c)
    assert len([e for e in respostas(tmp_path)
                if e["event_type"] == "document.acquisition.completed"]) == 3


def test_atualizacao_grava_carreira_e_data_no_catalogo(ctx, tmp_path):
    c = ctx()
    cons.processar(evento(career=None), c)
    cons.processar(atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f33", ["exam_date", "career"],
                               career="fiscal", exam_date="2026-12-13"), c)
    entry = Catalogo(tmp_path / "catalogo.yaml").dados["vigentes"][0]
    assert (entry["carreira"], entry["data_prova"]) == ("fiscal", "2026-12-13")


def test_status_novo_muda_o_cron_do_atual(ctx, tmp_path):
    c = ctx()
    cons.processar(evento(), c)
    cons.processar(atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f34", ["status"],
                               status="prova_realizada"), c)
    [alvo] = watchlist_auto(tmp_path)
    assert alvo["cron"] == c.politica["cron_por_status"]["prova_realizada"]


def test_banca_definida_depois_agenda_o_atual(ctx, tmp_path):
    c = ctx()
    cons.processar(evento(exam_board=None), c)
    assert watchlist_auto(tmp_path) == []
    cons.processar(atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f35", ["exam_board"]), c)
    assert [a["params"] for a in watchlist_auto(tmp_path)] == [{"slug": "sefaz_al_26"}]


def test_atualizacao_repetida_e_ignorada(ctx, tmp_path):
    c = ctx()
    cons.processar(evento(), c)
    up = atualizacao("01a13a52-3c1e-7f0a-9b44-5d2e8a7c1f36", ["status"], status="resultado")
    cons.processar(up, c)
    assert cons.processar(up, c).startswith("duplicado")


def test_carreira_fora_da_lista_do_contrato_e_recusada(tmp_path):
    cat = Catalogo(tmp_path / "c.yaml")
    with pytest.raises(ValueError, match="fora da lista"):
        cat.marcar_carreira("x_21", "magistratura", organ="X", banca="fgv", edital_ano=2021)
    with pytest.raises(ValueError, match="órgão, banca e ano"):
        cat.marcar_carreira("x_21", "fiscal")


def test_catalogo_de_estado_nasce_da_semente_e_depois_e_dele(tmp_path):
    semente = tmp_path / "semente.yaml"
    Catalogo(semente).marcar_carreira("sefaz_ce_21", "fiscal", organ="Sefaz-CE",
                                      banca="cebraspe", edital_ano=2021)
    estado = tmp_path / "state" / "catalogo.yaml"
    assert "sefaz_ce_21" in Catalogo.com_semente(estado, semente).dados["passados"]
    Catalogo(estado).marcar_carreira("prf_21", "policial", organ="PRF", banca="cebraspe",
                                     edital_ano=2021)
    assert "prf_21" in Catalogo.com_semente(estado, semente).dados["passados"]  # não recopia
