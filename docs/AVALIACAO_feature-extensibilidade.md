# Avaliação — branch `feature/extensibilidade` (LexCorpus)

- **Repositório:** github.com/otluiz/LexCorpus
- **Data da revisão:** 2026-08-09
- **Escopo:** commit `5348271` (08/08) — base agregadora, heuristics.py, agregador_generico.py, estrategia.py, reescrita do fgv.py, ADR-0004 e migração do backlog para org-mode

## 0. Aviso importante: a branch não tem commits exclusivos

No remoto, `main` e `feature/extensibilidade` apontam para **o mesmo commit** (`5348271`). O trabalho de extensibilidade foi commitado e está nas duas — ou o merge já aconteceu, ou o commit foi feito na `main` local antes de criar a branch. Se havia mais trabalho local, ele **não foi enviado** (`git push` pendente). Esta avaliação cobre o que está no GitHub.

## 1. O que a mudança traz (resposta curta: sim, agrega e evolui)

| Entrega | Valor |
|---|---|
| `lexcorpus/heuristics.py` | Classificação de papel (prova/gabarito) como **funções puras** com override por parâmetro — desacoplada de spider, testável sem instanciar Scrapy. É o desenho certo (ADR-0004) e encerra o ciclo de cópias divergentes que o backlog já temia. |
| `spiders/base.py` (`LexCorpusSpider`) | Fábrica `make_item` padroniza o `ArquivoItem` do contrato v2.0 — menos montagem manual, menos erro de slug/campo. |
| `agregador_generico.py` | Nova fonte **sem código novo**: URL, seletores e rótulos via `-a`. Dedup de PDFs no spider. É o maior salto de extensibilidade do commit. |
| `estrategia.py` | Segunda fonte agregadora (blog do Estratégia), com inferência de banca pelo título e paginação. |
| `fgv.py` reescrito | De exemplo a spider real: trata nomes opacos de PDF (b1101.pdf), associa cadernos ao bloco temático corrente, gera nomes semânticos, aceita `-a url=` (caso cpnu2 fora de /concursos/). Ensaiado com o CNU 2025. |
| ADR-0004 + BACKLOG.org | Documentação de arquitetura passa a existir de fato (primeiro ADR escrito); backlog ganha análise da cadeia de técnicas de extração com fallback. |

**Veredito:** a direção está correta e coerente com as decisões anteriores (monolito modular, PCI como descoberta, contrato v2.0 intacto — nada toca o contrato, então não há conflito com o §8/§9). O projeto evolui de "um spider por banca escrito à mão" para "plataforma com fontes configuráveis".

## 2. Problemas encontrados (antes do merge, na ordem de prioridade)

### 2.1 BUG [#A] — "Gabarito Oficial Preliminar" vira `gabarito_definitivo`

Reproduzido em teste direto das funções (09/08):

```
classificar_papel("Gabarito Oficial Preliminar", url) -> "gabarito_definitivo"   # ERRADO
```

Causa: `RE_GAB_DEF = gabarito.*(definitiv|final|oficial|pós-recurs)` — "oficial" é tratado como qualificador de definitivo, mas na FGV **todo** gabarito é "Oficial" (preliminar e definitivo). Como `RE_GAB_DEF` é checado antes de `RE_GAB_PRE`, o preliminar nunca é alcançado. O bug existe **nas duas cópias** (`heuristics.py` e `base.py`), e `estrategia.py`/`agregador_generico.py` herdam o erro. Dado que o ciclo preliminar→definitivo é parte central do contrato (§6.11), classificar preliminar como definitivo corrompe exatamente a semântica que o StateStore vai consumir. **O `fgv.py` escapou por acidente**: suas regex locais são mais estritas (`gabarito\s+oficial\s+definitiv`).

Correção sugerida: checar `RE_GAB_PRE` **antes** de `RE_GAB_DEF`, ou remover "oficial" dos qualificadores de definitivo.

### 2.2 [#B] — `base.py` contradiz o ADR-0004 (duplica em vez de delegar)

O ADR recém-escrito diz: "base fina e opcional... um método `classificar_papel()` que apenas **delega** ao módulo... **Nenhuma lógica de classificação vive na base.**" O código entregue faz o oposto: a base tem suas próprias regex e lógica (cópia da versão fraca). Consequências reais, já observadas:

- **Duas fontes da verdade** — exatamente o custo que o ADR queria evitar;
- **Semântica divergente de override**: no módulo, `re_descartar` passado = descarte **forte**; na base, sobrescrever o atributo `RE_DESCARTAR` = descarte **fraco** (a pista de prova/gabarito ainda resgata). Quem migrar a FCC pelos hooks de classe terá comportamento diferente do documentado;
- O bug 2.1 precisaria ser corrigido em dois lugares.

### 2.3 [#B] — Registros de "FEITO" que não correspondem ao código

O backlog marca como concluídos (07/08):

- "cebraspe.py refatorado (base fina + heuristics.py)" — **não aconteceu**: `CebraspeSpider(scrapy.Spider)`, `classificar_papel` local, item montado à mão;
- "Heurísticas em módulo + base fina desacoplada" — o módulo existe; a "base fina que delega" não;
- "CANCELADO Heurísticas como módulo solto" — contradiz o próprio ADR-0004 do mesmo dia (o módulo existe e é a decisão vigente);
- `fgv.py` nasceu com cópias locais de regex, sem `make_item` — caminho que o ADR explicitamente descartou.

Ou seja: o risco maior desta branch não é técnico, é de **processo** — documentação descrevendo código que não existe. A atualização de docs que acompanha este relatório marca essas entradas com erratas.

### 2.4 [#B] — Validação em HTML real do `estrategia` (feita em 09/08, parcial)

Teste direto contra o site (fetch da busca "prova gabarito" + 1 post, parse com os seletores do spider, sem crawl completo):

- ✅ Descoberta de posts funciona (155 links → 55 posts após dedup);
- ⚠️ O filtro `"/blog/" in url` deixa passar páginas `/blog/author/*` (crawl desperdiçado de páginas de autor);
- ❌ **Paginação quebrada**: nenhum seletor de next (`a.next`, `rel=next`, `.pagination a:last-child`) casa o HTML real — o spider para na página 1 mesmo com `-a paginas=5`;
- ⚠️ Post recente de "provas aplicadas" (Sefaz CE 2026) não tem `a[href$='.pdf']` de gabarito — só editais/retificações (corretamente descartados). Vale investigar como os gabaritos são publicados no blog (link sem extensão? botão JS?);
- ⚠️ `_inferir_banca` → "Desconhecida" em títulos sem nome de banca (limitação esperada; fallback pelo corpo do post é opção).

`agregador_generico.py` ainda não teve nenhum ensaio real.

### 2.5 [#C] — Menores

- `eventos_debug/cnu_2025.json` commitado: artefato de depuração no histórico (avaliar `.gitignore` se a pasta crescer);
- `fgv.py`: ciclo preliminar→definitivo emite ambos com `vigente=true` (consciente, documentado — depende do StateStore);
- `exemplo_banca.py` permanece com heurísticas próprias de exemplo — ok como modelo, mas vai desincentivar o padrão novo; apontar para `heuristics.py` no docstring.

## 3. Testes

Os testes existentes (`test_cebraspe_parse`, `test_pci_parse`, `test_e2e`) cobrem o estado anterior. **Nada da branch tem teste**: nem as heurísticas puras (que o ADR prometia "alimentar o CI"), nem os dois spiders novos, nem o `fgv.py`. Sugestão mínima antes do merge:

1. `tests/test_heuristics.py` — casos puros, incluindo os três do bug: "Gabarito Oficial Preliminar" → preliminar; "Gabarito Oficial Definitivo" → definitivo; "resultado_preliminar_prova_objetiva.pdf" com `re_descartar` forte → None;
2. Fixture de parse do `fgv.py` com o HTML do CNU (modelo `test_pci_parse.py`);
3. Import smoke de todos os spiders no CI (já existe item "CI: pytest no GitHub Actions" no backlog — esta branch reforça a prioridade).

## 4. Recomendação

**Não fazer merge ainda** — ou, como `main` e branch já estão no mesmo commit, tratar como hotfix na sequência:

1. Corrigir o bug do "oficial" (2.1) — é [#A] e contamina a semântica do contrato;
2. Fazer a base delegar de verdade (2.2) — uma correção passa a valer para todos os spiders;
3. Promover as regex locais do `fgv.py` a parâmetros nomeados do módulo e migrá-lo (elas são o caso de teste real mais valioso que o projeto já produziu);
4. Ajustar paginação/filtro de authors do `estrategia` antes de colocá-lo no watchlist;
5. Aplicar a atualização de documentação entregue junto (erratas + novos itens + README).

## 5. Documentação atualizada (entregue junto)

Arquivos revisados para commit (pasta `docs-atualizados/` + `atualizacao-docs.patch`):

- `docs/BACKLOG.org` — decisão de heurísticas corrigida; checkboxes do FGV atualizados; item do README em andamento; **2 itens novos** (bug do "oficial" [#A]; alinhar base.py com ADR-0004 [#B]); achados da validação do estrategia; erratas nas entradas FEITO/CANCELADO incorretas;
- `docs/architecture/decisions/ADR-0004-...md` — seção "Atualização (2026-08-09)" com a situação real da implementação; a decisão permanece;
- `README.md` — estrutura com os novos módulos, tabela de bancas/status, orientação de classificação via `heuristics.py` e seção "Por onde começar".

Para aplicar no repo:

```bash
cd LexCorpus
git checkout feature/extensibilidade
git apply atualizacao-docs.patch
git commit -am "docs: erratas ADR-0004/backlog, bug do 'oficial', validação estrategia (revisão 09/08)"
```
