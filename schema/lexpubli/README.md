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

**Fronteira (LexPubli ADR-0005, LexCorpus ADR-0008):** a automação por eventos
existe só entre LexPubli e LexCorpus. O LexLearn não consome estes eventos;
o que chega a ele continua sendo só o v2.1 (`concurso.*`). Um concurso que o
LexPubli aprova faz o LexCorpus coletar e alimentar o acervo do LexLearn, mas
nunca dispara geração de questões lá: isso é por demanda, decidido pelo
Administrador do LexLearn.
