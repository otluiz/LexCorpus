<!-- Arquivo:  docs/architecture/decisions/ADR-0005-migracao-spiders-antigos-para-heuristics.md
     Função:   registra o plano de migração dos spiders antigos (pci.py,
               fcc.py — e, na sequência, fgv.py) para o módulo compartilhado
               de heurísticas (ADR-0004), com pré-requisitos e critérios de
               aceite. Originado da nota "trabalho pendente" do ADR-0004. -->

# ADR-0005: Migração dos spiders antigos para heuristics.py

- Status: Aceito
- Data: 2026-08-09

## Contexto

O ADR-0004 decidiu que as heurísticas de classificação de papel vivem no
módulo `lexcorpus/heuristics.py` (funções puras, override por parâmetro
nomeado), e registrou a migração dos spiders antigos como "trabalho
pendente (ver backlog)". Em 09/08 a `base.py` foi alinhada à decisão
(base fina que delega), o que deixou o mapa de duplicação explícito:

| Spider | Situação | Ação |
|---|---|---|
| `estrategia.py`, `agregador_generico.py` | herdam a base fina | nada a fazer |
| `pci.py` | cópia local da versão FRACA (regex + `eh_relevante` + `classificar_papel`) | migrar |
| `fcc.py` | variante local FORTE (descarte vence "prova" no nome) | migrar passando `re_descartar` como parâmetro |
| `fgv.py` | variantes locais ESTRITAS e corretas para o padrão FGV | migrar depois, promovendo as regex a parâmetros |
| `cebraspe.py` | classifica por `descricaoArquivo` da API (fonte estruturada, não heurística) | fora do escopo |

Sem este plano, a duplicação remanescente perpetua o problema que o
ADR-0004 quis encerrar: correções não propagam (o bug do "oficial",
reproduzido em 09/08, atingiria cada cópia) e overrides divergem de
semântica entre os pontos de uso.

## Decisão

Migrar `pci.py` e `fcc.py` para `heuristics.py`, nesta ordem e com estes
pré-requisitos encadeados:

1. **Pré-requisito [#A]: corrigir o bug do "oficial" no módulo.**
   `RE_GAB_DEF` casa "Gabarito Oficial Preliminar" como definitivo.
   Enquanto não corrigir, migrar spiders é propagar o bug. Casos de
   teste obrigatórios: "Gabarito Oficial Preliminar" → `gabarito_preliminar`;
   "Gabarito Oficial Definitivo" → `gabarito_definitivo`; "Gabarito"
   (sem qualificador) → `gabarito_definitivo`.
2. **`fcc.py`**: trocar as cópias locais por
   `heuristics.classificar_papel(texto, url, re_descartar=RE_DESCARTAR_FCC)`.
   O descarte forte vira PARÂMETRO — o módulo trata `re_descartar`
   informado como regime forte (descarta mesmo com "prova" no nome;
   caso real: `resultado_preliminar_prova_objetiva.pdf`). O desempacote
   do wrapper rybena (`?file=`) **permanece no spider** — é específico
   da fonte, não é heurística de papel.
3. **`pci.py`**: trocar as cópias locais pelo import do módulo (regime
   default, fraco). ATENÇÃO: migrar junto com a revisão do falso
   positivo "editais de resultado com prova no nome" (item do backlog) —
   é o mesmo território de código.
4. **`fgv.py`** (depois de 1–3): promover `_RE_GAB_DEF`/`_RE_GAB_PRE`
   estritos e `eh_relevante` locais a parâmetros nomeados; adotar
   `make_item` da base no lugar da montagem manual do `ArquivoItem`.
   A lógica de bloco temático/tipo de prova é da fonte e fica no spider.
5. **Testes como parte da migração** (não depois): criar
   `tests/test_heuristics.py` com os golden cases acima + nomes reais
   coletados (FGV, FCC, CEBRASPE), e fixture de parse para cada spider
   migrado (modelo: `tests/test_pci_parse.py`).

Critério de aceite por spider: nenhuma regex/`classificar_papel`/
`eh_relevante` local restante; classificação idêntica aos golden cases
conhecidos; suíte pytest verde.

## Alternativas consideradas

1. **Migrar para o módulo (escolhida).** Fonte única de verdade;
   correções propagam para todos os spiders; heurísticas testáveis sem
   instanciar Scrapy (alimenta o CI, item do backlog).
2. **Manter cópias locais por spider (rejeitada).** Status quo que gerou
   o ADR-0004: as cópias já divergem (fraca/forte/estrita) e cada bug
   precisa de N correções.
3. **Migrar todos herdando a base fina (rejeitada como obrigação).**
   Herdar `LexCorpusSpider` dá `make_item` de graça, mas acoplaria os
   spiders antigos à hierarquia só para classificar — o ADR-0004 já
   decidiu que classificação não exige herança. Adotar `make_item` fica
   recomendado onde a montagem manual existe (fgv.py), não obrigatório.

## Consequências

**Positivas**

- Uma cópia das heurísticas: a correção do bug do "oficial" vale para
  todos os spiders no momento do merge.
- O override forte da FCC vira explícito e testado no ponto de chamada.
- `test_heuristics.py` nasce com casos reais e vira rede de segurança
  permanente do CI.

**Negativas / custos**

- Risco de regressão silenciosa de classificação nos spiders migrados —
  mitigado pelos golden cases e fixtures exigidos no critério de aceite.
- A migração do `pci.py` fica acoplada à revisão do descarte fraco
  (falso positivo), o que aumenta seu escopo.

## Notas

- O `cebraspe.py` permanece com classificador próprio por decisão
  consciente: `descricaoArquivo` da API oficial é dado estruturado, não
  heurística — forçar o módulo ali seria regressão de precisão. Se um
  dia a API mudar, reavaliar.
- Este ADR formaliza a nota "trabalho pendente" das Consequências do
  ADR-0004; o acompanhamento operacional continua no backlog (item
  "Migrar pci.py e fcc.py para heuristics.py").
