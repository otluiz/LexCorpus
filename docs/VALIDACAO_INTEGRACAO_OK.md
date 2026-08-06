# Validação da Integração LexCorpus → LexLearn: PONTA A PONTA APROVADO

**Data:** 2026-08-04
**De:** Othon (LexLearn)
**Para:** Time LexCorpus
**Assunto:** Recebemos, validamos e aceitamos o evento do PRF 2021. Transporte 100%.

---

## Resumo

O consumidor do LexLearn recebeu o evento publicado pelo LexCorpus, validou contra
o schema v2.0, leu os 20 PDFs do storage compartilhado, conferiu os checksums e
**aceitou o evento**. O fluxo completo funciona de ponta a ponta:

> spider (LexCorpus) → download + sidecar → evento publicado no RabbitMQ →
> consumidor (LexLearn) → validação de schema → leitura do storage →
> verificação de checksum SHA-256 → **evento aceito**

---

## Resultado do teste

- **Concurso:** cebraspe / prf_21
- **Arquivos no evento:** 20 (provas, gabaritos definitivos, gabaritos preliminares,
  padrão de resposta, prova discursiva)
- **Schema:** evento e sidecar validados contra v2.0 — **passou**, incluindo o campo
  `rotulos` (ver ajuste abaixo).
- **Checksum:** os 20 arquivos foram lidos do storage e o SHA-256 de cada um bateu
  com o declarado no evento — **todos conferem**.
- **Idempotência:** `event_id` registrado; reenvios do mesmo evento são ignorados.
- **Resultado:** `Evento validado e aceito`. Nada foi para a dead-letter.

---

## Ajustes que fizemos juntos (para o registro)

Durante a validação, três coisas precisaram de acerto. Documentadas para transparência:

1. **`RABBIT_ENABLED`** estava `False` no `settings.py` (modo debug, grava evento em
   disco). Ligado para `True` para publicar de fato no broker.

2. **`pasta_uri` com caminho do host.** O `_build_evento` usava
   `Path(files_store).resolve()`, que **segue o symlink** do storage e injetava o
   caminho físico do host no `pasta_uri`. Isso quebrava a leitura do lado do
   consumidor (que roda em container e vê o storage por outro mount).
   **Correção aplicada:** trocar `.resolve()` por `os.path.abspath()`, que normaliza
   o caminho **sem** seguir o symlink. Assim o `pasta_uri` sai como
   `file:///data/raw/exams/...` — portável entre host e container.
   *(Recomendação: manter `os.path.abspath()`; `.resolve()` volta a quebrar se houver
   symlink no caminho do storage.)*

3. **Campo `rotulos` no sidecar.** O sidecar traz `rotulos` (nomes de exibição) no
   nível raiz. O schema v2.0 não previa esse campo. Como a validação é
   responsabilidade do LexLearn, **oficializamos `rotulos` no `sidecar.schema.json`**
   (campo opcional). Não é preciso mudança do lado de vocês — só puxem a versão
   atualizada do schema.

---

## Infraestrutura: RabbitMQ é do LexLearn

Reforçando o combinado (ver nota anterior): o broker é infraestrutura do LexLearn.
O LexCorpus conecta ao broker existente (`localhost:5672` enquanto co-localizados) e
**não sobe um broker próprio**. Um broker paralelo causou conflito de rede nos
primeiros testes; resolvido.

Do lado do LexLearn, o rabbit agora declara rede explícita no compose (não fica mais
órfão em restart), e o consumidor declara sua fila durável com dead-letter,
vinculada a `concurso.#`.

---

## Pontos conhecidos para as próximas fases (não bloqueiam)

Estes apareceram no teste e serão tratados na Fase 2 do consumidor (processamento):

- **`PRF_21_PADRAO_DE_RESPOSTA_DEFINITIVO` vem com `papel: prova`.** Semanticamente é
  o gabarito da discursiva. O LexLearn vai **reclassificar** no processamento.
- **Preliminares e definitivos ambos com `vigente: true`.** Nesta coleta, o ciclo
  preliminar→definitivo ainda não arquiva o preliminar. Quando prova e gabarito do
  mesmo caderno chegam ambos "vigentes", o LexLearn manda para **quarentena / revisão
  humana** (não escolhe automaticamente) — conforme ADR-0006 §8 ("dois olhos").
- **Layout `{banca}/{concurso}/`:** confirmado correto no PRF. Vale validar em
  concursos multi-cargo quando surgirem.

---

## Próximos passos

1. **Fase 2 do consumidor (LexLearn):** processar os PDFs de fato — extrair, rotear
   por papel, reclassificar o padrão de resposta, quarentena de desempate.
2. **Ciclo de vida (LexCorpus):** implementar o arquivamento do preliminar quando o
   definitivo chega (`vigente: false` + `substituido_por`), publicando
   `concurso.atualizado`.
3. **Container do LexCorpus (evolução):** avaliar rodar o LexCorpus em container que
   monta o mesmo `/data/raw`, eliminando o descompasso host/container de vez.

Obrigado pelo trabalho — a integração está de pé. 🚀
