# Integração LexCorpus → LexLearn: teste de ponta a ponta APROVADO

**Data:** 2026-08-04
**De:** Othon (LexCorpus)
**Para:** Time LexLearn
**Assunto:** O LexCorpus já publica eventos conformes ao Contrato v2.0. Podem começar o consumidor.

---

## Resumo

O LexCorpus está coletando provas e gabaritos da **fonte primária** (site oficial
da banca) e publicando eventos no RabbitMQ conforme o **Contrato de Integração v2.0**.
O fluxo completo foi testado de ponta a ponta com dados reais e **passou**:

> coleta (API oficial CEBRASPE) → download dos PDFs no storage compartilhado →
> checksum SHA-256 → sidecar `.meta.json` → evento publicado no RabbitMQ →
> recebido e validado por um consumidor de teste.

Este documento registra o resultado e dá ao time do LexLearn o que é preciso para
construir o consumidor do lado de vocês.

---

## O que foi testado

- **Banca:** CEBRASPE
- **Concurso:** PRF 2021 (`slug = prf_21`)
- **Arquivos coletados:** 20 (11 provas, 6 gabaritos definitivos, 3 gabaritos preliminares)
- **Integridade:** o SHA-256 gravado no sidecar bate com o SHA-256 do PDF baixado
  (verificado manualmente com `sha256sum`). Os PDFs abrem e são válidos.
- **Storage:** `.../data/raw/exams/cebraspe/prf_21/` (pasta plana, um `.meta.json`
  por PDF, conforme §2 do contrato).
- **Fila:** evento publicado no exchange `lexcorpus.events`, routing key
  `concurso.disponivel`, mensagem persistente (`delivery_mode=2`).
- **Consumidor de teste:** recebeu o evento, parseou o JSON e confirmou (`ack`).
  A fila esvaziou (0 Ready / 0 Unacked), indicando ciclo limpo.

---

## Parâmetros da fila (o que o consumidor do LexLearn precisa saber)

| Item | Valor |
|------|-------|
| Exchange | `lexcorpus.events` |
| Tipo do exchange | `topic`, durável |
| Routing key (novo concurso) | `concurso.disponivel` |
| Routing key (atualização) | `concurso.atualizado` |
| Sugestão de binding | `concurso.#` (captura ambas) |
| Persistência | mensagens publicadas com `delivery_mode=2` |
| Broker de dev | `amqp://guest:guest@localhost:5672/` |

> **Importante:** um exchange sem fila vinculada **descarta** as mensagens. O
> consumidor do LexLearn deve declarar sua própria fila (durável) e vinculá-la ao
> exchange `lexcorpus.events` **antes** de o LexCorpus publicar. Uma vez vinculada,
> a fila retém os eventos mesmo que o consumidor esteja temporariamente fora do ar.

---

## Formato do evento (exemplo real, resumido)

O evento segue `schema/evento.schema.json` (v2.0). Abaixo, uma versão abreviada do
evento real do PRF 2021 (o array `arquivos` tem 20 itens; mostramos 2):

```json
{
  "schema_version": "2.0",
  "event": "concurso.disponivel",
  "event_id": "1481c234-8c30-4e50-96f5-1d60034be740",
  "produced_at": "2026-08-04T15:05:57Z",
  "banca": "cebraspe",
  "concurso": "prf_21",
  "pasta_uri": "file:///.../data/raw/exams/cebraspe/prf_21/",
  "arquivos": [
    {
      "nome": "578_PRF_001_01.PDF",
      "papel": "prova",
      "cargos": ["policial_rodoviario_federal"],
      "checksum_sha256": "6c795d351085976400c140bf89a06bf9ff9fcbb0624a7401b991caa961d7a02f",
      "tamanho_bytes": 114833,
      "multi_cargo": false,
      "vigente": true
    },
    {
      "nome": "GAB_DEFINITIVO_578_PRF_001_01.PDF",
      "papel": "gabarito_definitivo",
      "cargos": ["policial_rodoviario_federal"],
      "checksum_sha256": "48b945fad23ba4ac4af8ef2eb48f097a8fe1fe078d7abaa7773e519cc7d7cab5",
      "tamanho_bytes": 28230,
      "multi_cargo": false,
      "vigente": true
    }
  ],
  "fonte_url": "https://cdn.cebraspe.org.br/concursos/prf_21/arquivos/...",
  "extra": {
    "rotulos": {
      "banca": "CEBRASPE",
      "concurso": "POLÍCIA RODOVIÁRIA FEDERAL",
      "cargos": { "policial_rodoviario_federal": "POLICIAL RODOVIÁRIO FEDERAL" }
    }
  }
}
```

### Leitura rápida dos campos-chave

- `banca` / `concurso` / `cargos[]` são **slugs** (minúsculos, sem acento, underscore).
  O rótulo original de cada um está em `extra.rotulos` — usem os slugs como chave
  canônica e os rótulos para exibição.
- `pasta_uri` é o ponteiro para a pasta do concurso, com esquema (`file://` hoje,
  `s3://` no futuro). Resolvam o arquivo como `pasta_uri` + `nome`. **`nome` nunca
  tem barra** (garantido por schema), então não há risco de path traversal.
- `papel` ∈ {`prova`, `gabarito_preliminar`, `gabarito_definitivo`}. É declarado
  pelo LexCorpus a partir da fonte; **o LexLearn tem autoridade para reclassificar**.
- `checksum_sha256`: recalculem ao baixar e comparem. Se não bater, rejeitem o
  arquivo (sugestão: dead-letter), não travem a fila.
- `event_id` (UUID): usem para **idempotência**. Guardem os já processados e ignorem
  repetições — o LexCorpus pode reenviar em caso de dúvida.

---

## Passo a passo para validar do lado de vocês

1. Subam o RabbitMQ de dev:
   ```
   docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
   ```
2. Declarem a fila de vocês e vinculem ao exchange `lexcorpus.events` com routing
   key `concurso.#` (há um consumidor de exemplo em `consumer_teste.py` no repo do
   LexCorpus — serve de ponto de partida).
3. Peçam para o LexCorpus publicar (ou rodem o crawl de teste). O evento chega na
   fila de vocês.
4. Para cada arquivo do array: resolvam `pasta_uri` + `nome`, baixem/leiam do
   storage compartilhado, **recalculem o SHA-256** e comparem com o do evento.

---

## Pontos ainda em aberto (não bloqueiam o consumidor)

Estes itens são conhecidos e serão tratados; documentados aqui por transparência:

- **Associação arquivo → cargo em concursos multi-cargo.** No PRF (cargo único) a
  associação é trivial. Para concursos com vários cargos, o LexCorpus ainda pode
  marcar arquivos com o conjunto de cargos do concurso, sem discriminar por arquivo.
  Refinamento planejado.
- **Ciclo preliminar → definitivo.** A modelagem existe no contrato (`vigente`,
  `substituido_por`), mas a lógica incremental (arquivar o preliminar quando o
  definitivo chega) ainda será implementada. Numa primeira coleta, tudo vem com
  `vigente: true`.
- **"Padrão de resposta" da prova discursiva** hoje é classificado como `prova`.
  Pode ser reclassificado pelo LexLearn.
- **§9 do contrato:** ainda precisamos alinhar juntos o vocabulário de slugs de
  cargo e confirmar os nomes definitivos de exchange/routing keys.

---

## Referências no repositório do LexCorpus

- `CONTRATO.md` — o contrato de integração v2.0 (fonte da verdade)
- `schema/evento.schema.json` — schema do evento (validem contra ele)
- `schema/sidecar.schema.json` — schema do sidecar `.meta.json`
- `consumer_teste.py` — consumidor de exemplo (ponto de partida para o de vocês)

Dúvidas ou ajustes no contrato: como diz o §8, qualquer mudança é combinada entre
os dois lados antes de virar código. Podem me chamar.
