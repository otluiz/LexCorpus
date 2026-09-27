"""Items do LexCorpus.

Um ArquivoItem representa UM PDF a coletar (prova ou gabarito), carregando
todo o metadado que o contrato v2.0 exige no sidecar e no evento. Os spiders
produzem ArquivoItems; os pipelines baixam, escrevem o sidecar e agregam por
concurso para publicar o evento.
"""
import scrapy


class ArquivoItem(scrapy.Item):
    # --- para o FilesPipeline (download) ---
    file_urls = scrapy.Field()   # lista de URLs a baixar (FilesPipeline lê daqui)
    files = scrapy.Field()       # FilesPipeline preenche com resultado do download

    # --- identidade (slugs canônicos) ---
    banca = scrapy.Field()       # slug: 'fgv'
    concurso = scrapy.Field()    # slug: 'if_sergipe_2024'

    # --- rótulos crus (preservados em extra.rotulos) ---
    banca_rotulo = scrapy.Field()      # 'FGV'
    concurso_rotulo = scrapy.Field()   # 'IF-Sergipe 2024 - Professor EBTT'
    cargos_rotulo = scrapy.Field()     # {'geografia': 'Professor de Geografia', ...}

    # --- dimensões do arquivo (contrato v2.0 §3) ---
    papel = scrapy.Field()       # 'prova' | 'gabarito_preliminar' | 'gabarito_definitivo'
    cargos = scrapy.Field()      # lista de slugs: ['geografia'] ou ['*']
    tipo_prova = scrapy.Field()  # '1' | 'amarela' | None
    caderno = scrapy.Field()     # v2.1: código impresso no PDF, ex. 'BACEN13_002_04' | None
    multi_cargo = scrapy.Field() # bool
    segmentos = scrapy.Field()   # lista opcional [{cargo|caderno, pagina_inicio, pagina_fim, ancora}]
    vigente = scrapy.Field()     # bool (ciclo de vida)
    substituido_por = scrapy.Field()  # nome do definitivo, ou None

    # --- origem / rastreabilidade ---
    fonte_url = scrapy.Field()   # URL de onde o PDF foi raspado
    origem_extra = scrapy.Field()  # dict somado a sidecar.origem (ex.: metodo da ingestão manual)
    nome = scrapy.Field()        # basename final no storage (sem barra)

    # --- preenchidos pelos pipelines ---
    checksum_sha256 = scrapy.Field()
    tamanho_bytes = scrapy.Field()
    caminho_local = scrapy.Field()  # path absoluto resolvido após download
