# Changelog - CamDetector

## v2.10.11 (2026-03-19)

### Comparador de fondo reaplicado también a imágenes pendientes del evento

**Motivo:** el comparador ya corría al entrar cada archivo desde `inotify`, pero las imágenes acumuladas dentro del mismo evento podían relanzarse desde el callback sin volver a pasar por fondo. Eso permitía que frames con desviación ya menor al umbral siguieran yendo a IA.

#### Cambios
- `src/cam_watcher.py` agrega `ultima_revisada` al estado del evento para distinguir el último frame ya resuelto del último efectivamente analizado.
- `src/cam_watcher.py` centraliza el avance del evento en `_continuar_evento_con_filtro()`, que ahora revalida `comparar_fondo()` antes de cada nuevo análisis, incluso si la imagen estaba acumulada en el buffer.
- `src/cam_watcher.py` hace que `on_imagen_recibida()` y `_on_analisis_completo()` usen el mismo camino de decisión, evitando que los frames pendientes salteen el comparador.
- `src/app_version.py` actualiza la versión a `v2.10.11` y `cam_watcher v2.10.6`.

#### Efecto esperado
- `img1` sigue siendo solo trigger.
- Cada imagen posterior del evento vuelve a pasar por el comparador antes de ir a IA.
- Si una imagen pendiente ya cae por debajo del umbral de fondo, se registra como `FONDO ... SKIP` y no consume análisis IA.

#### Backup pre-cambio
- `backups/20260319_152804_fondo_recheck_pending/`

## v2.10.10 (2026-03-19)

### Parser TCP completo del DVR y comparador de fondo habilitable en A1-A4

**Motivo:** las pruebas controladas con `tcpdump` confirmaron que el DVR fragmenta la alerta TCP en al menos dos bloques, y que el primer bloque corto sesgaba el fallback productivo hacia `A1`. Además, para contener el aumento de capturas/IA al corregir ese parser, convenía extender el comparador de fondo a las cuatro cámaras DVR activas.

#### Cambios
- `src/captura_alertas.py` deja de depender de un solo `recv(4096)` y ahora reconstituye el payload completo de cada alerta TCP antes de parsearla.
- `src/captura_alertas.py` elimina el fallback binario corto a `byte8` como fuente productiva de canal; los paquetes Hikvision cortos quedan solo como diagnóstico.
- `src/captura_alertas.py` agrega configuración explícita para `TCP_READ_CHUNK_SIZE`, `TCP_MAX_PAYLOAD_BYTES` y `TCP_IDLE_TIMEOUT`.
- `src/cam_watcher.py` deja de hardcodear el comparador de fondo solo en `DVR-A1` y pasa a leer `FONDO_CAMARAS_ACTIVAS` desde `.env`.
- `config/.env` y `src/.env` ahora declaran `FONDO_CAMARAS_ACTIVAS=DVR-A1,DVR-A2,DVR-A3,DVR-A4` y los nuevos parámetros TCP.
- `src/app_version.py` actualiza la versión a `v2.10.10` y `cam_watcher v2.10.5`.

#### Evidencia validada antes del cambio
- Con solo `A3` habilitada en el DVR, el parser corto devolvía `A1`, mientras que el payload completo devolvía `byte27=4 -> A3`.
- Con `A2+A4` habilitadas, el parser corto devolvía `A1`, mientras que el payload completo devolvía `byte27=10 -> A2+A4`.
- Con cambios de sensibilidad y área en `A2/A4`, la distribución del `byte27` completo cambió entre `A2`, `A4` y `A2+A4`, confirmando que se trata de señal de alerta real y no de una lista fija de canales activos.

#### Backup pre-cambio
- `backups/20260319_140541_tcp_parser_fondo_all_cams/`

## v2.10.9 (2026-03-18)

### Retención de `dvr_capturas` ajustada a 500

**Motivo:** completar la unificación pedida para dejar en `500` la retención máxima de las carpetas principales, manteniendo `100` solo en referencias de fondo.

#### Cambios
- `src/captura_alertas.py` ajusta `MAX_IMAGENES = 500` para la carpeta `dvr_capturas`.
- Con esto quedan alineadas en `500` las carpetas `dvr_capturas`, `dvr_no_sospechosas` y `dvr_sospechosas`.
- Se mantiene `dvr_fondo_referencias` en `100`.
- `src/app_version.py` actualiza la versión a `v2.10.9`.

#### Backup pre-cambio
- `backups/20260318_231610_capturas_500/`

## v2.10.8 (2026-03-18)

### Ajuste de retención de imágenes a 500

**Motivo:** reducir el tamaño de las carpetas de trabajo y auditoría, unificando la retención máxima en `500` imágenes para los directorios principales, manteniendo `100` en referencias de fondo.

#### Cambios
- `config/.env` y `src/.env` quedan alineados con `MAX_IMAGENES_REFERENCIA_IA=500`.
- Se mantiene `MAX_IMAGENES_SOSPECHOSAS=500`.
- Se mantiene `MAX_IMAGENES_FONDO_REFERENCIAS=100`.
- `src/app_version.py` actualiza la versión a `v2.10.8`.

#### Backup pre-cambio
- `backups/20260318_231024_retention_500/`

## v2.10.7 (2026-03-18)

### Eliminación del intercalado de análisis por evento

**Motivo:** se confirmó un caso real en `DVR-A3` donde una persona quedó visible solo en un frame que el watcher descartó por el intercalado `ANALIZA / SKIP / ANALIZA`. Con el comparador de fondo ya activo para filtrar parte del ruido, el intercalado pasó a ser más riesgoso que útil para sensibilidad.

#### Cambios
- `src/cam_watcher.py` elimina el `SKIP` intercalado dentro de cada evento. A partir de ahora, luego del `img1=trigger`, las imágenes del evento se analizan en orden de llegada hasta detectar persona sospechosa o alcanzar `MAX_ANALISIS_POR_EVENTO`.
- Se mantiene sin cambios el comparador de fondo, la regla de `3 NO_PERSONA` para consolidar referencia y el rate limit actual del sistema.
- `src/app_version.py` actualiza la versión a `v2.10.7` y `cam_watcher v2.10.4`.

#### Evidencia operativa
- Caso observado: `alerta_A3_20260318_223902_821525.jpg` contenía una persona visible, pero quedó registrada como `SKIP` en el evento de `DVR-A3`, por lo que nunca llegó a la IA.

#### Backup pre-cambio
- `backups/20260318_225736_remove_interleaving/`

## v2.10.6 (2026-03-18)

### Historial ampliado, canales activos explícitos y revisión del modo forzado

**Motivo:** `daily_summary` ya mostraba métricas operativas más útiles que `/historial`, y además convenía dejar explícitos en `.env` los canales DVR realmente habilitados para captura para no depender del default del código.

#### Cambios
- `src/telegram_bot.py` amplía `/historial dia|semana|mes` para mostrar los mismos campos base que el resumen diario: skips por fondo, reemplazos de fondo, sospechosas/no sospechosas, sin persona y tiempo promedio de IA.
- `src/telegram_bot.py` agrega también el detalle resumido de personas no sospechosas y de análisis sin detección, reutilizando el histórico ya guardado.
- `config/.env` y `src/.env` ahora declaran `DVR_ENABLED_CHANNELS=1,2,3,4`.
- Se eliminó una duplicación accidental de `DVR_ENABLED_CHANNELS` en ambos `.env`, dejando una sola definición explícita y consistente.

#### Observación operativa
- En la prueba de modo forzado manual del `2026-03-18` alrededor de `14:02-14:08`, el sistema analizó fuera de horario como correspondía. Se vieron máscaras `12` y `14`, y la IA respondió `NO_PERSONA` en los análisis lanzados durante ese tramo.
- Tras aplicar esta tanda y reiniciar `dvr-alertas`, `cam_watcher` y `telegram_bot`, los tres servicios volvieron a quedar activos con la configuración final cargada.

#### Backup pre-cambio
- `backups/20260318_140957_historial_env_forzado_review/`

## v2.10.5 (2026-03-18)

### Aclaración de canales DVR A1-A8 y ajuste de `/camaras`

**Motivo:** el DVR sí expone canales `A5..A8`, aunque hoy no tengan cámara conectada. El parser debía quedar preparado para `A1..A8`, pero sin asumir que anoche hubo actividad en esos canales si el `byte27` no los marcó.

#### Cambios
- `src/captura_alertas.py` mantiene expansión bitmask `A1..A8`, pero solo intenta capturar los canales configurados en `DVR_ENABLED_CHANNELS` (default: `1,2,3,4`).
- Se confirma que en los logs analizados del `2026-03-18` solo aparecieron valores `9, 10, 12, 13, 14 y 15`; todos activan bits de `A1..A4`, no de `A5..A8`.
- `src/telegram_bot.py` actualiza `/camaras` para mostrar `A1..A8` en estadísticas y archivos en disco, alineado con `stats.json`.

#### Backup pre-cambio
- `backups/20260318_130948_dvr_a1_a8_support/`

## v2.10.4 (2026-03-18)

### Captura DVR: máscaras combinadas reales y cooldown inactivo sin carreras

**Objetivo:** dejar de perder alertas multi-cámara del DVR, evitar confundir valores combinados como canales directos y cerrar el race del cooldown inactivo entre workers.

#### Cambios en `src/captura_alertas.py`
- `byte27` ahora se interpreta como bitmask real; una misma alerta puede expandirse a varias capturas por cámara.
- Se corrige el bug que tomaba valores `3/5/6/7` como canales directos, cuando en realidad eran combinaciones de bits.
- Cada canal válido del paquete se encola por separado, con trazas `MASK` y `QUEUE` para auditoría.
- El cooldown inactivo pasa a reservar el canal antes de lanzar `ffmpeg`, evitando que dos workers capturen la misma cámara casi a la vez.
- Los defaults del servicio de captura suben a `4` workers y cola `100` para tolerar mejor eventos multi-cámara simultáneos.

#### Backup pre-cambio
- `backups/20260318_123042_mask_cooldown_fix/`

## v2.10.3 (2026-03-18)

### Fondo más conservador, auditoría IA separada y resumen diario ampliado

**Objetivo:** evitar que un solo falso `NO_PERSONA` reemplace el fondo, separar mejor la auditoría de IA y mejorar la lectura diaria de métricas reales.

#### Cambios en `config/.env`
- `FONDO_NO_PERSONA_CONSECUTIVOS=3` para actualizar referencia solo tras 3 análisis seguidos con `NO_PERSONA`.
- `FONDO_REFERENCIAS_DIR` y `MAX_IMAGENES_FONDO_REFERENCIAS=100` para guardar cada imagen usada como nuevo fondo.
- `AI_AUDIT_LOG_FILE` y `AI_AUDIT_LOG_WEEKLY_BACKUPS` para separar auditoría IA del log operativo.

#### Cambios en `src/cam_watcher.py`
- La referencia de fondo ya no se reemplaza con un único `NO_PERSONA`; ahora espera 3 consecutivos.
- Cada reemplazo real de fondo se guarda en `data/dvr_fondo_referencias`.
- Se agrega `ai_audit.jsonl` con una línea estructurada por análisis IA y por actualización de fondo.
- El histórico diario suma métricas nuevas: `skips_fondo`, `fondo_actualizaciones`, `analisis_ms_total` y conteo de sospechosas notificadas.
- El mensaje automático al desactivarse el sistema ahora resume esas métricas.

#### Cambios en `src/daily_summary.py`
- El resumen diario por Telegram incorpora skips por fondo, reemplazos de fondo y tiempo promedio de análisis IA.

## v2.10.2 (2026-03-18)

### Logging semanal, calidad IA al 100% y auditoría de imágenes analizadas

**Objetivo:** ordenar los logs, reducir ruido operativo y conservar evidencia diaria de todo lo que realmente pasó por la IA.

#### Cambios en `config/.env`
- `AI_IMAGE_SCALE=100` para enviar las imágenes originales a la IA, sin resize previo.
- `LOG_WEEKLY_BACKUPS=12` y `CAPTURA_LOG_WEEKLY_BACKUPS=12` para conservar 12 archivos semanales.
- `LOG_EXCLUDE_KEYWORDS` y `CAPTURA_LOG_EXCLUDE_KEYWORDS` para filtrar mensajes ruidosos por substring.
- `GUARDAR_IMAGENES_ANALIZADAS=true` para archivar toda imagen analizada.
- `MAX_IMAGENES_REFERENCIA_IA=2000` y `MAX_IMAGENES_SOSPECHOSAS=500` para controlar retención.

#### Cambios en `src/cam_watcher.py`
- El logger pasa de archivo fijo a rotación semanal con `TimedRotatingFileHandler`.
- Se agrega filtro configurable por keywords para bajar ruido en `detections.log` sin ocultar warnings ni errores.
- La carpeta `data/dvr_no_sospechosas` pasa a funcionar también como auditoría de IA: guarda toda imagen que se intenta analizar.
- Las imágenes sospechosas se siguen guardando aparte en `data/dvr_sospechosas`.

#### Cambios en `src/captura_alertas.py`
- `captura_alertas.log` también rota semanalmente desde la propia app.
- Se agregan filtros configurables para omitir mensajes repetitivos de conexión/cooldown.

#### Backup pre-cambio
- `backups/20260318_084953_logging_cleanup/`

## v2.10.1 (2026-03-17)

### Cambio de modelo a Qwen 3.5 Flash + prompt corto + envío 50%

**Objetivo:** reducir costo y latencia del análisis manteniendo buena precisión en detección de persona y nivel de sospecha.

#### Cambios en `config/.env`
- `AI_MODEL=qwen/qwen3.5-flash-02-23`
- `AI_IMAGE_SCALE=50`

#### Cambios en `src/cam_watcher.py`
- Los prompts ahora piden solo:
  - `PERSONA: SI o NO`
  - `SOSPECHOSA: [0-100]` para cámaras A1/A2
- Se elimina `DESCRIPCION` del prompt, pero la clave interna `descripcion` se mantiene vacía por compatibilidad.
- Las imágenes enviadas a OpenRouter se redimensionan según `AI_IMAGE_SCALE` antes de llamar a la API.
- Para modelos Qwen se envía `reasoning: {"enabled": false}` para evitar gasto de tokens en razonamiento.
- Se agrega `max_tokens=100` al payload para limitar respuestas largas.
- Los captions y logs de notificación ya no incluyen descripción generada por la IA.

#### Compatibilidad
- `state.json` y `stats_history.json` conservan el campo `descripcion`, pero vacío.
- `/ultimo` y `daily_summary` fueron adaptados para no mostrar textos de descripción vacíos.

#### Backup pre-cambio
- `backups/20260317_qwen_prompt_corto/`

## v2.10.0 (2026-03-17)

### Fondo subtraction: filtro de imágenes por comparación visual

**Problema:** Durante picos de actividad, el DVR generaba 38+ imágenes por minuto. Cada ráfaga podía costar hasta 20 llamadas a la API de IA aunque la escena no cambiara (ej: cortina moviéndose, reflejos, ruido de sensor). El intercalado (1 análisis, 1 skip) reducía el 50% pero seguía siendo costoso.

**Solución:** Sistema de "resta de fondo" (background subtraction) que compara cada imagen nueva contra una referencia almacenada. Si la diferencia es < 5%, la imagen se descarta sin llamar a la IA.

#### Datos de rendimiento (benchmark real)

| Métrica | Valor |
|---------|-------|
| Resolución de comparación | 960x540 (50% de nativa) |
| Resolución enviada a IA | 1920x1080 (nativa) |
| Tiempo por comparación | ~112ms |
| Ruido noche (consecutivas) | 0.23% - 3.37% |
| Ruido día (consecutivas) | 0.7% - 4.5% |
| Diferencia noche→día | 69% (incompatible, referencia dinámica) |
| Umbral configurado | 5% |
| Umbral por pixel | 30 (de 0-255) |

#### Resultados de prueba (verificado 2026-03-17 12:13)
- 30+ imágenes descartadas en 1 minuto (diferencia 3.02% - 4.64%)
- Solo 2 llamadas a la IA en ese minuto
- Reducción estimada: **85-95% de llamadas a la IA** en escenas estáticas

#### Cambios en `src/cam_watcher.py`

**Nuevos imports:**
- `from PIL import Image` — carga y procesamiento de imágenes
- `import numpy as np` — cálculo de diferencias por pixel

**Nuevas variables de configuración (líneas 113-117):**
- `FONDO_SUBTRACTION_ENABLED` — activar/desactivar filtro (default: true)
- `FONDO_UMBRAL` — diferencia por pixel para considerar "cambiado" (default: 30)
- `FONDO_PORCENTAJE_CAMBIO` — % mínimo de píxeles cambiados (default: 5.0)
- `FONDO_RESIZE` — tupla (960, 540) para redimensionar

**Nuevo estado en memoria (líneas 137-139):**
- `_fondo_lock` — Lock thread-safe para acceso a referencias
- `_fondo_referencias` — Dict[camara, np.ndarray] con arrays de referencia
- `FONDO_CAMARAS_ACTIVAS` — Lista de cámaras con filtro activo (['DVR-A1'])

**Nueva función `_cargar_imagen_gris()` (línea 147):**
- Carga imagen, convierte a escala de grises, redimensiona a 50%
- Retorna np.ndarray (int16) o None en caso de error

**Nueva función `comparar_fondo()` (línea 158):**
- Compara imagen nueva vs referencia almacenada
- Retorna (hay_cambio: bool, porcentaje: float)
- Si no hay referencia → retorna (True, 0.0) — procesar por seguridad

**Nueva función `actualizar_referencia_fondo()` (línea 183):**
- Actualiza referencia solo si desviación < 5% vs referencia actual
- Evita que una imagen con algo "raro" se convierta en la nueva referencia
- Log: `FONDO | {camara} | referencia actualizada`

**Integración en `watch_directory()` (línea 1403):**
- Después del rate limiter, antes de `on_imagen_recibida()`
- Si `hay_cambio=False` → SKIP con log y stats actualizados
- Si `hay_cambio=True` → continúa flujo normal

**Integración en `procesar_imagen()` (línea 1306):**
- Cuando IA responde NO_PERSONA → llama `actualizar_referencia_fondo()`
- Solo actualiza si desviación < 5% (protección contra cambios grandes)

#### Cambios en `config/.env`

**Nuevas variables (después de MAX_ANALISIS_POR_EVENTO):**
```
FONDO_SUBTRACTION_ENABLED=true
FONDO_UMBRAL=30
FONDO_PORCENTAJE_CAMBIO=5.0
```

#### Nueva dependencia
- `pillow` (PIL) — instalado en sistema (`pip install pillow numpy`)
- `numpy` — instalado en sistema

#### Archivos de backup
- Backup pre-cambio: `backups/20260317_113923/cam_watcher.py`
- Backup pre-cambio: `backups/20260317_113923/.env`

#### Comportamiento por horario

| Horario | Comportamiento |
|---------|---------------|
| Noche (00:01-07:45) | Filtro activo, referencia nocturna estable |
| Día (fuera horario) | Filtro activo, referencia se actualiza con cambios de sol |
| Modo forzado | Filtro activo igual que en horario |

#### Configuración para desactivar
```
FONDO_SUBTRACTION_ENABLED=false
```
En `.env` o comentar la línea. Reiniciar servicio.

---

## v2.9.1 (2026-03-05)

### Fix prompt: animales ahora son NO persona

**Problema:** El sistema detectaba personas en imágenes donde había animales (pájaro, perro pequeño, etc.). El modelo gemini-2.5-flash-lite confundía animales con personas porque el prompt no los excluía explícitamente.

**Ejemplos de falsos positivos corregidos:**
- "Una pequeña figura (posiblemente un animal o persona muy pequeña)"
- "Un animal pequeño (posiblemente un perro pequeño)"
- "Un pájaro pequeño está parado cerca de la base del poste"

**Solución en `src/cam_watcher.py`:**
- Ambos prompts (`DETECTION_PROMPT` y `DETECTION_PROMPT_SOSPECHOSA`) ahora incluyen:
  - `Ciclistas, vehículos y animales (perros, gatos, pájaros, etc.) = NO persona`
  - En el criterio: `Ciclistas, vehículos y animales (perros, gatos, pájaros, etc.) se clasifican como NO persona (SOSPECHOSA = 0)`

---

### Fix watchdog: cooldown post-reinicio para evitar loop de reinicios por inactividad

**Problema:** Esta madrugada el watchdog reinició el servicio en loop cada 5 minutos desde las 04:15 hasta las 07:45. Al detectar inactividad de >45 min reiniciaba, pero en el siguiente ciclo volvía a comparar contra la misma última imagen real en el log (que no cambia si no hay movimiento), disparando otro reinicio indefinidamente.

**Solución en `src/watchdog_dvr.py`:**
- Nueva constante `RESTART_STATE_FILE` → `logs/watchdog_last_restart.txt`
- Nueva función `guardar_timestamp_reinicio()` — escribe timestamp actual al archivo de estado tras cada reinicio por inactividad
- Nueva función `leer_timestamp_reinicio()` — lee ese archivo
- `check_inactivity()` ahora verifica primero si el último reinicio fue hace menos de `MAX_INACTIVITY_MINUTES` (45 min); si es así, retorna `True` (ok) sin evaluar los logs — el cooldown coincide con el período de inactividad
- `main()` llama a `guardar_timestamp_reinicio()` tras reiniciar por inactividad y deja registro en log: `Cooldown post-reinicio iniciado. Próxima verificación de inactividad en 45 min.`

**Sin cambio en política de notificaciones:**
- Reinicio por **inactividad** (caso 4): solo log, sin Telegram — sin cambios
- Reinicio por servicio caído/puerto/socket (casos 1-3): sigue notificando por Telegram

---

## v2.9.0 (2026-03-05)

### Refactorización de lógica de análisis: interleaving, cierre de eventos y fix de tokens

**Problemas resueltos (análisis de logs 2026-03-05):**
1. **`SimpleRateLimiter` perdía imágenes**: slot único `pending_image` hacía que durante el cooldown de 30s las nuevas imágenes se sobreescribieran entre sí, perdiendo frames intermedios.
2. **Animales cerraban eventos prematuramente**: detección de animal con 95% de confianza a las 08:04:37 cerró el evento, y se perdió la detección de una persona real que ingresó a las 08:05-08:06.
3. **Tokens siempre en 0**: `_analizar_con_openrouter()` retornaba solo el texto (string), nunca extraía el campo `usage` de la respuesta de OpenRouter.

#### Cambios en `src/cam_watcher.py`

**Eliminación total de detección de animales:**
- Removidos campos `ANIMAL: SI o NO` y `ANIMAL_CONF: [0-100]` de ambos prompts (`DETECTION_PROMPT` y `DETECTION_PROMPT_SOSPECHOSA`)
- Eliminado parsing de `ANIMAL:` / `ANIMAL_CONF:` en `analizar_imagen()`
- Eliminada toda la lógica de cierre de eventos por detección de animal en `_on_analisis_completo()`

**Fix de tokens (`_analizar_con_openrouter()`):**
- Antes retornaba `str` con el texto de respuesta
- Ahora retorna `dict` con `text`, `tokens_prompt`, `tokens_completion`, `tokens_total`
- Extrae los valores del campo `response.json()["usage"]` de la API de OpenRouter
- `analizar_imagen()` actualizado para leer tokens del dict

**Lógica interleaved de análisis por evento:**
- Nuevo contador `imagenes_recibidas: 0` en el estado del evento
- img1 (trigger del evento): no se analiza, solo inicia el evento
- img2, img4, img6... (índices impares en `imagenes_recibidas`): se analizan
- img3, img5, img7... (índices pares): se saltan
- Nueva función `_siguiente_a_analizar(camara, estado)`: respeta el patrón interleaved al elegir la siguiente imagen del buffer
- `on_imagen_recibida()` reescrito completamente con esta lógica

**Cierre de eventos más estricto:**
- Evento cierra SOLO cuando `persona=True AND sospechosa >= 50`
- Personas no sospechosas (sospechosa < 50) NO cierran el evento — permite detectar a alguien que pasa y luego re-ingresa
- Flag `cerrado: False` en estado del evento; ignorar imágenes si ya está cerrado
- Techo de seguridad: `MAX_ANALISIS_POR_EVENTO = 20`

**Directorio para imágenes sospechosas:**
- Nueva constante `SOSPECHOSAS_DIR` → `data/dvr_sospechosas/`
- Nueva función `guardar_imagen_en_dir(filepath, directorio)` reemplaza `guardar_imagen_no_sospechosa()`
- Imágenes con persona sospechosa se guardan en `dvr_sospechosas`
- Imágenes no sospechosas se guardan en `dvr_no_sospechosas`
- `MAX_IMAGENES_GUARDADAS = 200` (era `MAX_NO_SOSPECHOSAS = 100`)

**Nuevo formato de log:**
```
ANALIZADO | DVR-A1 | archivo.jpg | PERSONA sospechosa=80% | tokens=168 (p:123/c:45) | descripcion → NOTIFICADO (2/2)
```

#### Cambios en `src/captura_alertas.py`
- `MAX_IMAGENES`: 100 → 200

#### Cambios en `config/.env`
- `RATE_LIMIT_MAX`: 10 → **60** (prácticamente sin límite)
- `EVENTO_GAP_SEGUNDOS`: 8.0 → **10.0**
- `MAX_ANALISIS_POR_EVENTO`: 5 → **20**

#### Nuevo directorio
- `data/dvr_sospechosas/` — copias de imágenes donde se detectó persona sospechosa y se notificó

---

## v2.8.0 (2026-03-04)

### Cambio de modelo IA: OpenRouter (gemini-2.5-flash-lite)

**Problema:** El sistema anterior usaba Antigravity/OpenCode que tiene cuota limitada.

**Solución:** Se migró a OpenRouter API con modelo gratuito `google/gemini-2.5-flash-lite-preview-09-2025`.

**Ventajas:**
- Completamente gratuito (sin costo de API)
- Velocidad: ~1.8s por imagen (vs ~5-8s con otros modelos)
- Soporte nativo de visión
- Sin rate limits severos

**Cambios en `.env`:**
```
AI_MODEL=google/gemini-2.5-flash-lite-preview-09-2025
OPENROUTER_API_KEY=sk-or-v1-TU_API_KEY
```

**Cambios en `cam_watcher.py`:**
- Agregada variable `OPENROUTER_API_KEY` y función `usa_openrouter()`
- Agregada función `_analizar_con_openrouter()` para llamadas directas a OpenRouter
- Modificada lógica en `procesar_imagen()` para usar OpenRouter cuando corresponde

### Modificación de prompts

**Problema:** El modelo describía animales en DETECCION_PROMPT, generando notificaciones innecesarias.

**Solución:** Se modificó el formato de respuesta:
- Si hay **PERSONA**: `PERSONA: SI` + `DESCRIPCION: [descripción]`
- Si hay **ANIMAL** (perro, gato, etc) pero **NO PERSONA**: `ANIMAL: SI` + `DESCRIPCION: vacío`
- Si no hay nada: todo vacío

---

## v2.7.0 (2026-02-26)

### Arquitectura evento-based para detección (reemplaza throttle por cámara)

**Problema resuelto:** El throttle de 5s por cámara descartaba imágenes img2+ de una misma ráfaga,
analizando solo la primera imagen del burst. Cuando la persona aparecía en img2 y no en img1, quedaba
sin detectar (caso real: 2026-02-23 07:20, persona salió de la casa sin disparar alerta).

**Evidencia estadística (8.419 imágenes DVR-A1):**
- 71.6% de los eventos multi-imagen tienen img2 dentro de 2s de img1
- 94.7% tienen img2 dentro de 4s
- 97.3% tienen img3 dentro de 8s
- Eventos de imagen única: 31% del total. Detección de persona en esos eventos: 0.7% (casi siempre falsas alarmas: sombras, viento, luz)
- Gap entre evento de imagen única y siguiente evento: mínimo 30s, promedio 887s → separación perfecta de bursts

#### Cambios en `cam_watcher.py`

- **Eliminados:**
  - Variable `MIN_SECONDS_BETWEEN_ANALYSIS_PER_CAMERA`
  - Función `segundos_restantes_throttle_camara()`
  - Función `registrar_envio_analisis_por_camara()`
  - Locks `_camera_throttle_lock` y dict `_last_analysis_by_camera`

- **Agregados:**
  - Variables `EVENTO_GAP_SEGUNDOS` (8.0s) y `MAX_ANALISIS_POR_EVENTO` (3)
  - Lock `_eventos_lock` y dict `_eventos` — estado de evento por cámara
  - Función `_get_estado_evento(camara)` — inicializa/retorna estado del evento actual
  - Función `_lanzar_analisis_evento(camara, estado, filepath)` — lanza análisis en thread pool y registra callback
  - Función `_on_analisis_completo(future, camara, filepath_analizado)` — callback: decide si continuar con siguiente imagen del buffer
  - Función `on_imagen_recibida(filepath, camara)` — punto de entrada principal con lógica evento-based

- **Modificados:**
  - `procesar_imagen()` ahora retorna `bool` (True si persona detectada), necesario para el chain de callbacks
  - `watch_directory()` — eliminado bloque de throttle, llama a `on_imagen_recibida()`
  - `main()` — callback usa `on_imagen_recibida()`, mensajes de inicio actualizados
  - `SimpleRateLimiter.__init__` — default `max_per_minute` cambiado de 5 a 10

**Lógica del nuevo sistema:**

```
img1 llega → gap > 8s desde última imagen → NUEVO EVENTO, no analizar (solo trigger)
img2+ llega → gap <= 8s → mismo evento:
  - analisis_count >= MAX(3) → ignorar
  - análisis en curso → acumular en buffer
  - libre → lanzar análisis con esta imagen

Al completar análisis:
  - SI persona → cerrar evento (analisis_count = MAX)
  - NO persona → buscar imagen más nueva en buffer:
    - Hay nueva → lanzar siguiente análisis
    - No hay → esperar (próxima imagen lo dispara)
  - Tras 3 NO → evento cerrado automáticamente
```

**Por qué img1 nunca se analiza:** estadísticamente, cuando hay persona en img1 también está en img2.
Los eventos de imagen única son casi siempre falsas alarmas de movimiento. Analizar img1 desperdicia
cuota de API sin aporte real a la detección.

#### Cambios en `config/.env`

- `RATE_LIMIT_MAX`: `5` → `10`
- Eliminada: `MIN_SECONDS_BETWEEN_ANALYSIS_PER_CAMERA=5.0`
- Agregada: `EVENTO_GAP_SEGUNDOS=8.0`
- Agregada: `MAX_ANALISIS_POR_EVENTO=3`

---

## v2.6.2 (2026-02-20)

### Cambios
- `cam_watcher.py`: agregado throttle por cámara configurable (`MIN_SECONDS_BETWEEN_ANALYSIS_PER_CAMERA`, default 5.0s)
- El throttle se aplica antes del rate limit global para evitar que una sola cámara consuma los 5 análisis/minuto en ráfagas de 1-2s
- Nuevos logs de descarte por ráfaga: `IGNORADO_THROTTLE`
- `config/.env`: nueva variable `MIN_SECONDS_BETWEEN_ANALYSIS_PER_CAMERA=5.0`

---

## v2.6.1 (2026-02-20)

### Cambios
- Versionado unificado en `src/app_version.py` para `cam_watcher.py` y `telegram_bot.py`
- `telegram_bot.py`: alias `/status` mantenido como compatibilidad de `/estado`
- `captura_alertas.py`: credenciales RTSP y destino de Telegram movidos a `config/.env` (`DVR_RTSP_*`, `DVR_TELEGRAM_CHAT_ID`)
- `cam_watcher.py`: logs de destinatarios Telegram ahora usan recarga dinámica (`get_telegram_chat_ids()`)

### Limpieza técnica
- `antigravity_client.py`: eliminado soporte legado de selección manual de cuenta (`account_index`, `--use-account`)
- `config/.env`: removida variable obsoleta `NUM_ACCOUNTS`

---

## v2.6.0 (2026-02-20)

### Cambios
- **Eliminación completa del sistema multi-cuenta**: Se removió `MultiAccountRateLimiter` (~200 líneas) y se reemplazó por `SimpleRateLimiter` (~60 líneas)
- Se simplificaron `analizar_imagen()`, `procesar_imagen()`, `watch_directory()`, `main()`
- Eliminado `account_index` del flujo principal (`cam_watcher.py`) y la lógica de retry multi-cuenta (persistía soporte CLI legado, removido luego en v2.6.1)
- Rate limit simplificado: 5 imágenes/minuto con una sola cuenta

### captura_alertas.py - Threading
- Convertido de arquitectura single-thread bloqueante a threading con cola
- Socket ahora acepta conexiones sin bloquearse mientras ffmpeg captura
- 2 workers procesan capturas desde una cola (QUEUE_SIZE=50)
- Reintentos reducidos de 3 a 1 (se procesa la siguiente alerta en lugar de reintentar)
- Timeout ffmpeg reducido de 30s a 10s

### Bugs corregidos
- Referencia huérfana a `NUM_ACCOUNTS` en `main()` que causaba crash al iniciar (corregido en la misma sesión, ~90s de downtime)

---

## v3.0.0 (2026-02-17) - Refactorización de estructura

### Cambios
- Unificación de directorios: `dvr/` + `hs/camdetector/` -> `camdetector/`
- Nueva estructura: `src/`, `config/`, `data/`, `logs/`, `docs/`, `services/`
- Actualización de todas las rutas en scripts, servicios systemd y crons
- Enlace simbólico `src/.env -> config/.env`
- Backups completos en `/home/capw/projects/backups/`

Ver `docs/REFACTORIZACION.md` para detalles completos.

---

## v2.5.2 (2026-02-13)

### Cambios
- Configuración actualizada: 1 cuenta activa
- Rate limit: 2 img/min (1 cuenta x 2)

### Funcionalidades disponibles (en ese momento)
- Sistema soportaba hasta 5 cuentas (50 img/min cuando estuvieran activas)
- Rotación automática de cuentas ante error 429
- Bloqueo externo de cuentas agotadas

---

## v2.5.1 (2026-02-13)

### Cambios
- Nuevos criterios de evaluación de sospecha:
  - 50-100%: Persona DENTRO (aunque sea parcialmente) del patio
  - 0-49%: Persona caminando o a pie por vereda/calle
  - Ciclistas, vehículos y mascotas = NO persona
- Prompt simplificado con un solo ejemplo de SI y NO

### Optimizaciones
- Extracción de fecha/hora del nombre/path del archivo (no de la IA)
- Prompts simplificados sin campo FECHA_HORA
- Ahorro de ~80 tokens por análisis (~2000 tokens/día)

---

## v2.5.0 (2026-02-13)

### Nuevas funciones
- Función `extraer_fecha_hora()` extrae timestamp del nombre/path:
  - DVR: `alerta_A1_YYYYMMDD_HHMMSS.jpg`
  - FTP: `/YYYY-MM-DD/HH/pic/archivo.jpg`
- Prompts sin campo FECHA_HORA

### Fixes
- Corregida comparación `'no visible'` -> `'desconocida'`

---

## v2.4.0 (2026-01-31)

### Fixes
- Retry automático con otra cuenta ante error 429 (quota agotada)
- Directorio no_sospechosas movido fuera del directorio monitoreado
- Bloqueo externo de cuentas por duración real del 429

---

## v2.3.0 (2026-01-24)

### Nuevas funciones
- Detección de actividad sospechosa para DVR-A1/A2 (0-100%)
- Solo notifica si sospechosa >= 50%
- Guarda imágenes no sospechosas en directorio separado
- Incluye personas no sospechosas en resumen diario

---

## v1.5.0 (2026-01-24)

### Nuevas funciones
- Rotación de cuentas Antigravity: 5 cuentas para 50 img/min
- Cada cuenta tenía su propio rate limit de 10/min
- Solo entraba en cooldown global si TODAS las cuentas estaban saturadas

---

## v2.11.0 (2026-07-03)

### Cambio de modelo a Gemini 3.1 Flash Lite

**Motivo:** Falsos positivos con `qwen/qwen3.5-flash-02-23` (83% de tasa de FP en
pruebas controladas con 2 imágenes sin persona a 3 resoluciones).

#### Pruebas realizadas (ver `docs/TESTING.md`)

1. **test_fp_2img.py:** 2 imágenes FP × 2 modelos × 3 resoluciones = 12 llamadas.
   - Qwen: 5/12 PERSONA (falso positivo)
   - Gemini 3.1 Flash Lite: 0/12 PERSONA (correcto)

2. **test_gemini_6img.py:** 6 imágenes con sospecha original 55-100% al 50%.
   - Gemini: 6/6 PERSONA (consistente con detección original de Qwen)

#### Cambios

- `config/.env`: `AI_MODEL=qwen/qwen3.5-flash-02-23` → `google/gemini-3.1-flash-lite-preview`
- `docs/README.md`: actualizada tabla de variables y estructura de scripts
- `docs/TESTING.md`: creado con resultados de pruebas
- `scripts/`: organizados en subdirectorios (tests/, benchmarks/, compare/, analysis/, diagnostics/)

#### Archivos generados

- `scripts/tests/test_fp_2img.py` — test comparativo 2 modelos, 3 resoluciones
- `scripts/tests/test_gemini_6img.py` — test Gemini 6 imágenes al 50%
- `data/test_fp_2img_result.json` — resultados prueba 1
- `data/test_gemini_6img.json` — resultados prueba 2
- `docs/TESTING.md` — documentación de pruebas

#### Backup pre-cambio

- `backups/20260703_modelo_gemini_31_flash/`

---

## Versiones anteriores

### 2026-01-31 - Watchdog DVR
- Implementado `watchdog_dvr.py` que verifica cada 5 min que dvr-alertas responda
- Reinicia automáticamente si el socket deja de aceptar conexiones
- Notifica por Telegram cuando hace un reinicio

### 2026-01-29 - Optimización ffmpeg
- Eliminado parámetro `-ss 00:00:01` que causaba delay de 22s por captura
- Tiempo de captura reducido de 22s a 3s (7x más rápido)
