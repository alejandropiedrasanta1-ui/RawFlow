"""Lo que está pasando AHORA, para la pantalla de proceso.

Los hilos del proceso escriben aquí (qué programa trabaja, a qué ritmo, cuánto le falta a su paso según la fase en
que va) y la pantalla lo lee 1-15 veces por segundo. Sin señales de Qt entre hilos: un diccionario con candado.

  set(carril, texto)      «DxO PureRAW · 214 de 480 · 11,2 s/foto»  ("" lo quita)
  set_eta(paso, segundos) lo que le falta al paso, calculado por el propio paso con su ritmo real
  set_metrics(dict)       CPU, GPU, memoria, red, procesos (lo escribe metrics.Sampler)
  check(paso, estado, t)  lo COMPROBADO al acabar cada paso («597 de 597 con Imagen · una a una»);
                          estado: "ok" | "warn" | "off" (paso 5 = revisión final). Se ve en el panel COMPROBADO.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

_lock = threading.Lock()
_lanes: Dict[str, Tuple[str, float]] = {}
_eta: Dict[int, Tuple[float, float]] = {}
_metrics: Dict = {}
_checks: Dict[int, Tuple[str, str, float]] = {}

ORDER = ("mover", "ia", "dxo", "subida", "imagen", "bridge", "ret", "pres")
NAMES = {"mover": "Memoria", "ia": "Selección IA", "dxo": "DxO PureRAW", "subida": "Subida a Imagen", "imagen": "Imagen AI",
         "bridge": "Adobe Bridge", "ret": "Retouch4me", "pres": "CineSlide"}


def set(lane: str, text: str):  # noqa: A001
    with _lock:
        if text:
            _lanes[lane] = (text, time.monotonic())
        else:
            _lanes.pop(lane, None)


def clear(*lanes: str):
    with _lock:
        for k in (lanes or tuple(_lanes)):
            _lanes.pop(k, None)


def lanes(max_age: float = 180.0) -> List[Tuple[str, str]]:
    """[(carril, texto)] en orden del proceso; lo que lleva mucho sin actualizarse se descarta."""
    now = time.monotonic()
    with _lock:
        items = [(k, t) for k, (t, ts) in _lanes.items() if now - ts <= max_age]
    return sorted(items, key=lambda kv: ORDER.index(kv[0]) if kv[0] in ORDER else 99)


def set_eta(step: int, seconds: float):
    with _lock:
        _eta[step] = (max(0.0, float(seconds)), time.monotonic())


def eta(step: int, max_age: float = 90.0) -> Optional[float]:
    """Lo que le falta ahora al paso según él mismo (descontando lo que pasó desde que lo dijo), o None."""
    with _lock:
        v = _eta.get(step)
    if not v:
        return None
    sec, ts = v
    age = time.monotonic() - ts
    if age > max_age:
        return None
    return max(0.0, sec - age)


def set_metrics(m: Dict):
    with _lock:
        _metrics.clear()
        _metrics.update(m)


def metrics() -> Dict:
    with _lock:
        return dict(_metrics)


def reset():
    with _lock:
        _lanes.clear()
        _eta.clear()
        _metrics.clear()


def check(step: int, state: str, text: str):
    """Lo comprobado de un paso (se queda hasta clear_checks(): también se ve en la pantalla final)."""
    with _lock:
        old = _checks.get(step)
        ts = old[2] if old and old[0] == state else time.monotonic()      # la animación solo si cambia
        _checks[step] = (state, text, ts)


def checks() -> Dict[int, Tuple[str, str, float]]:
    with _lock:
        return dict(_checks)


def clear_checks():
    with _lock:
        _checks.clear()
