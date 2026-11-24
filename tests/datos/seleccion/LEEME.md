# Datos para calibrar la Selección IA (sin volver a analizar)

`rfui cull` (4.17) + rescate de caras (`seleccion.rescue`) de dos eventos reales, en orden de toma:
- `jutiapa_597.jsonl` — «14nov 2026 JUTIAPA/1 Original» (597 NEF, Nikon, EDN_…)
- `20nov_480.jsonl` — «20 nov 2026 2/1 Original» (480 NEF, Nikon, OSZ_…)

Probar una regla nueva en segundos (sin tocar fotos):
    .venv/bin/python -m rawflow.seleccion tests/datos/seleccion/jutiapa_597.jsonl
Resultado de referencia 4.17: JUTIAPA 103 apartadas (102 repetidas + 1 ojos cerrados); 20 nov 45 (42 + 2 fallidas + 1 negra).
Solo si cambia `rfui cull` hay que regenerarlos (≈75 s por 600 fotos) y volver a rescatar caras (pasar la carpeta RAW).
Ver las fotos de un caso: `tools/calibrar/hoja.py`.
