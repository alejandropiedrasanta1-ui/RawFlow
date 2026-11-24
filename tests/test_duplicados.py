"""Pruebas de casos duplicados (carpetas, archivos, proyectos). Se ejecutan con:

    .venv/bin/python tests/test_duplicados.py

Usan carpetas temporales: no tocan el Escritorio, las memorias ni las apps.
"""
from __future__ import annotations

import os as _os
_os.environ["RAWFLOW_SIN_APPS"] = "1"      # las pruebas NUNCA manejan ImagenAI, Bridge ni Retouch4me de verdad
import hashlib
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rawflow import core  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rawflow-pruebas-"))
from rawflow import imagen_ui as _iu  # noqa: E402
_iu._ACT = TMP / "acciones.log"        # las pruebas no escriben en el registro de acciones de verdad
RESULTS = []


def caso(fn):
    RESULTS.append(fn)
    return fn


def h(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def write(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def raw(card: Path, name: str, seed: int, size: int = 4096) -> core.RawFile:
    """Un «RAW» de prueba: contenido distinto según la semilla, mismo tamaño si se pide."""
    data = bytes((seed * 31 + i) % 251 for i in range(size))
    p = write(card / "DCIM" / name, data)
    st = p.stat()
    return core.RawFile(path=str(p), size=st.st_size, mtime=st.st_mtime, drive=str(card))


def fresh(name: str) -> Path:
    d = TMP / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------- carpetas de evento
@caso
def carpeta_evento_duplicada():
    base = fresh("eventos")
    a = core.unique_folder(base / "20 nov 2026")
    assert a.name == "20 nov 2026"
    a.mkdir()
    b = core.unique_folder(base / "20 nov 2026")
    assert b.name == "20 nov 2026 2", b
    b.mkdir()
    (base / "20 nov 2026 3").mkdir()                      # hueco ocupado a mano
    c = core.unique_folder(base / "20 nov 2026")
    assert c.name == "20 nov 2026 4", c


@caso
def nombre_evento_con_signos():
    for bad in ("Boda/Ana:Luis", "  boda  ana  ", "fin."):
        n = core.clean_name(bad)
        assert "/" not in n and ":" not in n and n == n.strip(" ."), (bad, n)


# ---------------------------------------------------------------- archivos repetidos al copiar
@caso
def dos_camaras_mismo_nombre_y_mismo_tamano():
    """El caso peligroso: DSC00001.ARW en dos memorias, distintas fotos, MISMO tamaño (RAW sin comprimir)."""
    c1, c2, dest = fresh("cam1"), fresh("cam2"), fresh("orig1")
    f1, f2 = raw(c1, "DSC00001.ARW", 1), raw(c2, "DSC00001.ARW", 2)
    h1, h2 = h(Path(f1.path)), h(Path(f2.path))
    assert h1 != h2 and f1.size == f2.size
    st = core.move_files_parallel([f1, f2], dest)
    got = sorted(dest.iterdir())
    assert st.failed == 0 and st.copied == 2, (st.copied, st.skipped, st.errors)
    assert sorted(h(p) for p in got) == sorted([h1, h2]), "se perdió una foto"
    assert not Path(f1.path).exists() and not Path(f2.path).exists()      # se borraron de la memoria…
    assert len(got) == 2                                                  # …porque las dos están copiadas


@caso
def misma_memoria_otra_vez():
    """Volver a meter la misma memoria: las fotos idénticas no se duplican."""
    c1, dest = fresh("cam3"), fresh("orig2")
    f = raw(c1, "DSC00002.ARW", 5)
    data = Path(f.path).read_bytes()
    core.move_files_parallel([f], dest)
    f_again = raw(c1, "DSC00002.ARW", 5)                  # la misma foto otra vez
    assert Path(f_again.path).read_bytes() == data
    st = core.move_files_parallel([f_again], dest)
    assert st.skipped == 1 and st.copied == 0
    assert [p.name for p in dest.iterdir()] == ["DSC00002.ARW"]


@caso
def mismo_nombre_base_otra_extension():
    """DSC0001.ARW (Sony) y DSC0001.CR3 (Canon): DxO e Imagen usan el nombre base → no pueden coincidir."""
    c1, c2, dest = fresh("sony"), fresh("canon"), fresh("orig3")
    st = core.move_files_parallel([raw(c1, "DSC0001.ARW", 7), raw(c2, "DSC0001.CR3", 8)], dest)
    stems = [p.stem.lower() for p in dest.iterdir()]
    assert st.copied == 2 and len(set(stems)) == 2, stems


@caso
def mayusculas_minusculas():
    c1, c2, dest = fresh("m1"), fresh("m2"), fresh("orig4")
    st = core.move_files_parallel([raw(c1, "dsc0009.arw", 9), raw(c2, "DSC0009.ARW", 10)], dest)
    assert st.copied == 2 and len({p.stem.lower() for p in dest.iterdir()}) == 2


@caso
def muchas_repetidas_en_paralelo():
    """3 memorias a la vez con los mismos 20 nombres: 60 fotos distintas, ninguna se pierde."""
    cards = [fresh(f"par{i}") for i in range(3)]
    dest = fresh("orig5")
    files = [raw(c, f"IMG_{k:04d}.CR3", i * 100 + k) for i, c in enumerate(cards) for k in range(20)]
    hashes = sorted(h(Path(f.path)) for f in files)
    st = core.move_files_parallel(files, dest)
    assert st.copied == 60 and st.failed == 0, (st.copied, st.errors[:3])
    assert sorted(h(p) for p in dest.iterdir()) == hashes
    assert len({p.stem.lower() for p in dest.iterdir()}) == 60


@caso
def ya_existia_un_sufijo():
    """Si ya hay DSC1.ARW y DSC1_1.ARW, la siguiente distinta es DSC1_2.ARW."""
    c, dest = fresh("suf"), fresh("orig6")
    write(dest / "DSC1.ARW", b"a" * 100)
    write(dest / "DSC1_1.ARW", b"b" * 100)
    st = core.move_files_parallel([raw(c, "DSC1.ARW", 3, size=100)], dest)
    assert st.copied == 1 and (dest / "DSC1_2.ARW").exists()


# ---------------------------------------------------------------- DNG / JPEG
@caso
def dng_pendientes_con_sufijos():
    ev = fresh("ev_dng")
    for n in ("A.ARW", "A_1.ARW", "B.CR3"):
        write(ev / core.RAW_SUBFOLDER / n, b"x")
    write(ev / core.OUTPUT_SUBFOLDER / "A-DxO_DeepPRIME XD3.dng", _mini_dng())
    pend = sorted(Path(p).name for p in core.pending_raws(ev))
    assert pend == ["A_1.ARW", "B.CR3"], pend


def _mini_dng(n_data: int = 5000) -> bytes:
    """TIFF/DNG mínimo: cabecera + 1 IFD con StripOffsets/StripByteCounts + datos."""
    import struct
    data_off = 8 + 2 + 2 * 12 + 4
    ifd = struct.pack("<H", 2)
    ifd += struct.pack("<HHII", 273, 4, 1, data_off)        # StripOffsets
    ifd += struct.pack("<HHII", 279, 4, 1, n_data)          # StripByteCounts
    ifd += struct.pack("<I", 0)
    return b"II" + struct.pack("<HI", 42, 8) + ifd + bytes(n_data)


@caso
def dng_cortado_no_cuenta():
    """DxO cerrado a mitad: el DNG a medias no cuenta como hecho y se borra para rehacerlo."""
    out = fresh("dng_cut")
    full = _mini_dng()
    write(out / "A-DxO_DeepPRIME XD3.dng", full)
    write(out / "B-DxO_DeepPRIME XD3.dng", full[: len(full) - 1500])       # cortado
    assert core.dng_complete(out / "A-DxO_DeepPRIME XD3.dng")
    assert not core.dng_complete(out / "B-DxO_DeepPRIME XD3.dng")
    assert core.processed_stems(out) == {"a"}
    assert core.remove_broken_dngs(out) == ["B-DxO_DeepPRIME XD3.dng"]
    assert [p.name for p in out.iterdir()] == ["A-DxO_DeepPRIME XD3.dng"]


@caso
def exportaciones_de_bridge_repetidas():
    from rawflow.imagen_ui import clean_exports
    out = fresh("bridge")
    write(out / "DSC1.jpg", b"viejo")                                   # de un intento anterior
    write(out / "DSC1-DxO_DeepPRIME XD3.jpg", b"nuevo")
    write(out / "DSC2-DxO_DeepPRIME XD3.jpg", b"dos")
    n = clean_exports(out)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["DSC1.jpg", "DSC2.jpg"] and n == 2, names
    assert (out / "DSC1.jpg").read_bytes() == b"nuevo"


@caso
def numeracion_final_sin_pisar():
    from rawflow.pipeline import Pipeline
    ev = fresh("ev_num")
    fin = ev / core.FINAL_SUBFOLDER
    for i, n in enumerate(("C.jpg", "2.jpg", "A.jpg")):                 # un nombre ya es un número
        write(fin / n, n.encode())
        write(ev / core.RAW_SUBFOLDER / (Path(n).stem + ".ARW"), b"r")
        os.utime(ev / core.RAW_SUBFOLDER / (Path(n).stem + ".ARW"), (1000 + i, 1000 + i))
    pl = Pipeline.__new__(Pipeline)
    pl.ev_dir = ev

    class _S:
        def emit(self, *_):
            pass
    pl.detail = _S()
    Pipeline._number_finals(pl, fin)
    names = sorted(p.name for p in fin.iterdir())
    assert names == ["1.jpg", "2.jpg", "3.jpg"], names
    assert sorted(p.read_bytes() for p in fin.iterdir()) == sorted([b"C.jpg", b"2.jpg", b"A.jpg"])
    assert (fin / "1.jpg").read_bytes() == b"C.jpg"                       # orden de captura


# ---------------------------------------------------------------- proyectos de Imagen / Retouch4me
@caso
def proyecto_imagen_nombre_libre():
    from rawflow import imagen_ui
    imagen_ui._HIST = TMP / "hist.txt"
    imagen_ui._HIST.write_text("20 nov 2026 #2\n")
    visibles = ["Proyectos", "20 nov 2026", "Boda Ana", "20 nov 2026 #3"]
    assert imagen_ui.free_name("20 nov 2026", visibles) == "20 nov 2026 #4"
    assert imagen_ui.free_name("Boda Luis", visibles) == "Boda Luis"
    assert imagen_ui.free_name("Boda Ana", ["Boda Ana", "boda  ana #2"]) == "Boda Ana #3"
    # «20 nov 2026 2» (evento repetido) no es lo mismo que «20 nov 2026 #2» (proyecto repetido)
    assert imagen_ui.free_name("20 nov 2026 2", ["20 nov 2026 #2", "20 nov 2026"]) == "20 nov 2026 2"


@caso
def proyecto_imagen_no_confundir():
    """Buscando «20 nov 2026 #2» nunca debe devolver la fila del proyecto viejo «20 nov 2026»."""
    from rawflow import imagen_ui
    rows = [{"t": "20 nov 2026", "x": 60, "y": 400, "w": 100, "h": 14},          # viejo, más abajo
            {"t": "20 nov 2026 #2", "x": 60, "y": 300, "w": 120, "h": 14},       # nuevo, arriba
            {"t": "Boda", "x": 60, "y": 200, "w": 50, "h": 14}]
    assert imagen_ui._row(None, "20 nov 2026 #2", rows)["y"] == 300
    assert imagen_ui._row(None, "20 nov 2026", rows)["y"] == 400               # el exacto, no el «#2»
    # nunca un parecido: «20 nov 2026» (u otro) no cuenta como «20 nov 2026 3 #2»
    otros = [{"t": "20 nov 2026", "x": 60, "y": 300, "w": 100, "h": 14},
             {"t": "20 nov 2026 3", "x": 60, "y": 340, "w": 100, "h": 14}]
    assert imagen_ui._row(None, "20 nov 2026 3 #2", otros) is None
    # nombre largo recortado por Imagen con «…»
    largo = [{"t": "Boda de Ana y Luis en la Hacienda…", "x": 60, "y": 320, "w": 200, "h": 14}]
    assert imagen_ui._row(None, "Boda de Ana y Luis en la Hacienda San Miguel", largo)["y"] == 320


@caso
def ningun_qobject_tapa_metodos_de_qt():
    """Un atributo con el nombre de un método de Qt (p. ej. «event») rompe las señales en silencio."""
    import inspect
    from PySide6.QtCore import QObject
    from rawflow import app, inputlock, pipeline, runscreen, overlays
    import re as _re
    bad = []
    for mod in (app, inputlock, pipeline, runscreen, overlays):
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if not issubclass(cls, QObject) or cls.__module__ != mod.__name__:
                continue
            reserved = {n for b in cls.__mro__ if b.__module__.startswith("PySide6")
                        for n in dir(b) if not n.startswith("_")}
            for attr in set(_re.findall(r"self\.([A-Za-z_]\w*)\s*(?:,[^=\n]*)?=(?!=)", inspect.getsource(cls))):
                if attr in reserved and attr not in cls.__dict__:
                    bad.append(f"{name}.{attr}")
    assert not bad, bad


@caso
def limpieza_al_terminar():
    """Evento terminado: no quedan marcas internas ni temporales, y las fotos siguen todas."""
    from rawflow.pipeline import Pipeline
    ev = fresh("ev_fin")
    d = ev / core.OUTPUT_SUBFOLDER
    write(d / "A-DxO_DeepPRIME XD3.dng", _mini_dng())
    write(d / ".rawflow_imagen_ok", b"x")
    write(d / ".rawflow_imagen_proyecto", b"20 nov 2026")
    write(ev / ".presentacion-123" / "x.mp4", b"v")
    write(ev / core.FINAL_SUBFOLDER / "1.jpg", b"j")
    write(ev / core.FINAL_SUBFOLDER / "2.jpg.part", b"p")
    pl = Pipeline.__new__(Pipeline)
    pl.ev_dir = ev
    Pipeline._clean_traces(pl)
    left = sorted(p.relative_to(ev).as_posix() for p in ev.rglob("*") if p.is_file())
    assert left == ["2 DNGs/A-DxO_DeepPRIME XD3.dng", f"{core.FINAL_SUBFOLDER}/1.jpg"], left


@caso
def funciona_en_windows():
    """Con SYSTEM=Windows ninguna parte exclusiva de Mac debe romper (se omite o se salta)."""
    import importlib
    from rawflow import pipeline as P, inputlock, overlays
    old = core.SYSTEM
    try:
        core.SYSTEM = "Windows"
        importlib.reload(P)
        assert P.full_speed() is None
        P.stage_manager(False); P.restore_stage_manager(); P.quit_other_apps(); P.prelaunch("X")
        # Escucha de «Esc ×3» (ya no bloquea teclado ni ratón): en Windows va por rawflow.winlock (o RawFlow.exe
        # --escuchar), nunca por rfui (Mac). El proceso se SIMULA: no hace falta poner un gancho de teclado de verdad
        # en el equipo de quien pasa las pruebas.
        import io
        lanzados = []

        class _Bloqueo:
            def __init__(self, cmd, *a, **k):
                lanzados.append([str(x) for x in cmd])
                self.stdin, self.stdout = io.StringIO(), io.StringIO("ERROR simulado en la prueba\n")

            def poll(self):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, *a, **k):
                return 0
        popen = inputlock.subprocess.Popen
        inputlock.subprocess.Popen = _Bloqueo
        try:
            assert inputlock.InputLock().listen() is False     # sin «LISTENING» del proceso: no hay escucha
            # y un ayudante que dijera «BLOCKING» (el bloqueo de antes) JAMÁS se acepta: nada debe bloquear
            _Bloqueo.__init__ = lambda self, cmd, *a, **k: (lanzados.append([str(x) for x in cmd]), setattr(
                self, "stdin", io.StringIO()), setattr(self, "stdout", io.StringIO("BLOCKING hook\n")))[0]
            assert inputlock.InputLock().listen() is False
        finally:
            inputlock.subprocess.Popen = popen
        assert len(lanzados) == 2 and not any("rfui" in x.lower() for x in lanzados[0]), lanzados
        assert lanzados[0][-2:] == ["-m", "rawflow.winlock"] or lanzados[0][-1] == "--escuchar", lanzados
        assert not any("block" in x.lower() or "bloqueo" in x.lower() for c in lanzados for x in c), lanzados
        import subprocess
        real = subprocess.run
        subprocess.run = lambda cmd, *a, **k: (_ for _ in ()).throw(FileNotFoundError(cmd[0])) \
            if cmd[0] in ("pgrep", "pkill", "osascript", "defaults") else real(cmd, *a, **k)
        try:
            P._running("PureRAWv6")                  # no debe usar pgrep en Windows
            P.Pipeline._reset_app(P.Pipeline.__new__(P.Pipeline), 3)
        except FileNotFoundError as e:
            raise AssertionError(f"usa {e} en Windows")
        except Exception:  # noqa: BLE001
            pass                                     # tasklist no existe en Mac: da igual aquí
        finally:
            subprocess.run = real
        from rawflow import timing
        assert len(timing.Estimator().estimate(10, 10 * 50_000_000)) == 6
    finally:
        core.SYSTEM = old
        importlib.reload(P)


@caso
def contar_fotos_del_proyecto():
    """La columna «Fotos» de Imagen leída por OCR (a veces lee «З» cirílica en vez de 3)."""
    from rawflow import imagen_ui
    fila = [{"t": "20 nov 2026 3 #3", "x": 180, "y": 300}, {"t": "З", "x": 680, "y": 301},
            {"t": "20 nov 2026", "x": 805, "y": 300}]
    assert imagen_ui._row_count(fila, 300) == 3
    assert imagen_ui._row_count([{"t": "12", "x": 677, "y": 500}], 500) == 12
    assert imagen_ui._row_count([{"t": "Lista", "x": 500, "y": 500}], 500) is None


@caso
def retouch_reanuda_sin_repetir():
    """Retouch4me reintentado: las que ya están en «4 Fotos finales» no se vuelven a retocar."""
    from rawflow import arams_ui
    if core.SYSTEM != "Darwin" or not arams_ui.APP_PATH.exists():
        return                                   # necesita Retouch4me Arams instalado (en GitHub no lo está)

    src, dest = fresh("ret_src"), fresh("ret_dst")
    for n in ("A.jpg", "B.jpg", "C.jpg"):
        write(src / n, b"x" * 200_000)
    write(dest / "A.jpg", b"y" * 200_000)                                      # ya retocada
    stage = Path.home() / "Library/Application Support/RawFlow/retouch"
    calls = {}
    orig = arams_ui.check_permissions
    arams_ui.check_permissions = lambda: "PRUEBA"                              # cortar antes de abrir Arams
    try:
        arams_ui.retouch(src, "Prueba", lambda m: calls.setdefault("log", m), dest=dest)
    except arams_ui.UIError as e:
        assert "PRUEBA" in str(e)
    finally:
        arams_ui.check_permissions = orig
    staged = sorted(p.name for p in (stage / "Prueba").glob("*.jpg"))
    shutil.rmtree(stage, ignore_errors=True)
    assert staged == ["B.jpg", "C.jpg"], staged


# ---------------------------------------------------------------- Retouch4me: motor directo (sin ventana)
_CINEMA = {"name": "CINEMA", "overrides": None, "editMode": "all", "customLut": None, "preset": [
    {"id": "Color Correction", "params": {"Color Correction Mode": "None", "Exposure": 0}, "selected": False},
    {"id": "Heal", "params": {"Scale": 0, "Alpha1": 58, "Alpha2": 0, "mask_skin": False}, "selected": True},
    {"id": "Dodge Burn", "params": {"Scale": 0, "Alpha1": 20, "Alpha2": 0, "mask_skin": False}, "selected": True},
    {"id": "Mattifier", "params": {"Scale": 0, "Alpha1": 10, "mask_skin": False}, "selected": True},
    {"id": "Skin Tone", "params": {"Scale": 0, "Alpha1": 100, "mask_skin": False}, "selected": False},
    {"id": "Portrait Volumes", "params": {"Scale": 0, "Alpha1": 30, "mask_skin": False}, "selected": True},
    {"id": "Eye Vessels", "params": {"Scale": 0, "Alpha1": 100, "mask_eyes": True}, "selected": False},
    {"id": "Stray Hairs", "params": {"Alpha1": 100}, "selected": True},
]}
_PL = "/Library/Application Support/Adobe/Plug-Ins/CC/Retouch4me "
_SPLIT = {"intensityGenderMale": 0, "intensityGenderFemale": 0, "intensityRest": 1}
_M = {"mask_skin": False, "mask_not_skin": False, "mask_eyes": False, "mask_not_eyes": False}
# lo que Arams 1.10.5 mandó de verdad a su motor con CINEMA (registro de Arams, 20-nov-2026 11:12:22)
_ARAMS_CHAIN = [
    {"id": "Heal", "User Params": {"Scale": 0, "Alpha1": 0.58, "Alpha2": 0, **_M, "moles": 0, "process_image": True,
                                   "splitProcessing": _SPLIT, "metadata": {"faces": []}}, "path": _PL + "Heal.plugin"},
    {"id": "Stray Hairs", "User Params": {"Alpha1": 1, **_M, "process_image": True, "splitProcessing": _SPLIT,
                                          "metadata": {"faces": []}}, "path": _PL + "Stray Hairs.plugin"},
    {"id": "Mattifier", "User Params": {"Scale": 0, "Alpha1": 0.1, **_M, "process_image": True,
                                        "splitProcessing": _SPLIT, "metadata": {"faces": []}},
     "path": _PL + "Mattifier.plugin"},
    {"id": "Dodge Burn", "User Params": {"Scale": 0, "Alpha1": 0.2, "Alpha2": 0, **_M, "process_image": True,
                                         "splitProcessing": _SPLIT, "metadata": {"faces": []}},
     "path": _PL + "Dodge Burn.plugin"},
    {"id": "Portrait Volumes", "User Params": {"Scale": 0, "Alpha1": 0.3, **_M, "process_image": True,
                                               "splitProcessing": _SPLIT, "metadata": {"faces": []}},
     "path": _PL + "Portrait Volumes.plugin"},
]
_INSTALLED = {k: Path(_PL + k + ".plugin") for k in ("Heal", "Stray Hairs", "Mattifier", "Dodge Burn",
                                                     "Portrait Volumes")}
_SETTINGS = {"faceAutoSearchEnabled": "0", "enableExpertSettings": "0", "useCloudProcessing": "false"}


@caso
def seleccion_fallida_desenfocada():
    """Sin caras y casi sin nitidez frente al evento (boda 14 nov: bokeh total, movida) → fallida; una foto blanda
    pero con algo nítido (el gato, a 0,13 de la mediana) o con una cara, NO; con pocas fotos no se aplica."""
    from rawflow import seleccion as S

    def fila(n, nit, caras=()):
        return {"foto": f"F{n:04d}.ARW", "t": n * 60.0, "caras": list(caras), "personas": len(caras), "lum": 80,
                "negro": 5, "quemado": 0, "desv": 50, "nit": nit}
    rows = [fila(i, 260 + i) for i in range(40)]
    rows[5]["nit"] = 4                                              # desenfocada del todo, sin caras
    rows[9]["nit"] = 36                                             # blanda pero buena (0,13 de la mediana)
    rows[12]["nit"] = 3
    rows[12]["caras"] = [{"x": 0.4, "y": 0.4, "w": 0.1, "h": 0.15, "ojoI": 0.3, "ojoD": 0.3, "px": 200, "nit": 3}]
    d = S.decide(rows)
    assert set(d) == {"F0005.ARW"} and "desenfocada" in d["F0005.ARW"], d
    assert S.decide(rows[:15]) == {}, "con menos de 20 fotos no hay mediana: no se aplica"


@caso
def fotos_malas_recuperar():
    """Ventana «Fotos malas»: lista lo apartado con su motivo; recuperar pasa la foto (y su .xmp) a «Fotos buenas» y
    desde entonces la Selección IA no la vuelve a apartar."""
    from rawflow import seleccion as S
    raw = fresh("malas_evento") / core.RAW_SUBFOLDER
    raw.mkdir(parents=True)
    for n in ("A.ARW", "B.ARW", "C.ARW"):
        write(raw / n, n.encode() * 100)
    write(raw / "B.xmp", b"<x/>")
    S.apply(raw, {"A.ARW": "toma negra (tapa puesta)", "B.ARW": "repetida: se queda C (la mejor de las dos)"})
    fotos = S.bad_photos(raw)
    assert [(p.name, w[:10]) for p, w in fotos] == [("A.ARW", "toma negra"), ("B.ARW", "repetida: ")], fotos
    assert S.restore(raw, ["B.ARW", "NO.ARW"]) == 1
    good = raw / core.GOOD_SUBFOLDER
    assert (good / "B.ARW").exists() and (good / "B.xmp").exists() and not (raw / core.BAD_SUBFOLDER / "B.ARW").exists()
    assert [p.name for p, _ in S.bad_photos(raw)] == ["A.ARW"]
    assert "B.ARW" in S.kept_restored(raw), "recuperada: nunca se vuelve a apartar"


@caso
def banco_descarte():
    """Las REGLAS con 5 eventos reales guardados (tools/calibrar/banco.py). Si esto cambia, mirar a ojo las fotos que
    cambian y actualizar las cifras a propósito. Boda 14 nov: las únicas «buenas» (según otra IA) que se apartan
    están revisadas (DSC02626 sin flash, DSC02628 ojos cerrados; las demás, repetidas de la misma pose)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "calibrar"))
    import banco
    ref = {"20nov_480": 45, "boda14nov_1604": 71, "jutiapa_597": 103, "xv15nov_376_win": 49, "xv8nov_516_win": 102}
    vistas = {}
    for f in sorted(banco.DATOS.glob("*.jsonl")):
        e = banco.evaluar(banco.cargar(f))
        vistas[f.stem] = e
        assert e["apartadas"] == ref.get(f.stem), (f.stem, e["apartadas"], e["resumen"])
    assert set(vistas) == set(ref), sorted(vistas)
    b = vistas["boda14nov_1604"]["buenas"]
    sueltas = {k for k, v in b.items() if not v.startswith("repetida")}
    assert sueltas == {"DSC02626.ARW", "DSC02628.ARW"} and len(b) == 14, sorted(b)


@caso
def motor_retouch_cadena_igual_a_arams():
    """El paso 4 sin ventana manda al motor EXACTAMENTE la misma cadena que Arams (mismo orden, mismos valores)."""
    from rawflow import retouch_engine as r
    def igual(c):                                       # la ruta del plugin, con «/» en cualquier sistema
        return [dict(x, path=Path(x["path"]).as_posix()) for x in c]
    assert igual(r.build_plugins(_CINEMA, _SETTINGS, _INSTALLED)) == igual(_ARAMS_CHAIN)


@caso
def motor_retouch_lo_que_no_cubre_va_por_arams():
    """Lo que el motor directo no reproduce igual que Arams → EngineUnavailable (y el paso usa la ventana)."""
    import copy
    from rawflow import retouch_engine as r

    def falla(preset, settings=_SETTINGS, installed=_INSTALLED):
        try:
            r.build_plugins(preset, settings, installed)
        except r.EngineUnavailable:
            return True
        return False
    p = copy.deepcopy(_CINEMA)
    p["overrides"] = {"male": []}
    assert falla(p)                                                      # ajustes por género
    p = copy.deepcopy(_CINEMA)
    p["preset"][1]["params"]["mask_skin"] = True
    assert falla(p)                                                      # máscaras
    p = copy.deepcopy(_CINEMA)
    p["preset"][4]["selected"] = True
    assert falla(p)                                                      # plugin que no sabe preparar (Skin Tone)
    assert falla(_CINEMA, dict(_SETTINGS, faceAutoSearchEnabled="1"))    # búsqueda de caras
    assert falla(_CINEMA, installed={k: v for k, v in _INSTALLED.items() if k != "Stray Hairs"})   # falta un plugin
    assert falla(_CINEMA, installed=dict(_INSTALLED, **{"Skin Mask": Path("/x")}))  # máscaras instaladas


@caso
def motor_retouch_intensidad_cero_no_se_manda():
    """Un plugin marcado con intensidad 0 no va en la cadena (como en Arams); el orden es el de Arams."""
    import copy
    from rawflow import retouch_engine as r
    p = copy.deepcopy(_CINEMA)
    p["preset"][7]["params"]["Alpha1"] = 0                               # Stray Hairs a 0
    ids = [x["id"] for x in r.build_plugins(p, _SETTINGS, _INSTALLED)]
    assert ids == ["Heal", "Mattifier", "Dodge Burn", "Portrait Volumes"], ids


@caso
def motor_retouch_reanuda_sin_repetir():
    """Con un motor simulado: solo se retocan las que faltan, cada foto aparece entera (sin temporales) y
    un fallo de una foto hace que el paso se reintente (sin perder las hechas)."""
    import json
    import stat
    from rawflow import retouch_engine as r
    src, dest, bindir = fresh("eng_src"), fresh("eng_dst"), fresh("eng_bin")
    for n in ("A.jpg", "B.jpg", "C.jpg"):
        write(src / n, n.encode() * 60_000)
    write(dest / "A.jpg", b"y" * 200_000)                                 # ya retocada antes
    if sys.platform == "win32":                         # Windows: envoltorio .cmd (CreateProcess no lee «#!»)
        fake = bindir / "motor_falso.cmd"
        fake.write_text(f'@"{sys.executable}" "{bindir / "motor_falso.py"}" %*\n')
    else:                                               # la ruta de Python tiene espacios: envoltorio sh
        fake = bindir / "motor_falso"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bindir / "motor_falso.py"}" "$@"\n')
    (bindir / "motor_falso.py").write_text("""
import json, os, shutil, sys
job = json.load(open(sys.argv[2], encoding="utf-8"))
if "RF_FILE" in os.environ:                             # Windows: los avisos van a un archivo, línea a línea
    out = open(os.environ["RF_FILE"], "a", encoding="utf-8", buffering=1)
else:
    out = os.fdopen(int(os.environ["RF_FD"]), "w", buffering=1)
print("READY 0.1", file=out)
for i, it in enumerate(job["items"]):
    print("START", i, file=out)
    if os.path.basename(it["in"]) == os.environ.get("FALLA", ""):
        print("ERR", i, 19, "fallo de prueba", file=out); continue
    shutil.copy(it["in"], it["tmp"]); os.rename(it["tmp"], it["out"])
    print("OK", i, "0.01", file=out)
print("END 0.2", file=out)
""", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    orig = (r.BIN, r.plan, r.WORK)
    r.BIN, r.plan, r.WORK = fake, (lambda name="CINEMA": _ARAMS_CHAIN), bindir
    try:
        os.environ["FALLA"] = "C.jpg"
        try:
            r.retouch(src, dest, lambda m: None)
            raise AssertionError("tenía que fallar con C.jpg")
        except RuntimeError as e:
            assert "C.jpg" in str(e), e
        assert sorted(p.name for p in dest.iterdir()) == ["A.jpg", "B.jpg"]        # B hecha, sin temporales
        job = json.loads((bindir / "retouch4me-trabajo.json").read_text())
        assert [Path(i["in"]).name for i in job["items"]] == ["B.jpg", "C.jpg"]   # A no se repitió
        os.environ["FALLA"] = ""
        assert r.retouch(src, dest, lambda m: None) == 3                          # reintento: solo C
        job = json.loads((bindir / "retouch4me-trabajo.json").read_text())
        assert [Path(i["in"]).name for i in job["items"]] == ["C.jpg"]
        assert (dest / "B.jpg").read_bytes() == (src / "B.jpg").read_bytes()
    finally:
        r.BIN, r.plan, r.WORK = orig
        os.environ.pop("FALLA", None)


@caso
def bridge_calidad_12_solo_si_lo_dice():
    """Bridge: la calidad solo se deja sin tocar si la casilla dice 12 en la fila de «Máxima» (10 u 11 también son
    «Máxima», y un 12 en otra fila no cuenta)."""
    from rawflow import imagen_ui as u
    lab = {"t": "Máxima", "x": 600, "y": 300, "w": 60, "h": 16}
    assert u.quality_is_12([{"t": "12", "x": 560, "y": 299, "w": 20, "h": 16}], lab)
    assert not u.quality_is_12([{"t": "10", "x": 560, "y": 299, "w": 20, "h": 16}], lab)
    assert not u.quality_is_12([{"t": "12", "x": 560, "y": 340, "w": 20, "h": 16}], lab)      # otra fila
    assert not u.quality_is_12([{"t": "12", "x": 560, "y": 299, "w": 20, "h": 16}], dict(lab, t="Alta"))


# ---------------------------------------------------------------- eventos grandes por tandas
@caso
def tandas_tamano():
    """Imagen sube por tandas de 1/5 del evento (mínimo 30) desde 60 fotos; con menos, todo seguido.
    RAWFLOW_TANDAS=0 las apaga."""
    assert core.tanda_size(59) == 0 and core.tanda_size(60) == 30
    assert core.tanda_size(100) == 30 and core.tanda_size(300) == 60 and core.tanda_size(480) == 96
    os.environ["RAWFLOW_TANDAS"] = "0"
    try:
        assert core.tanda_size(480) == 0
    finally:
        os.environ.pop("RAWFLOW_TANDAS", None)


@caso
def tandas_devolver_a_dngs():
    """Al terminar una tanda, sus DNG (editados por Imagen en su sitio O reescritos) y los .xmp vuelven a «2 DNGs»
    y la carpeta de la tanda desaparece. Nunca queda un DNG sin editar en «2 DNGs»."""
    from rawflow.pipeline import Pipeline
    d = fresh("tanda_merge")
    a, b = write(d / "A-DxO.dng", b"A0"), write(d / "B-DxO.dng", b"B0")
    t = Pipeline._new_tanda(d, [a, b])
    assert sorted(x.name for x in t.iterdir()) == ["A-DxO.dng", "B-DxO.dng"] and os.path.samefile(a, t / a.name)
    with open(t / "A-DxO.dng", "ab") as f:                 # Imagen escribe en el MISMO archivo (enlace)
        f.write(b"+edit")
    (t / "B-DxO.dng").unlink()                              # Imagen REESCRIBE el archivo (otro inodo)
    write(t / "B-DxO.dng", b"B1+edit")
    write(t / "B-DxO.xmp", b"<xmp/>")                       # y deja un .xmp al lado
    write(t / ".rawflow_imagen_ok", b"x")
    Pipeline._merge_tanda(t)
    assert not t.exists()
    assert (d / "A-DxO.dng").read_bytes() == b"A0+edit" and (d / "B-DxO.dng").read_bytes() == b"B1+edit"
    assert (d / "B-DxO.xmp").exists() and not (d / ".rawflow_imagen_ok").exists()


def _evento_tandas(n: int) -> Path:
    ev = fresh(f"tandas_ev_{n}")
    for i in range(n):
        write(ev / core.RAW_SUBFOLDER / f"DSC{1000 + i}.ARW", bytes([i % 250]) * 64)
    return ev


class _Falsos:
    """DxO, Imagen, Bridge y Retouch4me simulados (rápidos), con la hora de cada cosa para comprobar el orden."""

    def __init__(self, falla_edicion=""):
        import threading as th
        from rawflow import imagen_ui, pipeline as pl, retouch_engine as re_
        self.mods = (core, imagen_ui, pl, re_)
        self.th, self.falla_edicion = th, falla_edicion
        self.subidas, self.ediciones, self.bridges, self.archived, self.ret_calls, self.quits = [], [], [], [], [], []
        self.t = {}
        self._orig = []

    def _set(self, obj, name, val):
        self._orig.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def __enter__(self):
        import time as tm
        core_, iu, pl, re_ = self.mods
        stop = self.th.Event()
        self.stop = stop

        def batch(app, raws):                               # «DxO»: un DNG cada 30 ms, en otro hilo
            ev_ = next(q for q in Path(raws[0]).parents if q.name == core.RAW_SUBFOLDER).parent
            out = ev_ / core.OUTPUT_SUBFOLDER

            def go():
                for r in raws:
                    if stop.is_set():
                        return
                    tm.sleep(0.03)
                    write(out / (Path(r).stem + "-DxO_DeepPRIME XD3.dng"), b"DNG" * 100)
                self.t["dxo_fin"] = tm.monotonic()
            self.th.Thread(target=go, daemon=True).start()
            return Path("/nonexistent/batch.txt")
        for name, val in (("is_pureraw", lambda a: True), ("ensure_preset_first", lambda *a, **k: "CINEMA"),
                          ("dxo_extensions", lambda a: {".arw"}), ("kill_pureraw_batches", lambda: None),
                          ("start_pureraw_batch", batch), ("close_pureraw_helper", lambda b: None),
                          ("processed_stems", lambda d: {core.dng_base(q) for q in Path(d).glob("*.dng")}),
                          ("remove_broken_dngs", lambda d: []),
                          ("files_settled", lambda paths, wait=0.6: True)):
            self._set(core_, name, val)

        def start_project(ev, sub, log, should_stop=lambda: False, project="", **k):
            d = Path(ev) / sub
            (d / ".rawflow_imagen_proyecto").write_text(project, encoding="utf-8")
            self.subidas.append((sub, project, len(list(d.glob("*.dng"))), tm.monotonic()))
            return project

        def edit_only(ev, sub, log, should_stop=lambda: False, project="", **k):
            d = Path(ev) / sub
            if (d / ".rawflow_imagen_ok").exists():
                return                                         # ya editada: ni se pide ni se cobra otra vez
            if self.falla_edicion and sub.endswith(self.falla_edicion):
                raise iu.UIError("Imagen se cerró (prueba)")
            if not (d / ".rawflow_imagen_proyecto").exists():
                (d / ".rawflow_imagen_proyecto").write_text(project, encoding="utf-8")
            for q in d.glob("*.dng"):                          # «Imagen» escribe su edición en el DNG
                with open(q, "ab") as f:
                    f.write(b"+imagen " + core.IMAGEN_MARK)   # como Imagen: crs:Temperature en el DNG
            (d / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
            self.ediciones.append((sub, len(list(d.glob("*.dng"))), tm.monotonic()))

        def run(ev, sub, log, should_stop=lambda: False, out_dir=None, progress=lambda f: None, archive=True, **k):
            d = Path(ev) / sub                                 # como el de verdad: Imagen (si falta) y luego Bridge
            if not (d / ".rawflow_imagen_ok").exists():
                for q in d.glob("*.dng"):
                    if not core.has_imagen_edits(q):
                        with open(q, "ab") as fh:
                            fh.write(b"+imagen " + core.IMAGEN_MARK)
                (d / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
                self.ediciones.append((sub, len(list(d.glob("*.dng"))), tm.monotonic()))
            elif core.without_imagen(d.glob("*.dng")):         # marca sin ediciones: el de verdad se niega
                raise iu.UIError(f"DNG sin las ediciones de Imagen en {sub}: no se exportan sin Imagen.")
            assert not core.without_imagen(d.glob("*.dng")), f"Bridge sin ediciones de Imagen en {sub}"
            dn = sorted(d.glob("*.dng"))
            for q in dn:
                write(Path(out_dir) / (core.dng_base(q).upper() + ".jpg"),          # Camera Raw copia los ajustes
                      b"J" * 1000 + (b" " + core.IMAGEN_MARK if core.has_imagen_edits(q) else b""))
            self.bridges.append((sub, len(dn), tm.monotonic()))
            return Path(out_dir)
        from rawflow import seleccion as sel_

        def analizar(paths, rfui, log=None, should_stop=None, progress=None):
            return [{"foto": Path(q).name, "t": i * 20.0, "caras": [], "personas": 1, "lum": 120, "negro": 2,
                     "quemado": 1, "desv": 45, "nit": 400, "estetica": 0.6, "escena": [["people", 0.9]]}
                    for i, q in enumerate(paths)]
        self._set(sel_, "analyze", analizar)
        # Windows: la Selección IA mide con seleccion_win; que use la misma IA simulada (la que ponga cada prueba)
        if sys.platform == "win32":
            from rawflow import seleccion_win as sw
            self._set(sw, "analyze", lambda paths, log=None, should_stop=None, progress=None:
                      sel_.analyze(paths, "", log, should_stop, progress))
            self._set(sw, "rescue", lambda rows, req, path_of: 0)
        self._set(iu, "_rfui", lambda: "/bin/echo")
        self._set(iu, "start_project", start_project)
        self._set(iu, "edit_only", edit_only)
        self._set(iu, "run", run)
        self._set(iu, "archive_project", lambda s, name: self.archived.append(name) or True)
        self._set(iu, "Screen", lambda *a, **k: None)

        def retouch(src, dest, log, should_stop=lambda: False, progress=lambda f: None, expected_total=None, **k):
            Path(dest).mkdir(parents=True, exist_ok=True)
            todo = sorted(p for p in Path(src).glob("*.jpg") if not (Path(dest) / p.name).exists())
            self.ret_calls.append((len(todo), tm.monotonic()))
            for p in todo:
                shutil.copy(p, Path(dest) / p.name)
            return len(list(Path(dest).glob("*.jpg")))
        self._set(re_, "retouch", retouch)
        self._set(re_, "usable", lambda: (True, "prueba"))

        def quit_apps(names, wait=10.0):
            self.quits.append((tuple(names), tm.monotonic()))
        # nada del Mac de verdad: ni cerrar apps ni tocar el Stage Manager (podría haber un evento real en marcha)
        for name, val in (("_engine_ok", lambda: True), ("prelaunch", lambda *a: None), ("quit_apps", quit_apps),
                          ("_running", lambda *a: True), ("quit_other_apps", lambda *a, **k: None),
                          ("stage_manager", lambda on: None), ("restore_stage_manager", lambda: None)):
            self._set(pl, name, val)
        self._set(pl.Pipeline, "_reset_app", lambda self_, i: None)
        self._set(pl.Pipeline, "_dxo_dialog", lambda self_, stop: None)
        self._set(pl.Pipeline, "_present", lambda self_: "skip")
        self._set(pl.Pipeline, "_close_apps", lambda self_: None)
        if sys.platform == "win32":
            self._falsos_windows(run, tm)
        return self

    def _falsos_windows(self, run, tm):
        """Windows: Imagen y Bridge van por imagen_win (no por imagen_ui); los mismos «programas» simulados."""
        from rawflow import imagen_win as iw

        def run_win(ev, sub, log, should_stop=lambda: False, photo_type="", out_dir=None, progress=lambda f: None,
                    charge_ok=True):
            d = Path(ev) / sub                                 # como el de verdad: edita lo que falte (sin marca)
            sin = core.without_imagen(d.glob("*.dng"))
            for q in sin:
                with open(q, "ab") as fh:
                    fh.write(b"+imagen " + core.IMAGEN_MARK)
            if sin:
                self.ediciones.append((sub, len(sin), tm.monotonic()))
            (d / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
            return run(ev, sub, log, should_stop=should_stop, out_dir=out_dir, progress=progress)

        def bridge_only(ev, sub, log, should_stop=lambda: False, out_dir=None, progress=lambda f: None):
            dn = sorted((Path(ev) / sub).glob("*.dng"))
            for q in dn:
                write(Path(out_dir) / (core.dng_base(q).upper() + ".jpg"), b"J" * 1000)
            self.bridges.append((sub, len(dn), tm.monotonic()))
            return Path(out_dir)
        for name, val in (("available", lambda: True), ("bridge_path", lambda: Path("Bridge.exe")),
                          ("run", run_win), ("bridge_only", bridge_only), ("close_app", lambda *a, **k: None)):
            self._set(iw, name, val)

    def __exit__(self, *a):
        self.stop.set()
        for obj, name, val in reversed(self._orig):
            setattr(obj, name, val)

    def quit_time(self, app: str) -> float:
        return min((t for names, t in self.quits if any(app in n for n in names)), default=float("inf"))


def _correr(ev: Path, desde: int):
    from rawflow.pipeline import Pipeline
    p = Pipeline(ev, [], "/Applications/PureRAW 6/DxO PureRAW 6.app", "CINEMA", [], desde)
    p._run_steps()
    return p


def _hay_imagen() -> bool:
    return core.SYSTEM == "Darwin" and Path("/Applications/ImagenAI.app").exists()


@caso
def tandas_flujo_completo():
    """Evento de 100 fotos: mientras DxO trabaja, las tandas de 30 suben a Imagen; al terminar, DxO se CIERRA, Imagen
    descarga, TODO vuelve a «2 DNGs», Bridge exporta de una vez, Imagen y Bridge se cierran y solo entonces Retouch4me
    (nunca dos trabajos de GPU a la vez). Carpetas finales como siempre."""
    if not _hay_imagen():
        return
    from rawflow.pipeline import Pipeline
    ev = _evento_tandas(100)
    with _Falsos() as f:
        p = _correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    k = len(f.subidas)                                         # tandas de 30 exactas; la última, lo que quede
    assert k >= 2 and [x[0] for x in f.subidas] == [f"2 DNGs/Tanda {i}" for i in range(1, k + 1)], f.subidas
    assert [x[1] for x in f.subidas] == [f"Tanda {i} {ev.name}" for i in range(1, k + 1)], f.subidas
    assert all(x[2] == 30 for x in f.subidas[:-1]) and sum(x[2] for x in f.subidas) == 100, f.subidas
    assert f.subidas[0][3] < f.t["dxo_fin"], "la tanda 1 tenía que subir MIENTRAS DxO trabajaba"
    assert f.quit_time("DxO PureRAW") < min(x[2] for x in f.ediciones), "DxO tenía que cerrarse antes de Imagen"
    assert [(x[0], x[1]) for x in f.ediciones] == [(x[0], x[2]) for x in f.subidas], f.ediciones
    assert [(x[0], x[1]) for x in f.bridges] == [("2 DNGs", 100)], f.bridges          # UN Bridge con todo
    assert f.archived == [x[1] for x in f.subidas], f.archived
    assert f.quit_time("ImagenAI") < f.ret_calls[0][1], "Imagen y Bridge tenían que cerrarse antes de Retouch4me"
    assert f.ret_calls[0][1] > f.t["dxo_fin"] and [c[0] for c in f.ret_calls] == [100], f.ret_calls
    dngs = ev / core.OUTPUT_SUBFOLDER
    assert not Pipeline._tanda_dirs(dngs)
    assert len(list(dngs.glob("*.dng"))) == 100 and all(core.has_imagen_edits(q) for q in dngs.glob("*.dng"))
    assert len(core.jpg_stems(dngs.parent / core.IMAGEN_SUBFOLDER)) == 100
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 101))


@caso
def tandas_falla_imagen_y_se_retoma_sin_recobrar():
    """Si Imagen falla con la tanda 2, el evento se para en «Imagen» (paso 3). Al reintentar, la tanda 1 (ya editada)
    no se vuelve a pedir ni cobrar, ni se crean proyectos nuevos; las demás se retoman y todo termina bien."""
    if not _hay_imagen():
        return
    from rawflow.pipeline import Pipeline
    ev = _evento_tandas(100)
    with _Falsos(falla_edicion="Tanda 2") as f:
        p = _correr(ev, 2)
    assert p.summary.get("failed_step") == 3, p.summary
    assert [x[0] for x in f.ediciones] == ["2 DNGs/Tanda 1"], f.ediciones
    dngs = ev / core.OUTPUT_SUBFOLDER
    names = [x[0].split("/")[-1] for x in f.subidas]
    assert [t.name for t in Pipeline._tanda_dirs(dngs)] == names and len(names) >= 2, names
    with _Falsos() as g:
        p2 = _correr(ev, 3)
    assert "failed_step" not in p2.summary, p2.summary
    assert g.subidas == [], g.subidas                                   # ningún proyecto nuevo
    assert [x[0] for x in g.ediciones] == [f"2 DNGs/{n}" for n in names[1:]], g.ediciones
    assert [(x[0], x[1]) for x in g.bridges] == [("2 DNGs", 100)], g.bridges
    assert not Pipeline._tanda_dirs(dngs)
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 101))


@caso
def tandas_cancelar_cierra_dxo():
    """Esc ×3 mientras DxO trabaja y la tanda 2 sube: el evento se cancela, DxO se cierra y se reintenta desde DxO."""
    if not _hay_imagen():
        return
    from rawflow import imagen_ui
    from rawflow.pipeline import Pipeline
    ev = _evento_tandas(100)
    with _Falsos() as f:
        p = Pipeline(ev, [], "/Applications/PureRAW 6/DxO PureRAW 6.app", "CINEMA", [], 2)
        orig = imagen_ui.start_project

        def subir_y_cancelar(ev_, sub, *a, **k):
            if sub.endswith("Tanda 2"):
                p.stop()                                      # el usuario pulsa Esc ×3
                raise imagen_ui.UIError("Cancelado.")
            return orig(ev_, sub, *a, **k)
        imagen_ui.start_project = subir_y_cancelar
        try:
            p._run_steps()
        finally:
            imagen_ui.start_project = orig
    assert p.summary.get("failed_step") == 2, p.summary
    assert f.quit_time("DxO PureRAW") < float("inf"), "DxO tenía que cerrarse"
    assert f.ret_calls == [] and f.bridges == [], (f.ret_calls, f.bridges)


@caso
def tandas_reintento_con_dxo_a_medias():
    """Corte con DxO a medias y una tanda ya subiendo: reintentar desde DxO termina DxO, retoma esa tanda (sin
    crear otro proyecto) y lo que DxO hizo después va en una tanda nueva. Todo termina bien."""
    if not _hay_imagen():
        return
    from rawflow.pipeline import Pipeline
    ev = _evento_tandas(50)
    dngs = ev / core.OUTPUT_SUBFOLDER
    dngs.mkdir()
    for i in range(20):                                       # DxO hizo 20 antes del corte
        write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", b"DNG" * 100)
    t1 = Pipeline._new_tanda(dngs, sorted(dngs.glob("*.dng"))[:14])     # la tanda 1 ya subía a Imagen
    (t1 / ".rawflow_imagen_proyecto").write_text(f"Tanda 1 {ev.name}", encoding="utf-8")
    with _Falsos() as f:
        p = _correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    assert f.subidas == [], f.subidas
    assert [(x[0], x[1]) for x in f.ediciones] == [("2 DNGs/Tanda 1", 14), ("2 DNGs/Tanda 2", 36)], f.ediciones
    assert [(x[0], x[1]) for x in f.bridges] == [("2 DNGs", 50)], f.bridges
    assert not Pipeline._tanda_dirs(dngs) and len(list(dngs.glob("*.dng"))) == 50
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 51))


@caso
def reintento_solo_bridge_con_parte_exportada():
    """Como el evento real: Imagen ya editó todo («2 DNGs» con su marca), Bridge exportó 12 y se cortó. Reintentar:
    solo las que faltan van a Bridge (sin volver a Imagen ni crear proyectos) y nada se duplica."""
    if not _hay_imagen():
        return
    from rawflow.pipeline import Pipeline
    ev = _evento_tandas(40)
    dngs = ev / core.OUTPUT_SUBFOLDER
    for i in range(40):
        write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", b"DNG+imagen " + core.IMAGEN_MARK)
    (dngs / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
    for i in range(12):
        write(dngs.parent / core.IMAGEN_SUBFOLDER / f"DSC{1000 + i}.jpg", b"J" * 1000 + b" " + core.IMAGEN_MARK)
    with _Falsos() as f:
        p = _correr(ev, 3)
    assert "failed_step" not in p.summary, p.summary
    assert f.ediciones == [] and f.subidas == [], (f.ediciones, f.subidas)
    assert [(x[0], x[1]) for x in f.bridges] == [("2 DNGs/Tanda 1", 28)], f.bridges
    assert len(core.jpg_stems(dngs.parent / core.IMAGEN_SUBFOLDER)) == 40 and not Pipeline._tanda_dirs(dngs)
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 41))


@caso
def sin_tandas_evento_pequeno_igual_que_siempre():
    """Con pocas fotos no hay tandas: todo seguido como antes, y cada programa se cierra al acabar su paso."""
    if not _hay_imagen():
        return
    ev = _evento_tandas(8)
    with _Falsos() as f:
        (ev / core.OUTPUT_SUBFOLDER).mkdir(exist_ok=True)
        (ev / core.OUTPUT_SUBFOLDER / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")   # Imagen ya editó
        p = _correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    assert f.subidas == [] and [(x[0], x[1]) for x in f.bridges] == [("2 DNGs", 8)], (f.subidas, f.bridges)
    assert f.quit_time("DxO PureRAW") < f.bridges[0][2] and f.quit_time("ImagenAI") < f.ret_calls[0][1]
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 9))


@caso
def tiempos_se_aprenden_con_lo_que_falta():
    """Al reanudar (como el evento real: DxO hizo 413 de 480 antes del corte), el tiempo de DxO se aprende con las
    fotos que hizo ESTA vez, no con el total del evento (si no, la siguiente estimación sale 7 veces corta). Igual
    Retouch4me con las que ya estaban retocadas."""
    if not _hay_imagen():
        return
    ev = _evento_tandas(50)
    dngs = ev / core.OUTPUT_SUBFOLDER
    for i in range(20):                                       # DxO hizo 20 antes del corte
        write(dngs / f"DSC{1000 + i}-DxO_DeepPRIME XD3.dng", b"DNG" * 100)
    (dngs / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
    for i in range(5):                                        # y Retouch4me, 5 (con Imagen, como las de verdad)
        write(ev / core.FINAL_SUBFOLDER / f"DSC{1000 + i}.jpg", b"F" * 1000 + b" " + core.IMAGEN_MARK)
    with _Falsos() as f:
        p = _correr(ev, 2)
    assert "failed_step" not in p.summary, p.summary
    assert p.work[2] == (30, 30 * 64), p.work                 # 30 RAW de 64 bytes
    assert p.work[4][0] == 45, p.work
    assert [c[0] for c in f.ret_calls] == [45], f.ret_calls
    assert sorted(int(q.stem) for q in (ev / core.FINAL_SUBFOLDER).glob("*.jpg")) == list(range(1, 51))


@caso
def opcion_marcada_se_lee_con_vineta():
    """Imagen recuerda «Carpeta» y la marca: el OCR lee «• Carpeta». La búsqueda exacta la tiene que encontrar
    (por eso se paró el evento real en «Nuevo proyecto»), pero sin confundir textos más largos."""
    from rawflow import imagen_ui as u
    s = u.Screen.__new__(u.Screen)
    boxes = [{"t": "• Carpeta", "x": 459, "y": 234, "w": 60, "h": 14},
             {"t": "Elige una carpeta", "x": 558, "y": 368, "w": 120, "h": 14}]
    assert [b["t"] for b in s.find("Carpeta", exact=True, boxes=boxes)] == ["• Carpeta"]
    assert s.find("Elige", exact=True, boxes=boxes) == []


@caso
def dxo_aviso_modulos_opticos():
    """DxO parado en «Módulos ópticos DxO» (equipo nuevo): se pulsa «Guardar» (descargar el módulo que propone
    PureRAW). Cajas = lo que leyó el OCR del aviso real con la Nikon Z6 II + 28-75. Sin aviso, no se toca nada."""
    from rawflow import imagen_ui as u
    real = [{"t": "DxO PureRAW 6", "x": 40, "y": 2, "w": 90, "h": 12},
            {"t": "Módulos ópticos DxO", "x": 342, "y": 231, "w": 150, "h": 20},
            {"t": "Para aplicar correcciones ópticas personalizadas específicas de sus equipos, se necesitan",
             "x": 342, "y": 268, "w": 500, "h": 12},
            {"t": "• Nikon Z6 II + Nikkor Z 28-75mm F2.8 (RAW)", "x": 350, "y": 369, "w": 300, "h": 14},
            {"t": "Para descargar", "x": 792, "y": 369, "w": 90, "h": 14},
            {"t": "Cancelar", "x": 760, "y": 530, "w": 40, "h": 14},
            {"t": "Guardar", "x": 852, "y": 529, "w": 44, "h": 14}]
    clicks, screens = [], {"boxes": real}

    class FakeScreen:
        def __init__(self, *a, **k):
            pass

        def ocr(self):
            return screens["boxes"]

        def click(self, x, y, n=1):
            clicks.append((round(x), round(y)))
    orig = (u.Screen, u.check_permissions, u.subprocess.run)
    u.Screen, u.check_permissions = FakeScreen, (lambda: "")
    u.subprocess.run = lambda *a, **k: None
    try:
        assert u.dxo_modules_dialog(lambda m: None) == "Nikon Z6 II + Nikkor Z 28-75mm F2.8 (RAW)"
        assert clicks == [(874, 536)], clicks                    # el centro de «Guardar»
        screens["boxes"] = [b for b in real if "dulos" not in b["t"]]
        assert u.dxo_modules_dialog(lambda m: None) is None and len(clicks) == 1
    finally:
        u.Screen, u.check_permissions, u.subprocess.run = orig


def _raw_falso(path: Path, bgr, fecha: str = "2026:11:15 18:30:05", subseg: str = "37", orient: int = 6) -> Path:
    """Un «RAW» mínimo con la estructura de un NEF: TIFF con su JPEG incrustado en un sub-IFD, la orientación y los
    datos de la toma (hora con centésimas, f/2.8, 1/200 s, ISO 400)."""
    import struct
    import cv2
    jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
    fecha_b = fecha.encode() + b"\0"

    def ifd(entries, nxt=0):
        return struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", nxt)

    def e(tag, typ, count, value):
        return struct.pack("<HHI", tag, typ, count) + (value if isinstance(value, bytes) else struct.pack("<I", value))
    o_ifd0 = 8
    o_sub = o_ifd0 + 2 + 3 * 12 + 4
    o_exif = o_sub + 2 + 2 * 12 + 4
    o_data = o_exif + 2 + 5 * 12 + 4
    o_vel, o_f, o_fecha = o_data, o_data + 8, o_data + 16
    o_jpg = o_fecha + len(fecha_b)
    body = b"II*\0" + struct.pack("<I", o_ifd0)
    body += ifd([e(0x0112, 3, 1, struct.pack("<HH", orient, 0)), e(0x014A, 4, 1, o_sub), e(0x8769, 4, 1, o_exif)])
    body += ifd([e(0x0201, 4, 1, o_jpg), e(0x0202, 4, 1, len(jpg))])
    body += ifd([e(0x829A, 5, 1, o_vel), e(0x829D, 5, 1, o_f), e(0x8827, 3, 1, struct.pack("<HH", 400, 0)),
                 e(0x9003, 2, len(fecha_b), o_fecha), e(0x9291, 2, len(subseg) + 1, (subseg.encode() + b"\0\0\0\0")[:4])])
    body += struct.pack("<II", 1, 200) + struct.pack("<II", 28, 10) + fecha_b + jpg
    return write(path, body)


@caso
def seleccion_ia_en_windows():
    """Motor de Windows de la Selección IA (rawflow/seleccion_win.py, con OpenCV): lee de un RAW su vista previa ya
    derecha, la hora con centésimas y la exposición; da las medidas en la escala de Vision; y con dos tomas iguales
    seguidas, las reglas de siempre apartan una como repetida. (Sin OpenCV —en el Mac— no se usa: se salta.)"""
    import math
    from rawflow import seleccion as S, seleccion_win as W
    if not W.available()[0]:
        return
    import cv2
    import numpy as np
    d = fresh("sel_win")
    y, x = np.mgrid[0:200, 0:300]
    img = np.dstack([(x * 255 // 300), (y * 255 // 200), ((x + y) % 256)]).astype(np.uint8)
    cv2.circle(img, (150, 100), 50, (255, 255, 255), -1)
    a = _raw_falso(d / "DSC_0001.NEF", img)
    big, info = W.read_raw(a)
    assert big is not None and big.shape[:2] == (300, 200), None if big is None else big.shape    # girada (orientación 6)
    assert abs(info["t"] % 1 - 0.37) < 1e-6, info                                              # centésimas de la hora
    assert abs(info["ev"] - (math.log2(2.8 * 2.8 * 200) - 2)) < 1e-6, info                       # f/2.8, 1/200 s, ISO 400
    assert W.read_raw(write(d / "basura.NEF", b"esto no es una foto" * 50))[0] is None
    # escala de Vision: sin saltos, y los umbrales de las reglas caen donde se calibró
    assert abs(W._to_vision(0.37, 0.0) - 0.16) < 1e-9 and W._to_vision(0.0, 0.0) == 0.0
    vals = [W._to_vision(k / 100, 0.0) for k in range(0, 160)]
    assert all(q >= p for p, q in zip(vals, vals[1:])), "el parecido tiene que crecer siempre"
    assert W._to_vision(0.2, 0.3) > W._to_vision(0.2, 0.0)                # otra composición = más distintas
    elipse = [(100 * math.cos(t), 30 * math.sin(t)) for t in (k * math.pi / 8 for k in range(16))]
    girada = [(px * math.cos(0.5) - py * math.sin(0.5), px * math.sin(0.5) + py * math.cos(0.5)) for px, py in elipse]
    assert abs(W._eye_ratio(elipse) - 0.3) < 0.01 and abs(W._eye_ratio(girada) - 0.3) < 0.01      # cabeza ladeada: igual
    st = W.region_stats(np.zeros((40, 60), np.uint8))
    assert st["lum"] == 0 and st["negro"] == 100 and st["nit"] == 0, st
    # dos tomas iguales a 1 s y otra distinta 2 min después → una repetida apartada; la distinta y la negra aparte
    _raw_falso(d / "DSC_0002.NEF", img, fecha="2026:11:15 18:30:06")
    otra = cv2.GaussianBlur(np.random.default_rng(7).integers(0, 255, (200, 300, 3), dtype=np.uint8), (0, 0), 9)
    _raw_falso(d / "DSC_0003.NEF", otra, fecha="2026:11:15 18:32:10")
    _raw_falso(d / "DSC_0004.NEF", np.zeros((200, 300, 3), np.uint8), fecha="2026:11:15 18:40:00")
    rows = W.analyze([str(d / f"DSC_000{k}.NEF") for k in (1, 2, 3, 4)])
    assert [r.get("error") for r in rows] == [None] * 4 and rows[1]["dist_prev"] < 0.05 < rows[2]["dist_prev"], rows
    assert all(k in rows[0] for k in ("foto", "t", "ev", "lum", "negro", "quemado", "desv", "nit", "caras", "personas"))
    dec = S.decide(rows)
    assert sorted(dec) == ["DSC_0002.NEF", "DSC_0004.NEF"] or sorted(dec) == ["DSC_0001.NEF", "DSC_0004.NEF"], dec
    assert any(v.startswith("repetida") for v in dec.values()) and "negra" in dec["DSC_0004.NEF"], dec
    assert S.engine() == ("win" if core.SYSTEM == "Windows" else S.engine())


# ---------------------------------------------------------------- ejecutar
def main() -> int:
    ok = 0
    for fn in RESULTS:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            ok += 1
        except Exception:  # noqa: BLE001
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{ok} de {len(RESULTS)} pruebas bien")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
