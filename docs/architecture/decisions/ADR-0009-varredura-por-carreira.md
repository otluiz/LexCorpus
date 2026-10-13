<!-- Arquivo:  docs/architecture/decisions/ADR-0009-varredura-por-carreira.md
     Função:   registra a varredura do acervo por carreira (a "fonte 2" do
               related, ADR-0008): inventário por banca, sugestão de carreira,
               confirmação do operador e coleta com teto pelos spiders de banca. -->

# ADR-0009: Varredura por carreira (inventário por banca, coleta com teto)

- Status: **Proposto**
- Data: 2026-10-13
- Autor: Prof. Othon Luiz
- Estende: **ADR-0008** (relação `related`; este ADR é a "fonte 2", a descoberta)
  e **ADR-0007** (política de coleta).
- Relacionados: LexPubli ADR-0002 (fonte oficial; agregador só como índice),
  ADR-0004 (lista de carreiras; carreira inferida só como sugestão) e ADR-0005
  (o v2.1 entrega ao LexLearn automaticamente); ADR-0006 (buscador).
- Mantém: o contrato v2.1 com o LexLearn, que não muda.

## Contexto

Com o ADR-0008, um concurso anunciado pelo LexPubli recebe na hora o que já
está no **acervo**, da mesma banca e da mesma carreira. Mas o acervo só tem o
que alguém pediu: hoje são três concursos fiscais da Cebraspe, coletados à mão.
Para que o `related` sirva a qualquer concurso anunciado, o acervo precisa
crescer **por carreira**, antes do anúncio.

O dono do produto decidiu (13/10):

- usar a lista de 11 carreiras do LexPubli ADR-0004 §2 como **eixo da busca**;
- começar pelas carreiras **fiscal** e **tribunais**;
- teto inicial de **5 concursos por carreira e banca**.

O que foi verificado nas fontes oficiais (13/10):

| Banca | Listagem oficial dos concursos | Observação |
|---|---|---|
| Cebraspe | **Não há.** A API (`/cebraspe/eventos`) exige o identificador; o sitemap do site não lista concursos; a página pública é renderizada por JS | A API responde por slug; o resolvedor do ADR-0007 já sonda `{órgão}_{aa}` |
| FGV | Sim: `conhecimento.fgv.br/concursos` (modo `descoberta` do spider, já existe) | Gera `eventos_debug/descoberta/fgv.jsonl` |
| Cesgranrio | Sim: `/api/PortalEventos` | Lista os eventos do portal; a profundidade do histórico precisa ser medida |
| FCC | Fora (`robots.txt` bloqueia `/concursos/`; ADR-0007) | Lacuna relevante em **tribunais**, onde a FCC é forte |

## Decisão

### 1. A carreira é o eixo da busca, não do spider

Os spiders continuam **um por banca**: é a banca que define como um site é
lido. Não haverá spider por carreira. A carreira decide **o que procurar** e
**o que coletar**; a coleta é feita pelos spiders de banca que já existem,
pelo scheduler, sob a política.

### 2. Inventário por banca (só leitura, sem PDF)

Um passo novo, `python -m lexcorpus.varredura inventariar --banca <b>`, lista
os concursos que a fonte publica e grava `state/inventario/<banca>.jsonl`
(slug oficial, órgão, ano, cargos, URL oficial, data da leitura):

- **FGV:** a listagem oficial (modo descoberta).
- **Cesgranrio:** o `PortalEventos`.
- **Cebraspe:** o **índice PCI por banca** dá os candidatos (órgão, ano); cada
  um só entra no inventário depois de **confirmado na API oficial** por slug.
  É a regra do LexPubli ADR-0002: agregador só como índice, documento só de
  fonte oficial.

Respeita `robots.txt`, nunca contorna Turnstile ou captcha, e só lê bancas de
`bancas_validadas`.

### 3. Sugestão de carreira por regras versionadas

`carreiras_regras.yaml` (versionado) mapeia termos de **órgão** e **cargo**
para uma carreira. No início, só as duas decididas:

- **fiscal:** Sefaz, Secretaria da Fazenda/Economia, Receita, "auditor
  fiscal", "fiscal de tributos", "auditor da receita";
- **tribunais:** TJ, TRF, TRT, TRE, TST, STJ, STF, CNJ, "analista
  judiciário", "técnico judiciário", "oficial de justiça".

A regra produz uma **sugestão**, nunca uma carreira gravada (LexPubli
ADR-0004: inferência só como sugestão). Um concurso que casa com mais de uma
carreira, ou com nenhuma, fica sem sugestão.

### 4. Confirmação do operador, em lote

`python -m lexcorpus.varredura revisar --carreira fiscal` mostra as sugestões
(órgão, ano, cargos, URL oficial) e o operador confirma ou descarta em lote.
O confirmado vai para:

- o **catálogo**, com a carreira (o mesmo `marcar_carreira` do ADR-0008);
- a fila de coleta da varredura (próximo item).

O descartado fica registrado, para não voltar a cada inventário.

### 5. Coleta com teto, pelos spiders de banca

Seção nova em `politica_coleta.yaml`:

```yaml
varredura:
  ativo: true
  carreiras: [fiscal, tribunais]
  max_por_carreira_e_banca: 5      # os mais recentes primeiro
  cron: "45 5 * * 2"               # 1x/semana: o ritmo de entrada no LexLearn
```

- Os confirmados viram alvos num watchlist **gerado e separado**
  (`state/watchlist.varredura.yaml`), como o automático do ADR-0007. O manual
  continua soberano.
- **O teto protege o LexLearn.** Tudo o que o LexCorpus coleta entra no
  LexLearn automaticamente pelo v2.1 (LexPubli ADR-0005). Com 2 carreiras,
  3 bancas e 5 por par, a primeira leva tem no máximo 30 concursos,
  espalhados por semanas.
- Concursos **mais recentes primeiro**: o estilo atual da banca vale mais
  para o DNA do que o de dez anos atrás.

### 6. Nenhum evento novo

A varredura não anuncia concurso e não fala com o LexPubli. Ela só faz o
acervo crescer. Quando o LexPubli anunciar um concurso da mesma banca e
carreira, o ADR-0008 liga o que a varredura trouxe e responde na hora.

## Alternativas consideradas

- **Um spider por carreira:** repetiria em 11 lugares o código de leitura de
  cada banca, e qualquer mudança de site teria de ser consertada em todos.
  Rejeitada.
- **Buscador web por carreira** ("provas auditor fiscal cebraspe" no
  DuckDuckGo/Bing): resultados ruidosos, captcha e dependência de terceiros.
  Pode virar fonte **de candidatos** depois, sob o mesmo gate (§4), nunca de
  documentos.
- **Carreira aplicada automaticamente, sem operador:** rejeitada no LexPubli
  ADR-0004. Erro de classificação vira coleta errada em escala, e a coleta
  entra no LexLearn sozinha.
- **Varrer tudo de uma vez, sem teto:** despejaria centenas de concursos na
  ingestão e no DNA do LexLearn. Rejeitada; o teto sobe por decisão.

## Consequências

**Positivas.**
- O `related` deixa de depender de coleta manual: as carreiras decididas
  ganham acervo antes de o concurso ser anunciado.
- O DNA do LexLearn ganha N por banca de forma controlada e com carreira
  declarada.
- Nada muda nos spiders, no v2.1 nem nos contratos com o LexPubli.

**Negativas / a monitorar.**
- O gate humano custa tempo do operador. A revisão é em lote para isso
  ficar aceitável.
- **Tribunais sem a FCC** ficam com cobertura parcial até a questão do
  `robots.txt` ser resolvida (pedido à FCC ou fonte alternativa oficial).
- A Cebraspe depende do índice PCI para os candidatos. Se o PCI mudar, os
  candidatos da Cebraspe param; os documentos continuam vindo só da fonte
  oficial.
- A profundidade do histórico do `PortalEventos` da Cesgranrio não foi
  medida.

## Implantação

1. `carreiras_regras.yaml` (fiscal, tribunais) e a função de sugestão, com
   testes de casos reais (Sefaz-CE, SEEC-DF, TRT, TJ).
2. `varredura inventariar` para FGV e Cesgranrio (listagens oficiais).
3. `varredura inventariar` para a Cebraspe (índice PCI + confirmação na API).
4. `varredura revisar` (lote) e o registro dos descartados.
5. Seção `varredura` na política; watchlist da varredura; scheduler lendo os
   três (manual, automático e varredura), o manual soberano.
6. Primeira leva com o operador: fiscal e tribunais, até 5 por banca.
