# Arquivo:  tests/test_duplicado.py
# Função:   o mesmo PDF (mesmo SHA-256) não entra em dois concursos — caso real
#           do gab_definitivo_todos_cargos-1.pdf, que foi parar em
#           bacen13_analista/ e em serpro13_analista_ti/. A recusa acontece no
#           download: o PDF duplicado nem chega ao disco.
"""Rodar:  pytest tests/test_duplicado.py -v"""
import asyncio
import hashlib
import inspect
import logging

import pytest
from scrapy.http import Request, Response
from scrapy.pipelines.files import FileException
from scrapy.utils.test import get_crawler

from lexcorpus.items import ArquivoItem
from lexcorpus.pipelines import LexCorpusFilesPipeline
from lexcorpus.statestore import StateStore

PDF = b"%PDF-1.4\ngabarito todos os cargos\n%%EOF\n"
SHA = hashlib.sha256(PDF).hexdigest()


def registrar(db, banca, concurso, nome, checksum=SHA):
    with StateStore(db) as st:
        st.upsert_arquivos([{
            "banca": banca, "concurso": concurso, "nome": nome,
            "papel": "gabarito_definitivo", "cargos": ["*"],
            "checksum_sha256": checksum, "tamanho_bytes": len(PDF)}])


# --- StateStore -----------------------------------------------------------------

def test_buscar_por_checksum(tmp_path):
    db = tmp_path / "s.db"
    registrar(db, "cebraspe", "bacen13", "gab.pdf")
    registrar(db, "cebraspe", "prf_21", "outro.pdf", checksum="f" * 64)
    with StateStore(db) as st:
        achados = st.buscar_por_checksum(SHA)
        assert [(a["banca"], a["concurso"], a["nome"]) for a in achados] == \
            [("cebraspe", "bacen13", "gab.pdf")]
        assert st.buscar_por_checksum("0" * 64) == []


# --- FilesPipeline ----------------------------------------------------------------

class FakeInfo:
    def __init__(self, settings):
        self.spider = type("S", (), {"settings": settings,
                                     "logger": logging.getLogger("fake")})()


def baixar(tmp_path, concurso, nome="gab_definitivo_todos_cargos-1.pdf"):
    crawler = get_crawler(settings_dict={"LEXCORPUS_STATE_DB": str(tmp_path / "s.db")})
    pipe = LexCorpusFilesPipeline(str(tmp_path / "store"), crawler=crawler)
    url = f"https://cdn.exemplo/{nome}"
    item = ArquivoItem(banca="cebraspe", concurso=concurso, nome=nome)
    resp = Response(url=url, body=PDF, request=Request(url))
    res = pipe.file_downloaded(resp, resp.request, FakeInfo(crawler.settings), item=item)
    if inspect.isawaitable(res):
        asyncio.run(res)
    return tmp_path / "store" / "cebraspe" / concurso / nome


def test_mesmo_arquivo_em_outro_concurso_recusado(tmp_path):
    registrar(tmp_path / "s.db", "cebraspe", "bacen13", "gab_definitivo_todos_cargos-1.pdf")
    with pytest.raises(FileException, match="bacen13"):
        baixar(tmp_path, "serpro13")
    assert not (tmp_path / "store" / "cebraspe" / "serpro13").exists()


def test_controle_mesmo_concurso_aceito(tmp_path):
    """Re-download no MESMO concurso (ex.: rebaixar expirado) não é duplicata."""
    registrar(tmp_path / "s.db", "cebraspe", "bacen13", "gab_definitivo_todos_cargos-1.pdf")
    assert baixar(tmp_path, "bacen13").read_bytes() == PDF


def test_controle_arquivo_inedito_aceito(tmp_path):
    registrar(tmp_path / "s.db", "cebraspe", "bacen13", "x.pdf", checksum="f" * 64)
    assert baixar(tmp_path, "serpro13").read_bytes() == PDF
