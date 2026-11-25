# RawFlow

Conectas la memoria (o arrastras una carpeta) → escribes el evento → pulsas **Empezar**. RawFlow hace
siempre la misma secuencia (doble clic en un paso para desactivarlo):

1. **Mover** los RAW de la memoria (o de una carpeta arrastrada) a `1 Original` (copia nativa, verifica y borra de la memoria;
   varias memorias a la vez; al final las expulsa).
2. **DxO PureRAW** con el preajuste CINEMA → `2 DNGs`.
3. **Imagen AI** edita los DNG; Bridge exporta JPEG calidad máxima → `3 Imagen AI`
   (todas con las ediciones de Imagen: se comprueba foto a foto).
4. **Retouch4me** con el preajuste CINEMA de Arams, de `3 Imagen AI` → `4 Fotos finales` (motor directo, sin abrir Arams).
5. **CineSlide** (tema 4 · Montón de fotos, con tu música y ajustes de CineSlide) →
   `5 Presentación de fotos/Presentación.mp4`. Se hace con el motor de CineSlide, sin abrir su ventana.

    Escritorio/Quinceaños Ana/
        1 Original/              ← RAW (DSC01592.ARW …)
        2 DNGs/                  ← DNG de DxO, con la edición de Imagen dentro
        3 Imagen AI/             ← JPEG con las ediciones de Imagen (lo que retoca Retouch4me)
        4 Fotos finales/         ← 1.jpg, 2.jpg, 3.jpg… retocadas por Retouch4me (en el orden en que se tomaron)
        5 Presentación de fotos/ ← Presentación.mp4 (CineSlide, tema 4)

Si un paso falla, **Reintentar desde el paso N** sigue desde ahí con la misma carpeta (RawFlow lo recuerda
aunque se cierre: al abrirla, ofrece retomar el evento que quedó a medias).
El paso 3 maneja Imagen y Bridge con la pantalla: deja el Mac desbloqueado y no toques nada.

## Abrir
La carpeta `Instaladores` está ordenada por sistema (4.17): **`MAC`**, **`WINDOWS`** y, aparte,
**`MÁQUINA VIRTUAL (solo en el Mac)`** (lo que usa «Instalar máquina virtual»; no se copia a la PC).
- **Mac:** `Instaladores/MAC/RawFlow X para Mac.zip` → arrastra RawFlow.app a Aplicaciones
  (o, con el código, doble clic en `mac/Abrir RawFlow (Mac).command`).
- **Windows:** copia `Instaladores/WINDOWS/RawFlow X para Windows (INSTALADOR).zip` a la PC (es lo único de
  RawFlow que necesita), descomprímelo y doble clic en **«Instalar RawFlow.bat»** (lo demás va dentro de
  «archivos (no tocar)»): sin internet y sin administrador (trae Python
  3.14 x64/ARM, PySide6 Essentials y psutil), deja RawFlow en `%LOCALAPPDATA%\Programs\RawFlow` con
  accesos directos en el Escritorio y en Inicio. Repetirlo actualiza; «Desinstalar RawFlow.bat» lo quita.
  (El `…-Windows-codigo.zip` es para «Instalar máquina virtual».)
- **Sin cierres inesperados (4.15):** al salir (ventana, ⌘Q o el Dock) RawFlow para y espera a todos sus hilos;
  un fallo inesperado queda escrito en `Application Support/RawFlow/fallos.log` en vez de cerrarla de golpe.

La primera vez tarda un minuto porque instala lo necesario (PySide6 y psutil).
Requiere Python 3.9 o más nuevo (en Windows instálalo desde python.org marcando
*Add python.exe to PATH*).

## Cómo funciona
Cada paso deja lo suyo en una carpeta numerada del evento, en el orden en que trabajan (4.15):

    Escritorio/<evento>/1 Original/               ← Mover: los RAW
    Escritorio/<evento>/2 DNGs/                   ← DxO
    Escritorio/<evento>/3 Imagen AI/              ← Imagen + Bridge: JPEG con las ediciones de Imagen
    Escritorio/<evento>/4 Fotos finales/          ← Retouch4me retoca las de «3 Imagen AI» (1.jpg, 2.jpg…)
    Escritorio/<evento>/5 Presentación de fotos/  ← CineSlide

Un evento de antes («2 DNGs/Imagen AI», «3 Fotos finales», «4 Presentación de fotos») se reordena solo
la primera vez que RawFlow lo toca: solo cambia carpetas de sitio, no copia ni borra ninguna foto.

1. **Memorias conectadas:** aparece una tarjeta por cada USB, SD, CFexpress o disco
   externo. Se analiza al instante. Toca una tarjeta para excluirla.
   **Arrastrar (4.15):** suelta en la ventana cualquier carpeta de fotos (con sus subcarpetas, de
   cualquier disco) o fotos sueltas: cada carpeta es una tarjeta más. Nunca se expulsa. Si está en
   otro disco, sus fotos se **copian** (el original se queda); si está en el mismo, se mueven
   (instantáneo). Las fotos de eventos ya hechos que haya dentro no se tocan. Si sueltas un **evento
   de RawFlow**, ofrece «Seguir desde el paso N» (o «Revisar» si ya está terminado).
2. **Evento:** escribe el nombre. La fecha se detecta sola (el día con más fotos);
   puedes cambiarla.
3. **Empezar:** Paso 1 mueve los RAW a `Original` (verifica cada archivo antes de borrarlo
   de la memoria). Paso 2 DxO crea los DNG en `DNGs`. Al terminar se abre la carpeta.

Detalles:
- Formatos: DNG, CR2, CR3, CRW, NEF, NRW, ARW, SRF, SR2, RAF, ORF, RW2, RAW, RWL,
  PEF, SRW, X3F, 3FR, FFF, IIQ, MOS, MEF, MRW, KDC, DCR, ERF, GPR y más.
  JPG, HEIC y video se ignoran.
- Si dos cámaras generan el mismo nombre (`IMG_0001.CR3`), el segundo se guarda
  como `IMG_0001_1.CR3`. Nada se sobrescribe.
- Si vuelves a importar la misma memoria, las fotos que ya están se saltan.
- Cada RAW se borra de la memoria solo después de comprobar que su copia está completa y
  guardada. JPG y videos se quedan en la memoria.
- Solo salen dispositivos físicos: las imágenes de disco montadas (.dmg, .iso) no aparecen.
- La carpeta del evento se llama como escribas (evento o persona); si lo dejas vacío,
  con la fecha de las fotos. Por defecto va al **Escritorio**; cámbialo con
  **Cambiar** (se recuerda).

## Imagen AI: paso 3 (opcional, necesita internet)
Con **«Editar con Imagen»** marcada, al terminar DxO RawFlow sube los DNG a **Imagen AI**
con tu perfil (por defecto *CLEAN & CRISP*, tipo *Bodas*, recortar · enderezar · máscara de
sujeto), espera la edición y descarga los JPEG finales:

    Escritorio/Quinceaños/Original/        ← RAW
    Escritorio/Quinceaños/DNGs/            ← DNG de DxO  +  .xmp de Imagen (Bridge / Camera Raw)
    Escritorio/Quinceaños/Fotos finales/   ← DSC01592.jpg … editadas por Imagen

Usa la **API oficial de Imagen** (sin clics ni ventanas). Pide tu clave API al soporte de
Imagen y pégala en **Configurar Imagen**; se guarda en el Llavero de macOS. Imagen cobra las
fotos como en su app. Si lo repites, solo procesa las que aún no estén en «Fotos finales».

## Selección IA, paso 2: fotos buenas y fotos malas (y fuera las repetidas), antes de DxO
Justo después de **Mover**, la IA del Mac (Vision de Apple, sin internet) mira la vista previa de cada RAW
(≈0,13 s por foto: 597 en 75 s) y separa, **dentro de `1 Original`**:

    1 Original/Fotos buenas/   ← las que siguen: DxO, Imagen, Bridge, Retouch4me y CineSlide SOLO usan estas
    1 Original/Fotos malas/    ← apartadas, con el motivo de cada una en motivos.txt (nunca se borra ninguna)

Así no se paga Imagen (cobra por foto) ni se gasta tiempo en una foto que no sirve.
- **Repetidas:** la misma pose disparada otra vez (≤5 s entre tomas y casi iguales para la IA: parecido ≤0,16;
  medido foto a foto, desde 0,18 ya es otro encuadre, gesto o momento). De cada grupo **se queda una, la mejor**:
  la que tiene menos gente con los ojos cerrados (cada persona comparada consigo misma, aunque la cámara se mueva
  un poco), luego la de mejor calidad de cara, la más nítida y la más estética. Todas las del grupo tienen que
  parecerse entre sí (una ráfaga que va cambiando no se junta entera). No son repetidas: un horquillado HDR (misma
  toma con distinta exposición), otro encuadre o gesto, ni lo disparado más de 5 s después.
- **Nunca se tira una toma buena (4.17):** una repetida solo se aparta si la que se queda es **igual o mejor para
  TODAS las personas**: si en la apartada alguien tiene los ojos abiertos (o más abiertos: comiendo pastel, riendo),
  la cara más nítida o claramente mejor, o se le ve la cara (en la otra la tapa un brazo o un teléfono), **se quedan
  las dos**. Si hay duda, se quedan las dos.
- **Cada persona, medida en grande (4.17):** ojos, nitidez, luz y calidad de cada cara se miden en la vista previa a
  tamaño completo que guarda el RAW (en una foto de grupo, ~230 px por cara en vez de ~50); el ojo se mide a lo largo
  de sus comisuras (una cabeza ladeada ya no hace parecer abierto un ojo cerrado). Si Vision no ve una cara en una
  toma (en una foto de 15 personas ve 7 en una y 10 en otra), la vuelve a buscar en su sitio. Cada persona se
  compara consigo misma: un movimiento de cámara lo tienen que confirmar al menos dos caras (el novio de una toma
  ya no se confunde con la novia de la otra al caminar).
- **Ojos cerrados, movida o mal expuesta:** solo si la toma **de al lado, casi igual**, tiene a **esa misma
  persona** bien. Nunca por mirar abajo, de perfil, un beso, el maquillaje con los ojos cerrados, noches con flash
  ni detalles (vestido, anillos, ramo): juzgar cada foto sola tiraba fotos buenas de boda.
- **Accidentales** (aunque estén solas): toma negra o blanca, al piso, a la pared, al techo o al cielo sin nadie
  ni ningún detalle, tomas fallidas sin ninguna cara a las que la IA da muy mala nota (disparo al techo, baile
  movido), y caras blancas o negras del todo.
- Medido con 1.077 fotos reales (JUTIAPA y 20 nov), revisadas cara a cara: 148 apartadas (144 repetidas, 1 parpadeo,
  1 negra, 2 fallidas). Frente a 4.16 se quedan 45 tomas que antes se tiraban, y en muchos grupos se queda ahora la
  mejor (la novia con los ojos abiertos en vez de a medio cerrar, la invitada mirando a la cámara).
- Topes por si una cámara rara confunde a la IA: si por comparación salieran más del 15 %, o las reglas finas a
  solas más del 5 %, esas no se aplican; si saliera repetida más de la mitad del evento, solo las clavadas (≤0,10).
- Si la IA falla, el evento NO se para: pasan todas como buenas.
- Para recuperar una: muévela a `Fotos buenas` y pulsa «Seguir» (RawFlow la procesa y no la vuelve a apartar).
- Se activa o desactiva con **doble clic en «Selección IA»**.
- **También en Windows (23-nov-2026).** En el Mac cada foto la mide la IA de Apple; en Windows no existe, así que
  RawFlow lleva dentro su propio motor (`rawflow/seleccion_win.py`: OpenCV con el detector de caras YuNet, la malla
  facial de MediaPipe y una huella MobileNetV3; modelos en `rawflow/modelos`, 15 MB, sin internet). Saca las mismas
  medidas en la misma escala (ojos: cerrados <0,12, abiertos ≥0,18; parecido: repetidas ≤0,16) y las reglas son
  las mismas de arriba. ≈0,17 s por foto. Calibrado mirando las fotos de dos eventos reales (892 NEF): aparta el
  13 % y el 20 % (en el Mac, 17 % y 9 % en los suyos). Lee la vista previa incrustada en NEF, ARW, CR2 y DNG.
  En Windows **no** se aplican dos reglas que dependen de la IA de Apple: la toma «fallida» (nota estética) y la
  toma «al piso / a la pared» (etiquetas de escena); las negras, blancas, repetidas y de ojos cerrados, sí.

## Candado de Imagen (4.15): nada sin Imagen
En el evento «14nov 2026 JUTIAPA» (597 fotos) la descarga de ediciones de la tanda 1 falló **dentro
de Imagen** (`DOWNLOAD_XMP_ERROR`, 0 de 120, mientras la tanda 5 subía) y RawFlow 4.14 siguió:
120 fotos llegaron a Bridge, a Retouch4me y a las finales sin Imagen. Desde 4.15:
- Imagen anota en su registro cómo terminó cada descarga: RawFlow lo lee **al momento**, espera a que
  acaben las subidas y vuelve a pedir las ediciones **del mismo proyecto** (hasta 3 veces). Sin ellas
  en **todos** los DNG, el paso no sigue.
- Cada foto se comprueba **una a una** (los ajustes de Imagen dentro del DNG y del JPEG): Bridge nunca
  exporta un DNG sin Imagen, un JPEG sin Imagen se borra y se rehace, y **Retouch4me jamás recibe una
  foto sin Imagen** (vuelve al paso 3 por ella).
- Al terminar, revisión final: si una sola foto final no viene de Imagen, el evento no se da por
  entregado. «Reintentar desde el paso 3» rehace **solo** esas: conservan su número y el video se
  vuelve a hacer.

## Ver el proceso: COMPROBADO (4.15)
Al acabar cada paso, RawFlow revisa **su carpeta** (no lo que dice el programa) y lo enseña:
- En la pantalla de proceso, panel **COMPROBADO** (a la izquierda; EN MARCHA sigue a la derecha): «597 fotos en
  «1 Original» · copiadas y verificadas», «597 de 597 DNG completos», «597 de 597 con Imagen · comprobadas una a
  una», «597 retocadas · numeradas del 1 al 597», «Presentación.mp4 · … · con las 597 finales» y la **revisión
  final** («597 de 597 fotos finales vienen de Imagen»). El que va en marcha gira; lo que falla sale en rojo con «!».
- En la ventana, cada pastilla dice su carpeta («→ 3 Imagen AI») y, al terminar, lo comprobado («✓ 597 con
  Imagen»); al terminar: «¡Listo! 597 fotos finales, todas con Imagen…».
- Al arrastrar algo encima de la ventana: «Suelta para añadir». Las transiciones duran ≤ 0,5 s y con «Reducir
  movimiento» de macOS no hay animaciones.

## Pasos activados / desactivados (4.15): doble clic
Doble clic en **Imagen AI**, **Retouch4me** o **CineSlide** lo desactiva (gris, «–», *Desactivado*);
otro doble clic lo activa. Se recuerda para los próximos eventos.
- Imagen desactivado: no se sube nada; Bridge exporta los DNG tal cual a `3 JPEG sin Imagen`
  (nunca a «3 Imagen AI»).
- Retouch4me desactivado: las fotos finales son los JPEG del paso 3, numerados.
- CineSlide desactivado: sin presentación.
- Mover y DxO siempre se hacen (sin ellos no hay fotos ni DNG).

## CPU y GPU (medido)
Retouch4me es el 80 % del tiempo y su IA (Stray Hairs y los demás) solo funciona por OpenCL, que en
este Mac solo ofrece la **GPU** (comprobado: «Apple M5 · GPU», sin dispositivo CPU). Con la GPU al
99 % la CPU queda libre, pero usarla a la vez lo **frena**: el MacBook Air no tiene ventilador y CPU y
GPU comparten calor y energía (un solo núcleo ocupado: Stray Hairs de 57 a 72 s). Lo que sí acelera:
el cargador puesto y una base de refrigeración.

## Al abrir RawFlow, todo cerrado (4.15)
Al abrir RawFlow se cierran, en segundo plano, **UTM** (Windows se apaga bien antes) y los programas del proceso que
hubieran quedado abiertos (DxO, Imagen, Bridge, Retouch4me, CineSlide) con sus ayudantes. UTM solo se abre (y sigue
abierto) mientras «Instalar máquina virtual» trabaja. Tus demás apps se cierran al pulsar **Empezar**.

## Rápido y sin ventanas: un programa a la vez
- Al empezar con una memoria **y al reintentar desde cualquier paso** (4.14), RawFlow cierra **todas** las demás apps
  (sin excepción) y las ventanas del Finder: solo queda RawFlow. Lo que pide guardar no se fuerza (se oculta).
- Las máquinas virtuales encendidas (UTM) se apagan **bien**, como con el botón de encendido, mientras el paso 1 ya
  copia la memoria; la que a los 20 s siga encendida se apaga del todo. Si RawFlow está instalando una máquina
  virtual, UTM no se toca.
- También se cierran los procesos de ayuda que quedan huérfanos de Imagen y Retouch4me (al empezar y al terminar).
- Sin App Nap (macOS no frena RawFlow aunque su ventana quede tapada) y, si el **modo de bajo consumo** está
  activado, aviso en la pantalla del proceso (RawFlow no puede quitarlo solo).
- Cada paso cierra su programa al terminar: DxO al acabar los DNG; Imagen y Bridge al exportar los JPEG (antes
  archiva los proyectos en Imagen); Retouch4me trabaja por su motor, sin ventana. Al final no queda nada abierto.
- **Nunca dos trabajos de GPU a la vez.** Medido: DxO y Retouch4me juntos hacían que Retouch4me fuera 5 veces más
  lento (≈320 s por foto en vez de ≈60) y DxO 1,7 veces. Por eso Retouch4me empieza cuando DxO y Bridge ya cerraron.

## Durante el proceso (4.10)
- **El ratón, el trackpad y el teclado ya NO se bloquean** (desde el 23-nov-2026; antes quedaban bloqueados todo el evento), ni en
  Mac ni en Windows. Mientras RawFlow maneja Imagen y Bridge con la pantalla conviene no tocar esas ventanas: un clic
  tuyo en mal momento puede hacer fallar ese paso (se reintenta solo). **Esc tres veces** (pulsaciones, no la tecla
  mantenida) sigue cancelando al instante desde cualquier programa; luego «Reintentar desde el paso N» sigue donde se
  quedó. RawFlow solo escucha esa tecla (`rfui escuchar` en Mac, `rawflow/winlock.py` en Windows): no retiene ninguna.
- **«Ya puedes retirar la memoria»** se queda en pantalla, animado, hasta que la sacas de verdad: RawFlow la desmonta
  sin expulsarla y ve cuándo desaparece del lector; entonces dice «Memoria retirada» y se va.
- **Sin cargador**: aviso rojo arriba con la batería y para cuánto da, sonido, notificación y, con poca batería, voz.
  (Con batería macOS duerme el Mac al 1 % y el evento se para.)
- **EN MARCHA**: qué programa trabaja y a qué ritmo (DxO «214 de 480 · 11 s/foto», subida a Imagen en MB/s y MB
  subidos, Bridge «JPEG · s/foto», Retouch4me «última foto · media», CineSlide) y cuánto se usa del Mac
  (CPU, GPU, memoria, red ↑↓, qué programas).
- **Lo que falta** lo calcula cada paso con su ritmo real (DxO, subida, Bridge, Retouch4me con sus últimas fotos,
  CineSlide); el modelo de tiempos solo se usa para los pasos que aún no empezaron.

## Windows (4.13)
- **PC con Windows**: el instalador de `Instaladores/WINDOWS` (ver «Abrir»). RawFlow busca solo DxO PureRAW, Imagen AI, Adobe Bridge, Retouch4me y CineSlide en las
  carpetas donde los dejan sus instaladores (`rawflow/programas.py`) y lo vuelve a mirar cada 15 s: si instalas uno
  con RawFlow abierto, lo detecta sin reiniciar.
- En Windows ya funcionan: Mover, DxO, CineSlide (su Python de `C:\Program Files (x86)\CineSlide`), cancelar
  con Esc ×3 (`rawflow/winlock.py`, sin bloquear ratón ni teclado), el aviso de batería y las mediciones. Imagen/Bridge y
  Retouch4me siguen automatizados solo en Mac (en Windows se omiten) hasta portarlos con las apps instaladas.
- **Máquina virtual (Mac)**: «Instalar máquina virtual» lo hace todo en el Mac con los instaladores locales (ver
  «Instalar máquina virtual» más abajo): Windows, limpieza, programas, barra, inicio de sesión guiado y base.
  Compilar RawFlow.exe: `.venv/bin/python -m rawflow.vm_programas compilar`. Retouch4me Arams para Windows se baja
  sin cuenta de `retouch4.me/download_arams` (su web se mudó de retouch4me.com). Bridge no corre en Windows ARM
  (Adobe) y la VM no tiene GPU.

## Nada para el evento (4.11)
- Cada paso se reintenta solo. Si después de todos sus intentos quedan **fotos sueltas** que no salen (hasta 5 o el
  10 % del evento), se apartan y el evento **termina igual** con las demás, diciendo cuáles y por qué:
  RAW dañado que DxO no revela (se queda en «1 Original»), DNG que Bridge no exporta, foto que Retouch4me no retoca
  (se entrega como salió de Imagen), CineSlide que falla (las fotos se entregan; la presentación se rehace con
  «Reintentar desde el paso 5»). Una foto ilegible en la memoria se queda en la memoria y el resto sigue.
- Si falta algo de un paso anterior (p. ej. un JPEG), RawFlow vuelve **solo** a ese paso por esas fotos y sigue.
- Si falla casi todo (problema general: licencia, permiso, sin internet), se para en ese paso y
  «Reintentar desde el paso N» sigue desde ahí, sin rehacer nada de lo anterior.
- Si RawFlow se cierra de golpe o se va la luz, al abrirlo ofrece seguir desde el paso en que iba.
- Al terminar con avisos: «¡Listo, con avisos!» y la lista de lo que no salió perfecto.
- Pruebas: `tests/test_duplicados.py` (35) y `tests/pruebas_rawflow.py` (65 casos numerados: del 26 al 32 la
  máquina virtual simulada; del 33 al 37, cerrar todo, el modo de bajo consumo y Windows que se apaga; del 38
  al 44, el candado de Imagen (la 38 repite lo de JUTIAPA); del 45 al 48, el doble clic; del 49 al 52,
  arrastrar carpetas; la 53, una final borrada a mano; la 54, las carpetas 1-5 y los eventos de antes; la 55,
  el panel COMPROBADO; la 56, salir sin hilos vivos;
  del 57 al 60, la Selección IA; la 61, todo cerrado al abrir; la 62, las repetidas; la 63, cada persona con
  ella misma en las fotos de grupo y dos cámaras; la 64, no tirar tomas buenas; la 65, volver a buscar caras); la compilación no sigue si alguna falla.

## Eventos grandes: Imagen sube mientras DxO trabaja (automático)
Con 60 fotos o más, cada tanda de fotos que DxO ya terminó (1/5 del evento, mínimo 30) sube a **Imagen** en su
proyecto («Tanda N <evento>») mientras DxO sigue: la subida usa la red, no la GPU. Al terminar DxO solo queda
descargar las ediciones, devolverlo todo a `2 DNGs` y exportar con Bridge de una vez. Con 480 fotos ahorra la
subida casi entera (≈40 min a 12 MB/s). Las carpetas finales quedan igual que sin tandas.
- Si algo falla, el reintento solo procesa lo que falta: nada que Imagen ya editó se vuelve a mandar (no se cobra dos
  veces) y nunca se crea un proyecto repetido. Si DxO quedó a medias, el reintento empieza por DxO.
- `RAWFLOW_TANDAS=0` las desactiva.

## Retouch4me: paso 4 (motor directo, sin ventana)
RawFlow lee el preajuste **CINEMA** de Arams (hoy: Heal 58 · Stray Hairs 100 · Mattifier 10 ·
Dodge Burn 20 · Portrait Volumes 30) y retoca con el **mismo motor** que usa Arams por dentro
(`rawflow/retouch_engine.py` + `rawflow/r4m_worker.js`, que corre con el propio ejecutable de Arams
en modo Node). El resultado es idéntico píxel a píxel a exportar desde Arams (JPEG 100 %), pero sin
abrir su ventana, sin crear ni borrar proyectos, sin vistas previas y sin OCR ni clics: no usa la pantalla.
Cada foto aparece entera en «Fotos finales» al terminarla; si algo se corta, al reintentar solo se hacen
las que faltan. Si cambias CINEMA en Arams, RawFlow usa lo nuevo en el siguiente evento.

El tiempo lo marca la tarjeta gráfica (al 100 %): con CINEMA, en un MacBook Air M5 en frío ≈48 s por foto
(Stray Hairs ≈37 s); tras horas de trabajo el Air, sin ventilador, se calienta y baja a ≈60-75 s (Stray Hairs
50-60 s). Los otros cuatro dependen de las caras: un primer plano ≈3 s, una persona ≈7 s, un grupo grande ≈20 s.
Enchufado y con el Mac fresco (base con ventilador, superficie dura, tapa abierta) va mucho más rápido.

Si el preajuste usa algo que el motor directo no reproduce igual (máscaras, búsqueda de caras, ajustes por
género, LUT, plugins demo o no instalados), RawFlow usa la ventana de Arams como antes (`arams_ui.py`).

## Imagen abre en «Inicio» (Windows, 4.25)

La app de Imagen ahora abre en su pantalla «Inicio» y el OCR de Windows a veces no lee «Proyectos» en la barra
lateral: el paso 4 se paraba con «Imagen no muestra su pantalla principal («Proyectos»)». Ahora, si no lo lee, lo
encuentra por su sitio (entre «Perfiles de IA» y «Social Spotlight»).

- La letra pequeña («Bodas», 12 px) y la blanca sobre rojo («Explorar», «Siguiente») a veces no se leían: cada espera
  alterna ahora la lectura normal con una ampliada ×2.
- El asistente nuevo, al cerrarlo, pregunta «Proyecto aún no creado · Cancelar / Salir ahora». RawFlow pulsaba
  «Cancelar», el asistente se quedaba abierto y cada reintento fallaba («No apareció «Explorar»»): ahora pulsa
  «Salir ahora».
- Cerrar las apps de fondo: un proceso que no deja mirarse (ASUS DriverHub) ya no corta el barrido entero.
- Claude se queda abierto (y lo que usa por dentro) al empezar un evento, para poder vigilarlo y arreglar fallos.
- El nombre del proyecto se escribe clicando dentro de su casilla y se comprueba (una vez se creó como «2 DNGs»).
- Cada vez que algo no aparece en pantalla se guarda una captura en «diagnostico» y el registro dice cuál es.

## Memorias más rápidas y aviso de retirarla (4.26, Windows)

- **Detectar la memoria**: Windows avisa al ponerla (WM_DEVICECHANGE) y RawFlow mira al instante y cada 0,25 s
  unos segundos (antes, la siguiente vuelta de 0,8 s). Las horas de cada toma se leen con 8 hilos: la tarjeta de
  681 fotos, 7,4 s → 2,5 s.
- **Aviso «Ya puedes retirar la memoria»**: en Windows se iba a los 4 s. Ahora vigila el dispositivo USB expulsado
  (`core.win_device_id` antes de expulsar + `core.win_devnode`: sigue «en espera», problema 47, hasta
  desenchufarlo) y se queda hasta que la quitas. Un lector que solo suelta la tarjeta no avisa al sacarla: ahí
  el aviso dura 9 s.
- **Mover**: la tarjeta manda (≈75 MB/s medidos); mientras se lee una foto otro hilo cierra la anterior (fsync y
  comprobación) y las originales se borran al final de cada memoria, solo si su copia quedó completa.
- **Animaciones**: al quitar la última memoria, su tarjeta se iba de golpe (la lista se ocultaba antes de que se
  encogiera); ahora sale encogiéndose y «Conecta una memoria» entra con un fundido. Una memoria quitada mientras
  aún entraba sale desde donde estaba (`anim.slide_out` para la entrada). «Memoria retirada»: el borde pasa a verde
  y la palomita se dibuja. Comprobado con fotogramas de una memoria simulada.
- **Estimaciones**: Imagen y Bridge en Windows no guardaban su tiempo real (Imagen salía 1 h 13 y tardó 14 min);
  ahora sí, y la Selección IA usa lo medido (435 fotos en 19 s). Luminar compara sus fotos con varios hilos
  (411: 68 s → 15 s).

## Luminar con cientos de fotos (4.25)

Con 411 fotos, el paso 6 fallaba tres veces («Luminar no seleccionó las 411 fotos para exportar»): tras pegar el
ajuste, el menú Exportar sale vacío o gris unos segundos, y el OCR leía «41 seleccionadas». Ahora espera al menú
(hasta 0,15 s por foto, mín. 20 s, leyendo también ×2), cuenta como n un número con una cifra comida o partido
(solo con 3+ cifras; la exportación comprueba luego que salen las n) y el registro dice lo que leyó.

## Selección IA más estricta (4.25)

Pedido del usuario: «dejó tomas quemadas, con ojos cerrados o borrosas». Ahora se apartan también A SOLAS (sin otra
toma igual con la que comparar):

- **ojos cerrados del todo** (los dos ojos; a medio cerrar o sonriendo, no; dos caras pegadas —beso, baile—, no);
- **movida o desenfocada**: ninguna cara nítida (por debajo del 2,8 % de la nitidez mediana de las caras del evento);
- **quemada**: sin ninguna cara y casi toda blanca (con la cara bien expuesta, el blanco es el cielo y se queda).

Calibrado mirando cada cara ampliada en 6 eventos reales. Lo apartado sigue en «Fotos malas» y se recupera con un clic.

## Interfaz más pulida y tiempos reales en Windows (4.24)

- Las pastillas de los pasos ya no cortan el texto con la ventana estrecha, y cada paso tiene su icono (Luminar lo
  tenía mal y CineSlide no tenía).
- Los tiempos cambian con un fundido corto; la pantalla de proceso va a 30 fps (y en ahorro, el cambio de paso
  igual se ve fluido) y ya no crea una imagen de pantalla completa en cada fotograma.
- **Estimación en Windows con lo medido en el PC**, no en el Mac: 612 fotos marcaban ≈14 h 30 y son ≈6 h 15
  (Retouch4me ≈16,5 s/foto con 3 motores; DxO ≈4 s/foto). Si lo medido va más rápido que el punto de partida, manda
  lo medido.
- Luminar: al acabar de exportar espera solo hasta que los archivos dejan de crecer (antes, 3 s fijos).
- Medido: 4 motores de Retouch4me no van más rápido que 3 (la tarjeta ya va al tope), y hacer Retouch4me a la vez
  que Bridge exporta no ahorra tiempo (Bridge exporta a ~0,8 s/foto). Se queda como estaba.

## Luminar: paso 6 (Windows, 4.23)

Después de Retouch4me, Luminar Neo da el toque final con tu preajuste **«CINEMA AJUSTE»** (Mis preajustes), como en
el video «PASO 5 LUMINAR»:

1. catálogo **nuevo** para el evento en `Imágenes\Catálogos Luminar RawFlow\<evento>` (nunca se mezclan eventos);
2. añade la carpeta **«4 Fotos finales»**;
3. aplica «CINEMA AJUSTE» a una foto, **Copiar ajustes** y **Pegar ajustes** a todas;
4. exporta en **JPEG al 100 %** a **«5 Fotos finales Luminar»** (mismos nombres: 1.jpg, 2.jpg…);
5. comprueba que **cada foto cambió** respecto a la de Retouch4me (si el ajuste no se pegó, reintenta) y cierra Luminar.

CineSlide hace la presentación con esas fotos (carpeta **«6 Presentación de fotos»**; los eventos de antes con
«5 Presentación de fotos» se renombran solos). Si Luminar falla del todo, el evento termina igual, con aviso, y se
entregan las de Retouch4me. Se desactiva con doble clic en su paso, como los demás. Luminar se maneja leyendo la
pantalla (no tiene línea de comandos): mientras trabaja, mejor no tocar el ratón (RawFlow ya cierra las demás apps).
Medido: 5 fotos en ≈80 s (abrir Luminar y el catálogo ≈60 s; exportar ≈3 s por foto).

## DxO: procesado automático con CINEMA
Con la casilla **«Procesar con CINEMA»** marcada, al terminar de mover RawFlow
manda todas las fotos a **DxO PureRAW** y las procesa **solas** con tu preajuste
**CINEMA**, sin abrir ventanas ni hacer clics. Los DNG quedan en:

    Escritorio/Quinceaños/Original/   ← RAW originales
    Escritorio/Quinceaños/DNGs/       ← DSC01592-DxO_DeepPRIME XD3.dng …

RawFlow muestra el avance («DxO procesando con CINEMA: 120 / 480 · quedan ~25 min»).
Si vuelves a pulsar **Procesar en DxO**, solo manda las fotos que aún no tienen DNG.

Cómo lo logra: usa el modo de procesado del plugin de Lightroom de PureRAW, que
siempre aplica el **primer** preajuste de la lista. Antes de cada envío RawFlow deja
CINEMA de primero y con salida a la carpeta `DNGs` del evento. La primera vez guarda una copia
de tus preajustes en `ProcessingPresets.backup-antes-de-RawFlow.json`, en la misma
carpeta de PureRAW (`~/Library/DxO_Labs/DxO PureRAW 6/`).
Si cambias ajustes dentro de CINEMA en PureRAW (ruido, ópticas…), RawFlow los respeta.
Con una cámara u objetivo nuevo, PureRAW pide descargar su **módulo óptico** («Módulos ópticos DxO»):
RawFlow lo ve y pulsa «Guardar» solo (lo que propone PureRAW), así no se queda parado en 0 fotos.
PureRAW puede mostrar un aviso «¡Listo!» la primera vez; marca «No mostrar de nuevo».

Detecta automáticamente **DxO PureRAW** (preferido) o **DxO PhotoLab**. Si no lo
encuentra, usa **Elegir DxO** arriba a la derecha (en Windows elige el `.exe`,
normalmente en `C:\Program Files\DxO\...`).

## Permisos (Mac)
La primera vez que abres RawFlow.app aparece **Permisos** (también en el botón de arriba).
Pulsa **Pedir todos** y acepta cada aviso de macOS: carpetas Documentos, Descargas,
Escritorio e Imágenes, memorias externas y **controlar UTM**. Opcional: **Acceso total al
disco** (botón «Abrir Ajustes», añade RawFlow). macOS los recuerda; solo vuelve a
preguntar si se recompila la app.

## Instalar máquina virtual (solo Mac, sin clics)
El botón **Instalar máquina virtual** instala Windows completo en UTM de una vez:
1. Busca la ISO de Windows en Documentos, Descargas o Escritorio (prefiere Windows 11 ARM,
   que va nativo). Si no hay, el botón **Descargar Windows 11 ARM** abre la página de Microsoft.
2. Prepara un CD de respuestas con los drivers de UTM y un `Autounattend.xml`: idioma de la ISO,
   teclado igual al del Mac, Windows 11 Pro, disco entero, cuenta local (por defecto «Alejandro»,
   sin contraseña), sin pedir internet, privacidad al mínimo e instala los drivers al entrar.
3. Crea la VM (8 GB, 4 núcleos, 64 GB), la arranca y pulsa sola «Press any key».
4. Espera a que Windows arranque con drivers e internet (≈10–20 min en ARM).
5. **Windows limpio** (`vm_programas.debloat`): quita las apps de reclamo (Xbox, Outlook, Teams, noticias, el
   tiempo y los widgets, juegos, Clipchamp, Copilot, OneDrive…) para todos los usuarios y para los que se creen
   después, e impide que Windows las vuelva a meter. Se quedan Edge, la Tienda, Fotos, el Reproductor multimedia,
   Paint, Recortes, Bloc de notas, Calculadora, Terminal, Seguridad y los códecs de imagen y vídeo. Edge sin
   bienvenida, traductor, Copilot ni barra lateral, y sin reabrir pestañas viejas.
6. Instala en silencio lo que haya en `Instaladores/WINDOWS/Programas para Windows` y RawFlow (todo local, por la red interna
   de la VM; el tipo de instalador se reconoce solo: NSIS, Inno, WiX, MSI…).
7. **Escritorio y barra**: solo RawFlow, Imagen AI, DxO PureRAW, Retouch4me Arams y CineSlide (sin buscador, vista
   de tareas, widgets ni Copilot). El menú Inicio, igual.
8. **Inicio de sesión guiado, una cosa cada vez**: un aviso grande abajo en la ventana de Windows dice qué hacer.
   Si el instalador de DxO no está aún en «Instaladores», se abre UNA sola página (la tienda de DxO): inicias sesión
   y pulsas «Download»; RawFlow lo instala solo y lo guarda en el Mac (la próxima vez ya no se pide). Después se abre
   cada programa que pide cuenta (DxO PureRAW, Retouch4me Arams, Imagen AI), de uno en uno: al cerrar su ventana se
   abre el siguiente.
9. Reinicia Windows (la barra queda fija) y guarda la máquina como **«RawFlow Windows (base)»**.
La próxima vez, el botón **copia la base en segundos** (con las sesiones ya iniciadas), pone al día RawFlow, deja
Windows limpio y ordenado, e instala y pide solo lo que se haya añadido a «Instaladores» después.
Puedes ocultar la ventana: la instalación sigue sola. Windows queda sin activar.
Por línea de órdenes: `.venv/bin/python -m rawflow.vm_programas instalar|limpiar|ordenar|detectar|compilar`.

## Crear máquina virtual a mano (solo Mac)
El botón **Crear VM** (arriba a la derecha) busca ISOs en Documentos,
Descargas y Escritorio, detecta el sistema (Windows/Linux, x64/ARM), propone memoria,
núcleos y disco para tu Mac, crea la VM en **UTM** y la arranca hasta el instalador.
- Las ISO x64 van **emuladas** en Mac con Apple Silicon (lentas); las ARM64, nativas.
- Hay que tener UTM instalado y las demás VMs apagadas (UTM se reinicia un momento).
- La primera vez macOS pide permiso para que RawFlow controle UTM: acéptalo.
- Si sale «Press any key to boot from CD», haz clic en la VM y pulsa una tecla.

## Prueba con memoria falsa (4.18)
Para probar un evento de punta a punta sin una memoria de verdad: una imagen de disco FAT32 que RawFlow trata como
memoria, y la RawFlow instalada abierta con estas variables (solo valen para ese arranque; **no cambian tus ajustes**):

    hdiutil create -size 128m -fs "MS-DOS FAT32" -volname PRUEBA_SD tarjeta.dmg   # y copiar RAW a DCIM/100XXXXX/
    open --env RAWFLOW_SIMULAR_USB=PRUEBA_SD --env "RAWFLOW_EVENTO=PRUEBA RawFlow 2 fotos" \
         --env RAWFLOW_AUTOEJECUTAR=1 --env RAWFLOW_PASOS=todos -a /Applications/RawFlow.app
    hdiutil attach tarjeta.dmg        # = meter la memoria: RawFlow la ve y empieza sola

- `RAWFLOW_SIMULAR_USB`: la imagen de disco con ese nombre cuenta como memoria (las demás .dmg/.iso, no).
- `RAWFLOW_EVENTO`: nombre del evento. `RAWFLOW_AUTOEJECUTAR=1`: empieza sola al meter la memoria.
- `RAWFLOW_PASOS=todos`: los 6 pasos encendidos (sin tocar los que tengas apagados con doble clic).
- La ventana dice «RawFlow · PRUEBA». Para volver a lo normal, cerrar RawFlow y abrirla como siempre.

## Crear una app instalable
- **Windows:** ejecuta `windows\build_windows.bat` → `dist\RawFlow\RawFlow.exe`
- **Mac:** ejecuta `./build_mac.sh` → `dist/RawFlow.app`

Hay que compilar en cada sistema (el .exe se genera en Windows, la .app en Mac).

## Estructura
- `rawflow/core.py` — detección de unidades, escaneo, copia, DxO (sin interfaz)
- `rawflow/app.py` — ventana principal y hilos de trabajo
- `rawflow/widgets.py` — tarjetas, barra de progreso, icono
- `rawflow/theme.py` — colores y estilos
- `rawflow/vm.py` — creación de máquinas virtuales en UTM (sin interfaz)
- `rawflow/vm_auto.py` — instalación desatendida de Windows (Autounattend + drivers UTM)
- `rawflow/vm_dialog.py` — diálogos «Crear VM» e «Instalar máquina virtual»
