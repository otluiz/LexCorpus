"""Spider de exemplo — modelo para um spider POR BANCA.

Cada banca real vira um arquivo destes. A responsabilidade do spider é só:
  1. navegar até a página do concurso,
  2. descobrir os links de PDF (prova/gabarito),
  3. classificar papel/cargos/tipo_prova a partir do que a página diz,
  4. emitir um ArquivoItem por PDF, com slugs canônicos + rótulos crus.

Tudo o que é download, checksum, sidecar e evento é dos pipelines — o spider
não toca nisso. Este exemplo usa `start_requests` com URLs de exemplo e um
parse que mostra a construção do item; adapte os seletores à banca real.
"""
from __future__ import annotations

import scrapy

from ..items import ArquivoItem
from ..util import slugify


class ExemploBancaSpider(scrapy.Spider):
    name = "exemplo_banca"

    # Em produção: liste as páginas de concurso a varrer, ou descubra-as.
    start_urls = [
        "https://httpbin.org/html",  # placeholder navegável; troque pela banca real
    ]

    # Rótulos crus que viriam da página; slugificados na hora de montar o item.
    BANCA_ROTULO = "FGV"
    CONCURSO_ROTULO = "IF-Sergipe 2024 - Professor EBTT"

    def parse(self, response):
        banca = slugify(self.BANCA_ROTULO)          # 'fgv'
        concurso = slugify(self.CONCURSO_ROTULO)    # 'if_sergipe_2024_professor_ebtt'

        # --- Exemplo 1: prova de um cargo, tipo de prova "1" ---
        yield self._make_item(
            response,
            pdf_url="https://exemplo.br/ifs_geografia_prova_t1.pdf",
            nome="ifs_geografia_prova_t1.pdf",
            papel="prova",
            cargos_rotulo={"geografia": "Professor de Geografia"},
            tipo_prova="1",
            banca=banca, concurso=concurso,
        )

        # --- Exemplo 2: gabarito definitivo cobrindo VÁRIOS cargos (multi_cargo) ---
        yield self._make_item(
            response,
            pdf_url="https://exemplo.br/ifs_gab_definitivo_todos.pdf",
            nome="ifs_gab_definitivo_todos.pdf",
            papel="gabarito_definitivo",
            cargos_rotulo={
                "geografia": "Professor de Geografia",
                "ingles": "Professor de Inglês",
            },
            tipo_prova=None,
            multi_cargo=True,
            vigente=True,
            banca=banca, concurso=concurso,
        )

    def _make_item(self, response, *, pdf_url, nome, papel, cargos_rotulo,
                   banca, concurso, tipo_prova=None, multi_cargo=False,
                   vigente=True, substituido_por=None):
        cargos = ["*"] if cargos_rotulo == {"*": "*"} else list(cargos_rotulo.keys())
        item = ArquivoItem()
        item["file_urls"] = [pdf_url]        # FilesPipeline baixa isto
        item["fonte_url"] = pdf_url
        item["nome"] = nome
        item["banca"] = banca
        item["concurso"] = concurso
        item["banca_rotulo"] = self.BANCA_ROTULO
        item["concurso_rotulo"] = self.CONCURSO_ROTULO
        item["cargos_rotulo"] = cargos_rotulo
        item["papel"] = papel
        item["cargos"] = cargos
        item["tipo_prova"] = tipo_prova
        item["multi_cargo"] = multi_cargo
        item["vigente"] = vigente
        if substituido_por:
            item["substituido_por"] = substituido_por
        return item
