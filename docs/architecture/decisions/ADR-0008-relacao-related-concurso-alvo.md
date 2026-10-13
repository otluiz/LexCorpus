<!-- Arquivo:  docs/architecture/decisions/ADR-0008-relacao-related-concurso-alvo.md
     Função:   espelho, do lado do LexCorpus, do ADR-0004 do LexPubli
               (concurso-alvo): a relação `related` (mesma banca, mesma
               carreira) e o campo `carreira` no catálogo. -->

# ADR-0008: Provas relacionadas ao concurso-alvo (relação `related`)

- Status: **Aceito** em 2026-10-13, junto com o ADR-0004 do LexPubli
- Data: 2026-10-13
- Documento completo: **ADR-0004 do LexPubli** (`docs/architecture/decisions/ADR-0004-concurso-alvo.md`).
  Este ADR registra só o que muda no LexCorpus.
- Estende: **ADR-0007** (consumidor do LexPubli e política de coleta).
- Mantém: o contrato v2.1 com o LexLearn, que não muda.
- **REVISÃO (2026-10-13, LexPubli ADR-0005):** a automação é só LexPubli ↔ LexCorpus. O
  LexLearn não consome `contest.discovery.*` nem `document.*`; recebe as provas pelo v2.1,
  como qualquer coleta, e a geração lá é por demanda, decidida pelo Administrador dele. A
  automação LexPubli ↔ LexCorpus passa a usar o broker do LexPubli (URL separada aqui); o
  broker do LexLearn fica só com o v2.1.

## Contexto

O ecossistema adotou o **concurso-alvo**: um concurso anunciado e aprovado no LexPubli
(chave `contest_id`) para o qual o LexLearn prepara estudo no estilo da banca. O DNA do
LexLearn é por banca, e o perfil declara N=1 concurso por banca. Mais provas da **mesma
banca** e da **mesma carreira** é o que ele precisa.

O LexCorpus já faz isso à mão: o bloco "Carreira fiscal (26/09)" do `watchlist.yaml`
coletou Sefaz-AL 2019, Sefaz-DF 2019 e Sefaz-CE 2021 (Cebraspe) porque a Sefaz-AL 2026
abriu. Hoje o consumidor (ADR-0007) só conhece `current` e `previous` (mesmo órgão).

## Decisão

1. **Nova relação `related`:** mesma banca **e** mesma carreira, em outros órgãos. A carreira
   vem do alvo (`career`, no `contest.discovery.created` v1.1), escolhida pelo curador do
   LexPubli numa lista fixa (`fiscal`, `controle`, `tribunais`, `juridica`, `policial`,
   `legislativa`, `bancaria`, `administrativa`, `estatais`, `saude`, `educacao`).
2. **Campo `carreira` no catálogo.** Preenchido sozinho nos concursos que chegam por evento.
   No acervo já existente, o operador marca uma vez (ex.: `sefaz_ce_21`,
   `sefaz_al_19_auditor` e `seec_auditor_19` → `fiscal`).
3. **Acervo primeiro.** O consumidor procura `related` no que já foi coletado (catálogo +
   StateStore). Achou: liga ao alvo (`aquisicao_lexpubli`, `relation = related`) e responde
   `document.acquisition.completed` **na hora**, sem crawl. A ligação é por
   `(banca, concurso, contest_id)`, então um concurso do acervo serve a vários alvos.
4. **Descoberta depois.** Buscar relacionados fora do acervo (inventários das bancas,
   buscador) entra numa etapa posterior, sob a mesma política do ADR-0007: banca validada,
   confirmação, limite.
5. **Política** (`politica_coleta.yaml`, seção nova `relacionados`):
   - mesma banca obrigatória (o DNA é por banca);
   - no máximo N por alvo (inicial: 5), os mais recentes primeiro;
   - outras bancas da mesma carreira desligadas no início.
6. **Contratos (cópia em `schema/lexpubli/v1/`):** o LexCorpus atualiza a cópia **antes** de
   o LexPubli emitir `career`, `exam_date` e `contest.discovery.updated`. Os schemas usam
   `additionalProperties: false`, então até campo opcional novo seria recusado com a cópia
   antiga. O LexCorpus só passa a emitir `relation: related` depois que o LexPubli aceitar o
   valor (o LexLearn não consome estes eventos).

## Consequências

- O alvo recebe as provas da carreira **sem coleta nova** quando o acervo já as tem. É o
  caso da Sefaz-AL 2026 com o acervo fiscal da Cebraspe.
- A prática manual da "carreira fiscal" vira política versionada.
- O catálogo ganha um campo com disciplina de curadoria (a lista é do LexPubli; o LexCorpus
  não inventa valores).
- `contest.discovery.updated` passa a ser consumido: carreira ou banca podem chegar depois
  do anúncio, e o consumidor reavalia o alvo (idempotente, pela ligação existente).
