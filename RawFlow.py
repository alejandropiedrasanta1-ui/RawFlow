import sys

if __name__ == "__main__":
    if "--bloqueo" in sys.argv:                 # Windows (RawFlow.exe): el bloqueo de ratón y teclado va aparte
        from rawflow.winlock import main as bloqueo
        sys.exit(bloqueo())
    if "--programas" in sys.argv:               # qué programas ve RawFlow en este equipo (JSON a un archivo)
        import json
        from rawflow import programas
        i = sys.argv.index("--programas")
        txt = json.dumps(programas.found(), ensure_ascii=False)
        if len(sys.argv) > i + 1:
            open(sys.argv[i + 1], "w", encoding="utf-8").write(txt)
        else:
            print(txt)
        sys.exit(0)
    if "--ocr" in sys.argv:                     # Windows: ¿RawFlow puede leer la pantalla? (nº de rótulos a un archivo)
        from rawflow import winscreen
        i = sys.argv.index("--ocr")
        try:
            winscreen.dpi_aware()
            x, y, w, h = winscreen.virtual_rect()
            txt = f"OK {len(winscreen.ocr((0, 0, min(w, 1600), min(h, 900))))} rotulos"
        except Exception as e:  # noqa: BLE001
            txt = f"ERROR {type(e).__name__}: {e}"
        if len(sys.argv) > i + 1:
            open(sys.argv[i + 1], "w", encoding="utf-8").write(txt)
        else:
            print(txt)
        sys.exit(0 if txt.startswith("OK") else 1)
    from rawflow.app import main
    main()
