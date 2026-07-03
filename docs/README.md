# CamDetector - Sistema de Vigilancia con IA

Sistema de vigilancia que combina cámaras de seguridad (DVR Hikvision + cámara IP por FTP) con análisis de inteligencia artificial para detectar personas y enviar alertas por Telegram.

## Arquitectura General

```
┌─────────────────┐                      ┌──────────────────────┐
│  DVR Hikvision  │     Puerto 8088      │  captura_alertas.py  │
│  DS-7208HQHI-K1 │ ────────────────────>│  (dvr-alertas.service)│
│  (IP dinámica)  │   Alerta TCP         │  Threading + Queue    │
└─────────────────┘                      └──────────┬───────────┘
                                                    │ RTSP :5554
                                                    │ ffmpeg
                                                    ▼
┌─────────────────┐                      ┌──────────────────────┐
│  Cámara IP      │     FTP              │  data/dvr_capturas/  │
│  (ftpcam1)      │ ───────────────────> │  data/ftpcam1/       │
└─────────────────┘                      └──────────┬───────────┘
                                                    │ inotifywait
                                                    ▼
                                         ┌──────────────────────┐
│  cam_watcher.py      │
│  (cam_watcher.service)│
│  v2.10.1             │
                                         └──────────┬───────────┘
                                                    │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                             ┌────────────┐  ┌────────────┐  ┌────────────┐
│ OpenRouter │  │  Telegram  │  │   Logs +   │
                              │ OpenRouter │  │  Telegram  │  │   Logs +   │
                              │  (Gemini)  │  │  Bot/Fotos │  │   Stats    │
                             └────────────┘  └────────────┘  └────────────┘
```

## Servicios

El sistema se compone de 3 servicios systemd + 2 tareas cron:

| Servicio | Script | Función |
|----------|--------|---------|
| `dvr-alertas.service` | `src/captura_alertas.py` | Escucha alertas TCP del DVR, captura frames RTSP |
| `cam_watcher.service` | `src/cam_watcher.py` | Analiza imágenes con IA, envía alertas Telegram |
| `telegram_bot.service` | `src/telegram_bot.py` | Bot Telegram para control remoto (webhook) |
| Cron cada 5 min | `src/watchdog_dvr.py` | Monitorea que dvr-alertas esté funcionando |
| Cron 8:00 AM | `src/daily_summary.py` | Envía resumen diario por Telegram |

## Estructura de Archivos

```
/home/capw/projects/camdetector/
├── config/                           # Configuración
│   ├── .env                         # Variables de entorno
│   ├── state.json                   # Estado persistente (modo, stats del día)
│   └── stats_history.json           # Histórico de estadísticas por día
│
├── src/                              # Código fuente
│   ├── cam_watcher.py               # Watcher principal + IA (v2.10.1)
│   ├── captura_alertas.py           # Captura DVR Hikvision (threading)
│   ├── telegram_bot.py              # Bot Telegram webhook (v2.6.2)
│   ├── app_version.py               # Versionado centralizado del proyecto
│   ├── daily_summary.py             # Resumen diario 8:00 AM
│   ├── watchdog_dvr.py              # Monitoreo DVR cada 5 min
│   ├── debug_alertas.py             # Debug del protocolo DVR
│   └── .env -> ../config/.env       # Enlace simbólico

│
├── scripts/                          # Scripts de utilidad
│   ├── tests/                       # Tests de modelos (test_*.py)
│   ├── benchmarks/                  # Benchmarks de rendimiento (benchmark_*.py)
│   ├── compare/                     # Comparación de modelos (compare_*.py)
│   ├── analysis/                    # Análisis y re-análisis (analyze_*, reanalyze_*.py)
│   ├── diagnostics/                 # Diagnósticos (diag_*.py)
│   └── mqtt-firewall-sync/         # Sincronización firewall MQTT
│
├── backups/                          # Backups pre-modificación
│   └── YYYYMMDD_HHMMSS/            # Backup con timestamp
│
├── services/                         # Units systemd (copias de referencia)
│   ├── cam_watcher.service
│   ├── telegram_bot.service
│   └── dvr-alertas.service
│
├── data/                             # Datos
│   ├── dvr_capturas/                # Imágenes capturadas del DVR
│   ├── dvr_no_sospechosas/          # Personas no sospechosas (archivadas)
│   ├── dvr_sospechosas/             # Personas sospechosas (notificadas)
│   ├── referencia_ia/               # Imágenes de referencia para pruebas
│   ├── ftpcam1/                     # FTP de cámara IP (grupo: ftpcam)
│   ├── dvr_ip.txt                   # IP dinámica actual del DVR
│   └── stats.json                   # Estadísticas DVR por canal/día
│
├── logs/                             # Logs (rotación automática)
│   ├── detections.log               # Log de cam_watcher
│   ├── captura_alertas.log          # Log de DVR
│   ├── daily_summary.log            # Log resumen diario
│   └── watchdog.log                 # Log watchdog
│
└── docs/                             # Documentación
    ├── README.md                    # Este archivo
    ├── CHANGELOG.md                 # Historial de cambios
    ├── TAREAS.md                    # Bugs y tareas pendientes
    ├── MQTT_INTEGRACION.md          # Diseño futuro MQTT (no implementado)
    └── REFACTORIZACION.md           # Registro de la migración v3.0.0
```

## Componentes en Detalle

### 1. captura_alertas.py (dvr-alertas.service)

Escucha en el puerto TCP 8088 las alertas de movimiento del DVR Hikvision.

**Funcionamiento:**
- El DVR envía paquetes TCP con firma Hikvision (`ff 00 02 59`) cuando detecta movimiento
- El script valida la firma y extrae el canal (A1-A4) del byte 27
- Auto-detecta la IP del DVR (es dinámica, cambia ~1 vez/semana)
- Captura un frame HD (1080p, ~700KB) vía RTSP usando ffmpeg
- Usa threading con cola + 2 workers para no bloquear el socket

**Cooldown:**
- **Modo ACTIVO** (00:01-07:45): Sin cooldown, captura todo
- **Modo INACTIVO** (resto del día): 1 captura cada 10 min por cámara

**Mapeo de canales:**

| Byte 27 | Canal | Cámara | URL RTSP |
|---------|-------|--------|----------|
| 0x01 | 1 | A01 | .../Channels/101 |
| 0x02 | 2 | A02 | .../Channels/201 |
| 0x04 | 3 | A03 | .../Channels/301 |
| 0x08 | 4 | A04 | .../Channels/401 |

**Constantes clave:**
```python
LISTEN_PORT = 8088
DVR_RTSP_PORT = 5554
COOLDOWN_INACTIVO_SEG = 600    # 10 minutos
MAX_IMAGENES = 100             # Limpieza automática
TIMEOUT_FFMPEG = 10            # Segundos
NUM_WORKERS = 2                # Workers concurrentes
QUEUE_SIZE = 50                # Cola de alertas
```

### 2. cam_watcher.py (cam_watcher.service) - v2.10.1

Monitorea los directorios de imágenes y analiza con OpenRouter para detectar personas.

**Funcionamiento:**
- Usa `inotifywait` para detectar nuevas imágenes en `data/dvr_capturas/` y `data/ftpcam1/`
- **Fondo subtraction**: compara cada imagen con una referencia almacenada (escena "vacía"). Si diferencia < 5%, descarta sin llamar a la IA
- Agrupa las imágenes por evento (gap > 10s entre imágenes = nuevo evento)
- img1 de cada evento actúa solo como trigger — no se envía a la IA
- img2 en adelante se analizan con OpenRouter, hasta un máximo de 20 análisis por evento
- La IA responde si hay persona y, para A1/A2, nivel de sospecha (0-100%)
- Si sospecha >= 50%: envía foto + alerta por Telegram
- Registra estadísticas en `state.json` y `stats_history.json`

**Lógica de evento por cámara:**

```
img1 llega → gap > 10s desde última imagen → NUEVO EVENTO, no analizar (solo trigger)
img2+ llega → gap <= 10s → mismo evento:
  - analisis_count >= MAX(20) → ignorar
  - análisis en curso → acumular en buffer
  - libre → lanzar análisis con esta imagen

Al completar análisis:
  - SI persona sospechosa >= 50% → cerrar evento + notificar
  - SI persona no sospechosa < 50% → continuar evento
  - NO persona → actualizar referencia fondo + analizar siguiente
```

**Fondo subtraction (background subtraction):**
```
Nueva imagen → Comparar con última referencia (misma cámara)
  ├─ Diferencia < 5% → ⏭️ SKIP (solo ruido, no se envía a IA)
  └─ Diferencia > 5% → ✅ Continuar con intercalado y IA

Cuando IA responde NO_PERSONA → Actualizar referencia de fondo
  └─ Solo si desviación < 5% vs referencia anterior
```
- Resolución de comparación: 960x540 (50%, ~112ms por imagen)
- Resolución enviada a IA: configurable con `AI_IMAGE_SCALE` (actual: 50%)
- Cámaras activas: DVR-A1 (configurable en `FONDO_CAMARAS_ACTIVAS`)
- Desactivar: `FONDO_SUBTRACTION_ENABLED=false` en `.env`

**Rate limiting:**
- Máximo 60 imágenes/minuto (SimpleRateLimiter)
- Cooldown de 30 segundos cuando se alcanza el límite

**Modos de operación (state.json):**
- `auto`: Analiza solo en horario activo (00:01-07:45)
- `forzado_activado`: Analiza siempre (24/7)
- `forzado_desactivado`: No analiza nunca

### 3. telegram_bot.py (telegram_bot.service) - v2.6.2

Bot Telegram por webhook para control remoto del sistema.

**Endpoint:** `https://server1.whiskyargentina.org/webhook/camdetector`
**Puerto local:** 127.0.0.1:8085

**Comandos principales:**
- `/estado` (o `/status`) - Estado del sistema
- `/activar` / `/desactivar` - Forzar modo
- `/auto` - Volver a modo automático
- `/logs [servicio] [N]` - Ver últimas N líneas de logs
- `/camaras` - Estado de cámaras y DVR
- `/espacio` - Uso de disco
- `/limpiar` - Limpieza manual de imágenes
- `/stats` - Estadísticas del día
- `/help` - Lista de comandos

### 4. daily_summary.py (Cron 8:00 AM)

Envía un resumen diario por Telegram al terminar el horario de vigilancia.

Incluye: imágenes recibidas, analizadas, personas detectadas, sospechosas, por cámara.

### 5. watchdog_dvr.py (Cron cada 5 min)

Monitorea que `dvr-alertas.service` esté funcionando:
1. Verifica que el servicio systemd esté activo
2. Verifica que el puerto 8088 esté escuchando
3. Prueba que el socket acepta conexiones
4. Si falla, reinicia el servicio y notifica por Telegram

## Configuración

### Variables de entorno (config/.env)

| Variable | Valor actual | Descripción |
|----------|-------------|-------------|
| `TELEGRAM_BOT_TOKEN` | 818016... | Token del bot |
| `TELEGRAM_CHAT_IDS` | 708923002,1311417865 | Destinatarios de alertas |
| `TELEGRAM_ADMIN_ID` | 708923002 | Admin que puede usar comandos |
| `WATCH_DIRS` | .../ftpcam1,.../dvr_capturas | Directorios monitoreados |
| `DETECCION_INICIO` | 00:01 | Inicio horario activo |
| `DETECCION_FIN` | 07:45 | Fin horario activo |
| `AI_MODEL` | google/gemini-3.1-flash-lite-preview | Modelo de IA |
| `AI_IMAGE_SCALE` | 50 | % de resolución enviada a la IA |
| `DVR_RTSP_USER` | cata | Usuario RTSP del DVR |
| `DVR_RTSP_PASS` | DVR_PASSWORD | Password RTSP del DVR |
| `DVR_RTSP_PORT` | 5554 | Puerto RTSP del DVR |
| `DVR_TELEGRAM_CHAT_ID` | 708923002 | Chat para alertas operativas de DVR |
| `RATE_LIMIT_MAX` | 60 | Imágenes/minuto |
| `EVENTO_GAP_SEGUNDOS` | 10.0 | Segundos de silencio que marcan inicio de nuevo evento por cámara |
| `MAX_ANALISIS_POR_EVENTO` | 20 | Máximo de análisis IA por evento (img1 no cuenta, es solo trigger) |
| `FONDO_SUBTRACTION_ENABLED` | true | Activar filtro de fondo |
| `FONDO_UMBRAL` | 30 | Diferencia por pixel (0-255) para considerar "cambiado" |
| `FONDO_PORCENTAJE_CAMBIO` | 5.0 | % mínimo de píxeles cambiados para analizar |

### DVR Hikvision

| Parámetro | Valor |
|-----------|-------|
| Modelo | DS-7208HQHI-K1 |
| IP | Dinámica (guardada en `data/dvr_ip.txt`) |
| Puerto RTSP | 5554 |
| Credenciales | cata / DVR_PASSWORD |
| Canales | 4 (A1-A4) |

Configuración en el DVR: Configuración > Red > Centro de Alarmas > IP del VPS, puerto 8088, protocolo TCP.

## Comandos de Operación

### Estado de servicios

```bash
systemctl status dvr-alertas cam_watcher telegram_bot
```

### Logs en tiempo real

```bash
# DVR
journalctl -u dvr-alertas -f

# Watcher (análisis IA)
journalctl -u cam_watcher -f

# Bot Telegram
journalctl -u telegram_bot -f
```

### Reiniciar servicios

```bash
sudo systemctl restart dvr-alertas cam_watcher telegram_bot
```

### Verificaciones rápidas

```bash
# IP actual del DVR
cat /home/capw/projects/camdetector/data/dvr_ip.txt

# Modo actual
cat /home/capw/projects/camdetector/config/state.json | python3 -m json.tool

# Imágenes recientes
ls -lt /home/capw/projects/camdetector/data/dvr_capturas/ | head -10

# Puerto DVR escuchando
ss -tlnp | grep 8088
```

## Flujo de Alertas (ejemplo real)

```
1. DVR detecta movimiento en cámara A1
2. DVR envía ~6-8 paquetes TCP al puerto 8088 (1 por segundo)
3. captura_alertas.py:
   - Primer paquete: captura frame HD vía RTSP (3 seg)
   - Resto de paquetes: descartados por cooldown (si modo inactivo)
   - Imágenes guardadas: data/dvr_capturas/alerta_A1_20260223_072001.jpg, ...072003.jpg, ...072005.jpg
4. cam_watcher.py detecta nuevas imágenes (inotifywait)
5. Verifica modo: ¿está en horario activo?
   - NO → registra stats pero no analiza
   - SI → lógica evento-based:
       img1 (alerta_A1_..._072001): gap > 8s → nuevo evento, no analiza (trigger)
       img2 (alerta_A1_..._072003): gap 2s → mismo evento → lanza análisis #1
       img3 (alerta_A1_..._072005): gap 2s → mismo evento → queda en buffer
6. La IA responde análisis #1: persona detectada, sospecha 75%
7. cam_watcher envía foto por Telegram, cierra evento
```

## Volumen de Alertas Típico

El DVR es muy verboso. Datos reales del 2026-02-20:

- **Conexiones TCP recibidas:** ~94 en medio día
- **Bursts por evento:** 5-8 conexiones en 6-10 segundos
- **Eventos prolongados:** hasta 30+ conexiones en 2 minutos
- **Capturas reales (modo inactivo):** ~4 (cooldown 10min descarta el resto)
- **Capturas reales (modo activo):** todas

## Troubleshooting

### El servicio dvr-alertas no captura imágenes

```bash
# Verificar que el DVR puede alcanzar el VPS
journalctl -u dvr-alertas -n 20 --no-pager

# Probar RTSP manualmente
DVR_IP=$(cat /home/capw/projects/camdetector/data/dvr_ip.txt)
ffmpeg -rtsp_transport tcp -i "rtsp://cata:DVR_PASSWORD@${DVR_IP}:5554/Streaming/Channels/101" -frames:v 1 test.jpg

# Verificar puerto
ss -tlnp | grep 8088
```

### cam_watcher no analiza imágenes

```bash
# Verificar modo
cat /home/capw/projects/camdetector/config/state.json

# Verificar horario (solo analiza 00:01-07:45 en modo auto)
date

# Verificar rate limit
journalctl -u cam_watcher -n 30 --no-pager | grep -i "rate\|limit\|cooldown"
```

### Bot Telegram no responde

```bash
# Estado del servicio
systemctl status telegram_bot

# Verificar webhook
journalctl -u telegram_bot -n 20 --no-pager

# El webhook debe estar accesible en:
# https://server1.whiskyargentina.org/webhook/camdetector
```

## Backups

Backups de la migración (2026-02-17):
```
/home/capw/projects/backups/
├── camdetector-hs-20260217-160835.tar.gz    (estructura anterior hs/)
└── camdetector-dvr-20260217-160837.tar.gz   (estructura anterior dvr/)
```

---

**Última actualización:** 2026-02-26
**Versiones:** cam_watcher v2.7.0 | telegram_bot v2.6.2
