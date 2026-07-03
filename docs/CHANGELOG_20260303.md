# Cambios del 2026-03-03

## Resumen
Modificación del sistema de detección de eventos para implementar:
1. Análisis secuencial FIFO de 5 imágenes por evento
2. Detección de animales (perros/gatos) para cerrar eventos sin alerta (>90% confianza)
3. Early exit en análisis si se detecta persona o animal

## Archivos Modificados

### cam_watcher.py
**Prompts de IA (DETECTION_PROMPT y DETECTION_PROMPT_SOSPECHOSA):**
- Agregado `ANIMAL: SI o NO` - Detecta presencia de animales
- Agregado `ANIMAL_CONF: [0-100]` - Confianza de la detección
- Actualizados ejemplos de respuesta
- Cambio: ciclistas y vehículos ahora solo son NO persona (antes incluía "mascotas")

**Función analizar_imagen():**
- Agregados campos `animal` y `animal_conf` al resultado (líneas 779-780, 781-782, 828-829)
- Parsing de `ANIMAL:` y `ANIMAL_CONF:` de respuesta de IA (líneas 800-810)

**Función _on_analisis_completo() (callback de eventos):**
- Ahora maneja dict en lugar de bool
- Early exit inmediato si `cerrar_evento=True` (persona o animal detectado)
- Log específico para cierre por persona o por animal
- Manejo seguro de retornos `None`

**Buffer de imágenes:**
- Cambio de LIFO a FIFO: `imagenes_nuevas[-1]` → `imagenes_nuevas[0]`
- Comentario actualizado

**Función procesar_imagen():**
- Cambio de retorno: `bool` → `dict{cerrar_evento, persona_detectada, animal_detectado}`
- Retornos explícitos `_resultado_vacio` en casos de error
- Lógica de detección de animal >90% → cierre sin alerta
- Estado de log muestra "ANIMAL" cuando aplica

### .env
- `MAX_ANALISIS_POR_EVENTO=3` → `MAX_ANALISIS_POR_EVENTO=5`

## Comportamiento Nuevo

### Flujo de Evento:
1. **Img1** = trigger (no se analiza)
2. **Img2-6** = análisis secuencial FIFO (5 análisis máximo)
3. Si **img2-5** = persona SI → **alerta + cierre inmediato**
4. Si **img2-5** = animal >90% → **cierre sin alerta** (early exit)
5. Si **img6** = NO persona → **cierre sin alerta**
6. GAP en cualquier momento → cierra evento, siguiente imagen es img1 de nuevo evento

### Estados de Log:
- `PERSONA` - Persona detectada (con/sin alerta según sospecha)
- `ANIMAL` - Animal detectado con >90% confianza (cierre sin alerta)
- `NO` - Ni persona ni animal

## Backups
- cam_watcher_backup_20260303_093338.py
- env_backup_20260303_093338.env

## Pruebas Pendientes
- Verificar logs muestren "ANIMAL" cuando detecte perros/gatos
- Verificar early exit funcione correctamente
- Confirmar análisis FIFO con 5 imágenes máximo

---

## Fix: Bug de Logging Duplicado (2026-03-04)

### Problema
El log "Nueva imagen: ... de cámara ..." aparecía **2 veces** para ciertas imágenes.

### Causa Raíz
El logging ocurría en 2 lugares diferentes:
1. `watch_directory()` - cuando la imagen pasaba el check de horario (línea 1302)
2. `on_imagen_recibida()` - cuando la imagen se procesaba realmente (líneas 939 y 951)

Esto sucedía cuando:
- Imagen pasaba el check de horario → se logueaba en watch_directory
- Rate limiter estaba en cooldown → imagen quedaba pending
- Cuando el cooldown terminaba → se procesaba y se logueaba otra vez en on_imagen_recibida

### Solución Aplicada
- **Eliminar** el logging de "Nueva imagen" de `on_imagen_recibida()` (img1 trigger y img de evento)
- **Mover** el logging en `watch_directory()` a **después** del rate limiter

### Por qué NO rompe nada
- El logging es solo informativo, no afecta la lógica de detección
- La imagen sigue el mismo flujo: check horario → rate limiter → on_imagen_recibida → análisis
- Solo se eliminaron 2 líneas de logging y se agregó 1 en un lugar diferente
- La información mostrada es la misma (una sola vez en lugar de dos)

### Archivos modificados
- `cam_watcher.py`:
  - Línea 939: eliminado `logger.info(f"Nueva imagen...")` (img1 trigger)
  - Línea 951: eliminado `logger.info(f"Nueva imagen...")` (img evento)
  - Línea ~1319: agregado `logger.info(f"Nueva imagen...")` después de rate limiter

### Verificación
- Sintaxis Python: OK
- Servicio reiniciado: OK
- No afecta la lógica de detección, solo el logging
