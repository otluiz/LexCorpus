"""Utilitários compartilhados do LexCorpus.

Concentra as três operações que o contrato v2.0 exige em vários pontos:
- slugify:      produz slugs canônicos (banca, concurso, cargo).
- sha256_file:  checksum SHA-256 hex minúsculo do arquivo final (§6.3).
- atomic_write: escrita atômica via .tmp + rename no mesmo filesystem (§6.2).
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(texto: str) -> str:
    """Rótulo cru -> slug canônico.

    minúsculas, sem acento (NFKD), não-alfanumérico -> '_', colapsa e apara '_'.
    'PC-PE 2024 - Delegado' -> 'pc_pe_2024_delegado'
    'FGV' -> 'fgv'
    """
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = _SLUG_STRIP.sub("_", sem_acento.lower())
    return s.strip("_")


def sha256_file(caminho: str | os.PathLike, chunk: int = 1 << 20) -> str:
    """SHA-256 hex minúsculo do arquivo. Calculado sobre o arquivo FINAL."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(chunk), b""):
            h.update(bloco)
    return h.hexdigest()


def atomic_write_bytes(destino: str | os.PathLike, dados: bytes) -> None:
    """Escreve `dados` em `destino` de forma atômica.

    Escreve num .tmp no MESMO diretório (mesmo filesystem, rename atômico)
    e só então renomeia. Garante que ninguém leia um arquivo pela metade (§6.2).
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, destino)  # atômico no mesmo FS


def atomic_move(origem: str | os.PathLike, destino: str | os.PathLike) -> None:
    """Move (rename) atômico no mesmo filesystem. Usado no ciclo preliminar->definitivo."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.replace(origem, destino)
