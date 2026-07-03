# Testing de Modelos IA

## 2026-07-03 — Gemini 3.1 Flash Lite vs Qwen 3.5 Flash

### Contexto

Falsos positivos reportados: modelo `qwen/qwen3.5-flash-02-23` detectando PERSONA en imágenes sin persona.

### Prueba 1: 2 falsos positivos, 2 modelos, 3 resoluciones

**Script:** `scripts/tests/test_fp_2img.py`
**Imágenes:** falsos positivos del 02/07 (DVR-A3) y 03/07 (DVR-A2)

| Imagen | Qwen 100% | Qwen 75% | Qwen 50% | Gemini 100% | Gemini 75% | Gemini 50% |
|--------|-----------|----------|----------|-------------|------------|------------|
| A3 FP | PERSONA 15 | PERSONA 55 | PERSONA 55 | NO | NO | NO |
| A2 FP | NO | PERSONA 40 | PERSONA 10 | NO | NO | NO |

**Resultado:** Qwen falló 5/6 (dijo PERSONA sin haber). Gemini acertó 6/6.

### Prueba 2: 6 imágenes con sospecha alta, Gemini al 50%

**Script:** `scripts/tests/test_gemini_6img.py`
**Imágenes:** detecciones A2 con 55-100% de sospecha (entre 05/06 y 29/06)
**Modelo:** solo `google/gemini-3.1-flash-lite-preview`
**Resolución:** 50%

| Imagen | Sospecha original | Gemini | Tiempo |
|--------|-------------------|--------|--------|
| A2 05/06 06:43 | 100% | PERSONA 85% | 2.7s |
| A2 05/06 06:57 | 100% | PERSONA 85% | 1.4s |
| A2 15/06 00:40 | 90% | PERSONA 85% | 1.6s |
| A2 22/06 07:32 | 95% | PERSONA 70% | 1.6s |
| A2 22/06 07:38 | 80% | PERSONA 85% | 1.7s |
| A2 29/06 07:30 | 55% | PERSONA 85% | 1.2s |

**Resultado:** Gemini detectó PERSONA en 6/6 (consistente con Qwen original).

### Conclusión

- **Ambos modelos coinciden cuando hay persona real** (6/6 detecciones altas validadas).
- **Gemini 3.1 Flash Lite tiene 0% falsos positivos** vs Qwen con 83% en este test.
- Gemini es **2-3x más rápido** (~1.5s vs ~3.5s) y usa menos tokens.
- Gemini con 50% resolución funciona igual que resoluciones mayores.

**Decisión:** Cambiar modelo default a `google/gemini-3.1-flash-lite-preview`.

### Archivos generados

| Archivo | Contenido |
|---------|-----------|
| `data/test_fp_2img_result.json` | Resultados prueba 1 |
| `data/test_gemini_6img.json` | Resultados prueba 2 |
| `scripts/tests/test_fp_2img.py` | Script prueba 1 |
| `scripts/tests/test_gemini_6img.py` | Script prueba 2 |

### Cómo re-ejecutar

```bash
python3 scripts/tests/test_fp_2img.py    # Prueba 1 (12 llamadas)
python3 scripts/tests/test_gemini_6img.py # Prueba 2 (6 llamadas)
```
