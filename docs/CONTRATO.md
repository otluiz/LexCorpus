# Contrato de Entrega — LexCorpus → LexLearn

**Versão do contrato:** 2.1 · **Status:** rascunho para revisão conjunta

Este documento define **o que o LexCorpus entrega e como**. É o único ponto de acordo entre
os dois sistemas. Enquanto o LexCorpus respeitar este contrato, os dois projetos evoluem de
forma independente. O LexCorpus **não precisa conhecer nada do funcionamento interno do
LexLearn**.

> **Novidade da v2.0:** o modelo agora comporta a realidade irregular das bancas —
> múltiplos cargos por concurso, múltiplos tipos de prova por cargo, gabaritos que cobrem
> vários cargos num arquivo só, e o ciclo de vida preliminar→definitivo. Toda essa
> estrutura vive no **metadado**, não em subpastas.
>
> **Novidade da v2.1:** campo opcional `caderno` — o código que a banca imprime no
> PDF (ex.: `BACEN13_002_04`), no arquivo e nos `segmentos`. Na Cebraspe é ele, não o
> rótulo do cargo, que casa a prova com o bloco do gabarito (ver Caso E). A mudança é
> aditiva: um evento 2.0 continua válido no schema 2.1. Implantação em §4.

---

## 1. Visão geral

O LexCorpus raspa **provas e gabaritos** de concursos públicos e os entrega por dois canais:

- **Um diretório compartilhado** carrega os PDFs (dado pesado).
- **Uma fila RabbitMQ** carrega um aviso leve apontando para a pasta.

Padrão **Claim Check**: o binário nunca viaja dentro da mensagem. Os dois sistemas não se
conhecem como código; a fila é um intermediário anônimo.

---

## 2. Onde escrever — pasta PLANA por concurso

O LexCorpus escreve **exclusivamente** dentro de:

```
/data/raw/exams/{banca}/{concurso}/
```

- **Só escreva em `exams/`.** Nunca em `laws/` (território do LexLearn) nem no banco.
- **Pasta plana:** todos os arquivos do concurso ficam soltos na mesma pasta. **NÃO crie
  subpastas** por cargo, tipo de prova ou histórico. Cargo, tipo e ciclo de vida vivem no
  **metadado** de cada arquivo, não na árvore de diretórios.
- Preserve o **nome original** dos arquivos. Não renomear.
- Slugs de `{banca}`/`{concurso}` em minúsculas, sem acento, underscore.
- Para cada PDF, escreva um sidecar `{nome}.meta.json` ao lado (ver §4).

### Por que plano, e não `{cargo}/{tipo}/`?

Porque a estrutura das bancas é irregular e não cabe numa árvore fixa. Um gabarito pode
cobrir **vários cargos** num arquivo só — ele não pertence a nenhuma pasta de cargo único.
Um cargo pode ter 4 tipos de prova; outro, nenhum. Forçar isso em pastas geraria exceções
sem fim. A pasta é um contêiner burro; a inteligência está no metadado.

---

## 3. As dimensões que o metadado expressa

Cada arquivo declara, no evento e no sidecar:

| Campo | O que é | Exemplo |
|-------|---------|---------|
| `papel` | prova ou gabarito (e qual) | `prova`, `gabarito_preliminar`, `gabarito_definitivo` |
| `cargos` | **lista** de cargos que o arquivo cobre | `["geografia"]`, `["geografia","ingles","informatica"]`, `["*"]` |
| `tipo_prova` | versão quando a banca embaralha | `"1"`, `"amarela"`, ou `null` |
| `multi_cargo` | um arquivo com vários cargos dentro | `true`/`false` |
| `segmentos` | (opcional) onde cada cargo/caderno está no arquivo multi-cargo | ver §5 |
| `caderno` | (opcional, v2.1) código do caderno como impresso no PDF | `"BACEN13_002_04"` |
| `vigente` | ciclo de vida do gabarito | `true` (atual) / `false` (preliminar arquivado) |
| `substituido_por` | (opcional) o definitivo que substituiu um preliminar | nome do arquivo |

`cargos` é **sempre uma lista**, mesmo com um cargo só. `["*"]` significa "todos os cargos"
quando a banca não discrimina.

### Slugs canônicos + rótulo cru preservado

`banca`, `concurso` e os itens de `cargos` são **slugs** (minúsculas, sem acento, underscore).
O slug é o dado canônico — estável, filesystem-safe, é a chave de correlação. O **rótulo
original** (como veio da fonte) não se perde: vai em `extra.rotulos`, opcional mas recomendado.

```json
"extra": {
  "rotulos": {
    "banca": "FGV",
    "concurso": "IF-Sergipe 2024 — Professor EBTT",
    "cargos": { "geografia": "Professor de Geografia", "ingles": "Professor de Inglês" }
  }
}
```

`rotulos.cargos` é um mapa `slug → rótulo`, cobrindo os slugs que circulam nas listas
`cargos` dos arquivos. Assim todo valor slugificado tem seu rótulo humano recuperável, sem
poluir os campos canônicos. O LexLearn reclassifica/normaliza a partir do slug; o rótulo é
para rastreabilidade e exibição.

### Nome do arquivo: basename puro, barra proibida

`arquivos[].nome` (evento) e `arquivo` (sidecar) são **basename puro** — sem barra
(`pattern "^[^/]+$"`). Como a pasta é plana, nenhum arquivo ativo tem subcaminho; proibir a
barra fecha a porta a *path traversal* (`../`) na resolução `pasta_uri + "/" + nome`.

---

## 4. O sidecar e a fila

- Sidecar `{nome}.meta.json` ao lado de cada PDF — metadado de origem + as dimensões acima.
  **O LexCorpus nunca escreve no banco do LexLearn.** O metadado viaja pelo filesystem.
  Validado por `schema/sidecar.schema.json`. O LexLearn trata como **entrada não-confiável**:
  valida antes de usar.
- Evento na fila: exchange `lexcorpus.events` (topic), routing keys `concurso.disponivel` e
  `concurso.atualizado`. **Um evento por concurso**, com a lista plana de arquivos. Validado
  por `schema/evento.schema.json`. `schema_version` = `"2.0"` ou `"2.1"`.

### Implantação de uma versão nova: o consumidor primeiro

O schema é do LexCorpus (dono), mas o LexLearn valida com a **cópia** que carrega. Uma
versão nova só pode ser emitida depois que o LexLearn tiver a cópia nova — senão ele
recusa tudo. Por isso a versão emitida é configuração, não código:

1. O LexCorpus publica o schema novo (aceita a versão antiga e a nova).
2. O LexLearn atualiza a cópia e confirma.
3. O LexCorpus liga a versão nova: `LEXCORPUS_CONTRATO_VERSAO=2.1`.

Até o passo 3, o LexCorpus emite `2.0` e retira da saída o que a 2.0 não conhece
(`caderno`; e o segmento que só tinha `caderno`).

---

## 5. Casos concretos (exemplos reais)

### Caso A — concurso com vários cargos, prova 1:1 por cargo (ex.: IFS)

Cada cargo tem seu conjunto de provas; cada prova tem seu gabarito. Tudo na mesma pasta:

```
exams/ifs/2024/
├── ifs_geografia_prova_t1.pdf        + .meta.json
├── ifs_geografia_gabarito_t1.pdf     + .meta.json
├── ifs_informatica_prova_t1.pdf      + .meta.json
├── ifs_informatica_gabarito_t1.pdf   + .meta.json
└── ...
```

Metadado de uma prova:
```json
{ "papel": "prova", "cargos": ["geografia"], "tipo_prova": "1", "vigente": true }
```

### Caso B — múltiplos tipos de prova por cargo (embaralhamento)

Um cargo (informática) com 4 tipos. Cada tipo é um arquivo, distinguido por `tipo_prova`:

```json
{ "papel": "prova", "cargos": ["informatica"], "tipo_prova": "1" }
{ "papel": "prova", "cargos": ["informatica"], "tipo_prova": "2" }
{ "papel": "prova", "cargos": ["informatica"], "tipo_prova": "3" }
{ "papel": "prova", "cargos": ["informatica"], "tipo_prova": "4" }
```

### Caso C — gabarito único subdividido por vários cargos (ex.: cebraspe)

Um arquivo, vários cargos dentro. `multi_cargo: true`, `cargos` lista todos. Se o LexCorpus
conseguir extrair onde cada cargo está, declara em `segmentos` (opcional):

```json
{
  "papel": "gabarito_definitivo",
  "cargos": ["geografia", "ingles", "informatica"],
  "tipo_prova": null,
  "multi_cargo": true,
  "segmentos": [
    { "cargo": "geografia",   "pagina_inicio": 1, "pagina_fim": 2 },
    { "cargo": "ingles",      "pagina_inicio": 3, "pagina_fim": 4 },
    { "cargo": "informatica", "pagina_inicio": 5, "pagina_fim": 7 }
  ]
}
```

Se o LexCorpus **não** conseguir extrair a localização, omite `segmentos` — o LexLearn
desmembra por conta própria. Declarar o que der; não inventar.

### Caso D — ciclo de vida preliminar → definitivo

O preliminar sai primeiro; depois o definitivo. **O preliminar não é apagado** — é
arquivado como histórico via metadado (não via subpasta). Quando o definitivo chega, o
LexCorpus publica `concurso.atualizado` e marca o preliminar:

```json
// gabarito preliminar, depois que o definitivo saiu:
{ "papel": "gabarito_preliminar", "cargos": ["geografia"], "tipo_prova": "1",
  "vigente": false, "substituido_por": "ifs_geografia_gabarito_definitivo_t1.pdf" }

// o definitivo:
{ "papel": "gabarito_definitivo", "cargos": ["geografia"], "tipo_prova": "1",
  "vigente": true }
```

Ambos ficam na mesma pasta. "Histórico" é uma propriedade (`vigente: false`), não um lugar.

### Caso E — o código do caderno casa prova e gabarito (ex.: cebraspe, v2.1)

Na Cebraspe o rótulo não identifica o cargo ("Analista - Área 1" e "Técnico - Área 1";
blocos de conhecimentos básicos que servem várias áreas). O que identifica é o código do
caderno, impresso no cabeçalho da prova (`||BACEN13_002_04N800189||`) e sozinho na linha,
no topo de cada página do gabarito "todos os cargos":

```json
// a prova
{ "papel": "prova", "cargos": ["analista_area_2"], "caderno": "BACEN13_002_04" }

// o gabarito: um bloco por caderno; basta cargo OU caderno em cada segmento
{ "papel": "gabarito_definitivo", "cargos": ["*"], "multi_cargo": true,
  "segmentos": [
    { "caderno": "BACEN13_001_01", "pagina_inicio": 1, "pagina_fim": 1 },
    { "caderno": "BACEN13_002_04", "pagina_inicio": 2, "pagina_fim": 2 }
  ] }
```

`caderno` é o código **como impresso no PDF** (sem o sufixo de controle `N...`), só letras,
dígitos, `_` e `-`. **Não se deriva do nome do arquivo:** às vezes coincide (BACEN 2013),
às vezes não (PRF 2021: prova `633_PRF_CF_001.PDF`, gabarito `MATRIZ_633_PRF_001_00`).
Sem leitura do PDF ou declaração explícita, omite — o LexLearn continua extraindo o código
por conta própria.

---

## 6. Regras obrigatórias (garantias que o LexLearn assume)

1. Salve PDF + sidecar **antes** de publicar o evento.
2. **Escrita atômica** (`.tmp` → `rename`).
3. **Checksum SHA-256** por arquivo, no evento e no sidecar.
4. **`event_id` UUID único** por evento.
5. Declare `papel`, `cargos` (lista), e — quando aplicável — `tipo_prova`, `multi_cargo`,
   `vigente`.
6. Nunca coloque binário na mensagem.
7. Sempre inclua `schema_version` (`"2.0"` ou `"2.1"`; a 2.1 só depois do LexLearn — §4).
8. Mensagens persistentes (`delivery_mode=2`).
9. **Pasta plana:** nunca crie subpastas dentro de `{concurso}/`.
10. **Escreva apenas em `exams/`.** Nunca em `laws/`, nunca no banco.
11. Ao sair o definitivo: publique `concurso.atualizado`, adicione o definitivo e marque o
    preliminar com `vigente: false` + `substituido_por`.

---

## 7. Ponteiro (URI)

`pasta_uri` deve ser URI com esquema: `file://` hoje, `s3://` futuro. Nunca caminho cru.

---

## 8. Checklist de conformidade

- [ ] Escreve em `exams/{banca}/{concurso}/` — **pasta plana, sem subpastas**
- [ ] Um `.meta.json` por PDF, conforme `schema/sidecar.schema.json` (v2.1)
- [ ] `cargos` sempre como lista; `["*"]` para todos
- [ ] `banca`/`concurso`/`cargos` como slugs; rótulo cru em `extra.rotulos` (recomendado)
- [ ] `nome`/`arquivo` = basename puro, sem barra (`^[^/]+$`)
- [ ] `tipo_prova` quando a banca embaralha; `null` caso contrário
- [ ] `multi_cargo: true` + `cargos` completo para gabarito subdividido; `segmentos` se der
- [ ] Ciclo de vida: `vigente` correto; preliminar arquivado com `substituido_por`
- [ ] `caderno` só quando lido do PDF ou declarado — nunca derivado do nome (v2.1)
- [ ] Escrita atômica, checksum SHA-256, `event_id` UUID, `schema_version` (2.0 | 2.1)
- [ ] `pasta_uri` com esquema; mensagens persistentes
- [ ] Nunca escreve fora de `exams/`; nunca toca o banco

---

## 9. Pontos a alinhar

- Nível de detalhe viável em `segmentos` (página exata? âncora? só a lista de cargos?) —
  depende do que o scraper consegue extrair de cada banca.
- `papel` para padrão de resposta de discursiva: hoje sai como `prova`, embora faça as
  vezes de gabarito (ex.: `PRF_21_PADRAO_DE_RESPOSTA_DEFINITIVO.PDF`).
- Vocabulário de slugs de cargo (normalização fica no LexLearn, mas convém combinar os mais
  comuns).
- Nomes definitivos de exchange e routing keys.
