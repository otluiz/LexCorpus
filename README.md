# LexCorpus — coletor de provas e gabaritos

Coletor "burro" que raspa PDFs de concursos e os entrega ao LexLearn por
**arquivos em storage** + **eventos RabbitMQ** (padrão Claim Check). Toda a
inteligência de domínio (parsing, normalização, classificação) é do LexLearn.

O acordo entre os dois sistemas está em `CONTRATO.md` (v2.0). Os formatos de
mensagem estão em `schema/evento.schema.json` e `schema/sidecar.schema.json`.

## Estrutura

    lexcorpus/
      items.py        ArquivoItem — um PDF + seu metadado (espelha o contrato)
      heuristics.py   classificação de papel (prova/gabarito) em funções
                      puras — fonte da verdade (ADR-0004)
      pipelines.py    download (FilesPipeline) -> sidecar -> evento RabbitMQ
      settings.py     throttle, retry, storage, cadeia de pipelines
      util.py         slugify, checksum SHA-256, escrita atômica
      spiders/
        base.py              LexCorpusSpider — make_item (fábrica do
                             ArquivoItem do contrato v2.0)
        exemplo_banca.py     modelo de um spider por banca
        cebraspe.py          API oficial de eventos + CDN
        cesgranrio.py        API pública do portal (blocos de conteúdo)
        fcc.py               gabaritos oficiais (cadernos não são públicos)
        fgv.py               conhecimento.fgv.br (inclui o CNU)
        pci.py               PCI Concursos — modo teste (ADR-0001: PCI é
                             índice de descoberta, não fonte de PDFs)
        estrategia.py        blog do Estratégia Concursos (em validação)
        agregador_generico.py spider parametrizável por -a (em validação)
    schema/           os JSON Schemas do contrato v2.0
    tests/            validação do esqueleto contra os schemas
    docs/             CONTRATO.md, BACKLOG.org, comunicados e ADRs
                      (docs/architecture/decisions/)
    watchlist.yaml    alvos de coleta (automação — ver backlog)

## Bancas e fontes — status

| Fonte      | Spider               | Status                                   |
|------------|----------------------|------------------------------------------|
| CEBRASPE   | `cebraspe`           | ✅ funcional (API oficial)                |
| FCC        | `fcc`                | ✅ gabaritos oficiais (provas via PCI)    |
| FGV        | `fgv`                | ✅ funcional (+ descoberta de slugs)      |
| PCI        | `pci`                | 🔎 descoberta apenas (ADR-0001)           |
| Estratégia | `estrategia`         | 🧪 código pronto, validação em andamento  |
| Agregadores| `agregador_generico` | 🧪 código pronto, validação em andamento  |
| Cesgranrio | `cesgranrio`         | ✅ funcional (API pública do portal)      |

## Rodar (modo dev, sem RabbitMQ)

    pip install -r requirements.txt
    scrapy crawl exemplo_banca      # grava eventos em eventos_debug/*.json

Descoberta (só lista os concursos disponíveis, sem baixar PDF — grava JSONL
em `eventos_debug/descoberta/`):

    scrapy crawl fgv -a descoberta=1

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

Classificação de papel (prova/gabarito): importe `lexcorpus/heuristics.py`
(funções puras, override por parâmetro nomeado — ADR-0004) ou herde
`spiders/base.py` (`LexCorpusSpider`) pelo `make_item`. Não copie regex para
dentro do spider.

## Por onde começar

1. `docs/CONTRATO.md` — o acordo com o LexLearn (v2.0)
2. `docs/BACKLOG.org` — estado das tarefas e decisões recentes
3. `docs/architecture/decisions/` — ADRs (ADR-0004 heurísticas,
   ADR-0005 migração dos spiders antigos; ADR-0001/0002 pendentes — ver
   backlog)
4. `schema/` — os JSON Schemas do contrato
