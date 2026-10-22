# Tareas Pendientes - CamDetector

## Estado Actual (2026-10-22)

- **Versión cam_watcher:** 2.11.0 (gpt-6-luna, provider GitHub Copilot con fallback a OpenRouter)
- **Versión telegram_bot:** 2.6.2
- **Modelo IA:** gpt-6-luna (`AI_PROVIDER=copilot`)
- **Capacidad:** 60 imágenes/minuto
- **Lógica de evento:** gap 10s por cámara, máximo 20 análisis por evento
- **Horario de detección:** 00:01 - 07:45 (modo auto)

---

## Bugs Corregidos (resueltos desde última auditoría 2026-02-13)

| Bug | Estado | Fecha |
|-----|--------|-------|
| Race conditions multi-proceso (JSON) | CORREGIDO - `fcntl.flock` en cam_watcher, telegram_bot, daily_summary | 2026-02-17 |
| TOCTOU en actualización de stats | CORREGIDO - operación atómica read-modify-write con lock exclusivo | 2026-02-17 |
| /logs ignora argumentos | CORREGIDO - args se pasan correctamente al handler | 2026-02-17 |
| /limpiar y /espacio directorio incorrecto | CORREGIDO - usa WATCH_DIRS (plural) | 2026-02-17 |
| fecha_imagen corrupta en state.json | CORREGIDO - formato correcto YYYY-MM-DD HH:MM:SS | 2026-02-17 |
| Histórico no se actualiza cuando IA falla | CORREGIDO - `actualizar_historico()` se llama también en caso de error | 2026-02-17 |
| Lógica horario no soporta cruce medianoche | CORREGIDO - lógica `hora >= inicio or hora <= fin` | 2026-02-17 |
| shutdown ThreadPool wait=False | CORREGIDO - ahora usa `shutdown(wait=True)` | 2026-02-17 |
| _estado_anterior sin uso | CORREGIDO - ahora se lee para comparación antes de notificar | 2026-02-17 |
| Sistema multi-cuenta innecesario | CORREGIDO - eliminado MultiAccountRateLimiter, reemplazado por SimpleRateLimiter | 2026-02-20 |
| Versionado inconsistente en cam_watcher.py | CORREGIDO - docstring actualizado a v2.6.2 | 2026-02-20 |
| Documentación /status vs /estado | CORREGIDO - docs alineadas y alias `/status` agregado | 2026-02-20 |
| Versionado unificado del proyecto | CORREGIDO - `src/app_version.py` como fuente única | 2026-02-20 |
| TELEGRAM_CHAT_IDS desincronizado en logs | CORREGIDO - logs usan `get_telegram_chat_ids()` dinámico | 2026-02-20 |
| NUM_ACCOUNTS obsoleto en `.env` | CORREGIDO - variable eliminada | 2026-02-20 |
| `account_index` legado en cliente IA | CORREGIDO - removido de constructor y CLI | 2026-02-20 |
| Ráfagas de una cámara consumen 5/min en segundos | CORREGIDO - throttle por cámara (`MIN_SECONDS_BETWEEN_ANALYSIS_PER_CAMERA=5.0`) | 2026-02-20 |
| img1 de ráfaga consume análisis; img2+ (donde está la persona) se descartan por throttle | CORREGIDO - arquitectura evento-based: img1=trigger, img2+ analizadas hasta MAX=3 | 2026-02-26 |
| Workers muerden por paquete sin canal (colapsa sistema) | CORREGIDO - centinela _STOP_SIGNAL vs None, descarte pre-encola | 2026-03-01 |

---

## Bugs Vigentes

Sin bugs conocidos de severidad operativa al momento.

---

## Limpieza Opcional

- [ ] Extraer secretos actuales de `config/.env` a un gestor de secretos del VPS
- [ ] Agregar tests automatizados para parsing de respuesta IA
- [ ] Implementar smoke tests automáticos post-deploy
- [ ] Migrar a repo git profesional (ver `docs/TESTING.md` para procedimiento de tests)

---

## Ideas de Mejora

- [ ] Dashboard web para ver estadísticas
- [ ] Integración MQTT con actuadores (ver `docs/MQTT_INTEGRACION.md`)
- [ ] Detección de vehículos (no solo personas)
- [ ] Almacenamiento en base de datos (actualmente JSON)
- [ ] API REST para consultar detecciones
- [ ] Configurar alertas por email además de Telegram
