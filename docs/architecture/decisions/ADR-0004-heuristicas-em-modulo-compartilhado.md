<!-- Arquivo:  docs/architecture/decisions/ADR-0004-heuristicas-em-modulo-compartilhado.md
     Função:   registra a decisão de manter as heurísticas de classificação de
               papel (prova/gabarito) em MÓDULO compartilhado, e não em classe
               base de spiders. -->

# ADR-0004: Heurísticas de classificação em módulo compartilhado (heuristics.py)

- Status: Aceito
- Data: 2026-08-07

## Contexto

As regex de classificação de papel (`RE_GAB_DEF`, `RE_GAB_PRE`, `RE_GAB`,
`RE_PROVA`, `RE_DESCARTAR`) e a lógica `classificar_papel()`/`eh_relevante()`
nasciam dentro de cada spider (`pci.py`, `fcc.py`), e o `fgv.py` precisaria
das mesmas — caminho certo para 3+ cópias divergentes (o backlog já tinha um
item para extração).

Num primeiro desenho (07/08), a extração foi feita como **classe base**
`LexCorpusSpider` (`spiders/base.py`): os spiders herdariam as heurísticas e
faríamos override por atributo de classe. A revisão apontou o problema:
**acoplamento forte** — para usar as heurísticas, todo spider passa a ser
obrigado a herdar de `LexCorpusSpider`. Se um spider futuro precisar de outra
hierarquia (ex.: `SitemapSpider`, uma base com Playwright para a Cesgranrio),
a única saída seria herança múltipla ou reimplementação. Classificação de
papel é uma *função pura sobre texto* — não há motivo para ela exigir ser um
spider.

## Decisão

As heurísticas vivem no módulo **`lexcorpus/heuristics.py`**, como constantes
(regex compilados) e funções puras:

- `classificar_papel(texto, url, *, re_descartar=None, re_gab_def=None, ...) -> str | None`
- `eh_relevante(texto, url, *, re_descartar=None) -> bool`

Overrides por banca são **parâmetros nomeados** (ex.: a FCC passa seu
`re_descartar` forte) — sem copiar a função, sem herdar de nada.
Semântica do override: o `RE_DESCARTAR` default é **fraco** (só descarta se
não houver pista de prova/gabarito); um `re_descartar` passado pela banca é
**forte** (descarta sempre, mesmo com "prova" no nome — caso real da FCC:
"resultado_preliminar_prova_objetiva.pdf").

`spiders/base.py` **permanece, mas como base fina e opcional**: guarda a
fábrica `make_item()` (montagem do `ArquivoItem` do contrato v2.0) e um método
`classificar_papel()` que apenas **delega** ao módulo, expondo hooks de
classe (`RE_DESCARTAR = ...`) como conveniência. Nenhuma lógica de
classificação vive na base.

## Alternativas consideradas

1. **Módulo de funções puras (escolhida).** Composição: qualquer código —
   spider, pipeline, script, teste — importa e usa. Override por parâmetro é
   explícito no ponto de chamada e testável isoladamente (alimenta o CI).
2. **Classe base `LexCorpusSpider` com heurísticas (rejeitada).** Ergonômica
   no caso comum, mas acopla classificação à hierarquia de spiders; herança
   única do Python vira restrição arquitetural sem ganho correspondente.
3. **Manter cópias por spider (rejeitada).** Foi o status quo que gerou o
   item de backlog: cópias divergem (a FCC já tinha variante própria do
   descarte) e correções não propagam.

## Consequências

**Positivas**

- Qualquer spider usa as heurísticas independentemente de de quem herda.
- Testes unitários das heurísticas não precisam instanciar spiders.
- O caminho fica aberto para spiders com hierarquias especiais
  (Playwright/Cesgranrio, `SitemapSpider`) sem herança múltipla.
- A cadeia de técnicas de extração com fallback (item do backlog) pode
  evoluir na base fina sem arrastar a classificação junto.

**Negativas / custos**

- Dois pontos de importação possíveis (`heuristics` direto ou via base) —
  mitigado documentando que a base apenas delega e que o módulo é a fonte
  da verdade.
- Migração dos spiders antigos (`pci.py`, `fcc.py`) fica como trabalho
  pendente (ver backlog).

## Notas de migração

- `cebraspe.py`, `agregador_generico.py`, `estrategia.py`: já ajustados —
  seguem herdando a base fina (pelo `make_item`) e a classificação flui por
  delegação ao módulo.
- `fcc.py`: trocar as cópias locais por `heuristics.classificar_papel(...)`,
  passando o `re_descartar` forte como parâmetro (ou `RE_DESCARTAR` de classe
  se herdar a base). O desempacote do wrapper rybena **permanece no spider** —
  é específico da fonte, não é heurística de papel.
- `fgv.py`: nasce importando `heuristics` (ou herdando a base fina).
- `pci.py`: migração junto com a revisão do descarte fraco (item "Falso
  positivo" do backlog).

## Atualização (2026-08-09) — situação real da implementação

Revisão do código no commit `5348271` mostrou que a implementação
diverge desta decisão em quatro pontos:

1. **`base.py` não delega — duplica.** A base fina prevista ("nenhuma
   lógica de classificação vive na base") não se concretizou: a classe
   tem suas próprias regex e lógica, cópia do regime FRACO. Consequência
   prática: override de classe `RE_DESCARTAR` tem semântica fraca,
   enquanto o parâmetro `re_descartar` do módulo é forte — o mesmo
   override se comporta diferente conforme o ponto de importação.
2. **`fgv.py` nasceu com cópias locais** (`_RE_GAB_*`, `eh_relevante`),
   sem importar o módulo nem herdar a base, e monta o `ArquivoItem`
   manualmente. As regex locais são mais estritas e estão corretas para
   o padrão FGV — devem virar parâmetros nomeados na migração, não ser
   descartadas.
3. **`cebraspe.py` não foi refatorado** ao contrário do registrado acima
   ("já ajustados"): segue com `classificar_papel` local e sem
   `make_item`. (Há argumento para exceção: a classificação dele vem da
   `descricaoArquivo` da API — fonte estruturada, não heurística.)
4. **Bug no módulo e na base:** `RE_GAB_DEF` inclui "oficial" como
   qualificador, então "Gabarito Oficial Preliminar" (rótulo padrão da
   FGV) é classificado como `gabarito_definitivo`. Reproduzido em teste
   direto das funções em 09/08.

A **decisão permanece** (módulo de funções puras + base fina que
delega). Os itens de convergência estão no backlog: "Alinhar base.py
com o ADR-0004", "BUG: Gabarito Oficial Preliminar" e "Migrar pci.py e
fcc.py para heuristics.py".
