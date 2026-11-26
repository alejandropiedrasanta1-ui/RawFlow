"""Lo ejecuta el Python de CineSlide (no el de RawFlow): crea la presentación con el motor Express de CineSlide,
sin abrir su ventana, igual que el botón CREAR VIDEO, con tus ajustes guardados y el tema que se pida.

    python3 cineslide_run.py <carpeta_fotos> <carpeta_salida> <nombre> [tema]

Imprime «PROG <0..1> <texto>» mientras trabaja y «OK <ruta>» por cada video creado.
"""
import os
import sys

SEGUNDOS_POR_FOTO = 3.0


def main():
    fotos_dir, salida, nombre = sys.argv[1], sys.argv[2], sys.argv[3]
    tema = sys.argv[4] if len(sys.argv) > 4 else "4"
    cs = os.environ.get("CINESLIDE_DIR", "")      # Windows: CineSlide.py está en la carpeta del programa
    if cs and os.path.isfile(os.path.join(cs, "CineSlide.py")) and cs not in sys.path:
        sys.path.insert(0, cs)
    import CineSlide as m                         # importar no abre la ventana

    defs = dict((v["id"], v) for v in m.MANIFEST["variables"])
    vals = dict((i, d["default"]) for i, d in defs.items())
    prefs = {}
    try:
        prefs = m._prefs_cargar() or {}
    except Exception:
        pass
    vals.update((prefs.get("config") or {}).get("vals") or {})
    for i in tuple(m.T1_INTERNAS) + tuple(m.T6_INTERNAS) + tuple(m.AJUSTES_FIJOS):   # igual que _valores_de_corrida
        if i in defs:
            vals[i] = defs[i]["default"]
    opciones = [o for o in (defs.get("estilo_diseno", {}).get("choices") or []) if str(o).startswith(tema + ":")]
    vals["estilo_diseno"] = opciones[0] if opciones else tema
    vals["incluir_videos"] = True
    # Pedido del usuario (26-nov-2026): SIEMPRE 3 s por foto y los dos videos (horizontal y vertical), aunque en
    # CineSlide se haya dejado otra cosa la última vez.
    for i in ("t_segundos_por_foto", "t1_segundos_por_foto"):
        if i in defs:
            vals[i] = SEGUNDOS_POR_FOTO
    dos = [f for f in (getattr(m, "FORMATOS_VIDEO", None) or []) if str(f).startswith("Los dos")]
    if not dos or not hasattr(m, "formatos_de_video"):
        # RawFlow da la presentación por hecha solo con los DOS videos: con un CineSlide que no sabe hacerlos, se
        # rehacía en cada vuelta sin decir por qué (revisión 26-nov-2026). Mejor avisar claro.
        print("ERROR este CineSlide no puede hacer los dos formatos (horizontal y vertical): actualiza CineSlide",
              flush=True)
        return 3
    vals["formato_video"] = dos[0]

    ext = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp")
    fotos = [os.path.join(fotos_dir, f) for f in os.listdir(fotos_dir)
             if f.lower().endswith(ext) and not f.startswith(".")]
    fotos.sort(key=lambda r: m._clave_natural(os.path.basename(r)))
    if not fotos:
        print("ERROR no hay fotos en %s" % fotos_dir, flush=True)
        return 2
    items = [(r, False) for r in fotos]

    musica = None
    mu = prefs.get("musica") or {}
    if mu.get("ruta") and os.path.isfile(mu["ruta"]):
        musica = dict(mu)

    def prog(f, texto):
        print("PROG %.4f %s" % (f, texto), flush=True)

    m._HOOK_PROGRESO = prog
    os.makedirs(salida, exist_ok=True)
    formatos = m.formatos_de_video(vals)
    for i, (W, H, sufijo) in enumerate(formatos):
        n = len(formatos)
        m._HOOK_PROGRESO = (lambda f, t, i=i, n=n: prog((i + f) / float(n), t))
        r = m.crear_presentacion_express(items, vals, W, H, 30.0, musica=musica, carpeta_salida=salida,
                                         nombre=nombre + (sufijo if n > 1 else ""))
        print("OK %s" % r["ruta"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
