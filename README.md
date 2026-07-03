# CamDetector — Sistema de Vigilancia con IA

Vigilancia con cámaras DVR Hikvision + cámara IP, análisis con IA y alertas por Telegram.

| Aspecto | Detalle |
|---------|---------|
| Versión | 2.10.11 (2026-04-13) |
| Servicios | `cam_watcher`, `telegram_bot`, `dvr-alertas` |
| Webhook | `https://server1.dev0p.com/webhook/camdetector` |
| IA | OpenRouter (default: `qwen/qwen3.5-flash-02-23`) |
| Ruta | `/home/capw/projects/camdetector/` |

## Documentación

| Archivo | Contenido |
|---------|-----------|
| `docs/README.md` | Documentación completa — arquitectura, servicios, config, troubleshooting |
| `docs/TAREAS.md` | Estado del proyecto, bugs, tareas pendientes |
| `docs/CHANGELOG.md` | Historial de cambios por versión |
| `docs/REFACTORIZACION.md` | Migración de arquitectura y cambios estructurales |
| `docs/MQTT_INTEGRACION.md` | Diseño futuro de integración MQTT (no implementado) |

## Servicios

| Servicio | Script | Función |
|----------|--------|---------|
| `dvr-alertas.service` | `src/captura_alertas.py` | Escucha alertas TCP del DVR, captura frames RTSP |
| `cam_watcher.service` | `src/cam_watcher.py` | Analiza imágenes con IA, envía alertas Telegram |
| `telegram_bot.service` | `src/telegram_bot.py` | Bot Telegram para control remoto (webhook) |

## Comandos Rápidos

```bash
# Estado
systemctl status dvr-alertas cam_watcher telegram_bot

# Logs
journalctl -u cam_watcher -f

# Reiniciar
sudo systemctl restart dvr-alertas cam_watcher telegram_bot
```

## Stack

| Componente | Detalle |
|------------|---------|
| Lenguaje | Python 3 |
| IA | OpenRouter API (Gemini 3.1 Flash Lite) |
| Bot | Telegram Bot API (webhook) |
| Monitoreo DVR | ffmpeg + RTSP |
| Detección archivos | inotifywait |
| Estado | JSON (state.json + stats_history.json) |
