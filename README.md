# LexCorpus — coletor de provas e gabaritos

Coletor "burro" que raspa PDFs de concursos e os entrega ao LexLearn por
**arquivos em storage** + **eventos RabbitMQ** (padrão Claim Check). Toda a
inteligência de domínio (parsing, normalização, classificação) é do LexLearn.

O acordo entre os dois sistemas está em `CONTRATO.md` (v2.0). Os formatos de
mensagem estão em `schema/evento.schema.json` e `schema/sidecar.schema.json`.

## Estrutura

    lexcorpus/
      items.py        ArquivoItem — um PDF + seu metadado (espelha o contrato)
      pipelines.py    download (FilesPipeline) -> sidecar -> evento RabbitMQ
      settings.py     throttle, retry, storage, cadeia de pipelines
      util.py         slugify, checksum SHA-256, escrita atômica
      spiders/
        exemplo_banca.py   modelo de um spider por banca
    schema/           os JSON Schemas do contrato v2.0
    tests/            validação do esqueleto contra os schemas

## Rodar (modo dev, sem RabbitMQ)

    pip install -r requirements.txt
    scrapy crawl exemplo_banca      # grava eventos em eventos_debug/*.json

Em produção: `RABBIT_ENABLED=True` no settings (ou via -s), e o publisher
emite no exchange `lexcorpus.events` com routing key `concurso.disponivel`.

## Storage (onde os PDFs são gravados)

O caminho físico vem da variável de ambiente `LEXCORPUS_FILES_STORE`, com
fallback para `/data/raw/exams` (o ponto de montagem do volume no Docker).

**Dev local** — LexCorpus e LexLearn são pastas irmãs em `~/Workspace/`, e o
storage é o do LexLearn. Defina (caminho absoluto, funciona de qualquer pasta):

    export LEXCORPUS_FILES_STORE="$HOME/Workspace/LexLearn/LexLearn-v3/data/raw/exams"

    # depois é só rodar normalmente:
    scrapy crawl pci -a url="https://..." -a banca="..." -a concurso="..." -a cargo="..."

**Docker** — o volume compartilhado é montado em `/data/raw/exams`; não precisa
definir nada (o default já aponta pra lá).

O contrato (§7) mantém `pasta_uri` com esquema `file://` no evento, então o
LexLearn resolve o ponteiro independentemente de onde o storage esteja montado.

## Um spider por banca

Copie `spiders/exemplo_banca.py`, ajuste os seletores e a classificação de
papel/cargos/tipo_prova. Não implemente download, checksum, sidecar ou evento
no spider — isso é dos pipelines.
