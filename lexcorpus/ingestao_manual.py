# Arquivo:  lexcorpus/ingestao_manual.py
# Função:   ingestão de PDFs obtidos FORA do crawler (acervo antigo, banca sem
#           spider, arquivo baixado à mão) por um manifesto YAML versionado.
#           O PDF entra pelo mesmo caminho do crawler — SidecarPipeline e
#           EventoRabbitPipeline —, então ganha sidecar, StateStore, evento e o
#           ciclo preliminar→definitivo, e respeita a versão do contrato.
"""Ingestão manual por manifesto.

Motivo: PDFs postos na pasta à mão ficavam sem .meta.json (8 documentos do
acervo, entre eles BACEN13 e SERPRO13, com logical_type NULO no LexLearn) e
fora do StateStore — invisíveis à guarda de duplicata entre concursos.

USO (na raiz do projeto):
    # simulação (default): valida e mostra o plano, não grava nada
    python -m lexcorpus.ingestao_manual manifestos/cebraspe/bacen13_analista.yaml

    # grava: copia os PDFs, escreve sidecars, registra no StateStore, publica
    python -m lexcorpus.ingestao_manual manifestos/cebraspe/bacen13_analista.yaml --gravar

Manifesto (manifestos/{banca}/{concurso}.yaml):

    banca: cebraspe                    # slug
    concurso: bacen13_analista         # slug = nome da pasta
    fonte_url: https://...             # default dos arquivos (obrigatório em algum nível)
    rotulos: {banca: ..., concurso: ..., cargos: {slug: rótulo}}
    arquivos:
      - nome: bacen13_002_04.pdf       # basename no storage
        origem: ~/Downloads/x.pdf      # opcional; relativo ao manifesto. Sem
                                       # origem, o PDF já tem de estar na pasta
        papel: prova                   # prova | gabarito_preliminar | gabarito_definitivo
        cargos: [analista_area_2]      # slugs, ou ["*"]
        caderno: BACEN13_002_04        # opcional (contrato 2.1)
        tipo_prova, multi_cargo, segmentos, fonte_url, observacao  # opcionais

Recusa (nada é gravado se houver qualquer erro):
  - manifesto fora da forma (slug, papel, nome com barra, campo desconhecido...)
  - mesmo conteúdo (SHA-256) já registrado sob OUTRO concurso
  - dois arquivos do manifesto com o mesmo conteúdo
  - destino já existente com conteúdo diferente (não sobrescreve)
  - sidecar resultante inválido no schema/sidecar.schema.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .items import ArquivoItem
from .pipelines import (EventoRabbitPipeline, SidecarPipeline,
                        versao_contrato)
from .statestore import StateStore
from .util import atomic_write_bytes, sha256_file

PAPEIS = ("prova", "gabarito_preliminar", "gabarito_definitivo")
_RE_SLUG = re.compile(r"^[a-z0-9_]+$")
_RE_CADERNO = re.compile(r"^[A-Za-z0-9_-]+$")
_CHAVES_TOPO = {"banca", "concurso", "fonte_url", "rotulos", "arquivos", "observacao"}
_CHAVES_ARQ = {"nome", "origem", "papel", "cargos", "tipo_prova", "caderno",
               "multi_cargo", "segmentos", "fonte_url", "observacao"}
_SCHEMA_SIDECAR = Path(__file__).resolve().parent.parent / "schema" / "sidecar.schema.json"

log = logging.getLogger("ingestao_manual")


class _Settings(dict):
    """O mínimo da interface de Settings do Scrapy que os pipelines usam."""
    def get(self, k, d=None): return super().get(k, d)
    def getbool(self, k, d=False):
        v = super().get(k, d)
        return v.lower() in ("1", "true", "yes") if isinstance(v, str) else bool(v)


class _Spider:
    logger = log


@dataclass
class Manifesto:
    caminho: Path
    banca: str = ""
    concurso: str = ""
    fonte_url: str | None = None
    rotulos: dict = field(default_factory=dict)
    arquivos: list = field(default_factory=list)
    erros: list = field(default_factory=list)


@dataclass
class Resultado:
    erros: list = field(default_factory=list)
    plano: list = field(default_factory=list)


# --- manifesto -------------------------------------------------------------------

def carregar_manifesto(caminho: Path) -> Manifesto:
    """Lê e valida a FORMA do manifesto (não toca no storage)."""
    m = Manifesto(caminho=Path(caminho))
    try:
        dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        m.erros.append(f"manifesto ilegível ({caminho}): {exc}")
        return m
    if not isinstance(dados, dict):
        m.erros.append("manifesto: a raiz tem de ser um mapa")
        return m

    for k in sorted(set(dados) - _CHAVES_TOPO):
        m.erros.append(f"manifesto: campo desconhecido '{k}'")
    for k in ("banca", "concurso"):
        v = dados.get(k)
        if not isinstance(v, str) or not _RE_SLUG.match(v):
            m.erros.append(f"{k}: slug obrigatório (minúsculas, dígitos, '_'): {v!r}")
    m.banca, m.concurso = dados.get("banca") or "", dados.get("concurso") or ""
    m.fonte_url = dados.get("fonte_url")
    m.rotulos = dados.get("rotulos") or {}

    arquivos = dados.get("arquivos")
    if not isinstance(arquivos, list) or not arquivos:
        m.erros.append("arquivos: lista não vazia obrigatória")
        return m
    for i, a in enumerate(arquivos):
        onde = f"arquivos[{i}]"
        if not isinstance(a, dict):
            m.erros.append(f"{onde}: tem de ser um mapa")
            continue
        onde = f"arquivos[{i}] ({a.get('nome', '?')})"
        for k in sorted(set(a) - _CHAVES_ARQ):
            m.erros.append(f"{onde}: campo desconhecido '{k}'")
        nome = a.get("nome")
        if not isinstance(nome, str) or not nome or "/" in nome or nome in (".", ".."):
            m.erros.append(f"{onde}: nome tem de ser basename, sem barra: {nome!r}")
        if a.get("papel") not in PAPEIS:
            m.erros.append(f"{onde}: papel inválido {a.get('papel')!r}; "
                           f"aceitos: {', '.join(PAPEIS)}")
        cargos = a.get("cargos")
        if (not isinstance(cargos, list) or not cargos
                or not all(isinstance(c, str) and (c == "*" or _RE_SLUG.match(c))
                           for c in cargos)):
            m.erros.append(f"{onde}: cargos tem de ser lista não vazia de slugs "
                           f"ou ['*']: {cargos!r}")
        cad = a.get("caderno")
        if cad is not None and not (isinstance(cad, str) and _RE_CADERNO.match(cad)):
            m.erros.append(f"{onde}: caderno só com letras, dígitos, '_' e '-': {cad!r}")
        url = a.get("fonte_url") or m.fonte_url
        if not isinstance(url, str) or not re.match(r"^(https?|file)://", url):
            m.erros.append(f"{onde}: fonte_url (no arquivo ou no topo) tem de ser "
                           f"URL http(s):// ou file://: {url!r}")
    m.arquivos = arquivos
    return m


# --- execução --------------------------------------------------------------------

def _origem(m: Manifesto, a: dict, destino: Path) -> Path:
    if not a.get("origem"):
        return destino
    p = Path(os.path.expanduser(a["origem"]))
    return p if p.is_absolute() else m.caminho.parent / p


def _item(m: Manifesto, a: dict) -> ArquivoItem:
    cargos_rot = (m.rotulos.get("cargos") or {})
    if a["cargos"] != ["*"]:
        cargos_rot = {c: r for c, r in cargos_rot.items() if c in a["cargos"]}
    it = ArquivoItem(
        files=[{"path": f"{m.banca}/{m.concurso}/{a['nome']}"}],
        banca=m.banca, concurso=m.concurso,
        fonte_url=a.get("fonte_url") or m.fonte_url,
        papel=a["papel"], cargos=a["cargos"],
        tipo_prova=a.get("tipo_prova"), caderno=a.get("caderno"),
        multi_cargo=bool(a.get("multi_cargo", False)),
        segmentos=a.get("segmentos"), vigente=True,
        origem_extra={"metodo": "ingestao_manual",
                      "manifesto": m.caminho.name,
                      **({"observacao": a["observacao"]} if a.get("observacao") else {})},
    )
    if m.rotulos.get("banca"):
        it["banca_rotulo"] = m.rotulos["banca"]
    if m.rotulos.get("concurso"):
        it["concurso_rotulo"] = m.rotulos["concurso"]
    if cargos_rot:
        it["cargos_rotulo"] = cargos_rot
    return it


def executar(manifesto: Path, settings: dict, gravar: bool = False) -> Resultado:
    """Valida tudo; só com zero erros e gravar=True é que o storage é tocado."""
    settings = _Settings(settings)
    r = Resultado()
    m = carregar_manifesto(manifesto)
    r.erros.extend(m.erros)
    if r.erros:
        return r
    try:
        versao = versao_contrato(settings)
    except ValueError as exc:
        r.erros.append(str(exc))
        return r

    store = Path(settings.get("FILES_STORE"))
    pasta = store / m.banca / m.concurso
    db = Path(settings.get("LEXCORPUS_STATE_DB", "state/lexcorpus_state.db"))
    validador = Draft202012Validator(json.loads(_SCHEMA_SIDECAR.read_text()))

    preparados = []   # (arquivo, origem, destino, checksum)
    for a in m.arquivos:
        destino = pasta / a["nome"]
        origem = _origem(m, a, destino)
        if not origem.is_file():
            r.erros.append(f"{a['nome']}: PDF não encontrado em {origem}")
            continue
        checksum = sha256_file(origem)
        if destino.exists() and origem.resolve() != destino.resolve() \
                and sha256_file(destino) != checksum:
            r.erros.append(f"{a['nome']}: {destino} já existe com conteúdo "
                           f"diferente — não sobrescrevo (decisão do administrador)")
            continue
        preparados.append((a, origem, destino, checksum))

    # o mesmo conteúdo duas vezes no manifesto
    por_checksum = {}
    for a, _, _, ck in preparados:
        por_checksum.setdefault(ck, []).append(a["nome"])
    for nomes in por_checksum.values():
        if len(nomes) > 1:
            r.erros.append(f"mesmo conteúdo em {' e '.join(nomes)} — um arquivo só")

    # o mesmo conteúdo já registrado sob outro concurso (não cria o db em dry-run)
    if db.exists():
        with StateStore(db) as st:
            for a, _, _, ck in preparados:
                outros = [x for x in st.buscar_por_checksum(ck)
                          if (x["banca"], x["concurso"]) != (m.banca, m.concurso)]
                if outros:
                    onde = ", ".join(f"{x['banca']}/{x['concurso']}/{x['nome']}"
                                     for x in outros)
                    r.erros.append(f"{a['nome']}: mesmo conteúdo já existe em {onde}")

    # o sidecar que sairia passa no schema?
    for a, _, _, ck in preparados:
        it = _item(m, a)
        it["nome"], it["checksum_sha256"] = a["nome"], ck
        sc = SidecarPipeline._build_sidecar(it, versao)
        for e in validador.iter_errors(sc):
            caminho = "/".join(str(p) for p in e.absolute_path) or "(raiz)"
            r.erros.append(f"{a['nome']}: sidecar inválido em {caminho}: {e.message}")

    for a, origem, destino, _ in preparados:
        acao = "já está no destino" if origem.resolve() == destino.resolve() \
            or destino.exists() else f"copiar de {origem}"
        extra = f", caderno {a['caderno']}" if a.get("caderno") else ""
        r.plano.append(f"{a['nome']}: {a['papel']}, cargos {a['cargos']}{extra} — {acao}")
    if r.erros or not gravar:
        return r

    # --- gravar: PDFs, depois os pipelines do crawler (sidecar -> evento) -------
    for a, origem, destino, _ in preparados:
        if not destino.exists():
            atomic_write_bytes(destino, origem.read_bytes())
    sidecar = SidecarPipeline(store_root=str(store), versao=versao)
    evento = EventoRabbitPipeline(settings)
    spider = _Spider()
    evento.open_spider(spider)
    try:
        for a, _, _, _ in preparados:
            evento.process_item(sidecar.process_item(_item(m, a), spider), spider)
    finally:
        evento.close_spider(spider)
    return r


# --- CLI ---------------------------------------------------------------------------

_CHAVES_SETTINGS = ("FILES_STORE", "RABBIT_ENABLED", "RABBIT_URL", "RABBIT_EXCHANGE",
                    "RABBIT_ROUTING_DISPONIVEL", "RABBIT_ROUTING_ATUALIZADO",
                    "STORAGE_URI_SCHEME", "EVENTOS_OUT_DIR", "LEXCORPUS_STATE_DB",
                    "LEXCORPUS_CONTRATO_VERSAO")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lexcorpus.ingestao_manual",
        description="Ingere PDFs obtidos fora do crawler, por manifesto YAML.")
    ap.add_argument("manifesto", type=Path)
    ap.add_argument("--gravar", action="store_true",
                    help="grava de fato (sem isto: só valida e mostra o plano)")
    ap.add_argument("--store", help="storage de PDFs (default: FILES_STORE do settings)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from scrapy.utils.project import get_project_settings
    proj = get_project_settings()
    settings = {k: proj.get(k) for k in _CHAVES_SETTINGS if proj.get(k) is not None}
    if args.store:
        settings["FILES_STORE"] = args.store

    r = executar(args.manifesto, settings, gravar=args.gravar)
    for linha in r.plano:
        print(f"  {linha}")
    if r.erros:
        print(f"\n{len(r.erros)} erro(s) — nada foi gravado:", file=sys.stderr)
        for e in r.erros:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.gravar:
        print(f"\ngravado em {settings['FILES_STORE']} (contrato "
              f"{settings.get('LEXCORPUS_CONTRATO_VERSAO', '2.0')})")
    else:
        print("\nsimulação OK — nada gravado. Para gravar: --gravar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
