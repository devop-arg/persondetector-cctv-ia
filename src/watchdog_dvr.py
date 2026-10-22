#!/usr/bin/env python3
"""
Watchdog para DVR Alertas - Monitorea y reinicia el servicio si se traba

Este script verifica que el servicio dvr-alertas esté respondiendo correctamente.
Si el socket está ocupado pero no acepta conexiones nuevas, reinicia el servicio.

Ejecutar con cron cada 5 minutos:
    */5 * * * * /usr/bin/python3 /home/capw/projects/camdetector/src/watchdog_dvr.py

Autor: OpenCode
Fecha: 2026-01-31
"""

import socket
import subprocess
import sys
import json
import urllib.request
from datetime import datetime
from pathlib import Path

# Configuración
LISTEN_PORT = 8088
SERVICE_NAME = "dvr-alertas"
LOG_FILE = "/home/capw/projects/camdetector/logs/watchdog.log"
CHECK_TIMEOUT = 5  # Segundos para timeout de conexión
DETECTIONS_LOG = "/home/capw/projects/camdetector/logs/detections.log"
MAX_INACTIVITY_MINUTES = 45
# Archivo de estado: guarda el timestamp del último reinicio por inactividad
RESTART_STATE_FILE = "/home/capw/projects/camdetector/logs/watchdog_last_restart.txt"

# Telegram (misma config que captura_alertas.py)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = "708923002"


def log(mensaje: str):
    """Escribe al log con timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{timestamp}] {mensaje}"
    print(linea)
    
    # Append al archivo de log
    with open(LOG_FILE, "a") as f:
        f.write(linea + "\n")


def enviar_telegram(mensaje: str) -> bool:
    """Envía notificación por Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"Error enviando Telegram: {e}")
        return False


def check_service_status() -> tuple[bool, str]:
    """Verifica el estado del servicio systemd."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        status = result.stdout.strip()
        is_active = status == "active"
        return is_active, status
    except Exception as e:
        return False, str(e)


def check_port_listening() -> bool:
    """Verifica si hay algo escuchando en el puerto."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{LISTEN_PORT}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return f":{LISTEN_PORT}" in result.stdout
    except Exception as e:
        log(f"Error verificando puerto: {e}")
        return False


def check_socket_responsive() -> bool:
    """
    Intenta conectarse al socket para verificar que está respondiendo.
    Retorna True si el socket acepta conexiones, False si no.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CHECK_TIMEOUT)
        result = sock.connect_ex(('127.0.0.1', LISTEN_PORT))
        sock.close()
        return result == 0  # 0 = conexión exitosa
    except Exception as e:
        log(f"Error en check de socket: {e}")
        return False


def guardar_timestamp_reinicio():
    """Guarda el timestamp actual como momento del último reinicio por inactividad."""
    try:
        with open(RESTART_STATE_FILE, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        log(f"Error guardando timestamp de reinicio: {e}")


def leer_timestamp_reinicio() -> datetime | None:
    """Lee el timestamp del último reinicio por inactividad."""
    try:
        path = Path(RESTART_STATE_FILE)
        if not path.exists():
            return None
        contenido = path.read_text().strip()
        return datetime.strptime(contenido, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def check_inactivity() -> bool:
    """
    Verifica si ha habido actividad en el log de detecciones recientemente.
    Solo aplica si estamos en horario activo (00:01 - 07:45).
    Retorna True si la actividad es normal, False si hay silencio sospechoso.
    """
    ahora = datetime.now()
    
    # Solo verificar entre 01:00 y 07:45 para dar margen al inicio
    if not (ahora.hour >= 1 and ahora.hour < 7 or (ahora.hour == 7 and ahora.minute < 45)):
        return True # Fuera de horario crítico de monitoreo de actividad

    # Si ya se reinició por inactividad recientemente, esperar el período completo
    # antes de volver a disparar. Esto evita el loop de reinicios cuando hay
    # inactividad real (ej: noche sin movimiento).
    ultimo_reinicio = leer_timestamp_reinicio()
    if ultimo_reinicio is not None:
        minutos_desde_reinicio = (ahora - ultimo_reinicio).total_seconds() / 60
        if minutos_desde_reinicio < MAX_INACTIVITY_MINUTES:
            return True  # Cooldown post-reinicio activo, no verificar inactividad
        
    try:
        # Buscar la última línea con "Nueva imagen"
        cmd = f"grep 'Nueva imagen' {DETECTIONS_LOG} | tail -n 1"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if not result.stdout:
            return True
            
        # Extraer timestamp: 2026-02-19 00:50:42,598
        linea = result.stdout.strip()
        timestamp_str = linea.split(' | ')[0].split(',')[0]
        ultimo_evento = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        
        minutos_inactivo = (ahora - ultimo_evento).total_seconds() / 60
        
        if minutos_inactivo > MAX_INACTIVITY_MINUTES:
            log(f"ALERTA: Inactividad detectada. Última imagen hace {minutos_inactivo:.1f} min.")
            return False
            
        return True
    except Exception as e:
        log(f"Error verificando inactividad: {e}")
        return True


def restart_service() -> bool:
    """Reinicia el servicio DVR."""
    log("Reiniciando servicio...")
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            log("Servicio reiniciado exitosamente")
            return True
        else:
            log(f"Error al reiniciar: {result.stderr}")
            return False
    except Exception as e:
        log(f"Excepción al reiniciar: {e}")
        return False


def main():
    """Punto de entrada principal."""
    # 1. Verificar estado del servicio
    is_active, status = check_service_status()
    
    if not is_active:
        log(f"Servicio no activo (estado: {status}). Reiniciando...")
        if restart_service():
            enviar_telegram(
                f"⚠️ <b>Watchdog DVR</b>\n\n"
                f"El servicio {SERVICE_NAME} estaba caído.\n"
                f"Estado anterior: {status}\n"
                f"Se reinició automáticamente."
            )
        return 1
    
    # 2. Verificar que el puerto está escuchando
    if not check_port_listening():
        log("Puerto no está escuchando. Reiniciando...")
        if restart_service():
            enviar_telegram(
                f"⚠️ <b>Watchdog DVR</b>\n\n"
                f"El puerto {LISTEN_PORT} no estaba escuchando.\n"
                f"Se reinició el servicio automáticamente."
            )
        return 1
    
    # 3. Verificar que el socket responde (test crítico)
    if not check_socket_responsive():
        log("Socket no responde a conexiones. Reiniciando...")
        if restart_service():
            fecha_hora = datetime.now().strftime("%d-%m %H:%M")
            enviar_telegram(
                f"🚨 <b>Watchdog DVR - Socket trabado</b>\n\n"
                f"El socket en puerto {LISTEN_PORT} no aceptaba conexiones.\n"
                f"Esto indica que el proceso estaba colgado.\n"
                f"Se reinició el servicio automáticamente.\n\n"
                f"<i>Problema detectado el {fecha_hora}</i>"
            )
        return 1
    
    # 4. Verificar inactividad prolongada en horario activo
    if not check_inactivity():
        log("Inactividad prolongada detectada en horario activo. Reiniciando por seguridad...")
        if restart_service():
            # Guardar timestamp del reinicio para evitar reinicios en loop.
            # El watchdog no volverá a dispararse por inactividad durante los próximos 45 min.
            guardar_timestamp_reinicio()
            log(f"Cooldown post-reinicio iniciado. Próxima verificación de inactividad en {MAX_INACTIVITY_MINUTES} min.")
        return 1
    
    # Todo OK - log silencioso (solo en debug)
    # log("Check OK - servicio funcionando correctamente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
