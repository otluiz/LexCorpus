# ADR-0006 — Busca por palavra-chave e orquestração de descoberta de bancas

**Status:** proposto → aceito
**Data:** 2026-08-19
**Autores:** Prof. Othon Luiz, Manus AI

> **ERRATA (25/08):** a premissa "Cesgranrio bloqueada por WAF (Azure Front
> Door 403), exige esqueleto Playwright" — citada na Decisão §3, nas
> Consequências (risco b) e no Fluxo de referência — deixou de valer em
> 18/08: o portal foi reformulado e expõe uma API JSON pública
> (`/api/PortalEventos/{id}`), sem browser. `spiders/cesgranrio.py` é um
> spider real (`-a evento_id=N`) e o `SPIDERS_POR_BANCA` do orquestrador já
> aponta para ele. O mecanismo de esqueleto + rota Playwright permanece
> para bancas futuras com WAF. O restante do ADR segue vigente.

## Contexto

O LexCorpus já é funcional para **baixar** provas de bancas conhecidas
(CEBRASPE, FGV, FCC etc.) e alimenta o diretório compartilhado do LexLearn.
Porém o fluxo de **descoberta** era manual: quando um concurso concorrido
entra "no ar" (ex.: Transpetro 2026), o operador precisava descobrir por
conta própria qual banca examinou os certames anteriores — informação que
quase nunca é a mesma do certame vigente (a Transpetro 2026 é Cesgranrio,
mas os concursos anteriores também foram Cesgranrio; em outros órgãos a
banca troca entre certames, ex.: PRF 2018 → Cebraspe, PRF 2012 → Cespe).

Além disso, não existia um lugar único para responder a três perguntas que
o operador faz com frequência: (1) "quais concursos estou coletando agora?";
(2) "quem examinou este órgão antes e em que ano?"; (3) "quais certames já
encerraram e viraram histórico?". A lista de concursos muda a cada
ano/semestre — uns entram, outros saem.

## Decisão

Adotar uma camada de **descoberta + orquestração** sobre o Scrapy existente,
sem tocar em spiders, items, nem pipelines (contrato v2.0 / Claim Check
permanece intacto), composta por três módulos novos:

1. **`lexcorpus/buscador.py`** — cliente de busca por palavra-chave.
   Pesquisa na Internet por "{órgão} {cargo} concurso anterior", extrai
   candidatas {banca, ano, concurso, cargos} com heurísticas
   (menções explícitas de banca, co-ocorrência órgão+banca numa janela de
   250 caracteres, ano do certame) e **triangula em agregadores-índice**
   (PCI Concursos, seguindo o ADR-0001: o agregador é ÍNDICE, nunca fonte
   de PDF). Cada candidata recebe um score de confiança ranqueável.
   Múltiplos backends parametrizáveis (`--backend bing|duckduckgo|google`);
   o Bing HTML é o padrão por ser o mais estável em IP de datacenter.

2. **`lexcorpus/catalogo.py`** — Catálogo de Concursos dinâmico
   (`catalogo.yaml` na raiz), com as listas **`vigentes`** e **`passados`**,
   hierarquizadas por ano{mês/dia} → banca → cargos. Concursos entram em
   `vigentes` com status `aguardando_banca` quando o edital sai, e migram
   para `passados` via `move` (manual) ou `expire` (automático por data,
   roda a cada ano/semestre). O comando `update-busca` consulta o buscador
   e sugere candidatas para os vigentes sem banca — a promoção é sempre
   decisão do operador.

3. **`lexcorpus/orquestrador.py`** — fecha o ciclo descoberta → coleta.
   Recebe o resultado do buscador (`--from-json`), mapeia a banca ao spider
   existente (tabela `SPIDERS_POR_BANCA`, editável), monta o comando
   `scrapy crawl` parametrizado honrando a assinatura do spider, ou gera um
   **esqueleto parametrizável** (herdando `LexCorpusSpider` e os métodos
   `make_item`/`eh_relevante`/`classificar_papel` do `base.py`) quando não
   existe spider. Bancas bloqueadas por WAF (Cesgranrio — Azure Front Door
   403) recebem o esqueleto com o roteiro Playwright já comentado no
   `custom_settings`. O fallback para agregadores (`--fallback pci`)
   existe apenas como confirmação de índice, nunca para download de PDF.

## Consequências

**Positivas.** O operador ganha um fluxo de uma linha para responder
"quem fez o concurso anterior de X?" e disparar a coleta:

```bash
python3 -m lexcorpus.buscador "Transpetro" --cargo "analista de sistemas" --saida /tmp/tp.json
python3 -m lexcorpus.orquestrador --from-json /tmp/tp.json --rank 0 --concurso transpetro_2023
# → imprime: scrapy crawl <spider> -a ...  (ou gera esqueleto se banca sem spider)
```

O catálogo dá a visão dinâmica vigentes/passados por ano/banca/cargos,
coerente com `watchlist.yaml` (alvos novos nascem com `ativo=false`).
A descoberta não modifica nenhuma parte do pipeline existente — o contrato
Claim Check continua válido porque os novos spiders gerados herdam
`LexCorpusSpider` e emitem `ArquivoItem` pelos métodos padrão.

**Negativas / riscos.** (a) Motores de busca sem API (Bing/DDG/Google HTML)
podem servir captcha ou mudar o layout; mitigado por backends alternativos
e por nunca depender do snippet para download real. (b) Bancas com WAF
(Cesgranrio) exigem a imagem Playwright do Docker para o spider gerado
funcionar. (c) A heurística de extração pode confundir banca citada na
SERP com banca que examinou o órgão; mitigado pela triangulação com o
catálogo do órgão no PCI (`confirmado_por_catálogo_do_órgão`), que lista
os certames reais {banca, ano, cargo} — evidência mais forte que o snippet.

## Fluxo de referência (caso Transpetro)

```
Transpetro entra no ar (ago/2026)
        │
        ▼
catálogo: add --organ Transpetro --edital-ano 2026 --cargos "..."
        │  (status: aguardando_banca)
        ▼
catalogo update-busca --organ Transpetro   ←  ou buscador.py standalone
        │
        ▼  candidatas: Cesgranrio (2025/2023...), confirmado_por_catálogo_do_órgão=True
        ▼  (PCI lista 50 certames Transpetro×Cesgranrio, 2006–2023)
        ▼
orquestrador --from-json ... --concurso transpetro_2023
        │
        ▼  Cesgranrio não tem spider → gera lexcorpus/spiders/cesgranrio.py
        │  (esqueleto + roteiro Playwright, ativo=false)
        ▼
operador valida o seletor contra o HTML real → ativo=true no watchlist
        ▼
scrapy crawl cesgranrio -a url=... -a concurso=transpetro_2023
        ▼
pipelines intactos → /data/raw/exams/cesgranrio/transpetro_2023/
        │  (provas + sidecar .meta.json com checksum)
        ▼
LexLearn consome o diretório compartilhado
```

## Validação executada

| Teste | Resultado |
|---|---|
| `buscador "Transpetro" --cargo "analista de sistemas"` | 39 resultados analisados; Cesgranrio top-1 com coocorrência + catálogo do órgão confirmados |
| `buscador "PRF" --cargo "agente"` | Cebraspe descerto; catálogo PCI mostra certames 1998–2021 |
| `scrapy crawl cebraspe -a slug="prf_21"` (via orquestrador) | 40 arquivos em `cebraspe/prf_21/` (provas, gabaritos, sidecars `.meta.json`) |
| `catalogo add/list/move/expire/update-busca` | Ciclo vigentes→passados completo |
| `tests/test_buscador.py` (6 testes, backends mockados) | Todos PASS |
| Esqueleto `cesgranrio.py` | `ast.parse` válido; herda `LexCorpusSpider` |
