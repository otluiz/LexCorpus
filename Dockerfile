# Dockerfile do LexCorpus — coletor de provas e gabaritos (Scrapy)
# Vai na RAIZ do repositório LexCorpus.
#
# O container é EFÊMERO: roda um crawl e morre (não é serviço permanente).
# Quem fica no ar são RabbitMQ e LexLearn.
#
# Multi-stage (BACKLOG [#A]):
#   base       -> imagem enxuta, suficiente para TODOS os spiders ativos
#                 (nenhum precisa de browser hoje — Cesgranrio usa API JSON)
#   playwright -> base + Chromium/Playwright, só para spiders com WAF/JS
#                 (PCI, se reaberto). Não é buildada por default.
#   final      -> o default de build (== base). O compose não passa
#                 --target, então o ÚLTIMO estágio é o que vale.
#
# Build (a partir DESTA pasta, via compose — o compose mudou de casa em
# 01/09: era do LexLearn, agora vive aqui, com o docker-compose.lexlearn.yml
# de ponte):
#   export LEXCORPUS_STORAGE_DIR=../LexLearn/LexLearn-v3/data  # confira o caminho
#   docker compose -f docker-compose.crawler.yml -f docker-compose.lexlearn.yml build lexcorpus
#
# Build do target playwright (quando algum spider precisar de browser):
#   docker build --target playwright -t lexcorpus:playwright .
#
# Uso direto (sem compose):
#   docker build -t lexcorpus .
#   docker run --rm --network lexlearn-v3_default \
#     -v "$PWD/../LexLearn/LexLearn-v3/data:/data" \
#     -v lexcorpus_lexcorpus-state:/state \
#     -e RABBIT_ENABLED=true \
#     -e RABBIT_URL=amqp://guest:guest@rabbitmq:5672/ \
#     lexcorpus crawl fcc -a slug=dpeba125 -s ROBOTSTXT_OBEY=False

# --- base: imagem enxuta, todos os spiders ativos ------------------------------
FROM python:3.11-slim AS base

# sem cache de pip e sem bytecode: imagem menor, logs limpos
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# storage compartilhado com o LexLearn (montado pelo compose em /data)
ENV LEXCORPUS_FILES_STORE=/data/raw/exams

# estado persistente (StateStore SQLite + scheduler_state.json): montar um
# volume em /state para o estado sobreviver entre crawls efêmeros
ENV LEXCORPUS_STATE_DB=/state/lexcorpus_state.db \
    LEXCORPUS_STATE_FILE=/state/scheduler_state.json
VOLUME /state

# ENTRYPOINT = scrapy: o "comando" do container é o restante da linha scrapy
ENTRYPOINT ["scrapy"]
# default: só lista os spiders (sanity check); o compose sobrepõe com o crawl
CMD ["list"]

# --- playwright: base + Chromium (só para spiders com WAF/JS; nenhum ativo) ---
FROM base AS playwright

RUN pip install --no-cache-dir "scrapy-playwright>=0.0.40" \
    && playwright install --with-deps chromium

# --- final: default de build (== base; o compose não passa --target) ----------
FROM base AS final
