"""Pruebas numeradas de RawFlow: casos distintos (nombres duplicados, fallos a mitad, reintentos, actualizaciones…).
Lo que se comprueba en todas: el evento NO se para por una foto suelta, entrega el resultado final igualmente
(lo que no salió queda avisado) y un reintento sigue desde el paso donde se quedó, sin rehacer lo hecho.

    .venv/bin/python tests/pruebas_rawflow.py

Usan carpetas temporales y programas simulados (DxO, Imagen, Bridge, Retouch4me): no tocan el Escritorio, las memorias
ni las apps, y no cobran nada en Imagen.
"""
from __future__ import annotations

import os as _os
_os.environ["RAWFLOW_SIN_APPS"] = "1"      # las pruebas NUNCA manejan ImagenAI, Bridge ni Retouch4me de verdad
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_duplicados as td  # noqa: E402

from rawflow import core  # noqa: E402

DXO = "/Applications/PureRAW 6/DxO PureRAW 6.app"
PRUEBAS = []
DNG_IMAGEN = b"DNG+imagen " + core.IMAGEN_MARK     # DNG ya editado por Imagen (lleva su crs:Temperature)
JPG_IMAGEN = b"J" * 1000 + b" " + core.IMAGEN_MARK  # JPEG de Bridge con las ediciones de Imagen


class Saltada(Exception):
    """La prueba no aplica en este sistema (p. ej. un recorrido que solo existe en el Mac): no cuenta como fallo."""


SOLO_MAC = sys.platform != "darwin"


def prueba(titulo: str, solo_mac: str = "", solo_win: str = ""):
    """`solo_mac` / `solo_win`: motivo por el que la prueba solo tiene sentido en el Mac / en Windows; en el otro
    sistema sale como «saltada»."""
    def deco(fn):
        if solo_mac or solo_win:
            def envuelta():
                if solo_mac and SOLO_MAC:
                    raise Saltada(f"solo Mac: {solo_mac}")
                if solo_win and sys.platform != "win32":
                    raise Saltada(f"solo Windows: {solo_win}")
                return fn()
            envuelta.__name__ = fn.__name__
            PRUEBAS.append((titulo, envuelta))
        else:
            PRUEBAS.append((titulo, fn))
        return fn
    return deco


def finales(ev: Path):
    return sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg"))


def con_dng_y_jpg(ev: Path, n: int, jpg: bool = True):
    """Evento con n RAW, sus DNG «editados por Imagen» y (si jpg) sus JPEG de Bridge."""
    dngs = ev / core.OUTPUT_SUBFOLDER
    for i in range(n):
        td.write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", DNG_IMAGEN)
        if jpg:
            td.write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", JPG_IMAGEN)
    (dngs / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
    return dngs


def dxo_que_falla(f, malas: set, llamadas: list):
    """«DxO» que nunca revela las fotos `malas` (archivo dañado) y avisa enseguida de que se paró."""
    from rawflow import pipeline as pl

    def wait(self, dngs, stems, want, stop, emit=True):
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5:
            hechas = stems & {core.dng_base(q) for q in Path(dngs).glob("*.dng")}
            if len(hechas) >= len(stems - malas):
                break
            time.sleep(0.05)
        llamadas.append(len(stems))
        hechas = stems & {core.dng_base(q) for q in Path(dngs).glob("*.dng")}
        if len(hechas) < want:
            raise RuntimeError(f"DxO dejó de avanzar ({len(hechas)} de {want})")
        return hechas
    f._set(pl.Pipeline, "_wait_dxo", wait)
    batch = core.start_pureraw_batch

    def batch_sin_malas(app, raws):
        buenas = [r for r in raws if Path(r).stem.lower() not in malas]
        return batch(app, buenas) if buenas else Path("/nonexistent/batch.txt")   # DxO abre y no hace nada
    f._set(core, "start_pureraw_batch", batch_sin_malas)


def jpeg_xmp(raw_name: str, imagen: bool, extra: bytes = b"") -> bytes:
    """JPEG mínimo con XMP como el que deja Camera Raw: nombre del RAW y, si `imagen`, los ajustes de Imagen."""
    xmp = (b'<x:xmpmeta><rdf:Description xmpMM:PreservedFileName="' + raw_name.encode() + b'" '
           + (b'crs:Temperature="5200" ' if imagen else b'crs:WhiteBalance="As Shot" ') + b'/></x:xmpmeta>')
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    app1 = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return b"\xff\xd8" + app1 + b"\xff\xda\x00\x08" + b"\x00" * 6 + extra + b"\x00" * 200 + b"\xff\xd9"


def dng_xmp(imagen: bool) -> bytes:
    """DNG (TIFF) mínimo con su XMP en la etiqueta 700, con o sin las ediciones de Imagen."""
    import struct
    xmp = b"<x:xmpmeta>" + (b'crs:Temperature="5200"' if imagen else b'crs:Version="17.0"') + b"</x:xmpmeta>"
    ifd = struct.pack("<H", 1) + struct.pack("<HHII", 700, 1, len(xmp), 8 + 2 + 12 + 4) + struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + ifd + xmp + b"\x00" * 100


# ======================================================================= pruebas
@prueba("Evento normal de 12 fotos desde la memoria: llega entero, numerado del 1 al 12")
def p01():
    card, ev = td.fresh("p01_memoria"), td.fresh("p01_evento")
    files = [td.raw(card, f"DSC{1000 + i}.ARW", i) for i in range(12)]
    from rawflow.pipeline import Pipeline
    with td._Falsos() as f:
        (ev / core.OUTPUT_SUBFOLDER).mkdir()
        (ev / core.OUTPUT_SUBFOLDER / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
        p = Pipeline(ev, files, DXO, "CINEMA", [], 0)
        p._run_steps()
    assert "failed_step" not in p.summary and not p.summary.get("avisos"), p.summary
    assert not list((card / "DCIM").glob("*.ARW")), "las fotos tenían que salir de la memoria"
    assert len(core.good_raws(ev)) == 12 and (ev / core.RAW_SUBFOLDER / core.GOOD_SUBFOLDER).is_dir()
    assert finales(ev) == list(range(1, 13)), finales(ev)


@prueba("Dos memorias con fotos que se llaman igual (DSC1000 en las dos): ninguna pisa a otra")
def p02():
    a, b, ev = td.fresh("p02_a"), td.fresh("p02_b"), td.fresh("p02_ev")
    files = [td.raw(a, "DSC1000.ARW", 1), td.raw(a, "DSC1001.ARW", 2), td.raw(b, "DSC1000.ARW", 3)]
    sums = {td.h(Path(x.path)) for x in files}
    st = core.move_files_parallel(files, ev / core.RAW_SUBFOLDER, None, lambda: False, move=True)
    got = list((ev / core.RAW_SUBFOLDER).iterdir())
    assert st.failed == 0 and len(got) == 3 and {td.h(q) for q in got} == sums, [q.name for q in got]


@prueba("La misma memoria importada dos veces: no se duplica ninguna foto")
def p03():
    td.misma_memoria_otra_vez()


@prueba("Evento con un nombre que ya existe: se crea «… 2», nada se mezcla")
def p04():
    td.carpeta_evento_duplicada()


@prueba("Un RAW dañado que DxO no revela: el evento termina igual con las demás y lo avisa")
def p05():
    ev = td._evento_tandas(10)
    llamadas = []
    with td._Falsos() as f:
        dxo_que_falla(f, {"dsc1003"}, llamadas)
        (ev / core.OUTPUT_SUBFOLDER).mkdir()
        (ev / core.OUTPUT_SUBFOLDER / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
        p = td._correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    assert len(llamadas) == 3, llamadas                     # 3 intentos de DxO y luego se aparta
    assert any("DSC1003" in a for a in p.summary.get("avisos", [])), p.summary
    assert finales(ev) == list(range(1, 10)), finales(ev)     # 9 fotos entregadas
    assert p.summary.get("apartadas") == {"dsc1003": "DxO no pudo revelarla"}, p.summary
    assert not (ev / core.OUTPUT_SUBFOLDER / core.APARTADAS).exists(), "no deja marcas en la carpeta"


@prueba("Bridge se deja una foto sin exportar: reintenta solo esa y, si no sale, el evento SE PARA en Imagen "
        "(regla del 26-nov-2026: ningún paso se salta)", solo_mac="Imagen por tandas mientras DxO trabaja")
def p06():
    from rawflow import imagen_ui
    ev = td._evento_tandas(8)
    con_dng_y_jpg(ev, 8, jpg=False)
    intentos = []
    with td._Falsos() as f:
        run = imagen_ui.run

        def bridge_sin_una(ev_, sub, log, **k):
            intentos.append(sub)
            out = run(ev_, sub, log, **k)
            (Path(k["out_dir"]) / "DSC1005.jpg").unlink(missing_ok=True)   # esa no sale nunca
            return out
        f._set(imagen_ui, "run", bridge_sin_una)
        p = td._correr(ev, 3)
    assert p.summary.get("failed_step") == core.STEP_IMG, p.summary          # se para en Imagen
    assert len(intentos) >= 2 and any(s.startswith("2 DNGs/Tanda") for s in intentos[1:]), intentos  # reintenta la que falta
    assert not finales(ev), finales(ev)                                      # nada se entrega a medias


@prueba("Retouch4me no puede con una foto: lo reintenta SOLO con esa y, si no sale, el evento SE PARA en Retouch4me "
        "(regla del 26-nov-2026: ningún paso se salta; nunca se entrega una foto sin retocar)")
def p07():
    from rawflow import retouch_engine as re_
    ev = td._evento_tandas(6)
    con_dng_y_jpg(ev, 6)
    llamadas = []
    with td._Falsos() as f:
        def retouch(src, dest, log, **k):
            Path(dest).mkdir(parents=True, exist_ok=True)
            todo = [p for p in sorted(Path(src).glob("*.jpg")) if not (Path(dest) / p.name).exists()]
            llamadas.append(len(todo))
            for p in todo:
                if p.stem != "DSC1002":
                    td.write(Path(dest) / p.name, b"RETOCADA")
            raise RuntimeError("Retouch4me: 1 fotos sin retocar (DSC1002.jpg: error del plugin)")
        f._set(re_, "retouch", retouch)
        p = td._correr(ev, 4)
    assert p.summary.get("failed_step") == core.STEP_RET, p.summary    # se para en Retouch4me
    assert llamadas == [6, 1, 1, 1], llamadas                  # 4 intentos, cada uno solo con la que falta
    fin = ev / core.FINAL_SUBFOLDER
    assert not any(q.read_bytes() == JPG_IMAGEN for q in fin.glob("*.jpg"))  # ninguna «final» sin retocar


@prueba("CineSlide falla siempre: el evento SE PARA en CineSlide (regla del 26-nov-2026: ningún paso se salta); "
        "las fotos finales ya hechas se quedan y se reintenta desde ese paso")
def p08():
    from rawflow import pipeline as pl
    ev = td._evento_tandas(5)
    con_dng_y_jpg(ev, 5)
    veces = []
    with td._Falsos() as f:
        def present(self):
            veces.append(1)
            raise RuntimeError("CineSlide no pudo crear el video")
        f._set(pl.Pipeline, "_present", present)
        p = td._correr(ev, 4)
    assert p.summary.get("failed_step") == core.STEP_PRES and len(veces) == 3, (p.summary, veces)
    assert finales(ev) == list(range(1, 6)), p.summary


@prueba("Retouch4me se corta a mitad: el reintento automático hace SOLO las que faltan")
def p09():
    from rawflow import retouch_engine as re_
    ev = td._evento_tandas(10)
    con_dng_y_jpg(ev, 10)
    llamadas = []
    with td._Falsos() as f:
        def retouch(src, dest, log, **k):
            Path(dest).mkdir(parents=True, exist_ok=True)
            todo = [p for p in sorted(Path(src).glob("*.jpg")) if not (Path(dest) / p.name).exists()]
            llamadas.append(len(todo))
            for n, p in enumerate(todo):
                if len(llamadas) == 1 and n == 4:
                    raise RuntimeError("El motor de Retouch4me dejó de avanzar (4 de 10)")
                td.write(Path(dest) / p.name, b"RETOCADA")
            return len(list(Path(dest).glob("*.jpg")))
        f._set(re_, "retouch", retouch)
        p = td._correr(ev, 4)
    assert "failed_step" not in p.summary and llamadas == [10, 6], (p.summary, llamadas)
    assert finales(ev) == list(range(1, 11)) and not p.summary.get("avisos"), p.summary


@prueba("«Reintentar desde el paso 4»: no repite DxO, Imagen ni Bridge")
def p10():
    ev = td._evento_tandas(7)
    con_dng_y_jpg(ev, 7)
    lotes = []
    with td._Falsos() as f:
        f._set(core, "start_pureraw_batch", lambda app, raws: lotes.append(len(raws)))
        p = td._correr(ev, 4)
    assert "failed_step" not in p.summary, p.summary
    assert lotes == [] and f.bridges == [] and f.ediciones == [] and [c[0] for c in f.ret_calls] == [7]
    assert finales(ev) == list(range(1, 8))


@prueba("RawFlow se cierra de golpe a mitad de un paso: al abrirlo ofrece seguir desde ESE paso")
def p11():
    from types import SimpleNamespace as NS
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from rawflow import app as A
    QApplication.instance() or QApplication([])
    s = QSettings(str(td.TMP / "ajustes.ini"), QSettings.Format.IniFormat)
    ev = td.fresh("p11_evento")
    w = NS(settings=s, last_event=ev, pills=[NS(set=lambda st: None) for _ in range(5)],
           runscreen=NS(set_step=lambda i, st: None, set_eco=lambda on: None),
           status=NS(setText=lambda t: None), timepanel=NS(step=lambda i, st: None, dur=[None] * 5),
           _run_tick=lambda: None, pipe=None, estimator=None, run_n=1, run_bytes=1)
    w._remember_pending = lambda e, st: A.MainWindow._remember_pending(w, e, st)
    for i in range(4):                                      # pasos 1-4 en marcha… y se va la luz en el 4
        A.MainWindow._on_step(w, i, "run")
    assert s.value("pendiente_evento") == str(ev) and int(s.value("pendiente_paso")) == 3, dict(
        evento=s.value("pendiente_evento"), paso=s.value("pendiente_paso"))
    A.MainWindow._remember_pending(w, ev, None)             # al terminar bien, se olvida
    assert s.value("pendiente_evento", "") == ""


@prueba("Imagen falla en una tanda (evento grande): al reintentar no se cobra otra vez lo ya editado")
def p12():
    td.tandas_falla_imagen_y_se_retoma_sin_recobrar()


@prueba("Corte de luz con un DNG a medio escribir: se detecta y se rehace solo ese")
def p13():
    td.dng_cortado_no_cuenta()


@prueba("Bridge exportó algunas dos veces (-2, «-DxO»): se limpian, sin fotos repetidas")
def p14():
    td.exportaciones_de_bridge_repetidas()


@prueba("Numeración final 1, 2, 3… en orden de captura, sin pisar ninguna (aunque una ya se llame «2»)")
def p15():
    td.numeracion_final_sin_pisar()


@prueba("Actualización de CineSlide: la versión instalada tiene todo lo que RawFlow usa para la presentación")
def p16():
    import ast
    f = Path.home() / "Library/Application Support/CineSlide/CineSlide.py"
    if not f.exists():
        return
    tree = ast.parse(f.read_text(encoding="utf-8-sig"))
    top = {getattr(n, "name", None) for n in tree.body} | {t.id for n in tree.body if isinstance(n, ast.Assign)
                                                            for t in n.targets if isinstance(t, ast.Name)}
    need = {"MANIFEST", "_prefs_cargar", "T1_INTERNAS", "T6_INTERNAS", "AJUSTES_FIJOS", "_clave_natural",
            "_HOOK_PROGRESO", "formatos_de_video", "crear_presentacion_express"}
    assert need <= top, need - top
    fn = next(n for n in tree.body if getattr(n, "name", "") == "crear_presentacion_express")
    args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert {"musica", "carpeta_salida", "nombre"} <= set(args), args


@prueba("Actualización de RawFlow: el historial de tiempos de versiones anteriores (y líneas rotas) se lee igual")
def p17():
    from rawflow import timing
    hist = td.TMP / "tiempos.csv"
    hist.write_text("fecha,fase,fotos,bytes,segundos\n2026-11-18 10:00,dxo,7,0,90\n"
                    "basura,,,\n2026-11-20 17:45,bridge,408,1,1180.6\n2026-11-22 03:59,ret,468,1,34826\n"
                    "2026-11-22 04:12,pres,480,1,751\n2026-11-22 04:13,fase_nueva,3,1,2\n", encoding="utf-8")
    orig = timing.HIST
    timing.HIST = hist
    try:
        est = timing.Estimator().estimate(480, 480 * 30_000_000)
    finally:
        timing.HIST = orig
    assert len(est) == 7 and all(e > 0 for e in est), est
    assert 400 * 60 < est[core.STEP_RET] < 600 * 75, est     # Retouch4me: lo medido en este Mac


@prueba("Memoria con una foto que no se puede leer: se copian las demás, esa se queda en la memoria y se avisa")
def p18():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise Saltada("como administrador (root) todo se puede leer")
    card, ev = td.fresh("p18_memoria"), td.fresh("p18_evento")
    files = [td.raw(card, f"DSC{1000 + i}.ARW", i) for i in range(6)]
    mala = Path(files[2].path)
    candado = None
    if sys.platform == "win32":                               # Windows: chmod no quita la lectura; se bloquea
        import msvcrt
        candado = open(mala, "rb")
        msvcrt.locking(candado.fileno(), msvcrt.LK_NBLCK, mala.stat().st_size)
    else:
        os.chmod(mala, 0)                                     # ilegible, como un sector dañado
    from rawflow.pipeline import Pipeline
    try:
        with td._Falsos():
            (ev / core.OUTPUT_SUBFOLDER).mkdir()
            (ev / core.OUTPUT_SUBFOLDER / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
            p = Pipeline(ev, files, DXO, "CINEMA", [], 0)
            p._run_steps()
    finally:
        if candado:
            msvcrt.locking(candado.fileno(), msvcrt.LK_UNLCK, mala.stat().st_size)
            candado.close()
        else:
            os.chmod(mala, 0o644)
    assert "failed_step" not in p.summary, p.summary
    assert mala.exists(), "la foto que no se pudo leer tiene que seguir en la memoria"
    assert any("DSC1002" in a for a in p.summary.get("avisos", [])), p.summary
    assert finales(ev) == list(range(1, 6)), finales(ev)


@prueba("Fallo general (Retouch4me no va con casi ninguna): se para en ese paso y el reintento sigue desde ahí")
def p19():
    from rawflow import retouch_engine as re_
    ev = td._evento_tandas(10)
    con_dng_y_jpg(ev, 10)
    with td._Falsos() as f:
        def roto(src, dest, log, **k):
            raise RuntimeError("Retouch4me: 10 fotos sin retocar (licencia caducada)")
        f._set(re_, "retouch", roto)
        p = td._correr(ev, 4)
    assert p.summary.get("failed_step") == core.STEP_RET, p.summary   # no se entregan 10 fotos sin retocar a escondidas
    assert not list((ev / core.FINAL_SUBFOLDER).glob("*.jpg")) if (ev / core.FINAL_SUBFOLDER).exists() else True
    lotes = []
    with td._Falsos() as g:
        g._set(core, "start_pureraw_batch", lambda app, raws: lotes.append(len(raws)))
        p2 = td._correr(ev, 4)                                # «Reintentar desde el paso 4»
    assert "failed_step" not in p2.summary and lotes == [] and g.bridges == [], p2.summary
    assert finales(ev) == list(range(1, 11))


@prueba("Esc ×3 a mitad de Retouch4me: se cancela al instante y el reintento hace solo las que faltaban")
def p20():
    from rawflow import retouch_engine as re_
    from rawflow.pipeline import Pipeline
    ev = td._evento_tandas(9)
    con_dng_y_jpg(ev, 9)
    with td._Falsos() as f:
        p = Pipeline(ev, [], DXO, "CINEMA", [], 4)

        def retouch(src, dest, log, should_stop=lambda: False, **k):
            Path(dest).mkdir(parents=True, exist_ok=True)
            for n, q in enumerate(sorted(Path(src).glob("*.jpg"))):
                if n == 4:
                    p.stop()                                  # el usuario pulsa Esc tres veces
                    raise RuntimeError("Cancelado.")
                td.write(Path(dest) / q.name, b"RETOCADA")
        f._set(re_, "retouch", retouch)
        p._run_steps()
    assert p.summary.get("failed_step") == core.STEP_RET, p.summary
    with td._Falsos() as g:
        p2 = td._correr(ev, 4)
    assert "failed_step" not in p2.summary and [c[0] for c in g.ret_calls] == [5], g.ret_calls
    assert finales(ev) == list(range(1, 10))


@prueba("Falta una foto de Bridge al llegar a Retouch4me: vuelve SOLO a Bridge por ella y sigue, sin pararse", solo_mac="Imagen por tandas mientras DxO trabaja")
def p21():
    ev = td._evento_tandas(6)
    dngs = con_dng_y_jpg(ev, 6)
    (dngs.parent / core.IMAGEN_SUBFOLDER / "DSC1004.jpg").unlink()             # se perdió un JPEG antes de Retouch4me
    with td._Falsos() as f:
        p = td._correr(ev, 4)
    assert "failed_step" not in p.summary, p.summary
    assert [(x[0].split("/")[0], x[1]) for x in f.bridges] == [("2 DNGs", 1)], f.bridges
    assert finales(ev) == list(range(1, 7))


@prueba("Evento ya terminado y vuelto a lanzar: no rehace nada ni duplica nada")
def p22():
    ev = td._evento_tandas(4)
    con_dng_y_jpg(ev, 4)
    with td._Falsos() as f:
        td._correr(ev, 2)
        antes = sorted((q.name, td.h(q)) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg"))
        f.ret_calls.clear()
        f.bridges.clear()
        p = td._correr(ev, 2)
    assert "failed_step" not in p.summary and f.bridges == [] and f.ret_calls == [], (f.bridges, f.ret_calls)
    assert sorted((q.name, td.h(q)) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == antes


@prueba("Batería: con cargador no avisa; sin cargador sabe el % y para cuánto da")
def p23():
    class R:
        def __init__(self, out):
            self.stdout = out
    orig = core.subprocess.run
    salidas = {
        "ac": "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n",
        "bat": "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t58%; discharging; 2:40 remaining present: true\n",
        "mesa": "Now drawing from 'AC Power'\n",
    }
    try:
        res = {}
        for k, out in salidas.items():
            core.subprocess.run = lambda *a, _o=out, **kw: R(_o)
            res[k] = core.power_state()
    finally:
        core.subprocess.run = orig
    if core.SYSTEM != "Darwin":
        return
    assert res["ac"] == (True, 100, None) and res["bat"] == (False, 58, 160) and res["mesa"][0] is True, res


@prueba("Tiempo que falta: manda el ritmo REAL del paso (no una estimación vieja)")
def p24():
    from PySide6.QtWidgets import QApplication
    from rawflow import live
    from rawflow.app import TimePanel
    QApplication.instance() or QApplication([])
    tp = TimePanel()
    tp.begin_run([10, 40, 900, 900, 900, 60, 60], 0)             # el modelo decía 15 min para DxO
    tp.step(core.STEP_DXO, "run")
    live.reset()
    live.set_eta(core.STEP_DXO, 120)                          # pero DxO va rápido: le faltan 2 min
    assert abs(tp.remaining_current() - 120) < 2, tp.remaining_current()
    live.reset()
    assert tp.remaining_current() > 600                       # sin dato del paso: el modelo


@prueba("El evento se para (error o Esc ×3): igualmente cierra DxO, Imagen, Bridge y Retouch4me (nada abierto)")
def p25():
    from rawflow import pipeline as pl
    from rawflow import retouch_engine as re_
    ev = td._evento_tandas(4)
    con_dng_y_jpg(ev, 4)
    cerradas = []
    with td._Falsos() as f:
        f._set(pl.Pipeline, "_close_apps", lambda self_: cerradas.append("todas"))

        def roto(src, dest, log, **k):
            raise RuntimeError("Retouch4me: 4 fotos sin retocar (licencia caducada)")
        f._set(re_, "retouch", roto)
        p = td._correr(ev, 4)
    assert p.summary.get("failed_step") == core.STEP_RET and cerradas == ["todas"], (p.summary, cerradas)


# --------------------------------------------------------------------------- «Instalar máquina virtual» (simulada)
class _Parches:
    """Cambia funciones el rato de la prueba y las deja como estaban (no se toca ninguna máquina de verdad)."""

    def __init__(self):
        self._orig = []

    def set(self, obj, name, val):
        self._orig.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        for obj, name, val in reversed(self._orig):
            setattr(obj, name, val)


def _instalar(desde_base, nuevos=(), dxo_en_base=True):
    """Corre «Instalar máquina virtual» con UTM y Windows simulados; devuelve lo que se hizo, en orden."""
    import json as js

    from rawflow import vm, vm_auto, vm_dialog
    from rawflow import vm_programas as vp
    hecho, logs = [], []
    with _Parches() as p:
        p.set(vp, "from_base", lambda name, log: (hecho.append("copia de la base"), "RawFlow Windows 2")[1]
              if desde_base else None)
        p.set(vp, "update_exe", lambda v, log: (hecho.append("RawFlow al día"), "ok")[1])
        p.set(vp, "install_missing", lambda v, log: (hecho.append("instalar lo nuevo"), list(nuevos))[1])
        p.set(vp, "detect", lambda v: js.dumps({"DxO PureRAW": "C:\\dxo.exe" if dxo_en_base or nuevos else "",
                                                 "Imagen AI": "C:\\i.exe", "Retouch4me Arams": "C:\\r.exe"}))
        p.set(vp, "installers_root", lambda: Path("/tmp"))
        p.set(vp, "provision", lambda v, log, arch="arm64": (hecho.append("limpiar + programas + barra"), {})[1])
        p.set(vp, "guided_setup", lambda v, log, only=None: (hecho.append(("guía", only)), {})[1])
        p.set(vp, "finish_setup", lambda v, log: (hecho.append("barra + reinicio + base"), {})[1])
        p.set(vm_auto, "install_windows", lambda *a, **k: (hecho.append("Windows"), "UUID-NUEVA")[1])
        p.set(vm, "inspect_iso", lambda iso: type("I", (), {"arch": "aarch64"})())
        p.set(vm_dialog.subprocess if hasattr(vm_dialog, "subprocess") else subprocess, "run",
              lambda *a, **k: None)
        w = vm_dialog.InstallWorker(Path("/tmp/win.iso"), "Alejandro")
        res = []
        w.log.connect(logs.append)
        w.done.connect(lambda u, e: res.append((u, e)))
        w.run()
    return hecho, res, logs


@prueba("Instalar máquina virtual (nueva): Windows → limpieza y programas → inicio de sesión guiado → barra, reinicio y base")
def p26():
    hecho, res, logs = _instalar(desde_base=False)
    assert hecho == ["Windows", "limpiar + programas + barra", ("guía", None), "barra + reinicio + base"], hecho
    assert res == [("UUID-NUEVA", "")], res


@prueba("Instalar máquina virtual (copia de la base, nada nuevo): segundos, sin pedir ningún inicio de sesión")
def p27():
    hecho, res, logs = _instalar(desde_base=True)
    assert hecho == ["copia de la base", "RawFlow al día", "instalar lo nuevo"], hecho
    assert res == [("RawFlow Windows 2", "")], res


@prueba("Instalar máquina virtual (copia de la base + DxO añadido después): lo instala y SOLO pide la sesión de DxO")
def p28():
    hecho, res, logs = _instalar(desde_base=True, nuevos=("DxO PureRAW",), dxo_en_base=False)
    assert hecho == ["copia de la base", "RawFlow al día", "instalar lo nuevo", ("guía", ("DxO PureRAW",)),
                     "barra + reinicio + base"], hecho


def _guia(progs, instalador_dxo=None, only=None, baja=True, apagada=False):
    """La guía de inicio de sesión con Windows simulado; devuelve los avisos que se mostraron, en orden."""
    import json as js

    from rawflow import vm_programas as vp
    avisos, abiertas = [], []
    estado = {"dxo": instalador_dxo, "progs": dict(progs)}
    with _Parches() as p:
        p.set(vp, "detect", lambda v: js.dumps(estado["progs"]))
        p.set(vp, "plan", lambda root, arch="arm64": {"dxo": estado["dxo"]})
        p.set(vp, "guide_show", lambda v, texto, exe="", proc="", minutos=45: (avisos.append(texto), str(len(avisos)))[1])
        p.set(vp, "guide_closed", lambda v, tag: not apagada)
        p.set(vp, "vm_running", lambda v: not apagada)
        p.set(vp, "guide_hide", lambda v, tag: None)
        p.set(vp, "open_page", lambda v, url: abiertas.append(url))
        p.set(vp, "wait_download", lambda v, words, minutos, log: "C:\\Users\\A\\Downloads\\DxO_PureRAW6_Setup.exe"
              if baja else None)

        def collect(v, log, root):
            estado["dxo"] = Path("/tmp/DxO_PureRAW6_Setup.exe")
        p.set(vp, "collect_downloads", collect)
        p.set(vp, "installer_kind", lambda path: "burn")

        def provision(v, log, root=None, tidy=True, skip=()):
            estado["progs"]["DxO PureRAW"] = "C:\\Program Files\\DxO\\PureRAW.exe"
            return {"DxO PureRAW": "ok"}
        p.set(vp, "provision", provision)
        p.set(vp, "ps", lambda v, s, t=0: (0, ""))
        p.set(vp.time, "sleep", lambda s: None)
        res = vp.guided_setup("VM", lambda m: None, root=Path("/tmp"), only=only)
    return avisos, abiertas, res


@prueba("Inicio de sesión guiado: una sola página (DxO) y luego cada programa de uno en uno, en orden")
def p29():
    from rawflow import vm_programas as vp
    avisos, abiertas, res = _guia({"Imagen AI": "C:\\i.exe", "Retouch4me Arams": "C:\\r.exe", "DxO PureRAW": ""})
    assert abiertas == [vp.DXO_LOGIN], abiertas                        # UNA página, nunca varias pestañas
    assert [a.split(" · ")[0] for a in avisos[:4]] == ["Paso 1 de 4", "Paso 2 de 4", "Paso 3 de 4", "Paso 4 de 4"], avisos
    assert "DxO:" in avisos[0] and "DxO PureRAW" in avisos[1] and "Retouch4me" in avisos[2] and "Imagen" in avisos[3]
    assert avisos[-1].startswith("Listo"), avisos
    assert res.get("DxO PureRAW") == "hecho" and res.get("Imagen AI") == "hecho", res


@prueba("Inicio de sesión guiado en una copia: solo lo recién instalado; si DxO ya está en «Instaladores», sin web")
def p30():
    avisos, abiertas, res = _guia({"Imagen AI": "C:\\i.exe", "Retouch4me Arams": "C:\\r.exe", "DxO PureRAW": "C:\\d.exe"},
                                  only=("Retouch4me Arams",))
    assert abiertas == [] and len(avisos) == 2 and avisos[0].startswith("Paso 1 de 1 · Retouch4me"), avisos
    avisos, abiertas, res = _guia({"Imagen AI": "C:\\i.exe", "DxO PureRAW": ""}, instalador_dxo=Path("/tmp/d.exe"),
                                  only=())
    assert abiertas == [] and avisos[0].startswith("Paso 1 de 1 · DxO PureRAW"), avisos   # instala y pide activar


@prueba("Descarga en Windows: solo cuenta cuando está ENTERA (sin .crdownload y con el tamaño quieto)")
def p31():
    from rawflow import vm_programas as vp
    salidas = iter(["NADA 1", "ARCHIVO C:\\D\\DxO_PureRAW6_Setup.exe|5000000|1",          # aún bajando
                    "ARCHIVO C:\\D\\DxO_PureRAW6_Setup.exe|900000000|0",
                    "ARCHIVO C:\\D\\DxO_PureRAW6_Setup.exe|900000000|0"])
    with _Parches() as p:
        p.set(vp, "ps", lambda v, s, t=0: (0, next(salidas)))
        p.set(vp, "vm_running", lambda v: True)
        p.set(vp.time, "sleep", lambda s: None)
        got = vp.wait_download("VM", ("pureraw", "dxo"), 1, lambda m: None)
    assert got == "C:\\D\\DxO_PureRAW6_Setup.exe", got


@prueba("Windows limpio: nunca quita lo necesario (Edge, Tienda, Fotos, códecs…) ni borra claves del registro")
def p32():
    import re

    from rawflow import vm_programas as vp
    imprescindibles = ("Microsoft.MicrosoftEdge.Stable", "Microsoft.WindowsStore", "Microsoft.DesktopAppInstaller",
                       "Microsoft.Windows.Photos", "Microsoft.ZuneMusic", "Microsoft.RawImageExtension",
                       "Microsoft.HEIFImageExtension", "Microsoft.HEVCVideoExtension", "Microsoft.WebpImageExtension",
                       "Microsoft.SecHealthUI", "Microsoft.WindowsNotepad", "Microsoft.WindowsCalculator",
                       "Microsoft.WindowsTerminal", "Microsoft.StartExperiencesApp")
    assert not set(imprescindibles) & set(vp.QUITAR), set(imprescindibles) & set(vp.QUITAR)
    for script in (vp._LIMPIEZA_PS, vp._ORDEN_PS):            # New-Item -Force BORRA una clave que ya existía
        malas = [ln for ln in script.splitlines()
                 if re.search(r"New-Item\s+-Path", ln) and "function Key" not in ln]
        assert not malas, malas


@prueba("Cerrar todo: los ayudantes huérfanos de Imagen/Retouch4me se cierran; con la app abierta, nada suyo", solo_mac="procesos del Mac (ps) de ImagenAI")
def p33():
    import subprocess as sp

    from rawflow import pipeline as pl
    imagen = "/Applications/ImagenAI.app/Contents/"
    base = [f"  501  1 {imagen}Resources/dist_main/vendor/imagent-mac/imagent -p x",                 # huérfano
            f"  502  1 {imagen}Frameworks/Electron Framework.framework/Helpers/chrome_crashpad_handler",  # huérfano
            f"  503  777 {imagen}Frameworks/Electron Framework.framework/Helpers/ImagenAI Helper",    # de RawFlow
            "  504  1 /Applications/PureRAW 6/DxO PureRAW 6.app/Contents/MacOS/PureRAWv6",            # otra app
            "  505  1 HBU78K46ZD.com.dxo-labs.PureRAWv6.standalone.xpcbridge"]
    for abierta, esperado in ((True, []), (False, [501, 502])):
        filas = base + ([f"  600  1 {imagen}MacOS/ImagenAI"] if abierta else [])
        muertos = []
        with _Parches() as p:
            p.set(sp, "run", lambda *a, **k: type("R", (), {"stdout": "\n".join(filas), "returncode": 0})())
            p.set(pl.os, "kill", lambda pid, sig: muertos.append(pid))
            pl.kill_orphan_helpers()
        assert muertos == esperado, (abierta, muertos)


@prueba("Al empezar o reintentar DESDE CUALQUIER PASO (también Retouch4me y CineSlide) se cierra todo lo demás")
def p34():
    from rawflow import imagen_ui
    from rawflow import pipeline as pl
    for desde in (0, 1, 2, 3, 4):
        cierres = []
        with _Parches() as p:
            p.set(pl, "quit_other_apps", lambda *a, **k: cierres.append(desde))
            for name in ("full_speed", "stage_manager", "restore_stage_manager"):
                p.set(pl, name, lambda *a, **k: None)
            p.set(imagen_ui, "restore_clipboard", lambda *a, **k: None)
            p.set(pl.Pipeline, "_run_steps", lambda self_: None)
            pipe = pl.Pipeline(Path("/tmp/ev"), [], DXO, "CINEMA", [], desde)
            pipe.run()
            if pipe._quitter is not None:
                pipe._quitter.join(5)
        assert cierres == [desde], (desde, cierres)


def _cerrar_todo(abiertas, vm_ocupada=False, vm_tarda=False):
    """quit_other_apps con macOS y UTM simulados; devuelve lo que pidió (cerrar apps, apagar máquinas)."""
    import subprocess as sp

    from rawflow import pipeline as pl
    from rawflow import vm_programas as vp
    hecho = {"cerrar": [], "apagar": [], "apagar_ya": [], "ocultar": []}
    vms = {"U1": "started"}
    vueltas = {"n": 0}

    def run(cmd, **k):
        txt = " ".join(cmd)
        out = ""
        if "get name of every process" in txt:
            out = ", ".join(abiertas)
        elif "to quit" in txt:
            hecho["cerrar"].append(txt.split('tell application "')[1].split('"')[0])
        elif "set visible" in txt:
            hecho["ocultar"].append(txt)
        elif cmd[:1] == ["pgrep"]:
            return type("R", (), {"returncode": 1, "stdout": ""})()
        elif cmd[:1] == [vp.UTMCTL] and cmd[1] == "stop":
            hecho["apagar_ya"].append(cmd[2])
            vms[cmd[2]] = "stopped"
        return type("R", (), {"returncode": 0, "stdout": out})()

    def popen(cmd, **k):
        if cmd[:2] == [vp.UTMCTL, "stop"]:
            hecho["apagar"].append(cmd[2])
            if not vm_tarda:
                vms[cmd[2]] = "stopped"

    def rows():
        vueltas["n"] += 1
        return [(u, st, "RawFlow Windows") for u, st in vms.items()]
    with _Parches() as p:
        p.set(sp, "run", run)
        p.set(sp, "Popen", popen)
        p.set(pl.time, "sleep", lambda s: None)
        p.set(pl, "VM_STOP_WAIT", 0.05)
        p.set(vp, "utm_rows", rows)
        p.set(vp, "BUSY", vm_ocupada)
        p.set(pl, "kill_orphan_helpers", lambda: hecho.setdefault("huerfanos", True))
        os.environ.pop("RAWFLOW_NO_CERRAR", None)
        pl.quit_other_apps(lambda s: None, wait=0.5)
    return hecho


@prueba("Cerrar todo: Windows (UTM) se apaga BIEN antes de cerrar UTM; si RawFlow instala una máquina, ni se toca", solo_mac="Windows en UTM")
def p35():
    h = _cerrar_todo(["Finder", "Claude", "UTM", "Safari", "QuickTime Player"])
    assert sorted(h["cerrar"]) == ["Claude", "QuickTime Player", "Safari", "UTM"], h       # todo menos el Finder
    assert h["apagar"] == ["U1"] and h["apagar_ya"] == [] and h.get("huerfanos"), h       # apagado normal, a tiempo
    h = _cerrar_todo(["Finder", "UTM"], vm_tarda=True)
    assert h["apagar"] == ["U1"] and h["apagar_ya"] == ["U1"] and h["cerrar"] == ["UTM"], h   # no se apagó: del todo
    h = _cerrar_todo(["Finder", "UTM", "Safari"], vm_ocupada=True)
    assert h["cerrar"] == ["Safari"] and h["apagar"] == [] and not h["ocultar"], h        # instalando: UTM sigue


@prueba("Modo de bajo consumo: se detecta (para avisar en la pantalla del proceso)")
def p36():
    import subprocess as sp
    for valor, esperado in (("1", True), ("0", False)):
        salida = f"System-wide power settings:\nCurrently in use:\n standby              1\n lowpowermode         {valor}\n"
        with _Parches() as p:
            p.set(sp, "run", lambda *a, **k: type("R", (), {"stdout": salida, "returncode": 0})())
            p.set(core, "SYSTEM", "Darwin")
            assert core.low_power_mode() is esperado, valor


@prueba("Windows se apaga a mitad de la guía (p. ej. empieza un evento): para sin error y lo dice; nada revienta")
def p37():
    import subprocess as sp

    from rawflow import vm_programas as vp
    avisos, abiertas, res = _guia({"Imagen AI": "C:\\i.exe", "Retouch4me Arams": "C:\\r.exe", "DxO PureRAW": "C:\\d.exe"},
                                  apagada=True)
    assert res.get("Windows") == vp.APAGADA, res                       # se para en el primer programa, sin excepción
    hecho = []
    with _Parches() as p:                                              # el final no toca una máquina apagada
        p.set(vp, "vm_running", lambda v: False)
        p.set(vp, "organize_desktop", lambda v, log: hecho.append("barra"))
        p.set(vp, "make_base", lambda v, log: hecho.append("base"))
        r = vp.finish_setup("VM", lambda m: None)
    assert hecho == [] and r["Base"].startswith("pendiente"), (hecho, r)

    def tarda(*a, **k):
        raise sp.TimeoutExpired("utmctl", 60)
    with _Parches() as p:                                              # una consulta que no contesta: no revienta
        p.set(vp, "_utm", tarda)
        assert vp.ps("VM", "'hola'") == (124, "TIMEOUT")


# ======================================================================= candado de Imagen (JUTIAPA, 22-nov-2026)
@prueba("JUTIAPA: Imagen «terminó» la tanda 1 pero sus ediciones no llegaron a los DNG → NADA pasa a Bridge ni a "
        "Retouch4me sin Imagen: esas fotos se piden otra vez y TODAS las finales llevan Imagen", solo_mac="Imagen por tandas mientras DxO trabaja")
def p38():
    from rawflow import imagen_ui as iu, retouch_engine as re_
    ev = td._evento_tandas(30)
    vistos_bridge, vistos_retouch, primera = [], [], []
    with td._Falsos() as f:
        f._set(core, "tanda_size", lambda n: 10)              # 3 tandas que suben mientras DxO sigue
        edit_ok, run_ok, ret_ok = iu.edit_only, iu.run, re_.retouch

        def edit_only(ev_, sub, log, *a, **k):
            d = Path(ev_) / sub
            if sub.endswith("Tanda 1") and not primera:       # lo que hizo 4.14: «hecho» sin ediciones
                primera.append(1)
                (d / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
                return
            return edit_ok(ev_, sub, log, *a, **k)

        def run(ev_, sub, log, *a, **k):
            vistos_bridge.append(len(core.without_imagen((Path(ev_) / sub).glob("*.dng"))))
            return run_ok(ev_, sub, log, *a, **k)

        def retouch(src, dest, log, **k):
            vistos_retouch.append(len(core.without_imagen(Path(src).glob("*.jpg"))))
            return ret_ok(src, dest, log, **k)
        f._set(iu, "edit_only", edit_only)
        f._set(iu, "run", run)
        f._set(re_, "retouch", retouch)
        p = td._correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    assert primera and vistos_bridge and set(vistos_bridge) == {0}, vistos_bridge          # Bridge: nunca sin Imagen
    assert vistos_retouch and set(vistos_retouch) == {0}, vistos_retouch                  # Retouch4me: nunca sin Imagen
    fin = list((ev / core.FINAL_SUBFOLDER).glob("*.jpg"))
    assert finales(ev) == list(range(1, 31)) and not core.without_imagen(fin), core.without_imagen(fin)


@prueba("Imagen anota FAILURE al bajar las ediciones (DOWNLOAD_XMP_ERROR, como JUTIAPA): RawFlow lo lee al momento, "
        "las vuelve a pedir del MISMO proyecto y solo sigue con ellas en TODOS los DNG; si nunca llegan, se para")
def p39():
    from rawflow import imagen_ui as iu
    base = td.fresh("p39")
    logs = base / "logs"
    logs.mkdir(parents=True)
    log_f = logs / "renderer.2026-11-22.log"
    log_f.write_text("inicio\n", encoding="utf-8")
    d = base / "Boda Ñandú" / "2 DNGs" / "Tanda 1"            # con acentos: la ruta se compara bien
    for i in range(3):
        td.write(d / f"DSC{i}-DxO_DeepPRIME XD3.dng", dng_xmp(False))

    def linea(status, ok, code):
        return ('[2026-11-22T20:05:40Z] info: edit_download_directory_xmp_finished {"0":{"projectId":"1","path":"'
                + str(d) + f'","status":"{status}","errorCode":' + (f'"{code}"' if code else "null")
                + f',"total":3,"totalSuccess":{ok},"totalFailed":0}}}}\n')

    class Pantalla:
        def __init__(self, exito_en):
            self.intentos, self.exito_en, self.textos = 0, exito_en, []

        def log(self, m):
            self.textos.append(m)

        def should_stop(self):
            return False

        def find(self, *a, **k):
            return []

        def tap(self, text, **k):
            if text != "Descargar ediciones":
                return
            self.intentos += 1
            with open(log_f, "a", encoding="utf-8") as h:
                if self.intentos == self.exito_en:
                    for q in d.glob("*.dng"):
                        q.write_bytes(dng_xmp(True))
                    h.write(linea("SUCCESS", 3, None))
                else:
                    h.write(linea("FAILURE", 0, "DOWNLOAD_XMP_ERROR"))
    with _Parches() as p:
        p.set(iu, "IMAGEN_LOGS", logs)
        for name in ("_open_download_dialog", "_open_projects", "_wait_uploads_idle", "_diag", "_prog"):
            p.set(iu, name, lambda *a, **k: None)
        p.set(iu, "DL_WAIT", 0)
        p.set(iu, "PROMPT_WAIT", 0)
        p.set(iu.live, "set", lambda *a, **k: None)
        s = Pantalla(exito_en=2)
        t0 = time.monotonic()
        iu._download_edits(s, d, "Tanda 1 Boda")
        assert s.intentos == 2 and not core.without_imagen(d.glob("*.dng")), s.intentos
        assert any("DOWNLOAD_XMP_ERROR" in m for m in s.textos), s.textos
        assert time.monotonic() - t0 < 15, time.monotonic() - t0     # el fallo se sabe al momento, no a los 5 min
        for q in d.glob("*.dng"):
            q.write_bytes(dng_xmp(False))
        s = Pantalla(exito_en=99)                             # nunca llegan: se para, no sigue sin Imagen
        try:
            iu._download_edits(s, d, "Tanda 1 Boda")
            raise AssertionError("siguió sin Imagen")
        except iu.UIError as e:
            assert "sin Imagen no se sigue" in str(e) and s.intentos == iu.DL_TRIES, (str(e), s.intentos)
        # edit_only: sin las ediciones en todos los DNG, NUNCA deja la marca «hecho»
        p.set(iu, "check_permissions", lambda: "")
        p.set(iu, "Screen", lambda *a, **k: None)
        p.set(iu, "dismiss_crash_dialogs", lambda: None)
        p.set(iu, "imagen_edit", lambda *a, **k: False)       # «terminó» pero no escribió nada
        try:
            iu.edit_only(base / "Boda Ñandú", "2 DNGs/Tanda 1", lambda m: None)
            raise AssertionError("dio por hecha una tanda sin ediciones")
        except iu.UIError:
            pass
        assert not (d / ".rawflow_imagen_ok").exists()
        (d / ".rawflow_imagen_ok").write_text("ok")          # una marca vieja que miente: se vuelve a pedir
        pedidas = []
        p.set(iu, "imagen_edit", lambda *a, **k: pedidas.append(1) or [q.write_bytes(dng_xmp(True)) for q in d.glob("*.dng")] and False)
        iu.edit_only(base / "Boda Ñandú", "2 DNGs/Tanda 1", lambda m: None)
        assert pedidas == [1] and (d / ".rawflow_imagen_ok").exists() and not core.without_imagen(d.glob("*.dng"))


@prueba("Bridge NUNCA exporta un DNG sin Imagen: aunque la marca diga «hecho», si al DNG le faltan las ediciones se "
        "niega (sin crear otro proyecto); y un JPEG que sale de Bridge sin Imagen se borra y el paso se repite")
def p40():
    from rawflow import imagen_ui as iu
    ev = td.fresh("p40")
    d = ev / "2 DNGs"
    td.write(d / "A-DxO_DeepPRIME XD3.dng", dng_xmp(True))
    td.write(d / "B-DxO_DeepPRIME XD3.dng", dng_xmp(False))      # la de JUTIAPA: sin ediciones
    (d / ".rawflow_imagen_ok").write_text("ok")
    exports, ediciones = [], []
    with _Parches() as p:
        p.set(iu, "check_permissions", lambda: "")
        p.set(iu, "Screen", lambda *a, **k: object())
        p.set(iu, "dismiss_crash_dialogs", lambda: None)
        p.set(iu, "bridge_export", lambda s, dd, out, n: exports.append(n))
        p.set(iu, "imagen_edit", lambda *a, **k: ediciones.append(1))
        try:
            iu._run(ev, "2 DNGs", lambda m: None, lambda: False, "Bodas", ev / core.IMAGEN_SUBFOLDER, False, None)
            raise AssertionError("exportó sin Imagen")
        except iu.UIError as e:
            assert "sin Imagen" in str(e), e
        assert exports == [] and ediciones == [] and not (d / ".rawflow_imagen_ok").exists()
        (d / "B-DxO_DeepPRIME XD3.dng").write_bytes(dng_xmp(True))
        (d / ".rawflow_imagen_ok").write_text("ok")

        def export(s, dd, out, n):
            exports.append(n)
            out.mkdir(parents=True, exist_ok=True)
            (out / "A.jpg").write_bytes(jpeg_xmp("A.NEF", True))
            (out / "B.jpg").write_bytes(jpeg_xmp("B.NEF", False))   # Bridge la sacó sin los ajustes
        p.set(iu, "bridge_export", export)
        try:
            iu._run(ev, "2 DNGs", lambda m: None, lambda: False, "Bodas", ev / core.IMAGEN_SUBFOLDER, False, None)
            raise AssertionError("dejó un JPEG sin Imagen")
        except iu.UIError as e:
            assert "sin las ediciones de Imagen" in str(e), e
        assert exports == [2] and (ev / core.IMAGEN_SUBFOLDER / "A.jpg").exists() and not (ev / core.IMAGEN_SUBFOLDER / "B.jpg").exists()


@prueba("Retouch4me JAMÁS recibe una foto sin Imagen: un JPEG de «Imagen AI» sin sus ediciones hace volver al paso 3, "
        "esa foto se rehace con Imagen y solo entonces se retoca", solo_mac="Imagen por tandas mientras DxO trabaja")
def p41():
    from rawflow import retouch_engine as re_
    ev = td._evento_tandas(6)
    dngs = con_dng_y_jpg(ev, 6)
    (dngs.parent / core.IMAGEN_SUBFOLDER / "DSC1002.jpg").write_bytes(b"J" * 1000)         # exportada sin Imagen…
    (dngs / "DSC1002-DxO_DeepPRIME XD3.dng").write_bytes(b"DNG")     # …porque su DNG no tenía las ediciones
    vistos = []
    with td._Falsos() as f:
        ret_ok = re_.retouch

        def retouch(src, dest, log, **k):
            vistos.append(sorted(q.name for q in core.without_imagen(Path(src).glob("*.jpg"))))
            return ret_ok(src, dest, log, **k)
        f._set(re_, "retouch", retouch)
        p = td._correr(ev, 4)                                         # «Reintentar desde el paso 4»
    assert "failed_step" not in p.summary, p.summary
    assert vistos and all(v == [] for v in vistos), vistos
    assert [(e[0], e[1]) for e in f.ediciones] == [("2 DNGs/Tanda 1", 1)], f.ediciones   # solo esa, por Imagen
    fin = list((ev / core.FINAL_SUBFOLDER).glob("*.jpg"))
    assert finales(ev) == list(range(1, 7)) and not core.without_imagen(fin)


@prueba("Evento YA TERMINADO con finales sin Imagen (como JUTIAPA, la 1 y la 2): «Reintentar desde el paso 3» rehace "
        "SOLO esas con Imagen, conservan su número, las demás no se tocan y el video se vuelve a hacer", solo_mac="Imagen por tandas mientras DxO trabaja")
def p42():
    from rawflow import pipeline as pl
    ev = td._evento_tandas(8)
    t0 = time.time() - 7200
    for i, q in enumerate(sorted((ev / core.RAW_SUBFOLDER).glob("*.ARW"))):
        os.utime(q, (t0 + i * 60, t0 + i * 60))                        # orden de toma: DSC1000 primero
    dngs, fin = ev / core.OUTPUT_SUBFOLDER, ev / core.FINAL_SUBFOLDER
    for i in range(8):
        con = i >= 2                                                    # DSC1000 y DSC1001: sin Imagen
        td.write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", DNG_IMAGEN if con else b"DNG")
        td.write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", con))
        td.write(fin / f"{i + 1}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", con, extra=b"RETOCADA"))
    antes = {n: (fin / f"{n}.jpg").read_bytes() for n in range(3, 9)}
    viejo = t0 + 3600                                                   # todo se hizo hace una hora
    for q in list(dngs.rglob("*")) + list((ev / core.IMAGEN_SUBFOLDER).glob("*")) + list(fin.glob("*")):
        if q.is_file():
            os.utime(q, (viejo, viejo))
    for k in ("horizontal", "vertical"):
        vid = td.write(ev / core.PRES_SUBFOLDER / f"Presentación {k}.mp4", b"MP4")
        os.utime(vid, (viejo + 60, viejo + 60))
    videos = []
    with td._Falsos() as f:
        def present(self_):
            out, final = self_.ev_dir / pl.PRES_DIR, self_.ev_dir / pl.FINAL_DIR
            if pl.presentation_current(out, final):
                return "done"
            videos.append(len(list(final.glob("*.jpg"))))
            for k in ("horizontal", "vertical"):
                (out / f"Presentación {k}.mp4").write_bytes(b"MP4 nuevo")
        f._set(pl.Pipeline, "_present", present)
        p = td._correr(ev, 3)
    assert "failed_step" not in p.summary, p.summary
    assert [(e[0], e[1]) for e in f.ediciones] == [("2 DNGs/Tanda 1", 2)], f.ediciones
    assert [c[0] for c in f.ret_calls] == [2], f.ret_calls              # Retouch4me SOLO con esas 2
    assert finales(ev) == list(range(1, 9)), finales(ev)
    assert all((fin / f"{n}.jpg").read_bytes() == antes[n] for n in range(3, 9))   # las buenas, intactas
    assert not core.without_imagen(fin.glob("*.jpg"))
    assert videos == [8] and vid.read_bytes() == b"MP4 nuevo", videos


@prueba("Candado foto a foto: lee las ediciones de Imagen dentro del DNG (XMP del TIFF) y del JPEG (APP1), de qué "
        "foto es una final numerada, y el FAILURE/SUCCESS que Imagen anota en su registro (solo de esa carpeta)")
def p43():
    from rawflow import imagen_ui as iu
    d = td.fresh("p43")
    a, b = td.write(d / "a.dng", dng_xmp(True)), td.write(d / "b.dng", dng_xmp(False))
    c, e = td.write(d / "c.jpg", jpeg_xmp("EDN_4441.NEF", True)), td.write(d / "e.jpg", jpeg_xmp("EDN_4442.NEF", False))
    assert core.has_imagen_edits(a) and not core.has_imagen_edits(b)
    assert core.has_imagen_edits(c) and not core.has_imagen_edits(e)
    assert [q.name for q in core.without_imagen([a, b, c, e])] == ["b.dng", "e.jpg"]
    assert core.source_stem(c) == "edn_4441" and core.source_stem(e) == "edn_4442"
    lejos = td.write(d / "rara.dng", b"XX" + b"\0" * 3_000_000 + core.IMAGEN_MARK)   # estructura rara: todo el archivo
    assert core.has_imagen_edits(lejos)
    logs = d / "logs"
    logs.mkdir()
    f = logs / "renderer.2026-11-22.log"
    carpeta = d / "Boda Ñandú" / "2 DNGs" / "Tanda 1"
    f.write_text("antes\n", encoding="utf-8")
    with _Parches() as p:
        p.set(iu, "IMAGEN_LOGS", logs)
        mark = iu._imagen_log_mark()
        assert iu.download_result(mark, carpeta) is None
        with open(f, "a", encoding="utf-8") as h:
            h.write('x edit_download_directory_xmp_finished {"0":{"path":"/otra/2 DNGs/Tanda 1","status":"SUCCESS",'
                    '"errorCode":null,"total":9,"totalSuccess":9}}\n')
            h.write('x edit_download_directory_xmp_finished {"0":{"path":"' + unicodedata.normalize("NFD", str(carpeta))
                    + '","status":"FAILURE","errorCode":"DOWNLOAD_XMP_ERROR","total":120,"totalSuccess":0}}\n')
        assert iu.download_result(mark, carpeta) == ("FAILURE", 0, 120, "DOWNLOAD_XMP_ERROR"), iu.download_result(mark, carpeta)


@prueba("Revisión final: si UNA sola foto final no viene de Imagen, el evento NO se da por entregado (vuelve al paso 3); "
        "una final que Retouch4me dejó sin metadatos pero que sale de JPEG con Imagen sí vale")
def p44():
    from rawflow import pipeline as pl
    ev = td._evento_tandas(3)
    dngs, fin = ev / core.OUTPUT_SUBFOLDER, ev / core.FINAL_SUBFOLDER
    for i in range(3):
        td.write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", True))
        td.write(fin / f"{i + 1}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", True))
    p = pl.Pipeline(ev, [], DXO, "CINEMA", [], 0)
    p._imagen_app = lambda: True                                        # Imagen por su app (Mac o Windows)
    p._audit_imagen()                                                   # todas bien: nada
    (fin / "3.jpg").write_bytes(b"sin metadatos")                       # Retouch4me no dejó el XMP: vale
    p._audit_imagen()
    (fin / "2.jpg").write_bytes(jpeg_xmp("DSC1001.ARW", False))
    viejo = time.time() - 3600
    os.utime(fin / "2.jpg", (viejo, viejo))                             # hecha ANTES que su JPEG con Imagen
    try:
        p._audit_imagen()
        raise AssertionError("dio por entregada una final sin Imagen")
    except pl.LaneFailed as e:
        assert e.step == core.STEP_IMG and "2.jpg" in str(e), str(e)


# ======================================================================= pasos desactivados con doble clic
def _correr_off(ev: Path, desde: int, off):
    from rawflow.pipeline import Pipeline
    p = Pipeline(ev, [], DXO, "CINEMA", [], desde, off=off)
    estados = []
    p.step.connect(lambda i, st: estados.append((i, st)))
    p._run_steps()
    return p, estados


@prueba("Doble clic · Imagen AI desactivado: nada sube a Imagen, Bridge exporta los DNG tal cual a «JPEG sin Imagen» "
        "(nunca a «Imagen AI»), Retouch4me trabaja con esas y el evento acaba")
def p45():
    from rawflow import imagen_ui as iu
    ev = td._evento_tandas(40)
    exportadas = []
    with td._Falsos() as f:
        f._set(core, "tanda_size", lambda n: 10)                     # aunque fuera por tandas: no sube nada

        def bridge_only(ev_, sub, log, should_stop=lambda: False, out_dir=None, progress=lambda x: None):
            d = Path(ev_) / sub
            for q in d.glob("*.dng"):
                td.write(Path(out_dir) / (core.dng_base(q).upper() + ".jpg"), b"J" * 1000)   # sin Imagen
            exportadas.append((sub, Path(out_dir).name))
            return Path(out_dir)
        f._set(iu, "bridge_only", bridge_only)
        if sys.platform == "win32":                                    # Windows: Bridge por imagen_win
            from rawflow import imagen_win as iw
            f._set(iw, "bridge_only", bridge_only)
        p, estados = _correr_off(ev, 2, {3})
    assert "failed_step" not in p.summary, p.summary
    assert f.subidas == [] and f.ediciones == [] and f.bridges == [], (f.subidas, f.ediciones, f.bridges)
    assert exportadas == [("2 DNGs", core.JPEG_SIN_SUBFOLDER)], exportadas
    dngs = ev / core.OUTPUT_SUBFOLDER
    assert not (ev / core.IMAGEN_SUBFOLDER).exists() and len(list((ev / core.JPEG_SIN_SUBFOLDER).glob("*.jpg"))) == 40
    assert (3, "off") in estados and [c[0] for c in f.ret_calls] == [40], (estados, f.ret_calls)
    assert finales(ev) == list(range(1, 41))


@prueba("Doble clic · Retouch4me desactivado: las fotos finales son los JPEG de Imagen tal cual (numerados, con Imagen) "
        "y Retouch4me ni se abre")
def p46():
    ev = td._evento_tandas(5)
    dngs = con_dng_y_jpg(ev, 5)
    with td._Falsos() as f:
        p, estados = _correr_off(ev, 4, {4})
    assert "failed_step" not in p.summary, p.summary
    assert f.ret_calls == [] and (4, "off") in estados, (f.ret_calls, estados)
    fin = ev / core.FINAL_SUBFOLDER
    assert finales(ev) == list(range(1, 6)) and all(q.read_bytes() == JPG_IMAGEN for q in fin.glob("*.jpg"))
    assert not core.without_imagen(fin.glob("*.jpg")) and len(list((dngs.parent / core.IMAGEN_SUBFOLDER).glob("*.jpg"))) == 5


@prueba("Doble clic · CineSlide desactivado: sin video (y sin tocar CineSlide); el resto del evento, igual")
def p47():
    from rawflow import pipeline as pl
    present_de_verdad = pl.Pipeline._present
    ev = td._evento_tandas(4)
    con_dng_y_jpg(ev, 4)
    with td._Falsos() as f:
        f._set(pl.Pipeline, "_present", present_de_verdad)
        # (26-nov-2026: desactivaba el paso «5», que desde que llegó Luminar es Luminar; CineSlide es STEP_PRES)
        p, estados = _correr_off(ev, 4, {core.STEP_PRES})
    assert "failed_step" not in p.summary, p.summary
    assert (core.STEP_PRES, "off") in estados and not (ev / pl.PRES_DIR).exists(), estados
    assert finales(ev) == list(range(1, 5)) and [c[0] for c in f.ret_calls] == [4]


@prueba("Doble clic en la pantalla: la pastilla avisa (Desactivado, «–»), se recuerda para el próximo evento (por "
        "nombre); Mover y DxO no se pueden desactivar; la Selección IA sí")
def p48():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rawflow import app as ra
    pill = ra.StepPill(3, "Imagen AI", "Imagen AI")
    pill.setProperty("n", 3)
    pill.resize(160, 50)
    pulsados = []
    pill.double_clicked.connect(pulsados.append)
    QTest.mouseDClick(pill, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    assert pulsados == [2], pulsados
    pill.set_off(True)
    assert pill.sub.full() == "Desactivado" and pill.num.text() == "–"
    pill.set_off(False)
    assert pill.sub.full() == "Imagen AI" and pill.num.text() == "3"

    class Ajustes(dict):
        def setValue(self, k, v):
            self[k] = v

    class Pastilla:
        def __init__(self):
            self.off = False

        def set_off(self, v):
            self.off = v

    class Texto:
        t = ""

        def setText(self, t):
            self.t = t
    w = type("W", (), {})()
    w.pipe, w.steps_off, w.settings, w.status = None, set(), Ajustes(), Texto()
    w.pills = [Pastilla() for _ in range(6)]
    w._log = lambda m: None
    w._update_summary = lambda: None
    w.STEP_OFF_TXT = ra.MainWindow.STEP_OFF_TXT
    ra.MainWindow._toggle_step(w, 3)                                    # Imagen AI
    ra.MainWindow._toggle_step(w, 4)                                    # Retouch4me
    assert w.steps_off == {3, 4} and w.settings["pasos_apagados"] == "Imagen AI,Retouch4me" and w.pills[4].off
    ra.MainWindow._toggle_step(w, 3)                                    # otro doble clic: activado otra vez
    assert w.steps_off == {4} and w.settings["pasos_apagados"] == "Retouch4me" and not w.pills[3].off
    for i in (0, 2):                                                    # Mover y DxO: siempre se hacen
        ra.MainWindow._toggle_step(w, i)
        assert w.steps_off == {4} and "siempre se hace" in w.status.t, w.status.t
    ra.MainWindow._toggle_step(w, 1)                                    # Selección IA: se desactiva con doble clic
    assert w.steps_off == {1, 4} and w.settings["pasos_apagados"] == "Selección IA,Retouch4me"


# ======================================================================= arrastrar carpetas y fotos
@prueba("Arrastrar una carpeta de fotos (con subcarpetas, de cualquier sitio): se encuentran todos sus RAW; los de "
        "eventos ya hechos que haya dentro NUNCA se tocan (arrastrar el Escritorio no se lleva otros eventos)")
def p49():
    base = td.fresh("p49")
    td.write(base / "Boda" / "A.NEF", b"x" * 100)
    td.write(base / "Boda" / "Cámara 2" / "B.CR3", b"x" * 100)
    td.write(base / "Boda" / "nota.txt", b"no")
    td.write(base / "Boda" / "._A.NEF", b"basura de macOS")
    td.write(base / "Boda" / "20 nov 2026" / "1 Original" / "C.NEF", b"x" * 100)     # evento ya hecho
    td.write(base / "Boda" / "20 nov 2026" / "2 DNGs" / "C-DxO_DeepPRIME XD3.dng", b"x" * 100)
    d = core.folder_drive(base / "Boda")
    assert d.key == str(base / "Boda") and d.fstype == "carpeta" and not d.removable
    fotos = core.scan_paths([d.key], d.key)
    assert sorted(Path(f.path).name for f in fotos) == ["A.NEF", "B.CR3"], [f.path for f in fotos]
    assert all(f.drive == d.key for f in fotos)
    sueltas = core.scan_paths([str(base / "Boda" / "A.NEF"), str(base / "Boda" / "nota.txt")], "arrastradas-1")
    assert [Path(f.path).name for f in sueltas] == ["A.NEF"] and sueltas[0].drive == "arrastradas-1"


@prueba("Arrastrar desde OTRO disco: las fotos se COPIAN (las originales se quedan donde estaban); del mismo disco se "
        "mueven (instantáneo); y una carpeta arrastrada jamás se expulsa")
def p50():
    from rawflow.pipeline import Pipeline
    base = td.fresh("p50")
    a = [td.write(base / "disco externo" / f"E{i}.NEF", bytes([i]) * 500) for i in range(3)]
    b = [td.write(base / "mismo disco" / f"M{i}.NEF", bytes([i + 9]) * 500) for i in range(2)]
    files = core.scan_paths([str(base / "disco externo")], "ext") + core.scan_paths([str(base / "mismo disco")], "mis")
    ev = base / "Evento"
    pl = Pipeline(ev, files, DXO, "CINEMA", [], 0, copy_roots={"ext"})
    pl._run_steps = None
    estado = pl._move()
    raw = ev / core.RAW_SUBFOLDER
    assert sorted(q.name for q in raw.glob("*.NEF")) == ["E0.NEF", "E1.NEF", "E2.NEF", "M0.NEF", "M1.NEF"], estado
    assert all(q.exists() for q in a) and not any(q.exists() for q in b)      # copiadas / movidas
    assert pl.eject == [] and pl.summary.get("moved") == 5, pl.summary


@prueba("Arrastrar un evento de RawFlow (o su «1 Original»): se ofrece seguir desde el paso que le falta; si está "
        "terminado, «Revisar» lo comprueba entero desde el paso 3 (Imagen foto a foto, finales y video)")
def p51():
    from rawflow import pipeline as pl
    ev = td._evento_tandas(3)
    assert pl.event_root(ev) == ev and pl.event_root(ev / core.RAW_SUBFOLDER) == ev
    assert pl.event_root(ev.parent / "otra cosa") is None
    assert pl.resume_step(ev) == (1, "separar las fotos buenas y malas")
    assert pl.resume_step(ev, off={1}) == (2, "DNG")
    dngs = ev / core.OUTPUT_SUBFOLDER
    for i in range(3):
        td.write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", td._mini_dng())      # DNG completo de verdad
    assert pl.resume_step(ev) == (3, "JPEG")
    for i in range(3):
        td.write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", JPG_IMAGEN)
    assert pl.resume_step(ev) == (4, "fotos finales")
    for i in range(3):
        td.write(ev / core.FINAL_SUBFOLDER / f"{i + 1}.jpg", JPG_IMAGEN)
    assert pl.resume_step(ev) == (core.STEP_PRES, "la presentación")
    assert pl.resume_step(ev, off={core.STEP_PRES}) == (3, "")                    # CineSlide desactivado: ya está
    td.write(ev / pl.PRES_DIR / "Presentación.mp4", b"MP4")          # un solo video (de antes): faltan los dos
    assert pl.resume_step(ev) == (core.STEP_PRES, "la presentación")
    for k in ("horizontal", "vertical"):
        td.write(ev / pl.PRES_DIR / f"Presentación {k}.mp4", b"MP4")
    assert pl.resume_step(ev) == (3, "")                             # terminado: revisar desde Imagen
    assert pl.resume_step(td.fresh("p51_vacio")) is None


@prueba("Soltar en RawFlow (sin excepción): cada carpeta es una tarjeta, las fotos sueltas van juntas en otra, un evento "
        "se ofrece para seguir; y mientras trabaja no se añade nada")
def p52():
    from rawflow import app as ra
    base = td.fresh("p52")
    carpeta = base / "Fotos de Juan"
    td.write(carpeta / "A.NEF", b"x")
    f1, f2 = td.write(base / "X1.ARW", b"x"), td.write(base / "X2.ARW", b"x")
    ev = td._evento_tandas(2)
    tarjetas, ofrecidos = [], []

    class Texto:
        t = ""

        def setText(self, t):
            self.t = t
    w = type("W", (), {})()
    w.pipe, w.cards, w.dropped, w._drop_n, w.steps_off = None, {}, {}, 0, set()
    w.drive_count, w.status = Texto(), Texto()

    def add_drive(d):
        tarjetas.append(d)
        w.cards[d.key] = d
    w._add_drive = add_drive
    w._offer_event = ofrecidos.append
    w._log = lambda m: None
    w._update_summary = lambda: None
    ra.MainWindow._add_dropped(w, [carpeta, f1, f2, ev])
    assert [(d.fstype, d.label) for d in tarjetas] == [("carpeta", "Fotos de Juan"), ("sueltas", "2 fotos sueltas")]
    assert w.dropped[str(carpeta)] == [str(carpeta)] and sorted(w.dropped[tarjetas[1].key]) == sorted([str(f1), str(f2)])
    assert ofrecidos == [ev] and w.drive_count.t == "2 detectadas"
    ra.MainWindow._add_dropped(w, [carpeta])                              # la misma otra vez: no se repite
    assert len(tarjetas) == 2

    class Corriendo:
        def isRunning(self):
            return True
    w.pipe = Corriendo()
    ra.MainWindow._add_dropped(w, [base / "X1.ARW"])
    assert len(tarjetas) == 2 and "espera a que termine" in w.status.t


@prueba("Una foto final borrada a mano en un evento ya numerado: el reintento rehace SOLO esa (con su mismo número), "
        "no las 597")
def p53():
    ev = td._evento_tandas(6)
    t0 = time.time() - 7200
    for i, q in enumerate(sorted((ev / core.RAW_SUBFOLDER).glob("*.ARW"))):
        os.utime(q, (t0 + i * 60, t0 + i * 60))
    dngs, fin = ev / core.OUTPUT_SUBFOLDER, ev / core.FINAL_SUBFOLDER
    for i in range(6):
        td.write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", DNG_IMAGEN)
        td.write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", True))
        td.write(fin / f"{i + 1}.jpg", jpeg_xmp(f"DSC{1000 + i}.ARW", True, extra=b"RETOCADA"))
    (fin / "4.jpg").unlink()
    with td._Falsos() as f:
        p = td._correr(ev, 4)
    assert "failed_step" not in p.summary, p.summary
    assert [c[0] for c in f.ret_calls] == [1], f.ret_calls
    assert finales(ev) == list(range(1, 7)) and core.source_stem(fin / "3.jpg") == "dsc1002"


@prueba("Carpetas del evento en el orden de los pasos: 1 Original · 2 DNGs · 3 Imagen AI · 4 Fotos finales · "
        "5 Fotos finales Luminar · 6 Presentación de fotos; un evento de antes se reordena solo (solo cambia carpetas de sitio: ni copia ni borra)")
def p54():
    from rawflow import pipeline as pl
    ev = td._evento_tandas(3)
    assert [x.name for x in pl.folders(ev)] == ["1 Original", "2 DNGs", "3 Imagen AI", "4 Fotos finales"]
    assert pl.PRES_DIR == "6 Presentación de fotos" and pl.LUM_DIR == "5 Fotos finales Luminar" and pl.folders(ev, False)[2].name == "3 JPEG sin Imagen"
    dngs = ev / core.OUTPUT_SUBFOLDER                       # evento hecho con 4.14
    viejas = {}
    for i in range(3):
        td.write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", td._mini_dng())
        viejas[f"j{i}"] = td.write(dngs / "Imagen AI" / f"DSC{1000 + i}.jpg", JPG_IMAGEN)
        viejas[f"f{i}"] = td.write(ev / "3 Fotos finales" / f"{i + 1}.jpg", JPG_IMAGEN)
    viejas["v"] = td.write(ev / "4 Presentación de fotos" / "Presentación.mp4", b"MP4")
    inodos = {k: os.stat(q).st_ino for k, q in viejas.items()}
    # un solo video (de antes del 26-nov-2026): falta la presentación en los dos formatos… y ya está reordenado
    assert pl.resume_step(ev) == (core.STEP_PRES, "la presentación"), pl.resume_step(ev)
    nombres = sorted(x.name for x in ev.iterdir() if not x.name.startswith("."))
    assert nombres == ["1 Original", "2 DNGs", "3 Imagen AI", "4 Fotos finales", "6 Presentación de fotos"], nombres
    assert not (dngs / "Imagen AI").exists()
    nuevos = {f"j{i}": ev / "3 Imagen AI" / f"DSC{1000 + i}.jpg" for i in range(3)}
    nuevos.update({f"f{i}": ev / "4 Fotos finales" / f"{i + 1}.jpg" for i in range(3)})
    nuevos["v"] = ev / "6 Presentación de fotos" / "Presentación.mp4"
    assert all(os.stat(nuevos[k]).st_ino == inodos[k] for k in inodos)  # los mismos archivos: nada copiado ni borrado
    assert core.migrate_event_layout(ev) == []                          # otra vez: nada que hacer
    td.write(dngs / "Imagen AI" / "DSC9999.jpg", JPG_IMAGEN)            # restos en el sitio viejo: se juntan
    assert core.migrate_event_layout(ev) and (ev / "3 Imagen AI" / "DSC9999.jpg").exists()
    assert not (dngs / "Imagen AI").exists() and len(list((ev / "3 Imagen AI").glob("*.jpg"))) == 4
    ev2 = td._evento_tandas(2)                                          # evento hecho con 4.22: «5 Presentación»
    v = td.write(ev2 / "5 Presentación de fotos" / "Presentación.mp4", b"MP4")
    ino = os.stat(v).st_ino
    core.migrate_event_layout(ev2)
    assert os.stat(ev2 / "6 Presentación de fotos" / "Presentación.mp4").st_ino == ino and not (ev2 / "5 Presentación de fotos").exists()


@prueba("Comprobado: al acabar cada paso se revisa SU carpeta (fotos, DNG completos, con Imagen una a una, retocadas) "
        "y la revisión final; se ve en la pantalla de proceso y en las pastillas")
def p55():
    from rawflow import live
    from rawflow.pipeline import Pipeline
    ev = td._evento_tandas(6)
    con_dng_y_jpg(ev, 6)
    vistos = []
    with td._Falsos() as f:
        f._set(core, "processed_stems", lambda d: {core.dng_base(q) for q in Path(d).glob("*.dng")})
        live.clear_checks()
        p = Pipeline(ev, [], DXO, "CINEMA", [], 3)
        p.check.connect(lambda i, st, txt, corto: vistos.append((i, st, corto)))
        p._run_steps()
    assert "failed_step" not in p.summary, p.summary
    por = {i: (st, c) for i, st, c in vistos}
    assert por[0] == ("ok", "6 fotos") and por[1] == ("ok", "6 buenas · 0 malas") and por[2] == ("ok", "6 DNG"), por
    assert por[3] == ("ok", "6 con Imagen") and por[4] == ("ok", "6 retocadas"), por
    assert por[core.STEP_FINAL] == ("ok", "6 con Imagen"), por        # revisión final
    assert set(live.checks()) >= {0, 1, 2, 3, 4, core.STEP_FINAL}
    ev2 = td._evento_tandas(4)                                        # una foto SIN Imagen: se ve como aviso
    d2 = con_dng_y_jpg(ev2, 4)
    (d2.parent / core.IMAGEN_SUBFOLDER / "DSC1001.jpg").write_bytes(b"J" * 1000)
    q = Pipeline(ev2, [], DXO, "CINEMA", [], 0)
    q._imagen_app = lambda: True                                      # Imagen por su app (Mac o Windows)
    vistos.clear()
    q.check.connect(lambda i, st, txt, corto: vistos.append((i, st, corto)))
    q._verify_step(3, "done")
    assert vistos == [(3, "warn", "3 de 4 con Imagen")], vistos


@prueba("Salir de RawFlow mientras analiza una carpeta (o con el vigilante de memorias trabajando): todo se para y "
        "ningún hilo queda vivo → nunca «se cerró inesperadamente»")
def p56():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rawflow import app as ra
    base = td.fresh("p56")
    for i in range(400):                                          # carpeta con subcarpetas: el análisis tarda un poco
        td.write(base / "Fotos" / f"sub{i % 20}" / f"F{i}.NEF", b"x" * 10)
    w = ra.MainWindow()
    w.btn_auto.blockSignals(True)                                 # sin cuenta atrás y sin guardar ajustes
    w.btn_auto.setChecked(False)
    w.btn_auto.blockSignals(False)
    w._log = lambda m: None
    w._add_dropped([base / "Fotos"])
    app.processEvents()
    assert w.watcher.isRunning() and w.scanners, "debía estar trabajando"
    t0 = time.monotonic()
    w.shutdown()                                                  # lo mismo que al cerrar la ventana o ⌘Q
    assert not w.threads_alive(), "quedó un hilo vivo"
    assert time.monotonic() - t0 < 10, time.monotonic() - t0
    w.close()
    w.deleteLater()
    app.processEvents()


@prueba("Actualización automática: bajada la versión nueva, se instala SOLA cuando RawFlow está libre (aviso de 30 s); "
        "con una memoria puesta o un evento en marcha espera, y si pones una memoria durante el aviso se cancela")
def p66():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rawflow import app as ra, update
    from unittest import mock
    _Tarjeta = lambda: mock.MagicMock(selected=False)            # una memoria puesta (no seleccionada)
    inst = td.TMP / "RawFlow-9.9-Windows-Instalador.exe"
    inst.write_bytes(b"MZ")
    w = ra.MainWindow()
    w.btn_auto.blockSignals(True)
    w.btn_auto.setChecked(False)
    w.btn_auto.blockSignals(False)
    w._log = lambda m: None
    instalados, cerrar = [], []
    orig = update.install
    update.install = lambda p: instalados.append(Path(p).name)
    w.close = lambda: cerrar.append(1) or True
    try:
        w.update_info = {"version": "9.9", "size": 2}
        w.cards = {"memoria": _Tarjeta()}                           # una memoria puesta: no se toca nada
        w._auto_ready("9.9", str(inst), "")
        w._auto_tick()
        assert not getattr(w, "_upd_count", 0) and not instalados
        w.cards = {}                                              # libre: empieza el aviso
        w._auto_tick()
        assert w._upd_count == w.AUTO_WAIT and "sola en 30 s" in w.btn_update.text(), w.btn_update.text()
        for _ in range(5):
            w._auto_count()
        w.cards = {"memoria": _Tarjeta()}                           # pones una memoria durante el aviso: se cancela
        w._auto_count()
        assert w._upd_count == 0 and not instalados and w.btn_update.text() == "Actualizar a 9.9"
        w.cards = {}
        w._auto_tick()
        for _ in range(w.AUTO_WAIT):
            w._auto_count()
        assert instalados == [inst.name], instalados              # se instaló sola, una vez
        assert "Instalando" in w.status.text()
        instalados.clear()
        w._upd_count = 0
        w._update_now()                                           # el botón: ya está bajada → se instala sin bajarla otra vez
        assert instalados == [inst.name], instalados
    finally:
        update.install = orig
        for t in ("_upd_cd", "_upd_idle"):
            if getattr(w, t, None) is not None:
                getattr(w, t).stop()
        w.shutdown()
        w.deleteLater()
        app.processEvents()


@prueba("Memoria con DOS eventos (boda y XV): sin pulsar «2 eventos» es uno (el botón lo sugiere); pulsado, cada uno va "
        "a su carpeta («Nombre - Evento 1» y «- Evento 2»), uno detrás de otro, y la memoria se expulsa al terminar el último")
def p67():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rawflow import app as ra
    base = td.fresh("p67")
    card = base / "MEMORIA"
    t0 = time.time() - 5 * 86400
    files = []
    for i in range(30):                                             # sábado: boda
        files.append(td.raw(card, f"DSC{1000 + i}.ARW", i))
        os.utime(files[-1].path, (t0 + i * 60, t0 + i * 60))
    for i in range(25):                                             # domingo: XV
        files.append(td.raw(card, f"DSC{2000 + i}.ARW", 100 + i))
        t = t0 + 86400 + i * 60
        os.utime(files[-1].path, (t, t))
    files = core.scan_paths([str(card)], "MEMORIA")
    w = ra.MainWindow()
    w.btn_auto.blockSignals(True)
    w.btn_auto.setChecked(False)
    w.btn_auto.blockSignals(False)
    w._log = lambda m: None
    w.base_dir = str(base / "Escritorio")
    w.name_edit.setText("Fin de semana")
    corridas = []
    w._run = lambda ev, fs, eject, start, copy_roots=(): corridas.append((Path(ev).name, len(fs), list(eject)))
    w._missing_apps = lambda: []
    from unittest import mock
    w.cards = {"MEMORIA": mock.MagicMock(selected=True)}
    w.results = {"MEMORIA": files}
    w.dropped = {}
    orig = (core.list_drives, core.reveal_in_file_manager)
    core.list_drives = lambda *a, **k: []                          # nada de memorias ni Explorador de verdad
    core.reveal_in_file_manager = lambda *a, **k: None
    try:
        assert [len(g) for g in w._event_groups(files)] == [55], "sin pulsar «2 eventos»: un solo evento"
        assert w.btn_events.text() == "¿2 eventos?", w.btn_events.text()      # pero lo sugiere (1 día de hueco)
        w.btn_events.setChecked(True)                                         # lo pulsas
        assert [len(g) for g in w._event_groups(files)] == [30, 25]
        w._start()
        assert corridas == [("Fin de semana - Evento 1", 30, [])], corridas      # el primero, sin expulsar
        assert not w._next_event("Cancelado.", 0) and len(corridas) == 1       # si el primero falla, no sigue
        assert w._next_event("", 0)                                           # (lo que hace _after_finish al acabar)
        deadline = time.monotonic() + 5
        while len(corridas) < 2 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
        assert corridas[1] == ("Fin de semana - Evento 2", 25, ["MEMORIA"]), corridas   # el último expulsa
        assert not w._event_queue
    finally:
        core.list_drives, core.reveal_in_file_manager = orig
        w.cards = {}
        w.shutdown()
        w.deleteLater()
        app.processEvents()


@prueba("Paso 6 · Luminar: «CINEMA AJUSTE» a las fotos finales → «5 Fotos finales Luminar» y CineSlide hace la "
        "presentación CON ESAS; desactivado (doble clic), las de Retouch4me; si Luminar falla del todo, el evento SE PARA (ningún paso se salta) y no "
        "igual con aviso y se entregan las de Retouch4me")
def p68():
    from rawflow import luminar_win as L, pipeline as pl
    llamadas, usadas = [], []

    def run_ok(ev, src, out, log, should_stop=lambda: False, progress=lambda f: None):
        llamadas.append(Path(src).name)
        out.mkdir(parents=True, exist_ok=True)
        for q in sorted(Path(src).glob("*.jpg")):
            td.write(out / q.name, b"LUMINAR" + q.read_bytes())
        return len(list(out.glob("*.jpg")))

    def run_mal(*a, **k):
        raise RuntimeError("Luminar no abrió (prueba)")

    def present(self_):
        usadas.append(pl.deliver_dir(self_.ev_dir, self_.off).name)
        return "done"
    for caso, run, off in (("bien", run_ok, set()), ("apagado", run_ok, {core.STEP_LUM}), ("falla", run_mal, set())):
        ev = td._evento_tandas(4)
        con_dng_y_jpg(ev, 4)
        fin = ev / core.FINAL_SUBFOLDER
        for i in range(4):
            td.write(fin / f"{i + 1}.jpg", JPG_IMAGEN)
        llamadas.clear()
        usadas.clear()
        with td._Falsos() as f:
            f._set(L, "available", lambda: (True, ""))
            f._set(L, "run", run)
            f._set(pl.Pipeline, "_present", present)
            f._set(pl.Pipeline, "_reset_app", lambda self_, i: None)
            f._set(time, "sleep", lambda s: None)
            p = pl.Pipeline(ev, [], DXO, "CINEMA", [], core.STEP_LUM, off=off)
            p._run_steps()
        lum = ev / core.LUMINAR_SUBFOLDER
        if caso == "falla":                      # 26-nov-2026, regla: ningún paso se salta → se para en Luminar
            assert p.summary.get("failed_step") == core.STEP_LUM and not usadas, (p.summary, usadas)
            continue
        assert "failed_step" not in p.summary, (caso, p.summary)
        if caso == "bien":
            assert llamadas == ["4 Fotos finales"] and len(list(lum.glob("*.jpg"))) == 4, llamadas
            assert usadas == [core.LUMINAR_SUBFOLDER], usadas                       # la presentación, con Luminar
        elif caso == "apagado":
            assert not llamadas and not lum.exists() and usadas == [core.FINAL_SUBFOLDER], (llamadas, usadas)
        else:
            assert len(llamadas) == 0 and usadas == [core.FINAL_SUBFOLDER], usadas
            assert any("Luminar" in a for a in p.summary.get("avisos", [])), p.summary
            assert p.summary.get("sin_luminar")


# ======================================================================= Selección IA (tomas malas, antes de DxO)
def _cara(x=0.4, y=0.5, w=0.1, h=0.15, ojos=(0.3, 0.3), pitch=0.05, yaw=0.0, nit=300.0, lum=130.0):
    return {"x": x, "y": y, "w": w, "h": h, "ojoI": ojos[0], "ojoD": ojos[1], "pitch": pitch, "yaw": yaw,
            "nit": nit, "lum": lum}


def _foto(nombre, t, dist=None, caras=(), **k):
    r = {"foto": nombre, "t": t, "caras": list(caras), "personas": len(caras), "lum": 120, "negro": 2, "quemado": 1,
         "desv": 45, "nit": 400, "estetica": 0.6, "escena": [["people", 0.9]]}
    if dist is not None:
        r["dist_prev"] = dist
    r.update(k)
    return r


@prueba("Selección IA (reglas): un parpadeo se aparta SOLO si la toma de al lado, casi igual, tiene a esa persona con "
        "los ojos abiertos; nunca mirar abajo, otro momento, noches con flash ni detalles; sí tomas negras, al piso o fallidas")
def p57():
    from rawflow import seleccion as S
    a1 = _foto("A1.NEF", 0.0, caras=[_cara(ojos=(0.30, 0.32))])
    rows = [a1, _foto("A2.NEF", 0.4, 0.20, caras=[_cara(ojos=(0.06, 0.08))])]       # vecina casi igual, no repetida
    assert S.decide(rows) == {"A2.NEF": "ojos cerrados (en A1, la toma de al lado, está bien)"}, S.decide(rows)
    abajo = [a1, _foto("A2.NEF", 0.4, 0.20, caras=[_cara(ojos=(0.06, 0.08), pitch=0.45)])]    # mira abajo
    assert S.decide(abajo) == {}, S.decide(abajo)
    # 25-nov-2026 (el usuario: «dejó tomas con ojos cerrados»): cerrados DEL TODO se apartan aunque no haya otra toma
    solo = "ojos cerrados: no hay otra toma igual con los ojos abiertos"
    otro = [a1, _foto("A2.NEF", 0.4, 0.40, caras=[_cara(ojos=(0.06, 0.08))])]        # otro momento (no casi igual)
    assert S.decide(otro) == {"A2.NEF": solo}, S.decide(otro)
    lejos = [a1, _foto("A2.NEF", 9.0, 0.20, caras=[_cara(ojos=(0.06, 0.08))])]       # 9 s después: otra toma
    assert S.decide(lejos) == {"A2.NEF": solo}
    dos = [_foto("B1.NEF", 0, caras=[_cara(ojos=(0.06, 0.07))]), _foto("B2.NEF", 0.3, 0.20, caras=[_cara(ojos=(0.05, 0.08))])]
    d = S.decide(dos)                     # las dos con los ojos cerrados: 26-nov-2026, ningún momento sin foto → una se queda
    assert len(d) == 1 and set(d.values()) == {solo}, d
    medio = [a1, _foto("A2.NEF", 9.0, 0.20, caras=[_cara(ojos=(0.13, 0.15))])]       # a medio cerrar / sonriendo: no
    assert S.decide(medio) == {}
    beso = [a1, _foto("K1.NEF", 9.0, 0.40, caras=[_cara(x=0.40, ojos=(0.05, 0.06)), _cara(x=0.47, ojos=(0.06, 0.05))])]
    assert S.decide(beso) == {}, S.decide(beso)                                       # dos caras pegadas: a propósito
    noche = [_foto("N1.NEF", 0, lum=30, negro=60, caras=[_cara(lum=140)]),            # fondo negro, cara bien
             _foto("N2.NEF", 0.5, 0.20, lum=31, negro=58, caras=[_cara(lum=135)])]
    assert S.decide(noche) == {}
    detalle = [_foto("D1.NEF", 0, caras=[], personas=0, nit=40, desv=15, estetica=0.2,
                     escena=[["textile", 0.7], ["wedding_dress", 0.6]])]
    assert S.decide(detalle) == {}                                                   # vestido colgado: se queda
    negra = [_foto("X1.NEF", 0, caras=[], personas=0, lum=3, negro=98, escena=[])]
    assert list(S.decide(negra)) == ["X1.NEF"] and "negra" in S.decide(negra)["X1.NEF"]
    piso = [_foto("P1.NEF", 0, caras=[], personas=0, desv=9, estetica=0.05, escena=[["floor", 0.8], ["tile", 0.6]])]
    assert list(S.decide(piso)) == ["P1.NEF"] and "piso" in S.decide(piso)["P1.NEF"]
    cielo = [_foto("C1.NEF", 0, caras=[], personas=0, desv=6, estetica=0.1, escena=[["sky", 0.8]])]
    assert "cielo" in S.decide(cielo).get("C1.NEF", ""), S.decide(cielo)
    anillos = [_foto("P2.NEF", 0, caras=[], personas=0, desv=9, estetica=0.05, escena=[["floor", 0.6], ["jewelry", 0.7]])]
    assert S.decide(anillos) == {}                                                   # anillos en el piso: se queda
    fallida = [_foto("F1.NEF", 0, caras=[], personas=4, estetica=-0.5)]              # baile movido, sin ninguna cara
    assert "fallida" in S.decide(fallida).get("F1.NEF", ""), S.decide(fallida)
    assert S.decide([_foto("F2.NEF", 0, caras=[_cara()], estetica=-0.5)]) == {}      # con una cara: la nota sola no basta
    quemada = [_foto("Q0.NEF", 0, caras=[_cara()]), _foto("Q1.NEF", 2, 0.5, caras=[_cara(lum=250)])]
    assert "quemadas" in S.decide(quemada).get("Q1.NEF", "")
    assert S.decide(quemada[1:]) == {}                     # sola en su momento: no se queda el momento sin foto
    muchas = [_foto(f"M{i}.NEF", i * 0.4, 0.2 if i else None, caras=[_cara(ojos=(0.05, 0.05) if i % 2 else (0.3, 0.3))])
              for i in range(10)] + negra
    d = S.decide(muchas)                                                              # 5 de 11: no cuadra → solo lo seguro
    assert d == {"X1.NEF": S.decide(negra)["X1.NEF"]}, d
    raras = [_foto(f"R{i}.NEF", i * 30.0, caras=[], personas=2, estetica=-0.6) for i in range(10)]
    assert S.decide(raras) == {}, "si la nota baja salta en muchas, esa cámara es rara: no se aparta ninguna"
    assert S.summary({"a": "repetida: x", "b": "repetida, con ojos cerrados: x", "c": "toma negra"}) == "2 repetidas · 1 negra"


@prueba("Selección IA (paso 2): en «1 Original», la toma mala va a «Fotos malas» (con su motivo, nunca se borra) y "
        "las buenas a «Fotos buenas»; DxO, Imagen y Retouch4me SOLO trabajan con las buenas; si la IA falla, pasan todas")
def p58():
    from rawflow import seleccion as S
    from rawflow.pipeline import Pipeline
    for falla in (False, True):
        base = td.fresh(f"p58_{falla}")
        for i in range(5):
            td.write(base / "memoria" / f"DSC{1000 + i}.ARW", bytes([i + 1]) * 64)
        files = core.scan_paths([str(base / "memoria")], "memoria")
        ev = base / "Evento"

        def analizar(paths, rfui, log=None, stop=None, progress=None, falla=falla):
            if falla:
                raise RuntimeError("la IA del Mac no respondió")
            return [_foto(Path(q).name, i * 20.0, caras=[], personas=0, lum=3, negro=97, escena=[])
                    if Path(q).name == "DSC1002.ARW" else _foto(Path(q).name, i * 20.0, caras=[_cara()])
                    for i, q in enumerate(paths)]
        with td._Falsos() as f:
            f._set(S, "analyze", analizar)
            p = Pipeline(ev, files, DXO, "CINEMA", [], 0)
            p._run_steps()
        assert "failed_step" not in p.summary, p.summary
        raw = ev / core.RAW_SUBFOLDER
        mala = raw / core.BAD_SUBFOLDER / "DSC1002.ARW"
        buenas = sorted(q.name for q in (raw / core.GOOD_SUBFOLDER).glob("*.ARW"))
        dngs = {core.dng_base(q) for q in (ev / core.OUTPUT_SUBFOLDER).glob("*.dng")}
        assert not list(raw.glob("*.ARW")), "en «1 Original» no quedan fotos sueltas: buenas o malas"
        if falla:
            assert not mala.exists() and len(buenas) == 5 and len(dngs) == 5 and finales(ev) == [1, 2, 3, 4, 5]
        else:
            assert mala.exists() and "negra" in (mala.parent / S.REASONS_FILE).read_text(encoding="utf-8")
            assert buenas == ["DSC1000.ARW", "DSC1001.ARW", "DSC1003.ARW", "DSC1004.ARW"], buenas
            assert "dsc1002" not in dngs and len(dngs) == 4, dngs                      # DxO ni la tocó
            assert finales(ev) == [1, 2, 3, 4] and p.summary.get("descartadas") == 1, p.summary
            assert p.summary["checks"][core.STEP_SEL][1].startswith("4 buenas · 1 malas"), p.summary["checks"]


@prueba("Selección IA: una foto apartada que tú devuelves a «1 Original» nunca se vuelve a apartar")
def p59():
    from rawflow import seleccion as S
    raw = td.fresh("p59") / core.RAW_SUBFOLDER
    td.write(raw / "DSC1.ARW", b"x" * 10)
    td.write(raw / "DSC2.ARW", b"x" * 10)
    assert S.apply(raw, {"DSC2.ARW": "toma negra"}) == 1 and not (raw / "DSC2.ARW").exists()
    (raw / S.DIR_NAME / "DSC2.ARW").rename(raw / "DSC2.ARW")                  # la recuperas tú
    assert S.kept_restored(raw) == {"DSC2.ARW"}
    vistas = []

    def analizar(paths, *a, **k):
        vistas.extend(Path(q).name for q in paths)
        return [_foto(Path(q).name, 0, caras=[], personas=0, lum=3, negro=97, escena=[]) for q in paths]
    orig = S.analyze
    S.analyze = analizar
    try:
        d = S.run(raw, "/bin/echo", set())
    finally:
        S.analyze = orig
    assert "DSC2.ARW" not in vistas and "DSC2.ARW" not in d and (raw / "DSC2.ARW").exists(), (vistas, d)


@prueba("Pasos guardados por NOMBRE: lo desactivado antes (Imagen, Retouch4me, CineSlide y el interruptor de la IA) "
        "pasa solo al nuevo orden de 6 pasos, y un evento a medias sigue desde el mismo paso")
def p60():
    from rawflow import app as ra

    class Ajustes(dict):
        def value(self, k, default=None, type=None):
            v = self.get(k, default)
            if type is bool and isinstance(v, str):
                return v.lower() in ("1", "true")
            return v

        def setValue(self, k, v):
            self[k] = v
    w = type("W", (), {})()
    w.settings = Ajustes({"pasos_desactivados": "2,3,4", "seleccion_ia": False, "pendiente_paso": 3})
    assert ra.MainWindow._load_steps_off(w) == {1, 3, 4, core.STEP_PRES}
    assert w.settings["pasos_apagados"].split(",") == ["Imagen AI", "Retouch4me", "CineSlide", "Selección IA"]
    assert w.settings["pendiente_paso"] == 4 and w.settings["pasos_version"] == 2     # Retouch4me sigue siendo Retouch4me
    assert ra.MainWindow._load_steps_off(w) == {1, 3, 4, core.STEP_PRES} and w.settings["pendiente_paso"] == 4   # solo una vez
    nuevo = type("W", (), {})()
    nuevo.settings = Ajustes()
    assert ra.MainWindow._load_steps_off(nuevo) == set() and nuevo.settings["pasos_apagados"] == ""


@prueba("Al ABRIR RawFlow todo empieza cerrado: UTM (con Windows apagado bien antes) y DxO, Imagen, Bridge, Retouch4me "
        "o CineSlide que quedaran abiertos; UTM solo sigue si RawFlow está instalando una máquina virtual", solo_mac="Windows en UTM y apps del Mac")
def p61():
    from rawflow import pipeline as pl, vm_programas as vp
    abiertas = {"UTM", "ImagenAI", "Safari"}
    orden, cerradas = [], []
    with _Parches() as p:
        p.set(pl, "kill_orphan_helpers", lambda: 0)
        p.set(pl, "_bridge_names", lambda: ["Adobe Bridge 2027"])
        r = pl.close_on_launch(running=lambda n: n in abiertas, quit_=lambda ns: (orden.append("cerrar"), cerradas.extend(ns)),
                               stop=lambda log: orden.append("apagar Windows"))
        assert sorted(r) == ["ImagenAI", "UTM"] and orden == ["apagar Windows", "cerrar"], (r, orden)
        assert "Safari" not in cerradas                                  # tus apps: al pulsar Empezar, no al abrir
        p.set(vp, "BUSY", True)                                          # instalando una máquina: UTM se queda
        orden.clear(); cerradas.clear()
        r = pl.close_on_launch(running=lambda n: n in abiertas, quit_=lambda ns: cerradas.extend(ns),
                               stop=lambda log: orden.append("apagar Windows"))
        assert r == ["ImagenAI"] and orden == [] and "UTM" not in cerradas, (r, orden)
        p.set(vp, "BUSY", False)
        assert pl.close_on_launch(running=lambda n: False, quit_=lambda ns: cerradas.append("x"), stop=lambda l: None) == []


def _grupo(dx=0.0, cerrado=None, calidad=0.5):
    """4 caras pequeñas y apretadas, como en una foto de grupo (separadas un 5 % del encuadre)."""
    cs = []
    for k in range(4):
        c = _cara(x=0.10 + 0.05 * k + dx, y=0.5, w=0.03, h=0.045)
        c["calidad"] = calidad
        if k == cerrado:
            c.update(ojoI=0.05, ojoD=0.06)
        cs.append(c)
    return cs


@prueba("Selección IA, REPETIDAS: de la misma pose disparada otra vez se queda UNA, la mejor (nadie con los ojos "
        "cerrados, mejor cara); no junta otro momento, un horquillado HDR, tomas lejos en el tiempo ni una ráfaga que cambia")
def p62():
    from rawflow import seleccion as S

    def toma(n, t, dists=(), **k):
        r = _foto(n, t, dists[0] if dists else None, **k)
        if dists:
            r["dists"] = list(dists)
        return r
    g = [toma("G1.NEF", 0.0, caras=_grupo()),
         toma("G2.NEF", 1.0, [0.12], caras=_grupo(cerrado=2, calidad=0.6)),        # la 3.ª persona parpadea
         toma("G3.NEF", 2.2, [0.13, 0.14], caras=_grupo(dx=0.02, calidad=0.55))]   # la cámara se movió un poco
    d = S.decide(g)                      # 26-nov-2026: de 3 o más se quedan las 2 mejores (una de respaldo)
    assert d == {"G2.NEF": "repetida, con ojos cerrados: se quedan G3 y G1 (las 2 mejores de las 3: una de respaldo)"}, d
    otro = [toma("H1.NEF", 0, caras=_grupo()), toma("H2.NEF", 0.5, [0.30], caras=_grupo())]
    assert S.decide(otro) == {}, "otro gesto o encuadre (parecido 0,30): no es repetida"
    hdr = [toma("K1.NEF", 0, ev=8.0, caras=[], personas=0, escena=[["building", 0.9]]),
           toma("K2.NEF", 0.3, [0.08], ev=9.0, caras=[], personas=0, escena=[["building", 0.9]]),
           toma("K3.NEF", 0.6, [0.08, 0.09], ev=7.0, caras=[], personas=0, escena=[["building", 0.9]])]
    assert S.decide(hdr) == {}, "horquillado (misma toma, distinta exposición): se quedan todas"
    cambia = [toma("C1.NEF", 0, caras=_grupo()), toma("C2.NEF", 0.5, [0.12], caras=_grupo()),
              toma("C3.NEF", 1.0, [0.12, 0.30], caras=_grupo())]                     # C3 ya no se parece a C1
    d = S.decide(cambia)
    assert len(d) == 1 and set(d) <= {"C1.NEF", "C2.NEF"}, d
    dos_camaras = [toma("E1.NEF", 0, caras=_grupo()), toma("O1.NEF", 0.2, [0.6], caras=_grupo()),
                   toma("E2.NEF", 0.5, [0.6, 0.1], caras=_grupo())]                  # otra cámara en medio
    d = S.decide(dos_camaras)
    assert len(d) == 1 and set(d) <= {"E1.NEF", "E2.NEF"}, d
    lejos = [toma("L1.NEF", 0, caras=_grupo()), toma("L2.NEF", 8.0, [0.1], caras=_grupo())]
    assert S.decide(lejos) == {}, "8 s después ya es otra foto"
    sin_hora = [dict(toma("Z1.NEF", 0, caras=_grupo())), dict(toma("Z2.NEF", 0, [0.12], caras=_grupo()))]
    for r in sin_hora:
        r.pop("t")
    assert S.decide(sin_hora) == {}, "sin la hora de la cámara, solo las clavadas"
    sin_hora[1]["dists"] = [0.08]
    sin_hora[1]["dist_prev"] = 0.08
    assert len(S.decide(sin_hora)) == 1
    rafaga = [toma(f"R{i}.NEF", i * 0.5, [0.13] * min(i, 4), caras=_grupo()) for i in range(30)]
    assert S.decide(rafaga) == {}, "más de la mitad repetida con 0,13: algo no cuadra, solo las clavadas (≤0,10)"


@prueba("Selección IA: en una foto de grupo cada persona se compara CONSIGO MISMA aunque la cámara se mueva un poco "
        "entre tomas (no con su vecina), y con dos cámaras se analizan las tomas de cada una seguidas")
def p63():
    from rawflow import seleccion as S
    ref = _grupo()
    otra = _grupo(dx=0.02)
    pares = S.pair_faces(ref, otra)
    assert [round(f["x"], 3) for f in pares] == [round(c["x"], 3) for c in otra], pares
    falta = otra[:1] + otra[2:]                                                     # la 2.ª persona no sale
    pares = S.pair_faces(ref, falta)
    assert pares[1] is None and [round(f["x"], 3) for f in pares if f] == [0.12, 0.22, 0.27], pares
    assert S._camera("EDN_4441") == "EDN_" and S._camera("_DSC1234") == "_DSC" and S._camera("20231010_1") == ""
    raw = td.fresh("p63") / core.RAW_SUBFOLDER
    for k, n in enumerate(("EDN_0001.NEF", "OSZ_0001.NEF", "EDN_0002.NEF", "OSZ_0002.NEF")):
        td.write(raw / n, b"x" * 10)
        os.utime(raw / n, (1000 + k, 1000 + k))                                     # intercaladas al copiarlas
    vistas = []

    def analizar(paths, *a, **k):
        vistas.extend(Path(q).name for q in paths)
        return [_foto(Path(q).name, 0, caras=[_cara()]) for q in paths]
    orig = S.analyze
    S.analyze = analizar
    try:
        S.run(raw, "/bin/echo", set())
    finally:
        S.analyze = orig
    assert vistas == ["EDN_0001.NEF", "EDN_0002.NEF", "OSZ_0001.NEF", "OSZ_0002.NEF"], vistas


@prueba("Selección IA, NO tirar tomas buenas: una repetida se aparta SOLO si la que se queda es igual o mejor para "
        "TODAS las personas (ojos más abiertos, cara tapada, mejor cara); una cara sola que se mueve no se confunde")
def p64():
    from rawflow import seleccion as S

    def cara(x, ojos=(0.3, 0.3), q=0.5, **k):
        c = _cara(x=x, y=0.5, w=0.08, h=0.12, ojos=ojos, **k)
        c.update(calidad=q, px=400)
        return c

    def toma(n, t, d=None, caras=(), **k):
        r = _foto(n, t, d, caras=list(caras), **k)
        if d is not None:
            r["dists"] = [d]
        return r
    # A: la novia (izq.) con mejor cara, pero la mamá (der.) a medio cerrar los ojos; B: la mamá con los ojos abiertos
    a = toma("A.NEF", 0, caras=[cara(0.30, q=0.85), cara(0.55, ojos=(0.15, 0.14))])
    b = toma("B.NEF", 0.4, 0.10, caras=[cara(0.30, q=0.50), cara(0.55, ojos=(0.30, 0.31))])
    assert S.decide([a, b]) == {}, "cada una tiene algo mejor: se quedan las dos"
    a2 = toma("A.NEF", 0, caras=[cara(0.30, q=0.55), cara(0.55, ojos=(0.15, 0.14))])      # la novia, igual en las dos
    d = S.decide([a2, b])
    assert list(d) == ["A.NEF"] and "se queda B" in d["A.NEF"], d          # se queda la de los ojos abiertos
    # la mamá tapada (un brazo, un teléfono) en una toma: la otra, donde se le ve la cara, no se tira
    tapada = toma("T.NEF", 0, caras=[cara(0.30, q=0.9)], ocultas=[[0.51, 0.44, 0.08, 0.12]])
    se_ve = toma("V.NEF", 0.4, 0.10, caras=[cara(0.30, q=0.4), cara(0.55, q=0.5)])
    assert S.decide([tapada, se_ve]) == {}, S.decide([tapada, se_ve])
    # una sola cara en cada toma: que el novio esté donde estaba la novia NO es «la cámara se movió»
    novio, novia = [cara(0.40)], [cara(0.47)]
    assert S.camera_shift(novio, novia) == (0.0, 0.0) and S.pair_faces(novio, novia) == [None]
    # ...pero si se mueven todas (la cámara), sí se emparejan
    grupo = [cara(0.20), cara(0.40), cara(0.60)]
    movidas = [cara(0.23), cara(0.43), cara(0.63)]
    assert [round(f["x"], 2) for f in S.pair_faces(grupo, movidas)] == [0.23, 0.43, 0.63]


@prueba("Selección IA: las caras que Vision no vio en una toma se buscan otra vez en su sitio («rfui caras»): la "
        "encontrada se compara (sin repetir la que ya estaba) y la que no aparece queda como tapada", solo_mac="ayudante rfui (Vision)")
def p65():
    from rawflow import seleccion as S
    import json as _json
    base = td.fresh("p65")
    fake = base / "rfui"
    fake.write_text("#!/usr/bin/env python3\n"
                    "import sys, json\n"
                    "for line in sys.stdin:\n"
                    "    r = json.loads(line); n = r['foto'].split('/')[-1]\n"
                    "    caras = []; ocultas = []\n"
                    "    for c in r['cajas']:\n"
                    "        if c[0] < 0.5: caras.append({'x': c[0], 'y': c[1], 'w': c[2], 'h': c[3], 'ojoI': 0.3, 'ojoD': 0.3,\n"
                    "                                     'calidad': 0.6, 'px': 300, 'rescatada': True})\n"
                    "        else: ocultas.append(c)\n"
                    "    caras.append({'x': 0.70, 'y': 0.5, 'w': 0.05, 'h': 0.07, 'rescatada': True})  # ya estaba: no se repite\n"
                    "    print(json.dumps({'foto': n, 'caras': caras, 'ocultas': ocultas}))\n", encoding="utf-8")
    fake.chmod(0o755)
    grupo = [_cara(x=0.2, y=0.5, w=0.05, h=0.07), _cara(x=0.6, y=0.5, w=0.05, h=0.07), _cara(x=0.7, y=0.5, w=0.05, h=0.07)]
    x = _foto("X.NEF", 0, caras=grupo)
    k = _foto("K.NEF", 0.5, 0.1, caras=[grupo[2]], dists=[0.1])                 # en K, Vision solo vio una
    rows = [x, k]
    req = S.rescue_requests(rows)
    assert list(req) == [1] and len(req[1]) == 2, req                            # buscar las otras dos en K
    n = S.rescue(rows, req, lambda name: base / name, str(fake))
    assert n == 1 and len(k["caras"]) == 2 and len(k["ocultas"]) == 1, (n, k["caras"], k.get("ocultas"))
    assert _json.dumps(rows)                                                    # todo sigue siendo JSON


@prueba("Imagen (Windows) abre en «Inicio» y el OCR no lee «Proyectos» en la barra lateral (24-nov-2026, evento de "
        "1.119 fotos parado): se encuentra igual por su sitio, entre «Perfiles de IA» y «Social Spotlight»")
def p69():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    s = SimpleNamespace(rect=(758, 156, 1923, 1128))
    s.inside = lambda b, r: iw.Screen.inside(s, b, r)
    caja = lambda t, x, y, w=60, h=18: {"t": t, "x": x, "y": y, "w": w, "h": h}
    leido = [caja("Inicio", 795, 304), caja("Perfiles de", 772, 417, 80), caja("IA", 806, 436, 20),
             caja("Social", 790, 660), caja("Spotlight", 778, 679, 70), caja("Tienda de", 772, 781, 70),
             caja("Proyectos activos (13)", 880, 700, 230), caja("Crear proyecto", 970, 945, 120)]
    b = iw._sidebar(s, leido)
    assert b is not None and 520 < b["y"] + b["h"] / 2 < 590 and 760 < b["x"] < 820, b   # el botón «Proyectos»
    assert iw._sidebar(s, [caja("Proyectos", 790, 540)])["y"] == 540                     # leído: se usa tal cual
    assert iw._sidebar(s, [caja("Proyectos activos (13)", 880, 700, 230)]) is None       # el título de la lista no
    assert iw._sidebar(s, [caja("Perfiles de", 772, 417, 80)]) is None                   # sin el de abajo, no adivina
    # 25-nov-2026: tampoco leyó «Social», solo «Spotlight» (lo leído de verdad en la captura del fallo, ventana 1600×940)
    s.rect = (920, 250, 1600, 940)
    leido = [caja("Inicio", 967, 374), caja("Perfiles de", 951, 468, 64), caja("Spotlight", 956, 685, 54),
             caja("Tienda de", 952, 771, 60), caja("perfiles", 960, 786, 44),
             caja("Proyectos activos (14)", 1071, 703, 195), caja("Crear proyecto", 1148, 907, 110)]
    b = iw._sidebar(s, leido)
    assert b is not None and 250 + 290 < b["y"] + b["h"] / 2 < 250 + 350 and 940 < b["x"] < 1000, b   # su botón: 277-357


@prueba("Imagen (Windows), nombre del proyecto: Imagen recoloca el diálogo y PISA lo escrito con su propuesta «2 DNGs "
        "0517» (25-nov-2026, paso 4 parado cuatro veces): RawFlow vuelve a buscar la casilla, lo escribe otra vez y "
        "solo lo da por bueno si aguanta; y el nombre repetido es «… n2» (el OCR de Windows no lee «#2»)", solo_win="Imagen de Windows")
def p70():
    import tempfile
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w=120, h=15: {"t": t, "x": x, "y": y, "w": w, "h": h}
    nombre = "25 nov 2026 n2"
    hecho, lecturas = [], [0]
    s = SimpleNamespace(rect=(920, 250, 1600, 940), should_stop=lambda: False,
                        keys=lambda c: hecho.append(("teclas", c)), click=lambda x, y: hecho.append(("clic suelto", x, y)),
                        click_box=lambda b, after=0: hecho.append(("clic en", b["t"], b["y"])),
                        type=lambda t: (hecho.append(("escribe", t)), lecturas.__setitem__(0, 0))[0])
    s.find = lambda text, exact=False, boxes=None, region=None: iw.Screen.find(s, text, exact, boxes, region)

    def ocr(scale=1):                                   # lo visto en vivo: (rótulo 660, casilla 695) → (611, 646)
        veces = sum(1 for a in hecho if a[0] == "escribe")
        lecturas[0] += 1
        if veces == 0:
            return [caja("Total de fotos: 585", 1146, 598), caja("Nombra tu proyecto", 1146, 660, 144),
                    caja("Por ejennplc: acdas 2024, Tonn", 1162, 695, 230), caja("Elige un tipo de fotografía", 1147, 786, 215)]
        if veces == 1 and lecturas[0] == 1:             # recién escrito: se lee… y luego Imagen lo pisa y sube todo
            return [caja("Nombra tu proyecto", 1146, 660, 144), caja(nombre, 1162, 695)]
        dentro = "DNGs 0517" if veces == 1 else nombre
        return [caja("Total de fotos: 585", 1146, 549), caja("Nombra tu proyecto", 1146, 611, 144),
                caja(dentro, 1175, 646), caja("Social", 965, 670, 40), caja("Elige un tipo de fotografía", 1147, 737, 215)]
    s.ocr = ocr
    espera = iw.NAME_STAY
    try:
        iw.NAME_STAY = 0.0
        iw._type_name(s, caja("Nombra tu proyecto", 1146, 660, 144), Path("C:/eventos/25 nov 2026/2 DNGs"), nombre)
    finally:
        iw.NAME_STAY = espera
    assert hecho == [("clic en", "Por ejennplc: acdas 2024, Tonn", 695), ("teclas", "ctrl+a"), ("escribe", nombre),
                     ("clic en", "DNGs 0517", 646), ("teclas", "ctrl+a"), ("escribe", nombre)], hecho
    hist = iw.HIST
    try:
        iw.HIST = Path(tempfile.gettempdir()) / "rawflow-prueba-no-existe.txt"
        assert iw.free_name("25 nov 2026", ["Proyectos", "25 nov 2026"]) == "25 nov 2026 n2"
        assert iw.free_name("25 nov 2026", ["25 nov 2026", "25 nov 2026 #2", "25 nov 2026 n3"]) == "25 nov 2026 n2"
        assert iw.free_name("25 nov 2026", ["25 nov 2026", "25 nov 2026 n2"]) == "25 nov 2026 n3"
        assert iw.free_name("25 nov 2026 2", ["25 nov 2026", "25 nov 2026 n2"]) == "25 nov 2026 2"   # otro evento
    finally:
        iw.HIST = hist

@prueba("Imagen (Windows) sin esperas muertas (25-nov-2026, 20 min parado): minimizada sigue contando como ventana "
        "(vuelve maximizada); si «Descargar ediciones» sigue en pantalla a los 20 s sin ningún DNG editado, se pulsa "
        "otra vez; y «Revisar Ediciones» se pulsa por su sitio si el OCR no lee el botón rojo de un proyecto listo", solo_win="Imagen de Windows")
def p71():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w=120, h=14: {"t": t, "x": x, "y": y, "w": w, "h": h}
    guardado = (iw.ws.windows, iw.ws.foreground, iw.ws.activate, iw.download_result, iw.core.without_imagen,
                iw.RETAP_AFTER, iw.PROMPT_WAIT, iw._find_row, iw._close_uploads)
    try:
        # 1) minimizada (199×34): es la ventana principal y front() la trae maximizada a la pantalla principal
        mini = {"hwnd": 7, "title": "Imagen AI", "rect": (-32000, -32000, 199, 34), "min": True, "exe": "ImagenAI.exe"}
        iw.ws.windows = lambda title_part="", exe="": [mini, dict(mini, hwnd=8, title="")]
        iw.ws.foreground = lambda: {"hwnd": 1, "title": "Claude", "exe": "claude.exe"}
        traidas = []
        iw.ws.activate = lambda hwnd, maximize=True, primary=False: traidas.append((hwnd, maximize, primary)) or True
        s = iw.Screen(lambda m: None)
        assert s.main_window()["hwnd"] == 7 and s.window() is None
        s.front(force=True)
        assert traidas == [(7, True, True)], traidas

        # 2) el clic en «Descargar ediciones» no entró: a los RETAP_AFTER s se pulsa otra vez (el botón, no el título)
        hecho, t0 = [], time.time()
        iw.RETAP_AFTER, iw.PROMPT_WAIT = 0.0, 0.0
        iw.core.without_imagen = lambda dngs: list(dngs)                      # ningún DNG lleva ediciones aún
        iw.download_result = lambda mark, d: ("SUCCESS", 2, 2, "") if hecho else None
        f = SimpleNamespace(rect=(0, 0, 3440, 1440), should_stop=lambda: False, log=lambda m: None,
                            click_box=lambda b, after=0: hecho.append((b["t"], b["y"])))
        # (lo leído de verdad: el botón rojo sale A TROZOS y lo único entero es el TÍTULO del diálogo, que se llama
        # igual; RawFlow pulsaba el título y la descarga nunca arrancaba)
        dialogo = [caja("Descargar ediciones", 1354, 578, 200, 18), caja("¿Tus fotos siguen aquí?", 1353, 630, 170),
                   caja("nov 2026\\2 DNGS", 1557, 680, 129), caja("Explorar", 2016, 681, 61, 12),
                   caja("9 Descargar ediciones de Imagen reemplaza las ediciones existentes.", 1364, 757, 500),
                   caja("r Ediciones en Ad", 2212, 818, 200), caja("Desca", 1910, 847, 45, 12),
                   caja("ar ediciones", 1971, 846, 90, 12), caja("r Ediciones en Ad", 2212, 1070, 200)]
        f.ocr = lambda scale=1: list(dialogo)
        f.find = lambda text, exact=False, boxes=None, region=None: iw.Screen.find(f, text, exact, boxes, region)
        b = iw._download_button(f, dialogo)
        assert 1883 < b["x"] + b["w"] / 2 < 2087 and 835 < b["y"] + b["h"] / 2 < 869, b       # dentro del botón rojo
        b = iw._download_button(f, [q for q in dialogo if q["t"] not in ("Desca", "ar ediciones")])   # ni a trozos
        assert 1883 < b["x"] + b["w"] / 2 < 2087 and 835 < b["y"] + b["h"] / 2 < 869, b       # …por su sitio
        assert iw._download_button(f, [caja("Descargar ediciones", 1354, 578)]) is None        # sin diálogo: nada
        res = iw._await_download(f, Path("C:/e/2 DNGs"), (Path("x.log"), 0), [Path("a.dng"), Path("b.dng")])
        assert res[0] == "SUCCESS" and hecho == [("Descargar ediciones", 847)] and time.time() - t0 < 10, (res, hecho)

        # 3) proyecto «Lista para reseña» y el botón rojo sin leer: se pulsa en la columna «Siguiente paso», en su fila
        lista = [caja("Nombre del proyecto", 228, 160, 160), caja("Perfil", 805, 159, 40), caja("Estado", 1379, 160, 50),
                 caja("Fotos", 1997, 160, 40), caja("Seguiente paso", 3126, 160, 110),
                 caja("25 nov 2026 n2", 228, 222), caja("• Lista para reseña", 1378, 221, 140), caja("585", 1996, 222, 30),
                 caja("2 DNGS", 228, 307), caja("• Ajustar perfil", 1378, 306), caja("Entrenar Perfil", 3182, 308, 106)]
        iw._close_uploads = lambda s_: None
        iw._find_row = lambda s_, project, tries=4: (lista[5], lista)
        clics = []

        class Abierto(Exception):
            pass

        def espera(*a, **k):
            raise Abierto()
        g = SimpleNamespace(rect=(-9, -9, 3458, 1458), log=lambda m: None, wait=espera,
                            click_box=lambda b, after=0: clics.append((round(b["x"] + b["w"] / 2), round(b["y"] + b["h"] / 2))))
        g.inside = lambda b, r: iw.Screen.inside(g, b, r)
        try:
            iw._open_download_dialog(g, Path("C:/e/25 nov 2026/2 DNGs"), "25 nov 2026 n2")
        except Abierto:
            pass
        assert len(clics) == 1 and 3132 < clics[0][0] < 3352 and 215 < clics[0][1] < 245, clics   # dentro del botón
    finally:
        (iw.ws.windows, iw.ws.foreground, iw.ws.activate, iw.download_result, iw.core.without_imagen,
         iw.RETAP_AFTER, iw.PROMPT_WAIT, iw._find_row, iw._close_uploads) = guardado


@prueba("Bridge (Windows), botón azul «Exportar»: el OCR lo lee «capono» o «Expottor» (25-nov-2026, evento parado con "
        "las ediciones ya bajadas): vale un parecido y, si no, se pulsa por su sitio, justo encima de «Cancelar»", solo_win="Bridge de Windows")
def p72():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w, h: {"t": t, "x": x, "y": y, "w": w, "h": h}
    s = SimpleNamespace(rect=(1264, 351, 911, 737))               # el diálogo; el botón real: x 2025-2140, y 428-458
    s.inside = lambda b, r: iw.Screen.inside(s, b, r)
    dentro = lambda b: 2025 < b["x"] + b["w"] / 2 < 2140 and 428 < b["y"] + b["h"] / 2 < 458      # noqa: E731
    titulo, otro = caja("Exportar", 1290, 356, 55, 15), caja("Exportación", 1551, 456, 70, 14)
    s.ocr = lambda scale=1: [titulo, otro, caja("capono", 2032, 434, 114, 33), caja("Cancelar", 2059, 488, 55, 11)]
    assert dentro(iw._bridge_export_button(s, tries=1))                                    # por su sitio
    s.ocr = lambda scale=1: [titulo, otro, caja("Expottar", 2060, 437, 54, 12), caja("Cancelar", 2059, 488, 55, 11)]
    assert dentro(iw._bridge_export_button(s, tries=1))                                    # por parecido
    s.ocr = lambda scale=1: [titulo, otro]
    assert iw._bridge_export_button(s, tries=1) is None                                    # el título nunca vale


@prueba("Falta el preajuste de Retouch4me o de Luminar (25-nov-2026: «FIN OK» con 0 finales / sin Luminar): el evento "
        "se para con el aviso; si el programa no está instalado, el paso se sigue omitiendo")
def p73():
    from rawflow import pipeline as pl
    for why in ("no encuentro el preajuste «CINEMA» en Arams", "hay plugins de máscara instalados (Skin Mask / Eye "
                "Brilliance)", "no encuentro el preajuste «CINEMA AJUSTE» en Mis preajustes de Luminar"):
        assert not pl.not_installed(why), why
    for why in ("no encuentro Retouch4me Arams", "no encuentro Luminar Neo", "desactivado en pruebas", "solo en Windows"):
        assert pl.not_installed(why), why
    from types import SimpleNamespace                                 # _with_retries sin la ventana ni los hilos
    p = SimpleNamespace(_stop=False, RETRIES={4: 3}, retried=set(), detail=SimpleNamespace(emit=lambda *a: None),
                        _reset_app=lambda i: (_ for _ in ()).throw(AssertionError("no debe reintentar")),
                        _salvage=lambda i, e: (_ for _ in ()).throw(AssertionError("no debe apartar")))
    llamadas = []

    def paso():
        llamadas.append(1)
        raise pl.SetupMissing("Retouch4me: no encuentro el preajuste «CINEMA» en Arams.")
    try:
        pl.Pipeline._with_retries(p, 4, paso)
        raise AssertionError("debía parar el evento")
    except pl.SetupMissing:
        pass
    assert len(llamadas) == 1                                         # sin reintentos ni apartar fotos


@prueba("Selección IA en Windows, tomas al monte (25-nov-2026: 8 pasaron como buenas): toda la foto la misma textura "
        "de hierba y sin nadie → se aparta; el jardín con árboles, los caballos y las fotos con caras se quedan", solo_win="motor de selección con OpenCV")
def p74():
    import numpy as np
    from rawflow import seleccion as sel, seleccion_win as sw
    rng = np.random.default_rng(1)
    hierba = np.clip(np.dstack([rng.normal(40, 15, (400, 600)), rng.normal(130, 25, (400, 600)),
                                rng.normal(60, 15, (400, 600))]), 0, 255).astype(np.uint8)       # BGR verde con textura
    g = sw.ground_stats(hierba)
    assert g["igual"] >= sel.GROUND_SAME and g["verde"] >= sel.GROUND_GREEN, g
    jardin = hierba.copy()
    jardin[:200] = (200, 170, 120)                                                    # media foto de cielo/árboles
    assert sw.ground_stats(jardin)["igual"] < sel.GROUND_SAME
    base = {"lum": 70, "negro": 1, "quemado": 0, "desv": 30, "nit": 500, "caras": [], "personas": 0}
    assert "monte" in sel._accidental({**base, **g})
    assert not sel._accidental({**base, **sw.ground_stats(jardin)})
    assert not sel._accidental({**base, **g, "caras": [{"x": 0.4}], "personas": 1})
    assert not sel._accidental({**base, "igual": 0.9, "verde": 0.0})                 # tela con textura (no hierba)
    assert sel._accidental({**base, "igual": 0.9, "verde": 0.0, "desv": 12})          # pared lisa
    assert sel.kind(sel._accidental({**base, **g})) == "piso"


@prueba("Presentación (26-nov-2026): siempre 3 s por foto y los dos videos, «Presentación horizontal.mp4» y "
        "«Presentación vertical.mp4», aunque en CineSlide se dejara otra cosa")
def p75():
    import re
    from rawflow import pipeline as pl
    src = (Path(pl.__file__).with_name("cineslide_run.py")).read_text(encoding="utf-8")
    assert re.search(r"SEGUNDOS_POR_FOTO = 3\.0", src) and '"t_segundos_por_foto"' in src and "Los dos" in src
    assert pl.pres_name(Path("x/Presentación_horizontal.mp4"), 2) == "Presentación horizontal.mp4"
    assert pl.pres_name(Path("x/Presentación_vertical.mp4"), 2) == "Presentación vertical.mp4"
    assert pl.pres_name(Path("x/Presentación_vertical 2026-11-26 08.02.mp4"), 2) == "Presentación vertical.mp4"
    ev = td.fresh("p75")
    out, final = ev / pl.PRES_DIR, ev / pl.FINAL_DIR
    td.write(final / "1.jpg", JPG_IMAGEN)
    t = time.time() + 120
    v = td.write(out / "Presentación horizontal.mp4", b"MP4")
    os.utime(v, (t, t))
    assert not pl.presentation_current(out, final)                     # falta la vertical
    v = td.write(out / "Presentación vertical.mp4", b"MP4")
    os.utime(v, (t, t))
    assert pl.presentation_current(out, final)


@prueba("Selección IA en Windows, caras borrosas (26-nov-2026: «dejaste pasar fotos borrosas»): la nitidez de la cara "
        "se mide igual sea grande o pequeña, clara u oscura; la borrosa se aparta y la nítida (aunque oscura) se queda", solo_win="motor de selección con OpenCV")
def p76():
    import cv2
    import numpy as np
    from rawflow import seleccion as sel, seleccion_win as sw
    rng = np.random.default_rng(3)
    base = rng.integers(0, 255, (40, 40), dtype=np.uint8)
    nitida = cv2.cvtColor(cv2.resize(base, (300, 300), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    oscura = (nitida * 0.25).astype(np.uint8)                          # poca luz y poco contraste: sigue nítida
    pequena = cv2.resize(nitida, (80, 80), interpolation=cv2.INTER_AREA)
    borrosa = cv2.GaussianBlur(nitida, (0, 0), 25)                     # ≈ 5 px de desenfoque en la cara a 64 px
    vals = {k: sw.face_sharpness(v) for k, v in (("nitida", nitida), ("oscura", oscura), ("pequena", pequena),
                                                ("borrosa", borrosa))}
    assert min(vals["nitida"], vals["oscura"], vals["pequena"]) > sel.BLUR_NORM > vals["borrosa"], vals
    cara = {"x": 0.4, "y": 0.3, "w": 0.1, "h": 0.1, "px": 150, "ojoI": 0.3, "ojoD": 0.3}
    fila = {"lum": 120, "quemado": 0, "caras": [{**cara, "nitn": vals["borrosa"]}]}
    assert "movida" in sel._defect(fila)
    fila["caras"].append({**cara, "x": 0.6, "nitn": vals["nitida"]})   # otra cara nítida: la foto vale
    assert "movida" not in sel._defect(fila)
    assert "movida" not in sel._defect({"lum": 120, "quemado": 0, "caras": [cara]})   # Mac: sin «nitn», no se juzga


@prueba("Luminar (26-nov-2026: el paso 6 no se hacía): el preajuste del usuario se llama «CINEMA», no «CINEMA AJUSTE»: "
        "vale cualquiera de los dos, y en la lista «CINEMA» no se confunde con «Cinematic»", solo_win="Luminar de Windows")
def p77():
    import json as _json
    from rawflow import luminar_win as lw
    guardado = (lw.DATA, lw.PRESET)
    base = td.fresh("p77")
    try:
        lw.DATA = base
        users = base / "Data" / "Presets" / "Users"
        assert lw.find_preset() is None
        td.write(users / "a.lnp" / "template.lmps", _json.dumps({"name": "Cinematic"}).encode())
        assert lw.find_preset() is None                                   # otro que empieza igual: no
        td.write(users / "b.lnp" / "template.lmps", _json.dumps({"name": "CINEMA"}).encode())
        assert lw.find_preset() == "CINEMA" and lw.preset_exists()
        td.write(users / "c.lnp" / "template.lmps", _json.dumps({"name": "CINEMA AJUSTE"}).encode())
        assert lw.find_preset() == "CINEMA AJUSTE"                        # si están los dos, el de siempre
    finally:
        lw.DATA, lw.PRESET = guardado
    assert lw._is_item("CINEMA", "CINEMA") and lw._is_item("Cinema", "CINEMA")
    assert not lw._is_item("Cinematic", "CINEMA") and not lw._is_item("CINEMA AJUSTE", "CINEMA")
    assert lw._is_item("CINEMA AJUS…", "CINEMA AJUSTE")


@prueba("Aviso «SACA LA USB» (26-nov-2026: «tiene que estar hasta que yo retire la USB… en grande»): grande, en el "
        "centro, y no se va solo mientras la USB siga conectada (ni aunque no se sepa cuál es)", solo_win="aviso de la USB de Windows")
def p78():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from rawflow import core, overlays as ov
    QApplication.instance() or QApplication([])
    assert core.drive_present("wait:")                                    # sin saber cuál: sigue hasta cerrarlo
    t = ov.EjectToast([{"name": "USB (E:)", "disk": "wait:"}])
    t.place()
    g = ov.QGuiApplication.primaryScreen().geometry()
    assert t.width() >= min(560, g.width()) and t.height() >= 260, (t.width(), t.height())   # grande
    assert abs((t.x() + t.width() / 2) - g.center().x()) <= 2                                 # centrado
    t._check()
    assert not t.done_t and t.watching()                                  # sigue: la USB sigue puesta
    # USB conocida: expulsada pero conectada (con o sin el «problema 47» de Windows) → sigue; desenchufada → se va
    puesta = {"v": (True, 0)}
    guardado = core.win_devnode
    try:
        core.win_devnode = lambda dev: puesta["v"]
        u = ov.EjectToast([{"name": "USB (E:)", "disk": "win:USBSTOR\\X"}])
        for estado in ((True, 0), (True, 47), (True, 21)):
            puesta["v"] = estado
            u._check()
            assert not u.done_t, estado
        puesta["v"] = (False, 0)
        u._check()
        assert u.done_t                                                   # «USB RETIRADA»
    finally:
        core.win_devnode = guardado
    w = ov.EjectToast([{"name": "USB (E:)", "disk": "wait:"}])
    w.popup()
    assert w.poll.isActive() or not w.watching() is False                 # Windows: vigila, no se va sola a los 9 s
    w.close()


@prueba("Memorias (26-nov-2026: «más rápido y con animación al encontrar una»): el recorrido da las mismas fotos con "
        "su tamaño y fecha, sin las carpetas de eventos ni la basura de macOS; la tarjeta anima al llegar y al contar")
def p79():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")             # (en el servidor de GitHub no hay pantalla)
    from PySide6.QtWidgets import QApplication
    from rawflow import widgets as wd
    QApplication.instance() or QApplication([])
    raiz = td.fresh("p79")
    td.write(raiz / "DCIM" / "100MSDCF" / "DSC0001.ARW", b"R" * 10)
    td.write(raiz / "DCIM" / "100MSDCF" / "._DSC0001.ARW", b"x")
    td.write(raiz / "DCIM" / "101MSDCF" / "DSC0002.ARW", b"R" * 20)
    td.write(raiz / "DCIM" / "101MSDCF" / "vacia.ARW", b"")
    td.write(raiz / "25 nov 2026" / "1 Original" / "DSC9.ARW", b"R")           # evento ya hecho: ni se toca
    td.write(raiz / "notas.txt", b"t")
    vistos = []
    fs = core.scan_drive(str(raiz), lambda v, f: vistos.append((v, f)), skip_events=True)
    assert sorted(Path(f.path).name for f in fs) == ["DSC0001.ARW", "DSC0002.ARW"], fs
    assert {f.size for f in fs} == {10, 20} and all(f.mtime > 0 for f in fs)
    assert vistos and vistos[-1][1] == 2
    from rawflow import theme as T
    guardado_rm = T.reduce_motion
    T.reduce_motion = lambda: False                                            # (en el Mac de GitHub está activo)
    try:
        _p79_tarjeta(wd, raiz)
    finally:
        T.reduce_motion = guardado_rm


def _p79_tarjeta(wd, raiz):
    c = wd.DriveCard(core.Drive(str(raiz), "USB", "exFAT", 100, 50, True))
    assert c._animating()                                                      # ondas al aparecer
    c._born -= 10
    c.set_done(2, 30, ["ARW"])
    assert c._done_t and c._animating()                                        # el número cuenta
    c._done_t -= 10
    c._tick()
    assert not c._timer.isActive()                                             # y luego, quieta (sin gastar)


@prueba("Memorias (26-nov-2026: «debe detectar SOLO memorias que tengan imágenes RAW»): una USB sin RAW no aparece "
        "(ni vuelve a aparecer) ni arranca el autoejecutar; una con RAW de cualquier cámara sí")
def p80():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rawflow import app as ra
    base = td.fresh("p80")
    sin = base / "USB_DOCUMENTOS"
    td.write(sin / "trabajo.docx", b"d")
    td.write(sin / "fotos" / "playa.jpg", b"j")                            # JPEG: no es RAW
    con = base / "SD_CAMARA"
    td.write(con / "DCIM" / "100CANON" / "IMG_0001.CR3", b"R" * 50)
    w = ra.MainWindow()
    w.watcher.stop()
    try:
        drives = [core.Drive(str(sin), "USB_DOCUMENTOS", "exFAT", 100, 10, True),
                  core.Drive(str(con), "SD_CAMARA", "exFAT", 100, 10, True)]
        w._drives_changed(drives)
        assert not w.cards                                                  # mientras se revisan, no se ve nada
        t0 = time.time()
        while (str(con) not in w.cards or str(sin) not in w.no_raw) and time.time() - t0 < 15:
            app.processEvents()
            time.sleep(0.02)
        assert list(w.cards) == [str(con)], list(w.cards)                   # solo la que trae RAW
        assert str(sin) in w.no_raw and str(sin) not in w.hidden
        w._drives_changed(drives)                                           # otra vuelta del vigilante: no reaparece
        assert list(w.cards) == [str(con)] and str(sin) not in w.hidden
        w._drives_changed(drives[1:])                                       # quitas la USB: se olvida
        assert str(sin) not in w.no_raw
    finally:
        for s in list(w.scanners.values()):
            s.stop()
            s.wait(3000)
        w.close()


@prueba("Imagen (Windows), nombre del proyecto JUSTO antes de «Siguiente» (26-nov-2026, revisión del 25-nov): si "
        "Imagen lo pisó después de comprobarlo se escribe otra vez; si sigue ahí, o no se ve el rótulo, no se toca nada",
        solo_win="Imagen de Windows")
def p81():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w=120, h=15: {"t": t, "x": x, "y": y, "w": w, "h": h}
    nombre, rot = "25 nov 2026 n2", caja("Nombra tu proyecto", 1146, 611, 144)
    hecho, logs, pantalla = [], [], {}
    s = SimpleNamespace(rect=(920, 250, 1600, 940), should_stop=lambda: False, log=logs.append,
                        keys=lambda c: hecho.append(("teclas", c)), click=lambda x, y: hecho.append(("clic suelto",)),
                        click_box=lambda b, after=0: hecho.append(("clic en", b["t"])),
                        type=lambda t: (hecho.append(("escribe", t)), pantalla.__setitem__("dentro", t))[0])
    s.find = lambda text, exact=False, boxes=None, region=None: iw.Screen.find(s, text, exact, boxes, region)
    s.ocr = lambda scale=1: ([rot] if pantalla["rotulo"] else []) + [caja(pantalla["dentro"], 1175, 646)]
    espera = iw.NAME_STAY
    try:
        iw.NAME_STAY = 0.0
        pantalla.update(rotulo=True, dentro=nombre)                 # sigue ahí: nada
        iw._name_still_there(s, rot, nombre)
        assert hecho == [], hecho
        pantalla.update(rotulo=True, dentro="2 DNGs 0517")          # Imagen lo pisó: se escribe otra vez
        iw._name_still_there(s, rot, nombre)
        assert hecho == [("clic en", "2 DNGs 0517"), ("teclas", "ctrl+a"), ("escribe", nombre)], hecho
        hecho.clear()
        pantalla.update(rotulo=False, dentro="2 DNGs 0517")         # sin rótulo: no se sabe dónde está la casilla
        iw._name_still_there(s, rot, nombre)
        assert hecho == [] and any("no leo" in m for m in logs), (hecho, logs)
    finally:
        iw.NAME_STAY = espera


@prueba("Imagen (Windows), botón «Descargar ediciones» que no arranca (26-nov-2026, revisión): se vuelve a pulsar "
        "aunque algunos DNG ya trajeran ediciones (se compara con los que faltaban al empezar), se busca a tamaño real "
        "y ×2 por turnos, y tras las pulsaciones de más se para YA con un aviso claro", solo_win="Imagen de Windows")
def p82():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    from rawflow.imagen_ui import UIError
    caja = lambda t, x, y, w=120, h=14: {"t": t, "x": x, "y": y, "w": w, "h": h}
    dialogo = [caja("Descargar ediciones", 1354, 578, 200, 18), caja("¿Tus fotos siguen aquí?", 1353, 630, 170),
               caja("Explorar", 2016, 681, 61, 12), caja("Desca", 1910, 847, 45, 12), caja("ar ediciones", 1971, 846, 90, 12)]
    guardado = (iw.download_result, iw.core.without_imagen, iw.RETAP_AFTER, iw.RETAP_MAX, iw.PROMPT_WAIT, iw.diag)
    dngs = [Path("a.dng"), Path("b.dng")]
    try:
        iw.RETAP_AFTER, iw.RETAP_MAX, iw.PROMPT_WAIT, iw.diag = 0.0, 2, 0.0, lambda *a, **k: None
        hecho, escalas = [], []

        def falso(ocr):
            f = SimpleNamespace(rect=(0, 0, 3440, 1440), should_stop=lambda: False, log=lambda m: None,
                                click_box=lambda b, after=0: hecho.append((b["t"], b["y"])))
            f.ocr = ocr
            f.find = lambda text, exact=False, boxes=None, region=None: iw.Screen.find(f, text, exact, boxes, region)
            return f
        # 1) «a» ya traía ediciones de antes (falta solo «b»): antes NUNCA se volvía a pulsar
        iw.core.without_imagen = lambda d: [Path("b.dng")] if Path("b.dng") in list(d) else []
        iw.download_result = lambda mark, d: ("SUCCESS", 2, 2, "") if hecho else None
        res = iw._await_download(falso(lambda scale=1: list(dialogo)), Path("C:/e/2 DNGs"), (Path("x.log"), 0), dngs)
        assert res[0] == "SUCCESS" and len(hecho) == 1, (res, hecho)
        # 2) el botón solo se lee ampliado ×2: antes la escala no cambiaba si no se pulsaba
        hecho.clear()
        iw.core.without_imagen = lambda d: list(d)

        def solo_x2(scale=1):
            escalas.append(scale)
            return list(dialogo) if scale == 2 else []
        t0 = time.time()
        res = iw._await_download(falso(solo_x2), Path("C:/e/2 DNGs"), (Path("x.log"), 0), dngs)
        assert res[0] == "SUCCESS" and len(hecho) == 1 and 2 in escalas and time.time() - t0 < 15, (res, hecho, escalas)
        # 3) nunca arranca: RETAP_MAX pulsaciones de más y se da por fallido AL MOMENTO (no los 5 min del límite),
        #    devolviendo el fallo para que _download_edits haga sus reintentos (revisión 26-nov-2026: con UIError se
        #    los saltaba)
        hecho.clear()
        iw.download_result = lambda mark, d: None
        t0 = time.time()
        res = iw._await_download(falso(lambda scale=1: list(dialogo)), Path("C:/e/2 DNGs"), (Path("x.log"), 0), dngs)
        assert res and res[0] == "NO_ARRANCA" and "Descargar ediciones" in res[1], res
        assert len(hecho) == 2 and time.time() - t0 < 20, (hecho, time.time() - t0)
    finally:
        iw.download_result, iw.core.without_imagen, iw.RETAP_AFTER, iw.RETAP_MAX, iw.PROMPT_WAIT, iw.diag = guardado


@prueba("Imagen/Bridge (Windows), ventana delante (26-nov-2026, revisión): si Windows no la deja delante se prueba "
        "una vez más y luego UIError (nada de clics a ciegas en otra ventana); y si algo la sigue tapando, queda dicho",
        solo_win="Imagen de Windows")
def p83():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    from rawflow.imagen_ui import UIError
    guardado = (iw.ws.windows, iw.ws.foreground, iw.ws.activate, iw.ws.click, iw.ws.user32, iw.diag)
    imagen = {"hwnd": 7, "title": "Imagen AI", "rect": (0, 0, 1920, 1040), "min": False, "exe": "ImagenAI.exe", "pid": 1}
    tapa = {"hwnd": 9, "title": "Claude", "rect": (100, 100, 800, 600), "min": False, "exe": "claude.exe", "pid": 2}
    try:
        delante, activadas, clics, minimizadas, logs = ["claude.exe"], [], [], [], []
        iw.ws.windows = lambda title_part="", exe="": [w for w in (tapa, imagen) if not exe or w["exe"] == exe]
        iw.ws.foreground = lambda: {"hwnd": 1, "title": "", "exe": delante[0]}
        iw.ws.activate = lambda hwnd, maximize=True, primary=False: activadas.append(hwnd) or delante[0] == "ImagenAI.exe"
        iw.ws.click = lambda x, y, n=1, restore=True: clics.append((x, y))
        iw.ws.user32 = SimpleNamespace(ShowWindow=lambda h, c: minimizadas.append(h))   # «Claude» no se deja
        iw.diag = lambda *a, **k: None
        s = iw.Screen(logs.append)
        assert s._uncover() is False and minimizadas == [9] and any("sigue tapando" in m for m in logs), logs
        try:
            s.front(force=True)
            raise AssertionError("debía parar con UIError")
        except UIError as e:
            assert "a ciegas" in str(e), e
        assert activadas == [7, 7], activadas                       # una vez más, y no más
        try:
            s.click(10, 10)                                         # ocr() se traga el fallo: el clic NO va a ciegas
            raise AssertionError("debía parar con UIError")
        except UIError:
            pass
        assert clics == [], clics
        iw.ws.activate = lambda hwnd, maximize=True, primary=False: (activadas.append(hwnd), delante.__setitem__(0, "ImagenAI.exe"))[0] or True
        s.click(10, 10)                                             # ya queda delante: el clic va
        assert clics == [(10, 10)] and s._blind is False, clics
    finally:
        iw.ws.windows, iw.ws.foreground, iw.ws.activate, iw.ws.click, iw.ws.user32, iw.diag = guardado


@prueba("Bridge (Windows), diálogo de exportación (26-nov-2026, revisión): «N seleccionados» se lee de la barra de "
        "estado; «Formato de imagen» plegado se despliega; la calidad JPEG vale solo con DOS lecturas iguales (tamaño "
        "real y ×2, desplazada)", solo_win="Bridge de Windows")
def p84():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w=60, h=12: {"t": t, "x": x, "y": y, "w": w, "h": h}
    assert iw._bridge_selected([caja("Contenido", 0, 0), caja("120 elementos, 120 seleccionados - 2,81 GB", 0, 900)]) == 120
    assert iw._bridge_selected([caja("1.234 elementos, 1 seleccionado - 25 MB", 0, 900)]) == 1
    assert iw._bridge_selected([caja("2 items, 2 selected - 50 MB", 0, 900)]) == 2
    assert iw._bridge_selected([caja("Invertir selección", 0, 0), caja("120 elementos", 0, 900)]) is None

    s = SimpleNamespace(rect=(0, 0, 900, 700), log=lambda m: None)
    s.find = lambda text, exact=False, boxes=None, region=None: iw.Screen.find(s, text, exact, boxes, region)
    clics, plegado = [], [True]
    s.click = lambda x, y, n=1: (clics.append((x, y)), plegado.__setitem__(0, False))
    s.ocr = lambda scale=1: [caja("Formato de imagen", 300, 200, 120)] + (
        [] if plegado[0] else [caja("Formato", 320, 230), caja("Calidad de imagen", 320, 280, 100)])
    iw._bridge_unfold_format(s)
    assert clics == [(300 - 1.8 * 12, 206)], clics
    iw._bridge_unfold_format(s)                                     # desplegado: no se toca
    assert len(clics) == 1

    def lecturas(valores):
        escalas = []

        def ocr(scale=1):
            v = valores[min(len(escalas), len(valores) - 1)]
            escalas.append(scale)
            d = 30 if scale == 2 else 0                             # ×2: las cajas salen desplazadas
            return [caja("Calidad de imagen", 320, 280 + d, 100), caja(v, 330, 300 + d, 20), caja("Máxima", 380, 300 + d)]
        s.ocr = ocr
        return escalas
    esc = lecturas(["12", "12"])
    num, top = iw._bridge_quality(s)
    assert num["t"] == "12" and num["y"] == 300 and top and esc == [1, 2], (num, top, esc)   # la caja a tamaño real
    esc = lecturas(["12", "2", "12", "2"])                          # nunca dos iguales: no se da por buena
    num, top = iw._bridge_quality(s)
    assert num is not None and top is False and esc == [1, 2, 1, 2], (num, top, esc)


@prueba("Bridge (Windows), exportación a medias (26-nov-2026, revisión): los JPEG ya exportados se quedan (los "
        "cortados se borran) y Bridge exporta solo los que faltan, desde una carpeta con enlaces que luego se borra",
        solo_win="Bridge de Windows")
def p85():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    base = td.fresh("p85")
    dng, out = base / "2 DNGs", base / "3 Imagen AI"
    for n in ("a", "b", "c-DxO_DeepPRIME XD3", "d"):
        td.write(dng / f"{n}.dng", DNG_IMAGEN)
    bueno = b"\xff\xd8" + b"J" * 2000 + b"\xff\xd9"
    td.write(out / "a.jpg", bueno)
    td.write(out / "b.jpg", b"\xff\xd8" + b"J" * 2000)               # cortado a medias: no vale
    td.write(out / "c-DxO_DeepPRIME XD3.jpg", bueno)                 # de un intento sin terminar, pero completo
    guardado = (iw._bridge_export_dir, iw.close_app)
    llamadas = []

    def exporta(s, d, o, expected, progress=lambda f: None):
        llamadas.append((d, expected, sorted(p.name for p in d.glob("*.dng"))))
        for p in d.glob("*.dng"):
            (o / (core.dng_base(p) + ".jpg")).write_bytes(bueno)
        return len(list(o.glob("*.jpg")))
    try:
        iw._bridge_export_dir, iw.close_app = exporta, lambda exe, wait=10: None
        s = SimpleNamespace(log=lambda m: None)
        a0 = (out / "a.jpg").stat().st_mtime_ns
        assert iw.bridge_export(s, dng, out, 4) == 4
        assert llamadas == [(dng / iw.MISSING_DIR, 2, ["b.dng", "d.dng"])], llamadas
        assert not (dng / iw.MISSING_DIR).exists() and (out / "a.jpg").stat().st_mtime_ns == a0
        assert sorted(p.name for p in out.glob("*.jpg")) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
        assert iw.bridge_export(s, dng, out, 4) == 4 and len(llamadas) == 1          # todo hecho: ni se abre Bridge
        for p in out.glob("*.jpg"):
            p.unlink()
        assert iw.bridge_export(s, dng, out, 4) == 4 and llamadas[-1][:2] == (dng, 4)   # nada hecho: la carpeta tal cual
    finally:
        iw._bridge_export_dir, iw.close_app = guardado


@prueba("Luminar (Windows), «Guardar como» (26-nov-2026, revisión): el nombre escrito se reconoce sin mirar tildes ni "
        "mayúsculas", solo_win="Luminar de Windows")
def p86():
    from rawflow import luminar_win as lw
    caja = lambda t: {"t": t, "x": 0, "y": 0, "w": 100, "h": 12}
    p = Path("E:/Eventos/Boda José María/Boda José María.luminar")
    assert lw._name_typed(p, [caja("Nombre de archivo:"), caja("E:\\Eventos\\BODA JOSE MARIA.luminar")])
    assert lw._name_typed(p, [caja("boda josé maría")])
    assert not lw._name_typed(p, [caja("Nombre de archivo:"), caja("Catálogo sin título")])


@prueba("Imagen (Windows): un anuncio encima («Un año de Limitless: −30 %… Black Friday», prueba de 5 fotos del "
        "26-nov-2026) tapaba la barra lateral y Esc no lo cerraba: se pulsa «Quizá el año que viene» (o su ✕), NUNCA el "
        "botón de comprar", solo_win="Imagen de Windows")
def p87():
    from types import SimpleNamespace
    from rawflow import imagen_win as iw
    caja = lambda t, x, y, w=80, h=16: {"t": t, "x": x, "y": y, "w": w, "h": h}
    clics, s = [], SimpleNamespace(rect=(0, 0, 1938, 1098), log=lambda m: None)
    s.inside = lambda b, r: iw.Screen.inside(s, b, r)
    s.click_box = lambda b, after=0.8: clics.append(b["t"])
    anuncio = [caja("Un año de Limitless: -30 %", 815, 470), caja("Oferta de Black Friday. Por tiempo limitado.", 835, 540),
               caja("Ahorra un 30 %", 930, 590), caja("Quizá el año que viene", 900, 628), caja("Proyectos activos (15)", 129, 370)]
    assert iw._close_promo(s, anuncio) and clics == ["Quizá el año que viene"], clics
    clics.clear()
    assert iw._close_promo(s, anuncio[:3] + [caja("×", 1180, 432, 12, 12)]) and clics == ["×"], clics
    clics.clear()
    assert not iw._close_promo(s, [caja("Proyectos activos (15)", 129, 370), caja("Crear proyecto", 206, 548)])
    assert not clics                                                      # sin anuncio no se toca nada


@prueba("DxO (27-nov-2026, más rápido): si a los 90 s no hay ningún DNG y DxO no usa procesador ni disco en 45 s, se "
        "reinicia ya (antes esperaba 5 min); si está trabajando (cargando, bajando un módulo) se sigue esperando")
def p88():
    from rawflow import pipeline as pl
    clk = {}
    for t in range(0, 50, 5):                                    # parado: el contador casi no se mueve
        idle = pl._dxo_idle(clk, 100.0 + t, usage=10.0 + t * 0.01)
    assert idle, clk
    clk = {}
    for t in range(0, 50, 5):                                    # trabajando: procesador y disco suben
        idle = pl._dxo_idle(clk, 100.0 + t, usage=10.0 + t * 2)
    assert not idle, clk
    clk = {}
    for t in range(0, 30, 5):                                    # menos de 45 s mirando: aún no se decide
        idle = pl._dxo_idle(clk, 100.0 + t, usage=10.0)
    assert not idle, clk


@prueba("Windows (27-nov-2026, más rápido): durante el evento DxO, Imagen, Bridge, Retouch4me, Luminar y CineSlide van "
        "con prioridad «por encima de lo normal» (la persona sigue usando el equipo) y al acabar vuelven a «normal»")
def p89():
    import threading
    from rawflow import pipeline as pl
    import psutil
    if not hasattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS"):
        raise Saltada("solo Windows: prioridades de Windows")
    puestas = []

    class P:
        def __init__(self, pid, name, nice):
            self.info, self._n = {"pid": pid, "name": name}, nice

        def nice(self, v=None):
            if v is None:
                return self._n
            puestas.append((self.info["name"], v))
            self._n = v

        def is_running(self):
            return True
    procs = [P(1, "Adobe Bridge.exe", psutil.NORMAL_PRIORITY_CLASS), P(2, "chrome.exe", psutil.NORMAL_PRIORITY_CLASS),
             P(3, "Retouch4me Arams.exe", psutil.NORMAL_PRIORITY_CLASS),
             P(4, "PureRAWv6.exe", psutil.HIGH_PRIORITY_CLASS)]          # ya iba más alto: no se toca
    orig = psutil.process_iter
    psutil.process_iter = lambda attrs=None: list(procs)
    try:
        end = threading.Event()
        end.set()
        vistos = pl.boost_apps(end, every=0.01)
    finally:
        psutil.process_iter = orig
    assert vistos == {1, 3, 4}, vistos                                    # chrome: nunca
    assert puestas == [("Adobe Bridge.exe", psutil.ABOVE_NORMAL_PRIORITY_CLASS),
                       ("Retouch4me Arams.exe", psutil.ABOVE_NORMAL_PRIORITY_CLASS),
                       ("Adobe Bridge.exe", psutil.NORMAL_PRIORITY_CLASS),
                       ("Retouch4me Arams.exe", psutil.NORMAL_PRIORITY_CLASS)], puestas


def main() -> int:
    ok = saltadas = 0
    print(f"PRUEBAS DE RAWFLOW ({len(PRUEBAS)} casos)\n", flush=True)
    for n, (titulo, fn) in enumerate(PRUEBAS, 1):
        t0 = time.monotonic()
        try:
            fn()
            print(f"  ✓ Prueba {n:>2}: {titulo}  ({time.monotonic() - t0:.1f} s)", flush=True)
            ok += 1
        except Saltada as s:
            print(f"  – Prueba {n:>2}: {titulo}  [saltada: {s}]", flush=True)
            saltadas += 1
        except Exception:  # noqa: BLE001
            print(f"  ✗ Prueba {n:>2}: {titulo}", flush=True)
            traceback.print_exc()
            sys.stderr.flush()
    shutil.rmtree(td.TMP, ignore_errors=True)
    fallos = len(PRUEBAS) - ok - saltadas
    print(f"\n{ok} de {len(PRUEBAS)} pruebas bien" + (f" · {saltadas} saltadas" if saltadas else "")
          + f" · {fallos} fallos")
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
