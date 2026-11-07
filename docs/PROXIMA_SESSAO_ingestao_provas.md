# Ingestão de provas: o que o LexCorpus fez, o que cabe ao LexLearn

Atualizado em 26/09/2026, ao fim da sessão no LexCorpus (branch `feature/extensibilidade`, até `11d2b05`, com push feito).
O briefing original (deixado pelo LexLearn-v3 no mesmo dia) está no histórico do git; aqui fica o resultado.

## Resumo para o LexLearn

- **6 eventos esperam na fila `lexlearn.lexcorpus.ingest`**, sem consumidor. São 94 PDFs novos, contrato **2.0** (a cópia de schema do LexLearn aceita).
- **O RabbitMQ `lexlearn-v3-rabbitmq-1` estava parado.** Foi religado e continua de pé. A fila e o vínculo (`concurso.#`, DLQ `lexlearn.lexcorpus.dlq`) foram declarados **idênticos** a `backend/app/workers/lexcorpus_consumer.py:213-227`, porque exchange sem fila descarta a mensagem. O consumidor vai achar tudo igual ao que ele mesmo declararia.
- **As pastas novas estão com dono `root`** (o container do coletor grava como root). As antigas são do `otluiz`.

## O que entrou no acervo (`data/raw/exams/`)

| Pasta | PDFs | Observação |
|---|---|---|
| `cebraspe/sefaz_al_19_auditor` | 13 | 2 cargos; gabaritos definitivos |
| `cebraspe/seec_auditor_19` | 5 | Sefaz-DF. **A fonte não publica o caderno de conhecimentos básicos**, só o gabarito dele |
| `cebraspe/sefaz_ce_21` | 17 | 4 cargos. **Mesmo buraco:** gabarito de conhecimentos básicos sem o caderno |
| `cesgranrio/transpetro_psp_terra_nivelmedio_2023_1` | 15 | 13 provas + gabarito preliminar + final |
| `cesgranrio/transpetro_psp_terra_nivelsuperior_2023_2` | 32 | 28 provas + 2 padrões de resposta + 2 gabaritos |
| `cesgranrio/transpetro_psp_mar_2023_3` | 12 | 10 provas + 2 gabaritos |

A Transpetro 2023 é **um evento da Cesgranrio com três editais**. Cada edital virou um concurso, então o gabarito de um nível cobre só os cargos daquele nível (`cargos: ["*"]` dentro do concurso).

## O que mudou no que o LexLearn recebe

1. **`multi_cargo` na Cebraspe (`c282d4a`).** Antes, em concurso com vários cargos, **todo** arquivo saía com a lista inteira de cargos e `multi_cargo: true`. Agora o cargo vem da descrição da API ("PROVA OBJETIVA - CARGO 2"). Arquivo sem cargo declarado cobre todos; só o **gabarito** fica `multi_cargo: true` (pode vir por caderno, como o do BACEN13), a prova não.
2. **Contrato 2.1 publicado, ainda desligado (`13176ab`).** Campo opcional `caderno`: o código **impresso no PDF** (`BACEN13_002_04`), no arquivo e em cada segmento. O segmento passa a aceitar `cargo` **ou** `caderno`. Evento 2.0 segue válido no schema 2.1. Ver `docs/CONTRATO.md` §4 e Caso E.
3. **Padrão de resposta sai como `papel: prova`** (comportamento antigo, mantido). Na PRF, a discursiva já tem `PRF_21_PADRAO_DE_RESPOSTA_DEFINITIVO.PDF`; ela não "falta", está classificada como prova. Ficou em "pontos a alinhar" no contrato.

## Tarefas do LexLearn, nesta ordem

- [ ] **Subir o `lexcorpus_consumer`** para consumir as 6 mensagens.
- [ ] **Corrigir o dono das pastas novas**, se o LexLearn roda como `otluiz`:
      `sudo chown -R otluiz: data/raw/exams/{cebraspe/sefaz_al_19_auditor,cebraspe/seec_auditor_19,cebraspe/sefaz_ce_21,cesgranrio/transpetro_psp_*}`
- [ ] **Carregar o schema 2.1:** copiar `schema/evento.schema.json` e `schema/sidecar.schema.json` do LexCorpus para `contracts/lexcorpus/`, e **avisar**. Só então o LexCorpus liga `LEXCORPUS_CONTRATO_VERSAO=2.1`. Antes disso, o `caderno` é retirado da saída.
- [ ] **Usar o `caderno` para casar prova e bloco**, quando vier. Hoje o crawler **não** o preenche: o nome do arquivo não é fonte confiável (bate no BACEN 2013, não bate na PRF 2021). O primeiro a trazer o campo será o BACEN13, pela ingestão manual.
- [ ] **Apagar a cópia errada** `cebraspe/serpro13_analista_ti/gab_definitivo_todos_cargos-1.pdf` (é do BACEN13; mexe no banco, com confirmação).

## O que o LexCorpus deixou pronto, esperando o LexLearn

- **Ingestão manual por manifesto (`a7f719a`)**: `python -m lexcorpus.ingestao_manual <manifesto.yaml> [--gravar]`. PDF obtido fora do crawler entra pelos mesmos pipelines (sidecar, StateStore, evento), com `origem.metodo: ingestao_manual`. Recusa o mesmo conteúdo em outro concurso, sobrescrita de arquivo diferente e sidecar fora do schema.
- **BACEN13**: `manifestos/cebraspe/bacen13_analista.yaml`, com os cadernos e as 12 páginas do gabarito lidos dos PDFs. A simulação passa contra o acervo real. **Vai ser gravado depois do schema 2.1**: gravado em 2.0, perderia o `caderno`, e uma nova gravação em 2.1 não publicaria evento (o estado não veria mudança). A `fonte_url` (PCI) está marcada "A CONFIRMAR".
- **Guarda de duplicata (`93aa415`)**: o mesmo SHA-256 sob outro concurso é recusado já no download. Só enxerga o que está no StateStore, por isso arquivos postos à mão precisam passar pela ingestão manual.

## O que foi verificado e não tem o que buscar

- **PRF 2021, cadernos 632, 633 e 699**: a API da Cebraspe só tem o gabarito **preliminar**. O definitivo nunca foi publicado ali.
- **BACEN 2013 e SERPRO 2013** não existem na API da Cebraspe. O definitivo do SERPRO13 continua sem fonte.
- **Sefaz-CE 2026 (FCC, `sface125`)**: só o resultado preliminar publicado. Os cadernos da FCC ficam no portal do candidato; o gabarito sai como edital no fim.

## Coleta automática (watchlist)

Ativos: `prf_21`, `sefaz_al_19_auditor`, `seec_auditor_19`, `sefaz_ce_21`, `sefaz_al_26` (Cebraspe; edital aberto, inscrições até 21/10), `sface125` (FCC), Transpetro 2023 (portal `transpetro.cesgranrio.org.br`, evento 12), `transpetro_2026` (evento 22, prova em nov/dez) e `pc_ap_2026` (evento 26, prova em 06/12).
O watchlist agora aceita `settings:` (vira `-s`): a FCC roda com `ROBOTSTXT_OBEY=False`, decisão registrada no BACKLOG.

## Próximos passos do LexCorpus

- Gravar o BACEN13 assim que o LexLearn confirmar o schema 2.1.
- Pôr `user:` no `docker-compose.crawler.yml` para o coletor não gravar como root.
- Persistir o `caderno` no StateStore (hoje, arquivo que some da página volta sem ele nos eventos `concurso.atualizado`).
- Coletar os concursos da Cesgranrio que já estão no portal e não entraram: CEF 2024/2025, BNDES 2024, BASA 2024, BANESE 2025, CNU 2024, IPEA e Casa da Moeda 2023.
- Depois das provas, subir Sefaz-AL 2026 e PC-AP para 4x/dia enquanto o ciclo preliminar→definitivo estiver aberto.

## Armadilhas já pagas

1. **Exchange sem fila descarta a mensagem, em silêncio.** Antes de um crawl real, conferir `rabbitmqctl list_bindings`.
2. **Sem broker, não gravar.** O StateStore marcaria como publicado; o re-run cairia em "sem mudanças" e o evento nunca chegaria.
3. **Estado canônico é o volume `lexcorpus_lexcorpus-state`**. O `lexlearn-v3_lexcorpus-state` é cópia antiga, de antes de o compose mudar para cá.
4. **O `evento.schema.json` segue em mais de uma cópia.** O dono é o LexCorpus; mudança de contrato só vale depois de o LexLearn carregar a cópia nova.
5. **Na Cebraspe, o rótulo não identifica o cargo; o código do caderno sim.** Na Cesgranrio, um evento pode reunir vários editais.
