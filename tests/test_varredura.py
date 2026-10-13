# Arquivo:  tests/test_varredura.py
# Função:   testes da varredura por carreira (ADR-0009): sugestão por regras,
#           inventário da FGV e da Cesgranrio, revisão em lote com teto e o
#           watchlist da varredura no scheduler. Sem rede.
"""Rodar:  pytest tests/test_varredura.py -v"""
from datetime import date, datetime

import pytest
import yaml

from lexcorpus import scheduler as sch
from lexcorpus import varredura as var
from lexcorpus.catalogo import Catalogo
from lexcorpus.consumidor import carregar_politica
from lexcorpus.util import slugify

REGRAS = var.carregar_regras()
LIDO = "2026-10-13T12:00:00Z"


# --- sugestão -------------------------------------------------------------------

@pytest.mark.parametrize("texto,carreira", [
    ("Secretaria de Estado da Fazenda do Ceará - SEFAZ-CE", "fiscal"),
    ("Secretaria de Estado de Economia do Distrito Federal", "fiscal"),
    ("Concurso para Auditor-Fiscal da Receita Estadual", "fiscal"),
    ("Tribunal Regional do Trabalho da 2ª Região", "tribunais"),
    ("Tribunal de Justiça do Estado de São Paulo", "tribunais"),
    ("Analista Judiciário - Área Administrativa", "tribunais"),
    ("Tribunal de Contas do Estado do Rio de Janeiro", None),       # controle
    ("Auditor-Fiscal do Trabalho", None),                            # MTE
    ("Caixa Econômica Federal", None),
    ("Sefaz-BA: Analista Judiciário", None),                         # duas carreiras: sem sugestão
    ("Tribunal de Justiça do Estado de Pernambuco - Juiz Substituto", None),   # jurídica
    ("Tribunal de Justiça do Estado do Espírito Santo - Atividade Notarial e Registral", None),
    ("Prefeitura de Sefazópolis", None),                             # palavra inteira
])
def test_sugestao_por_regras(texto, carreira):
    assert var.sugerir_carreira(texto, REGRAS) == carreira


def test_regras_so_aceitam_carreiras_do_contrato(tmp_path):
    arq = tmp_path / "r.yaml"
    arq.write_text("carreiras:\n  magistratura:\n    termos: [juiz]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="magistratura"):
        var.carregar_regras(arq)


# --- inventário -----------------------------------------------------------------

FGV = [
    {"banca": "fgv", "slug": "sefazes22",
     "rotulo": "Concurso Público para a Secretaria de Estado da Fazenda do Espírito Santo - SEFAZ-ES",
     "url": "https://conhecimento.fgv.br/concursos/sefazes22"},
    {"banca": "fgv", "slug": "trt13_2022",
     "rotulo": "Concurso Público para o Tribunal Regional do Trabalho da 13ª Região",
     "url": "https://conhecimento.fgv.br/concursos/trt13_2022"},
    {"banca": "fgv", "slug": "alba",
     "rotulo": "Concurso Público para a Assembleia Legislativa do Estado da Bahia",
     "url": "https://conhecimento.fgv.br/concursos/alba"},
]

CESGRANRIO = {"success": True, "data": {"items": [
    {"id": 19, "nome": "CEF0125", "nomeFantasia": "CAIXA 2025", "dataInicio": "2025-11-07T00:00:00",
     "codigo": "https://concursos.cesgranrio.org.br/portal/login-evento/x",
     "cliente": {"nome": "CAIXA ECONÔMICA FEDERAL", "razaoSocial": "CAIXA ECONÔMICA FEDERAL"},
     "descricao": "Técnico Bancário Novo"},
    {"id": 40, "nome": "SEFAZ RJ 2026", "nomeFantasia": "SEFAZ-RJ", "dataInicio": "2026-03-01T00:00:00",
     "codigo": "https://concursos.cesgranrio.org.br/portal/login-evento/y",
     "cliente": {"nome": "SEFAZ RJ", "razaoSocial": "SECRETARIA DE ESTADO DE FAZENDA DO RIO DE JANEIRO"},
     "descricao": "Auditor Fiscal da Receita Estadual"},
]}}


def test_inventario_fgv():
    e = {x["slug"]: x for x in var.inventario_fgv(FGV, REGRAS, LIDO)}
    assert e["sefazes22"]["ano"] == 2022
    assert e["sefazes22"]["organ"].startswith("Secretaria de Estado da Fazenda")
    assert e["sefazes22"]["carreira_sugerida"] == "fiscal"
    assert e["sefazes22"]["params"] == {"slug": "sefazes22"}
    assert e["trt13_2022"]["ano"] == 2022 and e["trt13_2022"]["carreira_sugerida"] == "tribunais"
    assert e["alba"]["ano"] is None and e["alba"]["carreira_sugerida"] is None


def test_inventario_cesgranrio():
    e = {x["slug"]: x for x in var.inventario_cesgranrio(CESGRANRIO, REGRAS, LIDO)}
    assert e["40"]["carreira_sugerida"] == "fiscal" and e["40"]["ano"] == 2026
    assert e["40"]["params"] == {"evento_id": 40} and e["40"]["concurso"] == "sefaz_rj_2026"
    assert e["19"]["carreira_sugerida"] is None


def test_inventariar_grava_jsonl(tmp_path):
    destino = var.inventariar("cesgranrio", regras=REGRAS, destino_dir=tmp_path,
                              http_get=lambda url: (200, CESGRANRIO))
    assert [x["chave"] for x in var.carregar_inventario(tmp_path)] == ["cesgranrio:19", "cesgranrio:40"]
    assert destino.name == "cesgranrio.jsonl"


def test_banca_sem_inventario(tmp_path):
    with pytest.raises(ValueError, match="ADR-0009"):
        var.inventariar("fcc", regras=REGRAS, destino_dir=tmp_path)


# --- Cebraspe: índice PCI + confirmação na API oficial ---------------------------

def _linha_pci(cargo, ano, orgao, orgao_slug):
    url = f"https://www.pciconcursos.com.br/provas/download/{slugify(cargo)}-{orgao_slug}-cebraspe-{ano}"
    return (f'<tr class="lk_link c" data-url="{url}"> <td class="ca"><a href="{url}" '
            f'class="prova_download"><i class="fas fa-briefcase"></i>{cargo}</a></td> '
            f'<td class="cb">{ano}</td> <td class="cc"><a href="https://www.pciconcursos.com.br'
            f'/provas/{orgao_slug}" title="x">{orgao}</a></td> <td class="cd"><a href='
            f'"https://www.pciconcursos.com.br/provas/cebraspe">CEBRASPE</a></td> </tr>')


PAGINA1 = "<table>" + "".join([
    _linha_pci("Auditor Fiscal da Receita Estadual", 2020, "SEFAZ/AL", "sefaz-al"),
    _linha_pci("Analista Judiciário - Judiciária", 2024, "STJ", "stj"),
    _linha_pci("Técnico Judiciário - Área Administrativa", 2024, "STJ", "stj"),
]) + "</table>"
PAGINA2 = "<table>" + "".join([
    _linha_pci("Auditor Fiscal de Tributos Municipais", 2024, "Pref. Cachoeiro de Itapemirim/ES",
               "prefeitura-cachoeiro-de-itapemirim-es"),
    _linha_pci("Agente Administrativo", 2024, "Pref. Cachoeiro de Itapemirim/ES",
               "prefeitura-cachoeiro-de-itapemirim-es"),
    _linha_pci("Arquiteto", 2024, "Pref. Cachoeiro de Itapemirim/ES",
               "prefeitura-cachoeiro-de-itapemirim-es"),
    _linha_pci("Administrador", 2022, "Petrobras", "petrobras"),
]) + "</table>"


def api_fake(conhecidos):
    chamadas = []

    def get(url):
        slug = url.rsplit("/", 1)[-1]
        chamadas.append(slug)
        if slug in conhecidos:
            return 200, {"eventoURL": slug.upper()}
        return 400, {"errors": {}}
    get.chamadas = chamadas
    return get


def test_ler_pci():
    [l, *_] = var.ler_pci(PAGINA1)
    assert (l["cargo"], l["ano"], l["orgao"], l["orgao_slug"]) == \
        ("Auditor Fiscal da Receita Estadual", 2020, "SEFAZ/AL", "sefaz-al")


def test_sugestao_por_cargos_corta_a_prefeitura():
    assert var.sugerir_por_cargos("Pref. Cachoeiro/ES", ["Auditor Fiscal", "Arquiteto",
                                                         "Agente Administrativo"], REGRAS) is None
    assert var.sugerir_por_cargos("Pref. Mossoró/RN", ["Auditor Fiscal de Tributos Municipais",
                                                       "Auditor Fiscal - Direito"], REGRAS) == "fiscal"
    assert var.sugerir_por_cargos("SEFAZ/AL", ["Analista"], REGRAS) == "fiscal"   # o órgão basta


@pytest.mark.parametrize("orgao,ano,esperado", [
    ("SEFAZ/AL", 2020, "sefaz_al_19"),          # ano da prova -> ano do edital também
    ("TRF 6ª", 2025, "trf6_25"),
    ("TRT da 8º Região", 2022, "trt8_22"),
    ("Pref. Mossoró/RN", 2024, "pref_mossoro_rn_24"),
    ("CNJ", 2025, "cnj_24"),
])
def test_candidatos_de_slug(orgao, ano, esperado):
    assert esperado in var.candidatos_slug(orgao, ano)


def test_inventario_cebraspe_confirma_na_api_so_o_que_tem_carreira():
    api = api_fake({"stj_24"})
    e = {x["orgao"] if "orgao" in x else x["organ"]: x for x in
         var.inventario_cebraspe(var.ler_pci(PAGINA1 + PAGINA2), REGRAS, LIDO, http_get=api, pausa=0)}
    assert e["STJ"]["params"] == {"slug": "stj_24"} and e["STJ"]["carreira_sugerida"] == "tribunais"
    assert e["STJ"]["ano"] == 2024 and e["STJ"]["slug_confirmado"]
    assert e["SEFAZ/AL"]["carreira_sugerida"] == "fiscal" and e["SEFAZ/AL"]["params"] is None
    assert e["Pref. Cachoeiro de Itapemirim/ES"]["carreira_sugerida"] is None
    assert not any(c.startswith(("petrobras", "pref_cach")) for c in api.chamadas)   # sem carreira: sem sonda


def test_inventariar_cebraspe_pagina_ate_acabar(tmp_path):
    paginas = {1: PAGINA1, 2: PAGINA2, 3: PAGINA2}           # o PCI repete a última
    lidas = []

    def html(url):
        n = int(url.rstrip("/").rsplit("/", 1)[-1])
        lidas.append(n)
        return paginas.get(n, "")
    var.inventariar("cebraspe", regras=REGRAS, destino_dir=tmp_path,
                    http_get=api_fake({"stj_24"}), http_html=html, pausa=0)
    assert lidas == [1, 2, 3]
    chaves = {e["chave"] for e in var.carregar_inventario(tmp_path)}
    assert "cebraspe:pci/stj-2024" in chaves and len(chaves) == 4


def test_confirmar_exige_slug_confirmado(rev):
    e = var.inventario_cebraspe(var.ler_pci(PAGINA1), REGRAS, LIDO, http_get=api_fake(set()), pausa=0)
    sefaz = next(x for x in e if x["organ"] == "SEFAZ/AL")
    with pytest.raises(ValueError, match="--slug"):
        rev().confirmar([sefaz], "fiscal")


# --- revisão --------------------------------------------------------------------

HOJE = date(2026, 10, 13)


@pytest.fixture
def rev(tmp_path):
    def fabricar(hoje=HOJE, **varredura):
        pol = carregar_politica()
        pol["varredura"].update(varredura)
        return var.Revisao(politica=pol, catalogo=Catalogo(tmp_path / "catalogo.yaml"),
                           decisoes=tmp_path / "varredura.yaml",
                           watchlist=tmp_path / "watchlist.varredura.yaml", hoje=hoje)
    return fabricar


def fiscais(n, banca="fgv"):
    return [{"chave": f"{banca}:sefaz{i}", "banca": banca, "slug": f"sefaz{i}",
             "concurso": f"sefaz{i}", "organ": f"Sefaz {i}", "ano": 2015 + i, "spider": banca,
             "params": {"slug": f"sefaz{i}"}, "carreira_sugerida": "fiscal", "rotulo": "x",
             "url": None, "lido_em": LIDO} for i in range(n)]


def test_pendentes_mais_recentes_primeiro_e_so_bancas_validadas(rev):
    inv = fiscais(3) + fiscais(1, banca="fcc")
    assert [e["chave"] for e in rev().pendentes(inv, "fiscal")] == ["fgv:sefaz2", "fgv:sefaz1", "fgv:sefaz0"]


def test_confirmar_grava_catalogo_watchlist_e_decisao(rev, tmp_path):
    r = rev()
    r.confirmar(fiscais(2), "fiscal")
    cat = Catalogo(tmp_path / "catalogo.yaml").dados["passados"]
    assert cat["sefaz1"]["carreira"] == "fiscal" and cat["sefaz1"]["edital_ano"] == 2016
    alvos = yaml.safe_load((tmp_path / "watchlist.varredura.yaml").read_text())["alvos"]
    assert {a["params"]["slug"] for a in alvos} == {"sefaz0", "sefaz1"}
    assert all(a["origem"] == "varredura" and a["cron"] == r.pol["cron"] for a in alvos)
    assert r.vagas("fiscal", "fgv") == 3
    assert rev().pendentes(fiscais(3), "fiscal")[0]["chave"] == "fgv:sefaz2"   # persistido


def test_teto_recusa_o_lote_inteiro(rev, tmp_path):
    r = rev(max_por_carreira_e_banca=2)
    with pytest.raises(ValueError, match="teto"):
        r.confirmar(fiscais(3), "fiscal")
    assert not (tmp_path / "watchlist.varredura.yaml").exists()       # nada pela metade
    assert not (tmp_path / "catalogo.yaml").exists()


def test_sem_ano_e_carreira_fora_da_varredura_sao_recusados(rev):
    sem_ano = fiscais(1)
    sem_ano[0]["ano"] = None
    with pytest.raises(ValueError, match="sem ano"):
        rev().confirmar(sem_ano, "fiscal")
    with pytest.raises(ValueError, match="varredura.carreiras"):
        rev().confirmar(fiscais(1), "policial")


def test_descartado_nao_volta(rev):
    r = rev()
    r.descartar(["fgv:sefaz2"], "não é tributário")
    assert [e["chave"] for e in rev().pendentes(fiscais(3), "fiscal")] == ["fgv:sefaz1", "fgv:sefaz0"]


# --- scheduler ------------------------------------------------------------------

def test_scheduler_le_a_varredura_e_o_manual_e_soberano(tmp_path):
    manual = tmp_path / "watchlist.yaml"
    manual.write_text(yaml.dump({"version": 1, "alvos": [
        {"banca": "fgv", "spider": "fgv", "params": {"slug": "sefaz0"}, "cron": "0 1 * * *",
         "ativo": True}]}), encoding="utf-8")
    varr = tmp_path / "watchlist.varredura.yaml"
    varr.write_text(yaml.dump({"version": 1, "alvos": [
        {"banca": "fgv", "spider": "fgv", "params": {"slug": "sefaz0"}, "cron": "45 5 * * 2",
         "ativo": True, "concurso": "sefaz0"},
        {"banca": "fgv", "spider": "fgv", "params": {"slug": "sefaz1"}, "cron": "45 5 * * 2",
         "ativo": True, "concurso": "sefaz1"}]}), encoding="utf-8")
    alvos = sch.carregar_alvos(manual, None, varr)
    assert len(alvos) == 2
    assert alvos[0]["cron"] == "0 1 * * *"                      # o do operador
    assert alvos[1]["rotulo"].endswith("(varredura)")



# --- liberação escalonada (o ritmo de entrada no LexLearn) ---------------------

def datas(r):
    return {k: c["a_partir_de"] for k, c in r.confirmados.items()}


def test_um_novo_por_semana_mais_recentes_primeiro(rev):
    r = rev(novos_por_semana=1)
    r.confirmar(fiscais(3), "fiscal")
    # 13/10 é terça: a 1ª semana começa hoje; as seguintes, na segunda-feira
    assert datas(r) == {"fgv:sefaz2": "2026-10-13", "fgv:sefaz1": "2026-10-19",
                        "fgv:sefaz0": "2026-10-26"}


def test_lote_seguinte_continua_a_fila(rev):
    rev(novos_por_semana=2).confirmar(fiscais(3), "fiscal")
    r = rev(novos_por_semana=2, hoje=date(2026, 10, 14))
    r.confirmar(fiscais(1, banca="cesgranrio"), "fiscal")
    # a semana de 12/10 já tem 2; a de 19/10 tem 1 e ainda cabe mais um
    assert datas(r)["cesgranrio:sefaz0"] == "2026-10-19"


def test_scheduler_nao_dispara_antes_da_liberacao():
    alvos = [{"chave": "a", "cron": "45 5 * * 2", "a_partir_de": "2026-10-20", "rotulo": "a"},
             {"chave": "b", "cron": "45 5 * * 2", "a_partir_de": "2026-10-13", "rotulo": "b"}]
    agora = datetime(2026, 10, 13, 6, 0).astimezone()
    assert [a["chave"] for a in sch.alvos_vencidos(alvos, {}, agora)] == ["b"]


def test_reescalonar_refaz_so_os_ainda_nao_liberados(rev):
    rev(novos_por_semana=1).confirmar(fiscais(4), "fiscal")      # 13, 19, 26/10 e 02/11
    r = rev(novos_por_semana=2, hoje=date(2026, 10, 20))
    r.reescalonar()
    assert datas(r) == {"fgv:sefaz3": "2026-10-13", "fgv:sefaz2": "2026-10-19",   # já liberados
                        "fgv:sefaz1": "2026-10-20",     # a semana de 19/10 ainda tinha vaga
                        "fgv:sefaz0": "2026-10-26"}
