# Datos para calibrar la Selección IA (sin volver a analizar)

`rfui cull` (4.17) + rescate de caras (`seleccion.rescue`) de dos eventos reales, en orden de toma:
- `jutiapa_597.jsonl` — «14nov 2026 JUTIAPA/1 Original» (597 NEF, Nikon, EDN_…)
- `20nov_480.jsonl` — «20 nov 2026 2/1 Original» (480 NEF, Nikon, OSZ_…)

Probar una regla nueva en segundos (sin tocar fotos):
    .venv/bin/python -m rawflow.seleccion tests/datos/seleccion/jutiapa_597.jsonl
Resultado de referencia 4.17: JUTIAPA 103 apartadas (102 repetidas + 1 ojos cerrados); 20 nov 45 (42 + 2 fallidas + 1 negra).
Solo si cambia `rfui cull` hay que regenerarlos (≈75 s por 600 fotos) y volver a rescatar caras (pasar la carpeta RAW).
Ver las fotos de un caso: `tools/calibrar/hoja.py`.

## Windows (motor propio, `rawflow/seleccion_win.py`, 23-nov-2026)
Estos dos `.jsonl` son medidas de Vision (Mac): sirven igual para probar las REGLAS en Windows, pero no para calibrar
el motor de Windows (sus fotos no estaban en el PC). El motor se calibró con dos eventos de la misma cámara
(«15 NOV XV», 376 NEF, y «XV 8 NOV», 516 NEF), midiendo con `seleccion_win.analyze(..., raw_metrics=True)` (añade
`crudo`: contenido y composición sin convertir) y MIRANDO las fotos:
- **Parecido.** Pares de tomas seguidas (≤5 s) ordenados por distancia: hasta 0,37 de contenido son la misma pose;
  entre 0,38 y 0,48 ya hay otro gesto o encuadre. De ahí `EMB_KNOTS` (0,37 ↔ 0,16 de Vision). Con eso los cuantiles
  de los pares ≤5 s coinciden con los de JUTIAPA en el Mac (p27 0,166 frente a 0,159; p50 0,217 frente a 0,195).
  La huella de MobileNetV3 hay que alimentarla con píxeles 0-1 (con −1…1 separa mucho peor: AUC 0,84 frente a 0,99).
- **Ojos.** Recortes de 763 caras por franjas: <0,10 cerrados, 0,10-0,12 cerrados o mirando abajo, 0,12-0,18
  entrecerrados (riendo), ≥0,18 abiertos. Es la misma escala de Vision: `EYE_GAIN = 1`, sin corrección.
- **Resultado.** 15 nov: 49 de 376 apartadas (48 repetidas, 1 ojos cerrados); 8 nov: 102 de 516 (101 repetidas,
  1 muy oscura). Revisadas las más dudosas (parecido 0,15-0,16) y las tres decididas por ojos: correctas.
Si se cambia un modelo o el tamaño de las imágenes, hay que repetir la calibración.

### Tomas «fallida» y «al piso» en Windows (24-nov-2026): NO se aplican, a propósito
El Mac las decide con la nota estética y las etiquetas de escena de Vision; el motor de Windows no las tiene (la
huella de MobileNetV3 son 1280 números, sin clases). Se probó sustituirlas por luz/contraste/nitidez sin caras:
- en «15 NOV XV» y «XV 8 NOV» hay 32 fotos sin caras detectadas y las 32 son BUENAS (anillo, pastel, comida,
  vestido colgado, espaldas, maquillaje, novia tumbada); ninguna tiene `desv` < 20 y las más blandas (EDN_5067
  maquillaje `nit` 81, EDN_4035 espalda de noche `nit` 98) son fotos buenas;
- en los `.jsonl` del Mac, de 1077 fotos solo 2 son «fallidas» (OSZ_0445 `nit` 174, OSZ_0435 `nit` 32) y hay fotos
  buenas sin caras con `nit` 88-106 (EDN_4716, EDN_4806).
Una regla por nitidez o contraste apartaría fotos buenas y no hay ejemplos reales que la justifiquen. Para hacerlo
bien haría falta un modelo de escena (p. ej. Places365) y un evento con tomas accidentales de verdad para calibrar.
Las tomas negras y blancas (`_accidental`) y las caras quemadas/negras (`_soft_bad`) sí funcionan en Windows.

### Fallida por nitidez (24-nov-2026): SÍ, en los dos sistemas
Con «D:\TERMIANDO\BODA 14 NOV YO\ORIGINAL\100MSDCF» (1604 ARW Sony; `DNGCOMPRI` = 640 que otra IA marcó malas,
`BUENAS\BUENAS` = 728 buenas) apareció una señal limpia: sin caras y con `nit` < 3 % de la mediana del evento (y < 20)
son SIEMPRE fallos (bokeh total, movidas): DSC02492, 02493, 02509, 02637, 02702, 02750, 02883, todas en DNGCOMPRI y
revisadas a ojo. La buena sin caras más blanda de los 5 eventos está a ≥0,059 (el gato DSC01629, 0,13). Reglas
`BLUR_REL`/`BLUR_ABS` en `_soft_bad`; los otros 4 eventos no cambian (JUTIAPA 103, 20 nov 45, 15 nov 49, 8 nov 102).
En esa boda RawFlow no aparta ninguna buena de verdad: DSC02626 (sin flash) y DSC02628 (ojos cerrados) estaban mal
marcadas por la otra IA. La otra IA aparta muchas más (40 %): sobre todo gusto y repetidas que RawFlow deja a propósito.
