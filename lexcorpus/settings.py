"""Settings do LexCorpus.

Agrupado por tema. Os valores de throttle/retry são o que protege o LexCorpus
de ser bloqueado pelas bancas — ajuste por spider via custom_settings quando
uma banca exigir tratamento especial.
"""
import os

BOT_NAME = "lexcorpus"
SPIDER_MODULES = ["lexcorpus.spiders"]
NEWSPIDER_MODULE = "lexcorpus.spiders"

# --- Educação / robustez -----------------------------------------------------
ROBOTSTXT_OBEY = True                    # respeita robots.txt das bancas
USER_AGENT = "lexcorpus/0.1 (+contato@exemplo.br)"

# --- Concorrência e throttle (anti-bloqueio) ---------------------------------
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 2       # gentil com cada banca
DOWNLOAD_DELAY = 1.0                     # 1s entre requisições ao mesmo domínio

AUTOTHROTTLE_ENABLED = True              # ajusta o delay dinamicamente
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# --- Retry -------------------------------------------------------------------
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 408]

# --- Storage dos binários (FilesPipeline) ------------------------------------
# Onde os PDFs são gravados fisicamente. Cada AMBIENTE define o seu:
#   - dev local: aponte para o storage compartilhado do LexLearn, ex.:
#       export LEXCORPUS_FILES_STORE="../LexLearn/LexLearn-v3/data/raw/exams"
#   - Docker:    o volume é montado em /data/raw/exams (default abaixo).
# O caminho NÃO é hardcodado: vem de LEXCORPUS_FILES_STORE, com fallback Docker.
FILES_STORE = os.environ.get("LEXCORPUS_FILES_STORE", "/data/raw/exams")
# Esquema usado ao montar pasta_uri no evento (contrato §7). file:// hoje.
STORAGE_URI_SCHEME = "file://"
# Expira o cache de download: não rebaixa o mesmo arquivo dentro de N dias.
FILES_EXPIRES = 90

# --- RabbitMQ ----------------------------------------------------------------
RABBIT_ENABLED = False                   # True em produção; False grava evento em disco
RABBIT_URL = "amqp://guest:guest@localhost:5672/"
RABBIT_EXCHANGE = "lexcorpus.events"
RABBIT_ROUTING_DISPONIVEL = "concurso.disponivel"
RABBIT_ROUTING_ATUALIZADO = "concurso.atualizado"
EVENTOS_OUT_DIR = "eventos_debug"        # usado quando RABBIT_ENABLED=False

# --- Cadeia de pipelines -----------------------------------------------------
ITEM_PIPELINES = {
    "lexcorpus.pipelines.LexCorpusFilesPipeline": 100,   # baixa o PDF
    "lexcorpus.pipelines.SidecarPipeline": 200,          # checksum + sidecar
    "lexcorpus.pipelines.EventoRabbitPipeline": 300,     # publica evento
}

# --- Diversos ----------------------------------------------------------------
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = "INFO"
