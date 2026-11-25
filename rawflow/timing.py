"""Tiempos de cada paso, con datos REALES medidos en este Mac.

Cada vez que un paso termina se guarda en «tiempos.csv» (fecha, fase, fotos, bytes, segundos).
Para estimar, cada fase se ajusta a   tiempo = parte fija + segundos por foto × fotos
con mínimos cuadrados sobre sus últimas mediciones (si hay eventos de distinto tamaño, separa bien lo fijo
de lo que depende de cada foto). Sin datos todavía, usa las mediciones de las pruebas de RawFlow.

Fases:  move (por bytes) · dxo · imagen (nube) · bridge · ret (Retouch4me) · pres (CineSlide)
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Dict, List, Tuple

HIST = Path.home() / "Library/Application Support/RawFlow/tiempos.csv"

# Medido en las pruebas (MacBook Air M5, 2 fotos Sony ARW de 24 MP / 50 MB) — punto de partida.
SEED: Dict[str, Tuple[float, float]] = {        # fase: (segundos fijos, segundos por foto)
    "dxo": (6.0, 11.5),
    "imagen": (200.0, 2.0),                      # crear proyecto + subir + editar en la nube + descargar
    "bridge": (22.0, 3.0),                       # abrir Bridge + exportar JPEG calidad 12
    "ret": (3.0, 58.0),                          # Retouch4me por motor directo (CINEMA: Stray Hairs ~45 s)
    "lum": (60.0, 1.5),                          # Luminar Neo: abrir + catálogo + preajuste + exportar (se aprende)
    "pres": (7.0, 2.5),                          # CineSlide tema 4
}
MOVE_BPS = 90e6                                   # lectura típica de tarjeta por USB (se aprende)
UPLOAD_BPS = 15e6                                 # subida a Imagen (se aprende de cada subida real)
DNG_PER_RAW = 1.75                                # un DNG de DxO pesa ~1,75× el RAW


def record(phase: str, n: int, nbytes: int, seconds: float):
    """Guarda una medición real."""
    if seconds <= 0.5 or n <= 0:
        return
    try:
        HIST.parent.mkdir(parents=True, exist_ok=True)
        new = not HIST.exists()
        with open(HIST, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["fecha", "fase", "fotos", "bytes", "segundos"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), phase, n, nbytes, round(seconds, 2)])
    except OSError:
        pass


def _history() -> Dict[str, List[Tuple[int, int, float]]]:
    out: Dict[str, List[Tuple[int, int, float]]] = {}
    try:
        with open(HIST, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    out.setdefault(r["fase"], []).append((int(r["fotos"]), int(r["bytes"]), float(r["segundos"])))
                except (KeyError, ValueError):
                    pass
    except OSError:
        pass
    return out


def model(phase: str, hist=None) -> Tuple[float, float, int]:
    """(segundos fijos, segundos por foto, nº de mediciones usadas)."""
    keep = 6 if phase == "ret" else 30               # Retouch4me: el Mac (sin ventilador) se calienta → lo reciente
    pts = [(n, s) for n, _, s in (hist if hist is not None else _history()).get(phase, [])][-keep:]
    fixed0, spp0 = SEED[phase]
    if not pts:
        return fixed0, spp0, 0
    ns = {n for n, _ in pts}
    if len(ns) >= 2 and max(ns) >= 3 * min(ns):       # tamaños de evento bien distintos: fijo + por foto
        k = len(pts)
        mx = sum(n for n, _ in pts) / k
        my = sum(s for _, s in pts) / k
        sxx = sum((n - mx) ** 2 for n, _ in pts)
        spp = sum((n - mx) * (s - my) for n, s in pts) / sxx if sxx else spp0
        spp = max(0.05, spp)
        fixed = max(0.0, my - spp * mx)
        return fixed, spp, k
    # tamaños parecidos: se mantiene el por-foto conocido y se corrige la parte fija (o al revés si son muchas)
    n = sum(n for n, _ in pts) / len(pts)
    avg = sum(s for _, s in pts) / len(pts)
    if n >= 20:
        spp = max(0.05, (avg - fixed0) / n)
        return fixed0, spp, len(pts)
    fixed = max(0.0, avg - spp0 * n)
    return fixed, spp0, len(pts)


def _sel_time(n: int) -> float:
    """Selección IA: Mac ≈0,07 s por foto (vista previa del RAW con Vision); Windows ≈0,17 s (OpenCV), medido con NEF."""
    import sys
    return 2.0 + 0.08 * n if sys.platform == "darwin" else 4.0 + 0.2 * n


def _tanda1(n: int) -> int:
    """Fotos por tanda si un evento de n fotos sube a Imagen por tandas en este Mac (ver core.tanda_size)."""
    try:
        import sys
        from . import core
        if sys.platform != "darwin" or not Path("/Applications/ImagenAI.app").exists():
            return 0
        return core.tanda_size(n)
    except Exception:  # noqa: BLE001
        return 0


def upload_bps(hist=None) -> float:
    # (b > 0: una subida anotada con 0 bytes daba velocidad 0 y la estimación fallaba al dividir entre ella)
    pts = [(b, s) for _, b, s in (hist if hist is not None else _history()).get("subida", []) if s > 2 and b > 0][-10:]
    return sum(b for b, _ in pts) / sum(s for _, s in pts) if pts else UPLOAD_BPS


def _move_bps(hist) -> float:
    pts = [(b, s) for _, b, s in hist.get("move", []) if b > 200e6][-10:]
    if not pts:
        return MOVE_BPS
    return sum(b for b, _ in pts) / sum(s for _, s in pts)


class Estimator:
    """Interfaz que usa la app: estimate() por paso y learn() al terminar cada paso."""

    def __init__(self, settings=None):
        self.s = settings

    def estimate(self, n: int, nbytes: int) -> List[float]:
        if n <= 0:
            return [0, 0, 0, 0, 0, 0, 0]
        h = _history()

        def p(ph):
            f, spp, _ = model(ph, h)
            if ph == "ret" and n > 30:                 # muchas fotos = horas de GPU: ritmo sostenido (el más lento)
                paces = [(sec - f) / k for k, _, sec in h.get("ret", [])[-6:] if k > 0]
                paces = sorted(paces)
                spp = max(spp, paces[len(paces) // 2]) if paces else spp    # mediana: sin extremos
            return f + spp * n
        # Imagen: lo medido (crear proyecto + editar + descargar) + la SUBIDA de los DNG a la velocidad real
        dng_bytes = nbytes * DNG_PER_RAW
        small = sum(b for _, b, _ in h.get("imagen", [])[-5:]) / max(1, len(h.get("imagen", [])[-5:]))
        upload = max(0.0, dng_bytes - small) / upload_bps(h)
        sel = _sel_time(n)                               # Selección IA: por foto (vista previa del RAW)
        est = [max(2.0, nbytes / _move_bps(h)), sel, p("dxo"), p("imagen") + upload + p("bridge"), p("ret"), p("lum"),
               p("pres")]
        k = _tanda1(n)
        if k:       # por tandas: la subida a Imagen va mientras DxO trabaja; después solo la última tanda + Bridge
            f_img, spp_img, _ = model("imagen", h)
            est[3] = f_img + spp_img * k + upload * (k / n) + p("bridge")
        return est

    def estimate_event(self, ev, n: int, nbytes: int, start: int = 0) -> List[float]:
        """Como estimate(), pero solo con lo que de verdad FALTA en esa carpeta de evento: pasos ya hechos = 0,
        DxO solo los DNG que faltan, Imagen solo Bridge si ya editó, Retouch4me solo las que faltan…"""
        from pathlib import Path
        est = self.estimate(n, nbytes)
        if n <= 0:
            return est
        h = _history()

        def p(ph, k):
            f, spp, _ = model(ph, h)
            return 0.0 if k <= 0 else f + spp * k
        ev = Path(ev) if ev else None
        if ev is None or not ev.is_dir():
            return [0.0 if i < start else e for i, e in enumerate(est)]
        try:
            from . import core
            dngs = ev / core.OUTPUT_SUBFOLDER
            done_dng = len(core.processed_stems(dngs))
            pend_dng = max(0, n - done_dng)
            core.migrate_event_layout(ev)
            imgs = ev / core.IMAGEN_SUBFOLDER
            n_img = len(list(imgs.glob("*.jp*g"))) if imgs.is_dir() else 0
            fin = ev / core.FINAL_SUBFOLDER
            n_fin = len([q for q in fin.glob("*.jp*g")]) if fin.is_dir() else 0
            pres = ev / core.PRES_SUBFOLDER
            has_pres = pres.is_dir() and any(pres.glob("*.mp4"))
            lum = ev / core.LUMINAR_SUBFOLDER
            n_lum = len([q for q in lum.glob("*.jp*g")]) if lum.is_dir() else 0
            if done_dng:                                       # sin DNG aún: lo de estimate() (tandas incluidas)
                est[2] = p("dxo", pend_dng)
                est[1] = _sel_time(pend_dng)                  # la Selección IA solo mira las que aún no tienen DNG
            if n_img >= n:
                est[3] = 0.0
            elif (dngs / ".rawflow_imagen_ok").exists():
                est[3] = p("bridge", n)                       # Imagen ya editó: solo falta Bridge
            est[4] = p("ret", max(0, n - n_fin))
            est[5] = 0.0 if n_fin and n_lum >= n_fin else est[5]
            est[6] = 0.0 if has_pres else est[6]
        except Exception:  # noqa: BLE001
            pass
        return [0.0 if i < start else e for i, e in enumerate(est)]

    def learn(self, step: int, seconds: float, n: int, nbytes: int):
        """Paso terminado con trabajo real. El paso 3 lo guarda imagen_ui por partes (nube / Bridge)."""
        phase = {0: "move", 2: "dxo", 4: "ret", 5: "lum", 6: "pres"}.get(step)   # STEP_MOVE, _DXO, _RET, _LUM, _PRES
        if phase and (phase == "move" or seconds > 3):      # «ya estaba hecho» no cuenta
            record(phase, n, nbytes, seconds)

    def explain(self, n: int) -> str:
        """Texto corto: en qué se basa la estimación."""
        h = _history()
        k = sum(model(ph, h)[2] for ph in SEED)
        return f"según {k} mediciones reales" if k else "según las pruebas de RawFlow"


def fmt(sec: float) -> str:
    sec = int(round(max(0, sec)))
    if sec < 60:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}:{s:02d}"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def fmt_est(sec: float) -> str:
    """Estimaciones y lo que falta, en palabras («25 min», «1 h 05 min»): «25:10» se leía como una hora del día."""
    sec = max(0.0, float(sec))
    if sec < 60:
        return "menos de 1 min"
    m = int(round(sec / 60.0))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def approx(sec: float) -> str:
    """«≈ 25 min» (o «menos de 1 min», sin el ≈ delante)."""
    return fmt_est(sec) if sec < 60 else f"≈ {fmt_est(sec)}"


_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def end_phrase(left: float, now: float = None) -> str:
    """A qué hora termina: «≈ 17:48», «mañana ≈ 10:33» o «el jue ≈ 10:33». Sin «mañana», un evento de 13 h
    empezado por la noche parecía acabar a las 10:33 de HOY."""
    import datetime as _dt
    t0 = _dt.datetime.fromtimestamp(time.time() if now is None else now)
    end = t0 + _dt.timedelta(seconds=max(0.0, float(left)))
    days = (end.date() - t0.date()).days
    hhmm = end.strftime("%H:%M")
    if days <= 0:
        return f"≈ {hhmm}"
    if days == 1:
        return f"mañana ≈ {hhmm}"
    return f"el {_DIAS[end.weekday()]} ≈ {hhmm}"
