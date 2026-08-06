#+TITLE: LexCorpus — Backlog
#+AUTHOR: Othon Luiz
#+STARTUP: overview
#+TODO: TODO EM-ANDAMENTO BLOQUEADO | FEITO CANCELADO

* Sobre o arquivo
  Backlog do LexCorpus em org-mode (mesmas convenções do BACKLOG do LexLearn).

  Estados (na linha do item, após os **):
  - TODO         → livre para pegar
  - EM-ANDAMENTO → alguém já está fazendo (confirme antes de pegar)
  - BLOQUEADO    → depende de algo (ver descrição); não começar ainda
  - FEITO        → concluído
  - CANCELADO    → descartado

  Prioridade: [#A] alta, [#B] média, [#C] baixa (após o estado).

  Como pegar uma tarefa:
  1. Escolha um item TODO (não EM-ANDAMENTO nem BLOQUEADO)
  2. Prefira [#A] > [#B] > [#C]
  3. Marque-o EM-ANDAMENTO (C-c C-t) antes de começar
  4. Cheque se tem "ALINHAR com o LexLearn" na descrição — itens que
     tocam o contrato (docs/CONTRATO.md) não se mudam unilateralmente (§8)

  Atalhos Emacs: C-c C-t alterna estado · C-c , prioridade · C-c C-c checkbox

* Decisões recentes (contexto para quem chega)
  - MONOLITO MODULAR: 1 repositório, 1 imagem Docker, N containers/jobs.
    NÃO um container/imagem por banca. O que varia por banca é spider
    (código) e agenda (config), não artefato. Formalizar em ADR-0002.
  - PCI CONCURSOS: usar SÓ como índice de descoberta (quais concursos
    existem, qual banca, qual ano). Os PDFs vêm da fonte primária (site
    oficial da banca), como manda o contrato §1. Motivos: Turnstile
    (Cloudflare) protege as URLs reais de download e o robots.txt do PCI
    tem "Disallow: /*.pdf". Formalizar em ADR-0001.
  - FCC: os cadernos de prova NÃO são públicos (portal do candidato,
    acesso individual por hash de caderno). A fonte primária FCC entrega
    os GABARITOS oficiais (saem como editais públicos ao fim do certame).
    robots.txt da FCC bloqueia /concursos/ e *.pdf → crawls rodam com
    -s ROBOTSTXT_OBEY=False (decisão assumida, ver docstring do spider).
  - DOCS: os .md migram para docs/; na raiz fica só o README.md.
    Este backlog vive em docs/BACKLOG.org.

* Em andamento / próximos
** EM-ANDAMENTO [#A] Spider FGV (conhecimento.fgv.br)
   Site Drupal, HTML estático, SEM captcha (verificado 05/08). Página do
   concurso: /concursos/{slug} (ex.: dataprev26). PDFs servidos de
   /sites/default/files/concursos/*.pdf, nomes descritivos. Quando a
   prova é aplicada surge a seção "Provas e Gabaritos" na própria página.
   - [X] Copiar spiders/exemplo_banca.py → spiders/fgv.py
   - [ ] Listagem /concursos extrai os slugs disponíveis
   - [ ] Filtrar PDFs pelo path /concursos/ (rodapé tem termos de uso e
         aviso de cookies que NÃO são do concurso)
   - [ ] Ignorar "Vista de Prova" (espelho do candidato, exige CPF)
   - [ ] Classificação de papel: reusar heurísticas (ver item heuristics.py)
   - [ ] Teste de parse com fixture (modelo: tests/test_pci_parse.py)
   - Quick win: HTTP puro, sem browser. ~1 dia.

** TODO [#A] Watchlist YAML + scheduler (a automação)
   Hoje o crawl depende de `scrapy crawl X -a ...` na mão. Substituir
   por uma lista de alvos versionada no repo (watchlist.yaml, já há um
   esqueleto inicial) + um scheduler que dispara os crawls vencidos.
   É isto que transforma "rodar na mão" em "rodar sozinho". Continua
   monolito: o scheduler é um módulo do projeto, não um serviço novo.
   - [ ] lexcorpus/scheduler.py: lê watchlist.yaml, calcula alvos vencidos
         pelo cron de cada um, dispara CrawlerProcess (sequencial por
         banca; bancas distintas podem sobrepor)
   - [ ] Campos do alvo: banca, spider, params, cron, ativo (false =
         spider ainda não existe / pausado)
   - [ ] Seção "descoberta": spiders de listagem rodam 1x/dia e PROPÕEM
         novos alvos (saída em log/arquivo; promoção ao watchlist é
         manual por enquanto)
   - [ ] Começar com loop simples (schedule/APScheduler); supercronic é
         alternativa se preferir cron puro no container
   - [ ] Registrar último disparo por alvo (no StateStore, ver item abaixo)

** TODO [#A] StateStore + ciclo preliminar→definitivo (contrato §6.11)
   O contrato modela o ciclo (vigente, substituido_por, concurso.
   atualizado) mas NADA disso está implementado: hoje toda coleta publica
   concurso.disponivel com vigente=true, e a routing key
   RABBIT_ROUTING_ATUALIZADO existe no settings sem uso. O FILES_EXPIRES=90
   evita re-download, mas não sabe dizer "o definitivo chegou".
   Solução: SQLite pequeno no volume de estado.
   - [ ] Tabela arquivo_visto(banca, concurso, nome, papel, checksum,
         vigente, first_seen, last_seen)
   - [ ] EventoRabbitPipeline consulta o estado antes de agregar:
         concurso/arquivos novos → concurso.disponivel (como hoje);
         surgiu gabarito_definitivo onde havia preliminar → marca o
         preliminar vigente=false + substituido_por e publica o conjunto
         como concurso.atualizado; nada mudou → não publica nada
   - [ ] Idempotência de verdade do lado produtor (não depender só do
         event_id no consumidor)
   - [ ] Re-run diário fica barato e seguro (só publica delta)
   - ALINHAR com o LexLearn: a semântica do concurso.atualizado está no
     contrato; validar o primeiro evento real com um consumidor de teste
   - Fecha o ponto aberto "Ciclo preliminar → definitivo" do
     docs/COMUNICADO_INTEGRACAO_OK.md

** TODO [#B] Heurísticas compartilhadas (lexcorpus/heuristics.py)
   As regex de classificação de papel/relevância vivem hoje dentro do
   spiders/pci.py. O fcc.py nasceu com a sua própria cópia ajustada
   (desempacota rybena, descarta "condições específicas"); FGV precisará
   das mesmas. Extrair para módulo compartilhado antes que vire 3 cópias.
   - [ ] Mover _RE_GAB_DEF/_RE_GAB_PRE/_RE_GAB/_RE_PROVA/_RE_DESCARTAR,
         eh_relevante(), classificar_papel() para lexcorpus/heuristics.py
   - [ ] Permitir overrides por banca (ex.: FCC descarta resultado/
         convocação/condições específicas mesmo com "prova" no nome —
         ver item em correções)
   - [ ] pci.py, fcc.py e futuros spiders importam daí (sem duplicar regex)
   - [ ] Testes unitários das heurísticas contra nomes reais coletados
         (FGV, FCC, CEBRASPE)

** TODO [#B] Spiders de descoberta (listagens, sem download)
   Um spider por portal que percorre SÓ as listagens e emite "concurso
   candidato" (banca, órgão, ano, URL). Alimenta o watchlist. Não baixa
   PDF, então é leve e roda diário sem pesar os sites.
   - [ ] PCI /provas/ + /provas/{cargo} (sem captcha — verificado 05/08):
         o agregador mais rico; descobre concursos de TODAS as bancas
   - [ ] FGV /concursos (slugs ativos)
   - [ ] FCC raiz do concursosfcc.com.br
   - [ ] Saída: JSONL em eventos_debug/descoberta/ (ou fila própria depois)
   - É o uso correto do PCI: índice, não fonte de arquivo (ver ADR-0001)

** BLOQUEADO Spider Cesgranrio — WAF (Azure Front Door)
   cesgranrio.org.br retorna 403 para qualquer cliente não-navegador,
   mesmo com headers completos de browser (verificado 05/08 — o bloqueio
   é do Azure Front Door, aparentemente por fingerprint/reputação de IP).
   - Depende de: imagem variante com Playwright (item Docker) +
     scrapy-playwright com contexto persistente
   - Se o WAF barrar IP de datacenter mesmo com browser real: proxy
     residencial ou aceitar Cesgranrio como cobertura parcial
   - Não começar antes de FGV estar no ar (quick wins primeiro)

** BLOQUEADO PCI como fonte de PDFs (Turnstile + robots.txt)
   Página de download tem ZERO links .pdf: os arquivos ficam atrás de um
   POST /provas/link que exige token cf-turnstile-response válido. Teste
   com Chromium headless (05/08): o Turnstile NÃO liberou token sozinho.
   Agravante: robots.txt do PCI tem "Disallow: /*.pdf" e o spider roda
   com ROBOTSTXT_OBEY=True — mesmo achando link, o Scrapy não baixaria.
   - Decisão tomada: PCI é fonte de DESCOBERTA, não de arquivos (ADR-0001)
   - Reabrir SOMENTE se: (a) fizer sentido jurídico/comercial burlar uma
     proteção técnica explícita, E (b) houver serviço de resolução de
     captcha (2Captcha/CapSolver resolvem Turnstile) no orçamento
   - O contrato §1 já declara fonte primária como princípio — o PCI
     sempre foi exceção, não regra
   - NOTA 05/08: para PROVAS FCC (cadernos não públicos no site oficial),
     o PCI segue sendo a única fonte de cadernos — reforça a tensão
     deste item com a decisão "PCI só descoberta". Resolver no ADR-0001.

* Pipeline / correções
** EM-ANDAMENTO [#A] Config de ambiente: USER_AGENT real e RABBIT_URL por env
   Profissionalização básica antes de rodar automático contra as bancas.
   - [ ] USER_AGENT hoje é "lexcorpus/0.1 (+contato@exemplo.br)" —
         placeholder. Colocar contato real (boa-fé com os sites + exigido
         por vários ToS)
   - [X] RABBIT_URL hardcoded amqp://guest:guest@localhost → ler de env
         (padrão FILES_STORE que já usa os.environ). FEITO 05/08 junto
         com RABBIT_ENABLED: settings.py lê os.environ.get para ambos;
         default local inalterado (False / localhost)
   - [ ] Credenciais de produção fora do repo (.env + .env.example)

** TODO [#B] Falso positivo: editais de resultado com "prova" no nome
   A heurística eh_relevante() só descarta se NÃO houver pista de
   prova/gabarito. Na FCC, editais de resultado/convocação/reclassificação
   mencionam "prova_objetiva" no nome do arquivo → passam pelo filtro e
   são classificados como papel=prova (errado). O LexLearn reclassifica
   com autoridade, mas o coletor não deve poluir o evento.
   - [X] No fcc.py (05/08): _RE_DESCARTAR forte (resultado|convoca|
         reclassifica|homologa|inscri|condicoes_espec|habilitad) vence
         mesmo havendo "prova" no nome — validado no crawl dpeba125
         (26 descartes corretos, incluindo a armadilha
         "prova_e_condicoes_especificas_deferidas.pdf")
   - [ ] Generalizar: levar a regra para heuristics.py e avaliar se vira
         default geral (pci.py ainda tem a versão fraca)
   - [ ] Caso de teste: edital_de_resultado_preliminar_prova_objetiva.pdf

** TODO [#B] Associação arquivo → cargo em concursos multi-cargo
   Ponto aberto do comunicado de integração: hoje arquivos de concurso
   multi-cargo são marcados com o conjunto TODO de cargos, sem discriminar
   por arquivo (multi_cargo=true). Melhorar onde a fonte permitir.
   - [ ] FCC: o edital de gabaritos cobre vários cargos num único PDF —
         hoje vai cargos=["*"], multi_cargo=true (contrato §5, caso C).
         Extrair cargo exigiria parsear o PDF, o que é papel do LexLearn
   - [ ] FGV: a página costuma agrupar prova por cargo — capturar o
         contexto do link (cabeçalho da seção, texto do link)
   - [ ] CEBRASPE: a API não amarra arquivo→cargo; manter ["*"] ou todos
   - [ ] Não inventar: se não der para extrair, omite (contrato §5, caso C)
   - Não bloqueia nada; refinamento contínuo

** TODO [#C] "Padrão de resposta" da discursiva hoje vira papel=prova
   Registrado no comunicado como comportamento conhecido. O LexLearn tem
   autoridade para reclassificar. Revisitar só se virar ruído real.

* Docker / profissionalização
** TODO [#A] Dockerfile multi-stage (base + base-playwright)
   UMA imagem para todo o projeto; um segundo target adiciona Chromium+
   Playwright só para os spiders que precisam (Cesgranrio; PCI se um dia
   for reaberto). Imagem principal fica enxuta.
   - [X] Stage base (versão inicial 05/08): python:3.11-slim +
         requirements.txt + código, ENTRYPOINT scrapy, .dockerignore
         excluindo venv/.git/eventos_debug
   - [ ] Stage playwright: base + playwright install --with-deps chromium
   - [X] Volume /data/raw/exams (contrato: o LexLearn monta o mesmo) —
         validado no compose de integração
   - [ ] Volume /state (SQLite do StateStore) e /app/watchlist.yaml
         (ou copiar no build e permitir override por volume)

** EM-ANDAMENTO [#A] docker-compose de dev/prod (scheduler + rabbitmq + volumes)
   Containers por FUNÇÃO, todos da mesma imagem (decisão: monolito
   modular). Nada de um serviço por banca — banca é linha do watchlist.
   - [X] Integração com LexLearn-v3 (05/08): docker-compose.crawler.yml
         na raiz do LexLearn-v3 — override aditivo (base intocado),
         profile "crawler", mesma rede do projeto lexlearn-v3, mesmo
         bind ./data:/data do worker, RABBIT_URL=rabbitmq:5672
   - [ ] LEXCORPUS_DIR no .env do LexLearn-v3 (caminho relativo até o
         repo LexCorpus, ex.: ../../LexCorpus)
   - [ ] Teste e2e via compose: crawl fcc → evento consumido pelo worker
         do LexLearn
   - [ ] Serviço scheduler: command python -m lexcorpus.scheduler;
         env RABBIT_ENABLED=True, RABBIT_URL, LEXCORPUS_FILES_STORE
   - [ ] Serviço rabbitmq:3-management (dev; em prod pode ser externo)
   - [ ] Rede e depends_on mínimos; profiles para dev vs prod

** TODO [#B] CI: pytest no GitHub Actions
   Já há tests/ (test_e2e, test_cebraspe_parse, test_pci_parse).
   - [ ] Workflow: push/PR → pip install -r requirements.txt → pytest
   - [ ] Validar schemas (evento/sidecar) em todo PR que tocar pipelines
   - [ ] Adicionar fixture de parse da FCC (página real de concurso,
         com links rybena embrulhados)
   - [ ] Cache de pip; matrix só se necessário (uma versão basta por ora)

** TODO [#C] .env.example + settings por ambiente
   Documentar LEXCORPUS_FILES_STORE, RABBIT_URL, RABBIT_ENABLED,
   EVENTOS_OUT_DIR. O settings.py já lê FILES_STORE, RABBIT_ENABLED e
   RABBIT_URL de env (desde 05/08) — resta documentar e descrever no
   README.

* Documentação
** TODO [#A] Mover .md para docs/ e corrigir links
   Decisão tomada: raiz só com README.md.
   - [ ] git mv CONTRATO.md COMUNICADO_INTEGRACAO_OK.md docs/
   - [ ] Este backlog: docs/BACKLOG.org (conteúdo já em org-mode)
   - [ ] README: atualizar TODAS as referências (hoje cita CONTRATO.md,
         schema/, consumer_teste.py com paths da raiz)
   - [ ] Referências cruzadas dentro dos próprios docs (o comunicado cita
         CONTRATO.md e consumer_teste.py)
   - [ ] Avisar o time LexLearn: o contrato mudou de path no repo
   - BOA TAREFA PARA COMEÇAR UMA SESSÃO (isolada, sem risco)

** TODO [#B] Escrever os ADRs iniciais (docs/architecture/decisions/)
   O LexLearn já tem cultura de ADR; trazer para cá. Formato: 4 dígitos.
   - [ ] ADR-0001: agregador como índice, fonte primária como origem de
         arquivos (absorve a decisão PCI: Turnstile + robots.txt).
         NOVO 05/08: incluir o caso FCC — cadernos não públicos na fonte
         primária → de onde vêm as provas FCC? (tensão registrada no
         item BLOQUEADO do PCI)
   - [ ] ADR-0002: monolito modular — uma imagem, N containers por função
         (rejeita container-por-banca; justificativa: o que varia é
         código+config, não artefato)
   - [ ] ADR-0003: watchlist + StateStore como mecanismo de automação e
         do ciclo preliminar→definitivo

** TODO [#B] Atualizar README (novos spiders, automação, docs/)
   Depois dos itens acima:
   - [ ] Estrutura: adicionar scheduler.py, heuristics.py, watchlist.yaml,
         Dockerfile, docker-compose
   - [ ] Seção "Rodar": modo manual (scrapy crawl) E modo automático
         (scheduler + watchlist); modo container ao lado do LexLearn
         (docker-compose.crawler.yml)
   - [ ] Seção "Por onde começar": docs/CONTRATO.md → docs/BACKLOG.org →
         ADRs → schema/
   - [ ] Tabela de bancas suportadas e status (cebraspe ✅, fcc ✅
         gabaritos, fgv em construção, cesgranrio bloqueada, pci
         descoberta)

* Alinhar com o LexLearn (contrato §9 — não mexer unilateralmente)
** TODO Vocabulário de slugs de cargo
   Normalização fica no LexLearn, mas convém combinar os mais comuns.
   Com FGV/FCC entrando, colher rótulos reais e propor a lista inicial.
** TODO Nomes definitivos de exchange e routing keys
   lexcorpus.events / concurso.disponivel / concurso.atualizado são
   proposta v2.0. Confirmar antes do StateStore começar a publicar
   concurso.atualizado de verdade.
** TODO Nível de detalhe viável em segmentos
   Depende do que os scrapers extraem por banca. CEBRASPE não amarra;
   FCC entrega gabarito consolidado multi-cargo; FGV pode dar
   página/seção. Combinar o mínimo útil.
** TODO Validar o primeiro concurso.atualizado real
   Quando o StateStore existir: rodar um ciclo preliminar→definitivo
   real (FGV ou FCC são boas candidatas, publicam rápido) com o
   consumidor de teste do LexLearn ouvindo.

* Feito (histórico recente)
** FEITO Contrato v2.0 + schemas (evento/sidecar)                  :contrato:
   Modelo plano + metadado rico (cargos[], tipo_prova, multi_cargo,
   vigente, substituido_por). Fonte da verdade: docs/CONTRATO.md.
** FEITO Spider CEBRASPE via API oficial                            :spiders:
   Sem browser: apis.cebraspe.org.br/cebraspe/eventos/{slug} +
   cdn.cebraspe.org.br. Classificação por descricaoArquivo (não
   heurística). Concurso PRF 2021: 20 arquivos (11 provas, 6 gabaritos
   definitivos, 3 preliminares).
** FEITO Spider FCC (concursosfcc.com.br)                           :spiders:
   CLOSED: 2026-08-05
   spiders/fcc.py testado de ponta a ponta (dpeba125, pbaru125):
   desempacota o wrapper rybena (?file=), extrai rótulo do <title>,
   slug/ano do path (dpeba125 → 2025). Coleta gabaritos oficiais
   públicos (editais de divulgação/alteração de gabarito). Cadernos de
   prova NÃO são públicos (portal do candidato, hash individual) —
   provas FCC seguem dependendo do PCI (ver item BLOQUEADO + ADR-0001).
   Evento v2.0 validado: papel=gabarito_definitivo, cargos ["*"],
   multi_cargo=true. robots.txt da FCC bloqueia tudo → crawl com
   -s ROBOTSTXT_OBEY=False (decisão documentada no docstring).
** FEITO Pipelines: download → sidecar → evento RabbitMQ           :pipeline:
   FilesPipeline (pasta plana {banca}/{concurso}/) → SHA-256 + .meta.json
   atômico → agregação por concurso + publicação (delivery_mode=2).
** FEITO Teste e2e LexCorpus → LexLearn APROVADO (04/08)        :integracao:
   Fluxo completo com dados reais validado pelo consumidor de teste.
   Registro: docs/COMUNICADO_INTEGRACAO_OK.md.
** FEITO Settings por ambiente (RABBIT_ENABLED/RABBIT_URL via env)   :pipeline:
   CLOSED: 2026-08-05
   Patch de 2 linhas: os.environ.get com defaults inalterados
   (False / localhost). Habilita o container Docker a ligar publicação
   via compose sem tocar código. Testado: dev local, container e typo
   na variável (cai seguro para False).
** FEITO Dockerfile + .dockerignore na raiz do LexCorpus              :docker:
   CLOSED: 2026-08-05
   python:3.11-slim, ENTRYPOINT scrapy (CMD default: list),
   LEXCORPUS_FILES_STORE=/data/raw/exams. Container efêmero por crawl
   (run --rm), não é serviço permanente. Stage playwright fica para o
   item multi-stage.
** FEITO docker-compose.crawler.yml (integração LexLearn-v3)          :docker:
   CLOSED: 2026-08-05
   Override aditivo na raiz do LexLearn-v3 — docker-compose.base.yml
   intocado. Profile "crawler" (não sobe com o up normal), mesma rede
   do projeto lexlearn-v3 (resolve rabbitmq pelo nome), mesmo bind
   ./data:/data do worker (pasta_uri do contrato §7 resolve igual nos
   dois lados). Pendente: LEXCORPUS_DIR no .env e teste e2e via compose
   (acompanhar no item "docker-compose de dev/prod").
** FEITO Diagnóstico das 4 fontes (05/08)                        :pesquisa:
   FGV e FCC: estáticas, sem captcha (quick wins). Cesgranrio: 403 do
   Azure Front Door. PCI: Turnstile + robots.txt Disallow /*.pdf →
   reclassificado como fonte de descoberta apenas (ADR-0001).
