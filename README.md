# LexCorpus — coletor de provas e gabaritos

Coletor "burro" que raspa PDFs de concursos e os entrega ao LexLearn por
**arquivos em storage** + **eventos RabbitMQ** (padrão Claim Check). Toda a
inteligência de domínio (parsing, normalização, classificação) é do LexLearn.

O acordo entre os dois sistemas está em `docs/CONTRATO.md` (v2.0). Os formatos de
mensagem estão em `schema/evento.schema.json` e `schema/sidecar.schema.json`.

## Estrutura

    lexcorpus/
      items.py        ArquivoItem — um PDF + seu metadado (espelha o contrato)
      heuristics.py   classificação de papel (prova/gabarito) em funções
                      puras — fonte da verdade (ADR-0004)
      pipelines.py    download (FilesPipeline) -> sidecar -> evento RabbitMQ
      settings.py     throttle, retry, storage, cadeia de pipelines
      util.py         slugify, checksum SHA-256, escrita atômica
      buscador.py            busca por palavra-chave: descobre a banca e o ano
                             dos concursos anteriores de um órgão (ADR-0006)
      catalogo.py            catálogo dinâmico vigentes/passados
                             (ano{mês/dia}/banca/cargos, YAML versionado)
      orquestrador.py        mapeia banca→spider, monta o `scrapy crawl` ou
                             gera esqueleto parametrizável (ADR-0006)
      consulta.py            CLI de consulta ao storage: filtra os sidecars
                             por banca/concurso/cargo/papel e lista ou copia
                             os PDFs (lado leitor; não baixa nada da rede)
      scheduler.py           a automação: lê watchlist.yaml, dispara os alvos
                             cujo cron venceu (subprocesso por crawl, estado
                             em JSON), sequencial por banca
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

## Rodar (modo automático — scheduler + watchlist)

O `watchlist.yaml` (raiz) é a lista versionada de alvos: `banca`, `spider`,
`params`, `cron`, `ativo`. O scheduler dispara `scrapy crawl` para cada alvo
ativo cujo cron venceu — sequencial dentro de cada banca, bancas distintas
em paralelo — e grava o último disparo num estado JSON (re-run sem tick
novo não faz nada):

    python -m lexcorpus.scheduler --once          # roda os vencidos e sai
    python -m lexcorpus.scheduler --loop 60       # verifica a cada 60s
    python -m lexcorpus.scheduler --once --dry-run  # só imprime os comandos

O estado fica em `./state/scheduler_state.json` (override: `--state-file`
ou `LEXCORPUS_STATE_FILE`; no Docker, apontar para o volume `/state`).
Adicionar um concurso à automação = commit de uma entrada no watchlist.

## Configuração por ambiente

O `settings.py` lê tudo de variáveis de ambiente (os defaults cobrem dev local
e Docker). O `.env.example` (raiz) é o template — copie para `.env` (não
versionado) e ajuste por ambiente; credenciais de produção ficam só lá:

- `LEXCORPUS_FILES_STORE` — onde os PDFs são gravados (ver seção Storage)
- `RABBIT_ENABLED` / `RABBIT_URL` — publicação de eventos (produção)
- `EVENTOS_OUT_DIR` — pasta dos eventos em modo debug (`RABBIT_ENABLED=False`)
- `LEXCORPUS_WATCHLIST` / `LEXCORPUS_STATE_FILE` — scheduler

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

## Consultar o storage

`lexcorpus/consulta.py` lista (ou copia) os PDFs já coletados, filtrando pelos
metadados dos sidecars — sem baixar nada da rede:

    python -m lexcorpus.consulta --banca cesgranrio
    python -m lexcorpus.consulta --concurso bnb0124 --papel prova
    python -m lexcorpus.consulta --cargo analista_bancario_1 --papel gabarito
    python -m lexcorpus.consulta --banca fgv --copiar /tmp/selecao

`--papel gabarito` abrange preliminar + definitivo; `--cargo` inclui os
consolidados multi-cargo (`cargos: ["*"]`, contrato §5). O storage vem de
`--store`, de `LEXCORPUS_FILES_STORE` ou do default Docker.

## Descoberta por palavra-chave (ADR-0006)

Quando um concurso concorrido entra "no ar" (ex.: Transpetro 2026), use o
fluxo de descoberta para achar quem examinou os certames anteriores:

    # 1. buscar na Internet a banca/ano dos concursos anteriores
    python3 -m lexcorpus.buscador "Transpetro" --cargo "analista de sistemas" \
        --saida /tmp/tp.json          # JSON completo
    python3 -m lexcorpus.buscador "Transpetro" --cargo "analista" --resumo   # legível

    # 2. orquestrar a coleta a partir da melhor candidata (rank 0)
    python3 -m lexcorpus.orquestrador --from-json /tmp/tp.json \
        --rank 0 --concurso transpetro_2023
    # → imprime o `scrapy crawl` pronto (banca com spider) ou gera o
    #   esqueleto parametrizável (banca sem spider, ex.: Cesgranrio/WAF)

    # 3. manter o catálogo dinâmico vigentes/passados
    python3 -m lexcorpus.catalogo add --organ Transpetro --edital-ano 2026 \
        --edital-data 2026-08-01 --cargos "Analista de Sistemas,Administrador"
    python3 -m lexcorpus.catalogo update-busca --organ Transpetro   # sugere banca
    python3 -m lexcorpus.catalogo move --chave transpetro_2026      # vigente→passado
    python3 -m lexcorpus.catalogo expire --hoje 2027-01-01          # migra vencidos

Backends de busca: `--backend bing` (default, estável), `duckduckgo`,
`google`. A descoberta triangula no catálogo do órgão no PCI Concursos
(ADR-0001: índice, nunca fonte de PDF) — o campo
`confirmado_por_catálogo_do_órgão` indica que a banca+ano foram vistos em
certames reais daquele órgão.

## Catálogo dinâmico

`catalogo.yaml` mantém as listas **vigentes** (certames no ar, status
`aguardando_banca|coletando|pausado`) e **passados** (histórico, hierarquia
ano → banca → cargos). A lista é viva: novos editais entram em vigentes,
encerrados migram para passados — manualmente (`move`) ou por política de
idade (`expire --max-dias-vigente`, ideal em cron semestral).

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
3. `docs/ADR-0006-busca-por-palavra-chave.md` — descoberta, catálogo e
   orquestração (ADR-0006)
4. `docs/architecture/decisions/` — ADRs (ADR-0004 heurísticas,
   ADR-0005 migração dos spiders antigos; ADR-0001/0002 pendentes — ver
   backlog)
5. `schema/` — os JSON Schemas do contrato
