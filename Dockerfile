# Dockerfile do LexCorpus — coletor de provas e gabaritos (Scrapy)
# Vai na RAIZ do repositório LexCorpus.
#
# O container é EFÊMERO: roda um crawl e morre (não é serviço permanente).
# Quem fica no ar são RabbitMQ e LexLearn.
#
# Build (a partir da pasta do LexLearn-v3, via compose):
#   docker compose -f docker-compose.base.yml -f docker-compose.crawler.yml build lexcorpus
#
# Uso direto (sem compose):
#   docker build -t lexcorpus .
#   docker run --rm --network lexlearn-v3_default \
#     -v "$PWD/../LexLearn-v3/data:/data" \
#     -e RABBIT_ENABLED=true \
#     -e RABBIT_URL=amqp://guest:guest@rabbitmq:5672/ \
#     lexcorpus crawl fcc -a slug=dpeba125 -s ROBOTSTXT_OBEY=False

FROM python:3.11-slim

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

# ENTRYPOINT = scrapy: o "comando" do container é o restante da linha scrapy
ENTRYPOINT ["scrapy"]
# default: só lista os spiders (sanity check); o compose sobrepõe com o crawl
CMD ["list"]
