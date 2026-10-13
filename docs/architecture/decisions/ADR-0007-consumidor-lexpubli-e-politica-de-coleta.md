<!-- Arquivo:  docs/architecture/decisions/ADR-0007-consumidor-lexpubli-e-politica-de-coleta.md
     Função:   espelho, do lado do LexCorpus, do ADR-0002 do LexPubli: o
               LexCorpus passa a consumir os concursos aprovados no LexPubli e
               a coletar automaticamente o que a política permitir. -->

# ADR-0007: Consumidor do LexPubli e política de coleta automática

- Status: **Aceito** em 2026-10-12, junto com o ADR-0002 do LexPubli
- Data: 2026-10-12
- Documento completo: **ADR-0002 do LexPubli**
  (`LexPubli/docs/architecture/decisions/ADR-0002-fronteira-lexpubli-lexcorpus.md`).
  Este ADR registra só o que muda no LexCorpus.
- Revisa: **ADR-0006** (a regra "a promoção é sempre decisão do operador").
- Mantém: **ADR-0001** (agregador é índice, nunca fonte de PDF) e o
  contrato v2.1 com o LexLearn, que não muda.

## Contexto

O LexPubli (portal público de concursos) publica `contest.discovery.created`
no exchange `lexpubli.events` quando a curadoria humana dele aprova um
concurso. Hoje o LexCorpus não consome eventos: o `catalogo add` e a
promoção de candidatas do buscador são manuais.

## Decisão

1. **O LexPubli avisa e o LexCorpus decide.** O LexPubli não escolhe spider,
   não baixa PDF e não roda o buscador. Tudo isso continua aqui.
2. **Consumidor novo** (`lexcorpus/consumidor.py`, processo de longa duração):
   - fila `lexcorpus.lexpubli.contests`, ligada a `lexpubli.events` com
     `contest.discovery.*`;
   - idempotência pelo `event_id`, no StateStore;
   - dead-letter para mensagem inválida.
3. **O que o consumidor faz com o concurso aprovado:**
   - upsert em `vigentes` no catálogo, com chave `contest_slug` e o
     `contest_id` guardado; sem banca, o status é `aguardando_banca`;
   - **concurso atual:** se a banca está validada e o alvo foi resolvido,
     entra no watchlist automático;
   - **provas anteriores:** roda o `buscador` (órgão e cargo) e passa as
     candidatas pela política (item 4).
4. **Política em vez de aprovação caso a caso.** Este é o trecho que revisa
   o ADR-0006: o operador decide a política, e a política decide os casos de
   rotina. A política é configuração versionada:
   - **bancas validadas:** só entra uma banca com crawl real já conferido.
     Hoje seriam cebraspe, fgv, fcc e cesgranrio;
   - **candidata do buscador:** `confirmado_por_catálogo_do_órgão` e score
     mínimo;
   - **volume:** no máximo N certames anteriores por concurso (inicial: 3).

   O que a política não resolve vai para o operador, como hoje: banca sem
   spider, esqueleto gerado, candidata abaixo do limiar, spider novo.
5. **Watchlist automático em arquivo separado.** O `watchlist.yaml` manual
   continua versionado e soberano. O automático é gerado e pode ser
   descartado e reconstruído a partir dos eventos.
6. **Resposta ao LexPubli** no exchange `lexcorpus.events`, no envelope v1
   do LexPubli, com `correlation_id` = `event_id` do aviso:
   - `document.acquisition.completed`
   - `document.acquisition.awaiting_operator`
   - `document.acquisition.failed`

   O LexLearn se liga com `concurso.#`, então esses eventos **não** chegam a
   ele. Eles não carregam caminhos do storage.
7. **Revisar o `ROBOTSTXT_OBEY=False` da FCC.** A regra comum aos dois
   projetos é respeitar o robots.txt e não contornar proteções técnicas.

## Consequências

- O LexCorpus ganha um serviço de longa duração (o consumidor) ao lado do
  scheduler em lote.
- Os limiares da política precisam de calibração. No início, é melhor
  mandar demais à fila do operador.
- O ADR-0006 continua valendo para os comandos manuais do buscador, do
  catálogo e do orquestrador. Muda só quem dispara os casos de rotina.
