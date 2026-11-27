# CLAUDE.md

Este arquivo orienta o Claude Code (claude.ai/code) ao trabalhar com código neste repositório.

## O que é isto

LexCorpus é o coletor "burro" da suíte Lex. Spiders Scrapy baixam provas e gabaritos de
concursos e os entregam ao LexLearn como arquivo em storage mais um evento RabbitMQ
(padrão Claim Check). Toda a inteligência de domínio (parsing, normalização,
classificação) fica com o LexLearn. A exceção é a classificação de papel
(prova/gabarito), que está em `lexcorpus/heuristics.py` (ADR-0004).

## Suíte Lex

O LexCorpus é um dos três projetos da suíte Lex (com LexPubli e LexLearn). O mapa
estrutural da suíte (fronteiras, contratos, brokers, storage compartilhado, máquinas)
está em `../ARQUITETURA-DA-SUITE.md`, fora deste repositório. **Leia esse arquivo antes de
qualquer mudança estrutural**: um canal novo entre projetos, um contrato, o broker, um
caminho em `/data`, ou onde algo roda. Se a mudança alterar o que o mapa descreve, ela
precisa de um ADR e de uma revisão do mapa, como manda a §9 dele.

Fronteiras que este projeto não cruza:

- O LexPubli avisa e o LexCorpus decide o que coletar (`politica_coleta.yaml`, ADR-0007).
  Essa conversa passa só pelo broker do LexPubli (`lexpubli-rabbitmq`), nunca pelo do
  LexLearn (`rabbitmq`).
- O consumidor entra só na rede do LexPubli. Os crawls entram nas duas.
- Com o LexLearn vale o contrato `docs/CONTRATO.md` (v2.1) e `schema/{evento,sidecar}.schema.json`.
  O `pasta_uri` (`file:///data/raw/exams/...`) tem de resolver igual nos dois containers.
- Os schemas de `schema/lexpubli/v1/` são uma cópia de `LexPubli/contracts/events/v1/`.
  A fonte é o LexPubli, então não edite só aqui.
- O nome do projeto compose é fixo em `lexcorpus`. Sem ele, o volume de estado muda de nome
  e o coletor republica tudo como novo.

## Onde está o resto

- `docs/BACKLOG.org`: o estado atual e o que falta.
- `docs/architecture/decisions/`: os ADRs do projeto. O ADR-0006 está em `docs/`.
- `README.md`: a estrutura e como rodar.
