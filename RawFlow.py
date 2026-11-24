import sys

if __name__ == "__main__":
    if "--escuchar" in sys.argv or "--bloqueo" in sys.argv:   # Windows (RawFlow.exe): escucha «Esc ×3» para cancelar
        from rawflow.winlock import main as escuchar        # (ya no bloquea ratón ni teclado; «--bloqueo» = nombre viejo)
        sys.exit(escuchar())
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
    if "--seleccion" in sys.argv:               # ¿puede este equipo hacer la Selección IA? (resultado a un archivo;
        i = sys.argv.index("--seleccion")       # con una foto detrás, la analiza: solo la LEE)
        try:
            from rawflow import seleccion
            motor = seleccion.engine()
            txt = f"OK motor={motor or 'ninguno'}"
            if motor == "win":
                from rawflow import seleccion_win
                eng = seleccion_win.Engine()    # carga OpenCV y los tres modelos
                txt += " modelos=3"
                if len(sys.argv) > i + 2:
                    r = eng.analyze_file(sys.argv[i + 2], [])
                    txt += (f" foto={r['foto']} caras={len(r.get('caras', []))}"
                            f" hora={'si' if 't' in r else 'no'}" + (f" error={r['error']}" if r.get("error") else ""))
            elif not motor:
                from rawflow import seleccion_win
                txt = f"ERROR sin motor: {seleccion_win.available()[1]}"
        except Exception as e:  # noqa: BLE001
            txt = f"ERROR {type(e).__name__}: {e}"
        if len(sys.argv) > i + 1:
            open(sys.argv[i + 1], "w", encoding="utf-8").write(txt)
        else:
            print(txt)
        sys.exit(0 if txt.startswith("OK") else 1)
    from rawflow.app import main
    main()
