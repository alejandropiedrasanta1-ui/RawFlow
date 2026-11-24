"""Banco de pruebas de la Selección IA: pasa las REGLAS por los análisis guardados de eventos reales, sin fotos.

    python tools/calibrar/banco.py              # resumen de cada evento
    python tools/calibrar/banco.py --buenas     # además, las fotos con etiqueta «B» (buena) que se apartan

Los análisis están en tests/datos/seleccion/*.jsonl. Los que traen «etiqueta» (M = otra IA/el fotógrafo la marcó
mala, B = buena, - = sin marcar) dicen cuántas buenas se tiran: eso es lo que NUNCA debe subir sin mirarlo a ojo.
Las cifras de referencia están en tests/test_duplicados.py (caso banco_descarte): si un cambio de reglas las mueve,
hay que revisar las fotos que cambian (tools/calibrar/hoja.py) y actualizarlas a propósito.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from rawflow import seleccion as S  # noqa: E402

DATOS = RAIZ / "tests" / "datos" / "seleccion"


def cargar(f: Path) -> list:
    return [json.loads(line) for line in open(f, encoding="utf-8") if line.startswith("{")]


def evaluar(rows: list) -> dict:
    """{apartadas, resumen, por_etiqueta, buenas: {foto: motivo}} de un evento."""
    d = S.decide(rows)
    lab = {r["foto"]: r.get("etiqueta", "-") for r in rows}
    return {"apartadas": len(d), "resumen": S.summary(d), "decisiones": d,
            "por_etiqueta": Counter(lab[k] for k in d),
            "buenas": {k: v for k, v in d.items() if lab[k] == "B"},
            "etiquetado": any(r.get("etiqueta", "-") != "-" for r in rows)}


def main() -> int:
    ver_buenas = "--buenas" in sys.argv
    for f in sorted(DATOS.glob("*.jsonl")):
        e = evaluar(cargar(f))
        extra = ""
        if e["etiquetado"]:
            pe = e["por_etiqueta"]
            extra = f"  ·  de ellas: {pe['M']} malas, {pe['B']} BUENAS, {pe['-']} sin marcar"
        print(f"{f.stem:22s} {e['apartadas']:4d} apartadas: {e['resumen']}{extra}")
        if ver_buenas:
            for k, v in sorted(e["buenas"].items()):
                print(f"      B {k}\t{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
