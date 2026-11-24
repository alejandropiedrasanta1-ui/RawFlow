"""Abre RawFlow sola al conectar una memoria con fotos RAW (solo Mac).

Instala un agente de macOS (LaunchAgent «com.rawflow.usb») con StartOnMount: cada vez que se monta un
disco, macOS ejecuta un script muy corto que mira las memorias externas y, si alguna tiene RAW,
abre RawFlow. Las imágenes de disco (.dmg/.iso) y el disco del Mac se ignoran.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import core

LABEL = "com.rawflow.usb"
AGENT = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
DIR = Path.home() / "Library/Application Support/RawFlow"
SCRIPT = DIR / "usb_check.sh"


def _app() -> str:
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return str(p)
    return "/Applications/RawFlow.app"


def _script() -> str:
    exts = "|".join(sorted(e.lstrip(".") for e in core.RAW_EXTENSIONS))
    return f'''#!/bin/bash
# RawFlow: al montarse un disco, abre RawFlow si una memoria externa tiene fotos RAW.
sleep 2
for v in /Volumes/*; do
  [ -d "$v" ] || continue
  [ "$(readlink "$v")" = "/" ] && continue
  info=$(diskutil info "$v" 2>/dev/null)
  echo "$info" | grep -q "Protocol: *Disk Image" && continue
  echo "$info" | grep -qE "Internal: *Yes" && ! echo "$info" | grep -qE "Protocol: *(USB|Secure Digital|SD)" && continue
  if find "$v" -maxdepth 5 \\( -name ".*" -prune \\) -o -type f -print 2>/dev/null \\
     | grep -m1 -iE "\\.({exts})$" >/dev/null; then
    open -a "{_app()}"
    exit 0
  fi
done
'''


def installed() -> bool:
    return AGENT.exists() and SCRIPT.exists()


def install() -> bool:
    """Crea o actualiza el agente. Devuelve True si quedó activo."""
    if core.SYSTEM != "Darwin":
        return False
    DIR.mkdir(parents=True, exist_ok=True)
    new = _script()
    if not SCRIPT.exists() or SCRIPT.read_text() != new:
        SCRIPT.write_text(new)
        os.chmod(SCRIPT, 0o755)
    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>{SCRIPT}</string></array>
  <key>StartOnMount</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
'''
    changed = not AGENT.exists() or AGENT.read_text() != plist
    if changed:
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        AGENT.write_text(plist)
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(AGENT)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(AGENT)], capture_output=True)
    return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True).returncode == 0
