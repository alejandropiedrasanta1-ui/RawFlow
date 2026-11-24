"""Mediciones del Mac para la pantalla de proceso: CPU, GPU, memoria, red (MB/s de subida) y qué programas están
trabajando y cuánto usan. Un hilo las toma cada 2 s (la GPU cada 4 s y la batería cada 20 s) y las deja en live.
Gasta muy poco (~20 ms por vuelta) para no quitarle nada al proceso."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Dict, Optional

from . import core, live

# (etiqueta en pantalla, palabras del nombre o la ruta del proceso). Incluye sus procesos auxiliares (Helper…).
PROGRAMS = (
    ("DxO PureRAW", ("pureraw",)),
    ("Imagen AI", ("imagenai", "imagen ai")),
    ("Adobe Bridge", ("adobe bridge", "bridge.app")),
    ("Retouch4me", ("retouch4me",)),
    ("CineSlide", ("application support/cineslide",)),
    ("RawFlow", ("rawflow",)),
    ("Spotlight", ("mdworker", "mds_stores")),
)


def gpu_percent() -> Optional[int]:
    """Uso de la tarjeta gráfica (0-100) que publica macOS; None si no se puede leer."""
    if core.SYSTEM != "Darwin":
        return None
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    return int(m.group(1)) if m else None


class Sampler(threading.Thread):
    def __init__(self, every: float = 2.0):
        super().__init__(daemon=True)
        self.every = every
        self._stop = threading.Event()
        self._procs: Dict[int, object] = {}        # pid → psutil.Process (para medir su CPU entre vueltas)
        self._label: Dict[int, Optional[str]] = {}

    def stop(self):
        self._stop.set()

    def _label_of(self, p) -> Optional[str]:
        pid = p.pid
        if pid in self._label:
            return self._label[pid]
        lab = None
        try:
            name = (p.info.get("name") or "").lower()
            text = name
            if not any(w in name for _, ws in PROGRAMS for w in ws):
                try:
                    text += " " + (p.exe() or "").lower()
                except Exception:  # noqa: BLE001
                    pass
            for label, words in PROGRAMS:
                if any(w in text for w in words):
                    lab = label
                    break
        except Exception:  # noqa: BLE001
            pass
        self._label[pid] = lab
        return lab

    def run(self):
        try:
            import psutil
        except Exception:  # noqa: BLE001
            return
        psutil.cpu_percent(None)
        net0 = psutil.net_io_counters()
        t_net = time.monotonic()
        gpu, t_gpu, power, t_pow = None, 0.0, (True, None, None), 0.0
        while not self._stop.wait(self.every):
            try:
                now = time.monotonic()
                cpu = psutil.cpu_percent(None)
                vm = psutil.virtual_memory()
                net = psutil.net_io_counters()
                dt = max(0.1, now - t_net)
                up = (net.bytes_sent - net0.bytes_sent) / dt / 1e6
                down = (net.bytes_recv - net0.bytes_recv) / dt / 1e6
                net0, t_net = net, now
                if now - t_gpu >= 4:
                    gpu, t_gpu = gpu_percent(), now
                if now - t_pow >= 20:
                    power, t_pow = core.power_state(), now
                per: Dict[str, float] = {}
                seen = set()
                for p in psutil.process_iter(["name"]):
                    lab = self._label_of(p)
                    if not lab:
                        continue
                    seen.add(p.pid)
                    q = self._procs.get(p.pid)
                    if q is None:
                        self._procs[p.pid] = p
                        try:
                            p.cpu_percent(None)            # primera vez: solo arranca la medida
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    try:
                        per[lab] = per.get(lab, 0.0) + q.cpu_percent(None)
                    except Exception:  # noqa: BLE001
                        pass
                for pid in [k for k in self._procs if k not in seen]:
                    self._procs.pop(pid, None)
                    self._label.pop(pid, None)
                procs = sorted(((k, v) for k, v in per.items() if v >= 1.0), key=lambda kv: -kv[1])
                live.set_metrics({"cpu": cpu, "gpu": gpu, "mem_used": vm.used / 1e9, "mem_total": vm.total / 1e9,
                                  "up": max(0.0, up), "down": max(0.0, down), "procs": procs, "power": power,
                                  "ncpu": psutil.cpu_count() or 1})
            except Exception:  # noqa: BLE001
                pass
