"""Teste ponta-a-ponta do esqueleto, sem rede e sem RabbitMQ.

Simula o que os pipelines fazem: monta um ArquivoItem já 'baixado' (com um PDF
real em disco), roda SidecarPipeline e EventoRabbitPipeline, e valida os
artefatos produzidos (sidecar + evento) contra os JSON Schemas do contrato v2.0.
"""
import json
import os
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from itemadapter import ItemAdapter

from lexcorpus.items import ArquivoItem
from lexcorpus.pipelines import SidecarPipeline, EventoRabbitPipeline
from lexcorpus.util import sha256_file


class FakeSettings(dict):
    def get(self, k, d=None): return super().get(k, d)
    def getbool(self, k, d=False): return bool(super().get(k, d))


class FakeSpider:
    import logging
    logger = logging.getLogger("fake")


def build_downloaded_item(store_root: Path, banca, concurso, nome, papel, cargos,
                          cargos_rotulo, **extra):
    """Cria um PDF de verdade no storage e devolve um item como pós-FilesPipeline."""
    pdf_dir = store_root / banca / concurso
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / nome
    pdf_path.write_bytes(b"%PDF-1.4\n%fake pdf bytes\n%%EOF\n")
    rel = f"{banca}/{concurso}/{nome}"

    it = ArquivoItem()
    it["files"] = [{"path": rel, "checksum": "x", "url": f"https://ex/{nome}"}]
    it["fonte_url"] = f"https://exemplo.br/{nome}"
    it["banca"] = banca
    it["concurso"] = concurso
    it["banca_rotulo"] = "FGV"
    it["concurso_rotulo"] = "IF-Sergipe 2024 - Professor EBTT"
    it["cargos_rotulo"] = cargos_rotulo
    it["papel"] = papel
    it["cargos"] = cargos
    it["tipo_prova"] = extra.get("tipo_prova")
    it["multi_cargo"] = extra.get("multi_cargo", False)
    it["vigente"] = extra.get("vigente", True)
    if extra.get("segmentos"):
        it["segmentos"] = extra["segmentos"]
    if extra.get("substituido_por"):
        it["substituido_por"] = extra["substituido_por"]
    return it


def main():
    root = Path(tempfile.mkdtemp())
    settings = FakeSettings({
        "FILES_STORE": str(root),
        "STORAGE_URI_SCHEME": "file://",
        "RABBIT_ENABLED": False,
        "EVENTOS_OUT_DIR": str(root / "eventos"),
    })
    spider = FakeSpider()

    sidecar_pipe = SidecarPipeline(store_root=str(root))
    evento_pipe = EventoRabbitPipeline(settings)
    evento_pipe.open_spider(spider)

    evento_schema = Draft202012Validator(json.load(open("schema/evento.schema.json")))
    sidecar_schema = Draft202012Validator(json.load(open("schema/sidecar.schema.json")))

    items = [
        build_downloaded_item(root, "fgv", "if_sergipe_2024",
            "ifs_geografia_prova_t1.pdf", "prova", ["geografia"],
            {"geografia": "Professor de Geografia"}, tipo_prova="1"),
        build_downloaded_item(root, "fgv", "if_sergipe_2024",
            "ifs_gab_def_todos.pdf", "gabarito_definitivo", ["geografia", "ingles"],
            {"geografia": "Professor de Geografia", "ingles": "Professor de Inglês"},
            tipo_prova=None, multi_cargo=True,
            segmentos=[{"cargo": "geografia", "pagina_inicio": 1, "pagina_fim": 2}]),
    ]

    for it in items:
        sidecar_pipe.process_item(it, spider)
        evento_pipe.process_item(it, spider)

    # valida cada sidecar escrito
    n_side = 0
    for meta in root.rglob("*.meta.json"):
        doc = json.loads(meta.read_text())
        errs = sorted(sidecar_schema.iter_errors(doc), key=str)
        assert not errs, f"sidecar {meta.name} inválido: {errs[0].message}"
        # checksum do sidecar bate com o PDF real?
        pdf = meta.parent / doc["arquivo"]
        assert doc["checksum_sha256"] == sha256_file(pdf), "checksum não bate"
        n_side += 1
    print(f"[OK] {n_side} sidecars escritos e válidos contra sidecar.schema.json")

    # dispara close -> grava evento
    evento_pipe.close_spider(spider)
    n_ev = 0
    for ev_file in (root / "eventos").glob("*.json"):
        ev = json.loads(ev_file.read_text())
        errs = sorted(evento_schema.iter_errors(ev), key=str)
        assert not errs, f"evento inválido: {errs[0].message}"
        assert ev["pasta_uri"].startswith("file://")
        assert len(ev["arquivos"]) == 2
        assert ev["extra"]["rotulos"]["cargos"]["ingles"] == "Professor de Inglês"
        n_ev += 1
    print(f"[OK] {n_ev} evento(s) publicado(s) e válido(s) contra evento.schema.json")
    print("[OK] pasta_uri com esquema file://; rótulos crus preservados; checksums batem")
    print("\nTUDO VERDE — o esqueleto produz artefatos conformes ao contrato v2.0.")


if __name__ == "__main__":
    main()
