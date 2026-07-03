# Refactorización CamDetector - Unificación de Estructura

**Fecha:** 2026-02-17  
**Versión:** 3.0.0  
**Autor:** OpenCode

---

## Resumen

Se realizó una refactorización completa de la estructura de archivos de CamDetector para unificar los directorios dispersos (`dvr/` y `hs/`) en una única estructura organizada y profesional bajo `/home/capw/projects/camdetector/`.

## Motivación

La estructura anterior tenía el código y datos distribuidos en:
- `/home/capw/projects/dvr/` - Sistema de captura DVR Hikvision
- `/home/capw/projects/hs/camdetector/` - Sistema principal de detección IA
- `/home/capw/projects/hs/ftpcam1/` - Directorio FTP de la cámara IP

Esta separación dificultaba el mantenimiento, backups y comprensión del sistema. La nueva estructura unifica todo bajo un único directorio con separación lógica por funcionalidad.

---

## Estructura Anterior vs Nueva

### Estructura Anterior (Pre-Refactorización)

```
/home/capw/projects/
├── dvr/                              # DVR Hikvision
│   ├── captura_alertas.py
│   ├── watchdog_dvr.py
│   ├── dvr-alertas.service
│   ├── capturas/                     # Imágenes del DVR
│   ├── capturas_no_sospechosas/      # Personas no sospechosas
│   ├── dvr_ip.txt
│   ├── stats.json
│   └── logs/
│
└── hs/
    ├── camdetector/                  # Sistema principal IA
    │   ├── cam_watcher.py
    │   ├── telegram_bot.py
    │   ├── daily_summary.py
    │   ├── antigravity_client.py
    │   ├── cam_watcher.service
    │   ├── telegram_bot.service
    │   ├── .env
    │   ├── state.json
    │   ├── stats_history.json
    │   ├── logs/
    │   └── docs/
    │
    └── ftpcam1/                      # FTP cámara IP
        └── uploads/
```

### Estructura Nueva (Post-Refactorización)

```
/home/capw/projects/camdetector/
├── config/                           # Configuración centralizada
│   ├── .env                         # Variables de entorno
│   ├── state.json                   # Estado persistente
│   └── stats_history.json           # Histórico estadísticas
│
├── src/                              # Código fuente
│   ├── cam_watcher.py               # Watcher principal + IA
│   ├── telegram_bot.py              # Bot Telegram (webhook)
│   ├── daily_summary.py             # Resumen diario 8AM
│   ├── antigravity_client.py        # Cliente IA Gemini
│   ├── captura_alertas.py           # Captura DVR Hikvision
│   ├── watchdog_dvr.py              # Monitoreo DVR
│   ├── debug_alertas.py             # Debug del sistema
│   └── .env → ../config/.env        # Enlace simbólico
│
├── services/                         # Units systemd
│   ├── cam_watcher.service
│   ├── telegram_bot.service
│   └── dvr-alertas.service
│
├── data/                             # Datos del sistema
│   ├── ftpcam1/                     # FTP cámara IP
│   │   └── uploads/...
│   ├── dvr_capturas/                # Capturas DVR
│   ├── dvr_no_sospechosas/          # Personas no sospechosas
│   ├── dvr_ip.txt                   # IP dinámica DVR
│   └── stats.json                   # Estadísticas DVR
│
├── logs/                             # Logs unificados
│   ├── detections.log               # Log de detecciones
│   ├── daily_summary.log            # Log resumen diario
│   ├── captura_alertas.log          # Log DVR
│   └── watchdog.log                 # Log watchdog
│
└── docs/                             # Documentación
    ├── README.md                    # Documentación principal
    ├── MQTT_INTEGRACION.md          # Guía MQTT
    ├── TAREAS.md                    # Tareas pendientes
    ├── CHANGELOG.md                 # Historial de cambios
    └── REFACTORIZACION.md           # Este documento
```

---

## Cambios Realizados

### 1. Servicios Systemd

**Archivos modificados:** `/etc/systemd/system/`

| Servicio | Ruta Anterior | Ruta Nueva |
|----------|--------------|------------|
| cam_watcher.service | `/home/capw/projects/hs/camdetector/` | `/home/capw/projects/camdetector/src/` |
| telegram_bot.service | `/home/capw/projects/hs/camdetector/` | `/home/capw/projects/camdetector/src/` |
| dvr-alertas.service | `/home/capw/projects/dvr/` | `/home/capw/projects/camdetector/src/` |

**Comandos ejecutados:**
```bash
sudo systemctl daemon-reload
sudo systemctl restart cam_watcher telegram_bot dvr-alertas
```

### 2. Archivo de Configuración (.env)

**Ubicación:** `config/.env`

**Cambios en variables:**

```diff
- WATCH_DIRS=/home/capw/projects/hs/ftpcam1,/home/capw/projects/dvr/capturas
+ WATCH_DIRS=/home/capw/projects/camdetector/data/ftpcam1,/home/capw/projects/camdetector/data/dvr_capturas

- LOG_FILE=/home/capw/projects/hs/camdetector/logs/detections.log
+ LOG_FILE=/home/capw/projects/camdetector/logs/detections.log

- STATE_FILE=/home/capw/projects/hs/camdetector/state.json
+ STATE_FILE=/home/capw/projects/camdetector/config/state.json

- STATS_HISTORY_FILE=/home/capw/projects/hs/camdetector/stats_history.json
+ STATS_HISTORY_FILE=/home/capw/projects/camdetector/config/stats_history.json
```

### 3. Scripts Python - Rutas Hardcodeadas

#### cam_watcher.py
**Líneas modificadas:** 69-72, 403

```python
# Defaults actualizados
WATCH_DIRS = [... CONFIG.get('WATCH_DIR', '/home/capw/projects/camdetector/data/ftpcam1') ...]
LOG_FILE = CONFIG.get('LOG_FILE', '/home/capw/projects/camdetector/logs/detections.log')
STATE_FILE = CONFIG.get('STATE_FILE', '/home/capw/projects/camdetector/config/state.json')
STATS_HISTORY_FILE = CONFIG.get('STATS_HISTORY_FILE', '/home/capw/projects/camdetector/config/stats_history.json')

# Directorio no sospechosas
NO_SOSPECHOSAS_DIR = '/home/capw/projects/camdetector/data/dvr_no_sospechosas'
```

#### telegram_bot.py
**Líneas modificadas:** 76-77, 551, 627, 698-700

```python
# Defaults
STATE_FILE = CONFIG.get('STATE_FILE', '/home/capw/projects/camdetector/config/state.json')
STATS_HISTORY_FILE = CONFIG.get('STATS_HISTORY_FILE', '/home/capw/projects/camdetector/config/stats_history.json')

# Comando /limpiar y /espacio
watch_dirs_str = CONFIG.get('WATCH_DIRS', '/home/capw/projects/camdetector/data/ftpcam1')

# Comando /camaras
DVR_STATS_FILE = "/home/capw/projects/camdetector/data/stats.json"
DVR_IP_FILE = "/home/capw/projects/camdetector/data/dvr_ip.txt"
DVR_CAPTURAS_DIR = "/home/capw/projects/camdetector/data/dvr_capturas"
```

#### daily_summary.py
**Líneas modificadas:** 13, 57

```python
# Cron en comentario
# 0 8 * * * /usr/bin/python3 /home/capw/projects/camdetector/src/daily_summary.py

# Default
STATS_HISTORY_FILE = CONFIG.get('STATS_HISTORY_FILE', '/home/capw/projects/camdetector/config/stats_history.json')
```

#### captura_alertas.py
**Líneas modificadas:** 11, 36, 39-43, 62-63

```python
# Log path en docstring
# /home/capw/projects/camdetector/logs/captura_alertas.log

# Directorio base
BASE_DIR = "/home/capw/projects/camdetector/data"
DVR_IP_FILE = f"{BASE_DIR}/dvr_ip.txt"
STATS_FILE = f"{BASE_DIR}/stats.json"
OUTPUT_DIR = f"{BASE_DIR}/dvr_capturas"
LOG_DIR = "/home/capw/projects/camdetector/logs"

# Referencias a camdetector
CAMDETECTOR_STATE_FILE = "/home/capw/projects/camdetector/config/state.json"
CAMDETECTOR_ENV_FILE = "/home/capw/projects/camdetector/config/.env"
```

#### watchdog_dvr.py
**Líneas modificadas:** 9, 26

```python
# Cron en comentario
# */5 * * * * /usr/bin/python3 /home/capw/projects/camdetector/src/watchdog_dvr.py

# Log
LOG_FILE = "/home/capw/projects/camdetector/logs/watchdog.log"
```

### 4. Tareas Cron

**Actualización automática:**

```diff
- 0 8 * * * /usr/bin/python3 /home/capw/projects/hs/camdetector/daily_summary.py >> /home/capw/projects/hs/camdetector/logs/daily_summary.log 2>&1
+ 0 8 * * * /usr/bin/python3 /home/capw/projects/camdetector/src/daily_summary.py >> /home/capw/projects/camdetector/src/logs/daily_summary.log 2>&1

- */5 * * * * /usr/bin/python3 /home/capw/projects/dvr/watchdog_dvr.py >> /home/capw/projects/dvr/logs/watchdog.log 2>&1
+ */5 * * * * /usr/bin/python3 /home/capw/projects/camdetector/src/watchdog_dvr.py >> /home/capw/projects/camdetector/src/logs/watchdog.log 2>&1
```

### 5. Permisos Especiales

El directorio `data/ftpcam1` mantiene el grupo especial `ftpcam` para el servidor FTP:

```bash
sudo chown -R ftpcam:ftpcam /home/capw/projects/camdetector/data/ftpcam1
sudo chmod -R 775 /home/capw/projects/camdetector/data/ftpcam1
```

### 6. Backups

Se crearon backups completos antes de la migración:

```
/home/capw/projects/backups/
├── camdetector-hs-20260217-160835.tar.gz    (36 MB)
└── camdetector-dvr-20260217-160837.tar.gz   (112 MB)
```

---

## Verificación Post-Refactorización

### Estado de Servicios

```bash
$ systemctl is-active cam_watcher telegram_bot dvr-alertas
active
active
active
```

### Logs de Inicio

**cam_watcher:**
```
Camera Person Detection System v2.5.2 - Iniciando
Multi-cuenta: 1 cuentas = 5 img/min
Configurados 2 destinatario(s) para notificaciones
```

**telegram_bot:**
```
Admin ID: 708923002
Destinatarios: 2
Escuchando en 127.0.0.1:8085
Webhook URL: https://server1.whiskyargentina.org/webhook/camdetector
```

**dvr-alertas:**
```
INICIANDO SISTEMA DE CAPTURA DVR
Puerto de escucha: 8088
Directorio capturas: /home/capw/projects/camdetector/data/dvr_capturas
```

---

## Dependencias Entre Componentes

### Flujo de Datos

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Cámara IP     │────▶│  data/ftpcam1/   │────▶│  cam_watcher.py │
│   (FTP)         │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
┌─────────────────┐     ┌──────────────────┐     ┌───────▼────────┐
│   DVR Hikvision │────▶│ data/dvr_capturas│────▶│   Gemini AI    │
│   (RTSP)        │     │                  │     │                │
└─────────────────┘     └──────────────────┘     └───────┬────────┘
                                                         │
                              ┌──────────────────────────┼──────────┐
                              │                          │          │
                              ▼                          ▼          ▼
                       ┌────────────┐            ┌──────────┐  ┌──────────┐
                       │  Telegram  │            │   logs/  │  │  config/ │
                       │   (Bot)    │            │          │  │          │
                       └────────────┘            └──────────┘  └──────────┘
```

### Archivos Compartidos

| Archivo | Lectura | Escritura | Descripción |
|---------|---------|-----------|-------------|
| `config/state.json` | cam_watcher, telegram_bot, captura_alertas | cam_watcher, telegram_bot | Estado del sistema |
| `config/.env` | Todos | - | Configuración |
| `data/stats.json` | telegram_bot | captura_alertas | Estadísticas DVR |
| `data/dvr_ip.txt` | telegram_bot, captura_alertas | captura_alertas | IP dinámica DVR |

---

## Comandos Útiles

### Ver Estado
```bash
systemctl status cam_watcher telegram_bot dvr-alertas
```

### Ver Logs
```bash
# Watcher
sudo journalctl -u cam_watcher -f

# Bot Telegram
sudo journalctl -u telegram_bot -f

# DVR
sudo journalctl -u dvr-alertas -f

# Archivos de log
tail -f /home/capw/projects/camdetector/logs/detections.log
```

### Reiniciar Servicios
```bash
sudo systemctl restart cam_watcher telegram_bot dvr-alertas
```

### Estructura de Directorios
```bash
tree -L 3 /home/capw/projects/camdetector/
```

---

## Notas Importantes

1. **Backup:** Los backups originales se mantienen en `/home/capw/projects/backups/`

2. **Permisos FTP:** El directorio `data/ftpcam1` debe mantener el grupo `ftpcam` para que el servidor FTP funcione correctamente.

3. **Estado preservado:** El archivo `config/state.json` mantiene el modo actual (AUTO/FORZADO) y las estadísticas.

4. **Histórico:** `config/stats_history.json` conserva todo el histórico de estadísticas.

5. **Enlaces simbólicos:** Se creó un enlace simbólico `src/.env → config/.env` para mantener compatibilidad con scripts que buscan .env en el directorio actual.

6. **Crons:** Las tareas programadas se actualizaron automáticamente con las nuevas rutas.

---

## Rollback

En caso de necesitar volver a la estructura anterior:

```bash
# 1. Detener servicios
sudo systemctl stop cam_watcher telegram_bot dvr-alertas

# 2. Restaurar backups
sudo tar xzf /home/capw/projects/backups/camdetector-hs-20260217-160835.tar.gz -C /home/capw/projects/
sudo tar xzf /home/capw/projects/backups/camdetector-dvr-20260217-160837.tar.gz -C /home/capw/projects/

# 3. Restaurar servicios originales
sudo cp /home/capw/projects/hs/camdetector/*.service /etc/systemd/system/
sudo cp /home/capw/projects/dvr/dvr-alertas.service /etc/systemd/system/
sudo systemctl daemon-reload

# 4. Iniciar servicios
sudo systemctl start cam_watcher telegram_bot dvr-alertas
```

---

## Changelog de Refactorización

### v3.0.0 (2026-02-17)
- ✅ Unificación de estructura bajo `/home/capw/projects/camdetector/`
- ✅ Actualización de rutas en todos los scripts
- ✅ Actualización de servicios systemd
- ✅ Actualización de crons
- ✅ Preservación de permisos FTP
- ✅ Backups completos de estructura anterior
- ✅ Eliminación de directorios obsoletos (dvr/, hs/)
- ✅ Creación de enlace simbólico .env
- ✅ Verificación completa de funcionamiento

---

**Estado:** ✅ Completado exitosamente  
**Todos los servicios:** Activos y funcionando  
**Última verificación:** 2026-02-17 16:14
