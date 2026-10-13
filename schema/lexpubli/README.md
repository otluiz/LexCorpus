# Contratos LexPubli × LexCorpus (cópia)

**Esta pasta é uma CÓPIA.** O dono dos schemas é o LexPubli
(`LexPubli/contracts/events/v1/`, ADR-0002 do LexPubli, ADR-0007 daqui).
Não edite aqui: mude no LexPubli e copie de novo, seguindo a regra
"consumidor primeiro" (o guia está em `LexPubli/contracts/README.md`).

O LexCorpus valida contra esta cópia:
- toda mensagem **recebida** (`contest.discovery.created`, do exchange `lexpubli.events`);
- toda mensagem **emitida** (`document.acquisition.*`, no exchange `lexcorpus.events`).

Isto não tem relação com o contrato v2.1 LexCorpus → LexLearn
(`schema/evento.schema.json`, `docs/CONTRATO.md`), que continua igual.
