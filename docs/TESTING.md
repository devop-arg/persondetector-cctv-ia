# Testing - CamDetector

## 2026-10-22 — Paridad Copilot vs OpenRouter (gpt-6-luna)

**Objetivo:** validar el cambio de proveedor (OpenRouter → GitHub Copilot) con el mismo
modelo antes de aplicarlo al servicio.

**Hallazgo previo:** en Copilot, `gpt-6-luna` sólo acepta `/responses`
(`supported_endpoints: ["/responses", "ws:/responses"]`). `POST /chat/completions`
devuelve `400 unsupported_api_for_model`. Por eso el cambio incluye un cliente nuevo
(Responses API), no sólo otra URL.

**Script:** `/home/capw/.hermes/cache/scratch/paridad_copilot.py` (prueba en seco, no
toca config ni servicio) + `src/cam_watcher._analizar_con_copilot` vía `analizar_imagen`.

**Casos:** 6 imágenes reales ya analizadas por el watcher (3 con persona del audit,
3 sin persona), con el prompt real de cada cámara.

| Caso | Audit | Copilot | OpenRouter | Latencia Copilot |
|------|-------|---------|------------|------------------|
| A1 17/10 22:50 | SI 80 | SI 80 | SI 75 | 2.26s |
| A1 17/10 22:51 | SI 90 | SI 90 | SI 90 | 2.29s |
| A2 18/10 21:10 | SI 80 | SI 80 | SI 75 | 2.77s |
| A2 22/10 07:34 | NO | NO 0 | NO 0 | 2.27s |
| A2 22/10 07:34 | NO | NO 0 | NO 0 | 1.94s |
| A2 22/10 07:34 | NO | NO 0 | NO 0 | 1.81s |

**Resultado:** paridad 6/6 en PERSONA; sospecha dentro de ±5; ~2.65k tokens en ambos.
Copilot respondió 0.5-2.5s más rápido por análisis.

**Fallback verificado:** con token inválido, Copilot devuelve 401 → warning
`Copilot falló (...); reintentando con OpenRouter` y el análisis se completa con
OpenRouter (persona=False, 2658 tokens, 4.9s).

**Limitaciones de la API Copilot:** soporta 1 imagen por request y 3 MB por imagen
(`capabilities.limits.vision`); las capturas del DVR (~500-650 KB) entran sin problema.

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
