# Arquivo:  lexcorpus/consulta.py
# Função:   CLI de consulta ao storage de PDFs (lado cliente/leitor).
#           O layout no disco é fixo pelo contrato (§6.9: pasta plana
#           {banca}/{concurso}/); o filtro por cargo/papel/tipo_prova é
#           feito nos METADADOS (sidecars *.meta.json), não no path.
"""Consulta o storage do LexCorpus filtrando por banca, concurso, cargo e papel.

Não baixa nada da rede: lê os sidecars (*.meta.json) já gravados pelos
pipelines e lista (ou copia) os PDFs que casam com os filtros.

USO:
    # listar tudo de uma banca
    python -m lexcorpus.consulta --banca cesgranrio

    # provas de um concurso
    python -m lexcorpus.consulta --concurso bnb0124 --papel prova

    # gabaritos que cobrem um cargo (inclui os consolidados multi-cargo "*")
    python -m lexcorpus.consulta --cargo analista_bancario_1 --papel gabarito

    # copiar os PDFs encontrados (e seus sidecars) para uma pasta
    python -m lexcorpus.consulta --banca fgv --cargo bloco_tematico_1 --copiar /tmp/selecao

O storage vem de --store, ou de LEXCORPUS_FILES_STORE, ou do default Docker
(/data/raw/exams) — mesma precedência do settings.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

STORE_DEFAULT = "/data/raw/exams"

PAPEIS = ("prova", "gabarito_preliminar", "gabarito_definitivo")


def iter_arquivos(store: Path):
    """Varre {banca}/{concurso}/*.meta.json e devolve (sidecar, pdf_path)."""
    for meta in sorted(store.glob("*/*/*.meta.json")):
        try:
            sc = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"aviso: sidecar ilegível ({meta}): {exc}", file=sys.stderr)
            continue
        pdf = meta.with_suffix("")  # remove ".json"
        pdf = pdf.with_suffix("")   # remove ".meta" -> *.pdf
        yield sc, pdf


def casa(sc: dict, args) -> bool:
    """True se o sidecar passa em TODOS os filtros informados."""
    if args.banca and sc.get("banca") != args.banca:
        return False
    if args.concurso and args.concurso not in (sc.get("concurso") or ""):
        return False
    if args.papel:
        if args.papel == "gabarito":
            if sc.get("papel") not in ("gabarito_preliminar", "gabarito_definitivo"):
                return False
        elif sc.get("papel") != args.papel:
            return False
    if args.cargo:
        cargos = sc.get("cargos") or []
        # "*" = arquivo consolidado multi-cargo: cobre qualquer cargo
        if args.cargo not in cargos and "*" not in cargos:
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m lexcorpus.consulta",
        description="Consulta o storage do LexCorpus por banca/concurso/cargo/papel.",
    )
    ap.add_argument("--store", type=Path,
                    default=Path(os.environ.get("LEXCORPUS_FILES_STORE", STORE_DEFAULT)),
                    help="raiz do storage (default: LEXCORPUS_FILES_STORE ou /data/raw/exams)")
    ap.add_argument("--banca", help="slug exato da banca (ex.: cesgranrio)")
    ap.add_argument("--concurso", help="trecho do slug do concurso (ex.: bnb0124)")
    ap.add_argument("--cargo", help="slug do cargo (ex.: analista_bancario_1); "
                                    "inclui consolidados multi-cargo ('*')")
    ap.add_argument("--papel", choices=[*PAPEIS, "gabarito"],
                    help="'gabarito' abrange preliminar + definitivo")
    ap.add_argument("--copiar", type=Path, metavar="DIR",
                    help="copia os PDFs encontrados (e sidecars) para DIR")
    args = ap.parse_args(argv)

    if not args.store.is_dir():
        print(f"storage não encontrado: {args.store}", file=sys.stderr)
        print("defina LEXCORPUS_FILES_STORE ou passe --store", file=sys.stderr)
        return 2

    encontrados = [
        (sc, pdf) for sc, pdf in iter_arquivos(args.store) if casa(sc, args)
    ]

    for sc, pdf in encontrados:
        cargos = ",".join(sc.get("cargos") or [])
        tipo = f" tipo={sc['tipo_prova']}" if sc.get("tipo_prova") else ""
        print(f"{sc.get('banca')}/{sc.get('concurso')}  {sc.get('papel'):21s} "
              f"[{cargos}]{tipo}  {sc.get('arquivo')}")

    print(f"\n{len(encontrados)} arquivo(s) em {args.store}")

    if args.copiar and encontrados:
        args.copiar.mkdir(parents=True, exist_ok=True)
        for sc, pdf in encontrados:
            if pdf.is_file():
                shutil.copy2(pdf, args.copiar)
            meta = pdf.with_suffix(".pdf.meta.json")
            if meta.is_file():
                shutil.copy2(meta, args.copiar)
        print(f"copiados para {args.copiar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
