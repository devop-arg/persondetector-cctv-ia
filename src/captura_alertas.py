#!/usr/bin/env python3
"""
Sistema de Captura de Alertas DVR
Escucha alertas del DVR Hikvision y captura frames HD del stream RTSP.

Uso:
    python3 captura_alertas.py          # Ejecución directa
    systemctl start dvr-alertas         # Como servicio

Logs:
    /home/capw/projects/camdetector/logs/captura_alertas.log
"""

import socket
import subprocess
import os
import glob
import logging
import re
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, date
from logging.handlers import TimedRotatingFileHandler
import threading
from queue import Queue, Full
from pathlib import Path
from typing import List, Optional, Tuple

# --- CONFIGURACIÓN ---
def load_env():
    """Carga variables de entorno desde config/.env."""
    env_path = Path("/home/capw/projects/camdetector/config/.env")
    config = {}
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    return config


CONFIG = load_env()

LISTEN_PORT = 8088


def parse_channel_list(raw_value: str, fallback: Tuple[int, ...]) -> Tuple[int, ...]:
    """Parsea una lista de canales separada por coma y mantiene el orden."""
    canales = []
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            canal = int(part)
        except ValueError:
            continue
        if canal not in canales:
            canales.append(canal)
    return tuple(canales) if canales else fallback

# ============================================
# IP DEL DVR - AUTO-DETECCIÓN
# ============================================
# La IP del DVR es dinámica y cambia aproximadamente 1 vez por semana.
# El sistema detecta automáticamente la nueva IP cuando recibe un paquete
# con firma Hikvision válida (ff 00 02 59).
# 
# La IP actual se guarda en: /home/capw/projects/camdetector/data/dvr_ip.txt
# Para forzar una IP manualmente, editar ese archivo y reiniciar.
# ============================================
BASE_DIR = "/home/capw/projects/camdetector/data"
DVR_IP_FILE = f"{BASE_DIR}/dvr_ip.txt"
STATS_FILE = f"{BASE_DIR}/stats.json"
OUTPUT_DIR = f"{BASE_DIR}/dvr_capturas"
LOG_DIR = "/home/capw/projects/camdetector/logs"
CAPTURA_LOG_FILE = CONFIG.get("CAPTURA_LOG_FILE", f"{LOG_DIR}/captura_alertas.log")
CAPTURA_LOG_LEVEL = CONFIG.get("CAPTURA_LOG_LEVEL", "INFO").upper()
CAPTURA_LOG_WEEKLY_BACKUPS = int(CONFIG.get("CAPTURA_LOG_WEEKLY_BACKUPS", "12"))
CAPTURA_LOG_EXCLUDE_KEYWORDS = [
    kw.strip()
    for kw in CONFIG.get(
        "CAPTURA_LOG_EXCLUDE_KEYWORDS",
        "Conexión recibida desde,Alerta de canal:,Cooldown activo para"
    ).split(",")
    if kw.strip()
]

# Credenciales RTSP del DVR (configuradas en config/.env)
DVR_USER = CONFIG.get("DVR_RTSP_USER", "cata")
DVR_PASS = CONFIG.get("DVR_RTSP_PASS", "cata0106")
DVR_RTSP_PORT = int(CONFIG.get("DVR_RTSP_PORT", "5554"))

# ============================================
# TELEGRAM - Notificaciones de cambio de IP
# ============================================
TELEGRAM_BOT_TOKEN = CONFIG.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = CONFIG.get("DVR_TELEGRAM_CHAT_ID", CONFIG.get("TELEGRAM_ADMIN_ID", ""))

# ============================================
# COOLDOWN - Control de capturas
# ============================================
# Cuando el sistema de detección está INACTIVO: 1 captura cada 10 min por cámara
# Cuando está ACTIVO: captura siempre (la IA decide si es alarma)
COOLDOWN_INACTIVO_SEG = 600  # 10 minutos
CAMDETECTOR_STATE_FILE = "/home/capw/projects/camdetector/config/state.json"
CAMDETECTOR_ENV_FILE = "/home/capw/projects/camdetector/config/.env"

# Mapeo de cámaras del DVR: A1..A8
# URL RTSP: canal 1 = 101 (stream principal), 102 (substream)
MAX_IMAGENES = 500      # Máximo de imágenes a mantener
TIMEOUT_FFMPEG = 10     # Timeout en segundos para ffmpeg
MIN_SIZE_KB = 50        # Tamaño mínimo válido para una imagen 1080p
MAX_REINTENTOS = 1      # Sin reintentos - se procesa la siguiente alerta
TCP_READ_CHUNK_SIZE = int(CONFIG.get("TCP_READ_CHUNK_SIZE", "4096"))
TCP_MAX_PAYLOAD_BYTES = int(CONFIG.get("TCP_MAX_PAYLOAD_BYTES", "16384"))
TCP_IDLE_TIMEOUT = float(CONFIG.get("TCP_IDLE_TIMEOUT", "0.25"))

# ============================================
# COLA DE TRABAJO - Previene bloqueo del socket
# ============================================
QUEUE_SIZE = int(CONFIG.get("CAPTURA_QUEUE_SIZE", "100"))  # Tamaño máximo de la cola de alertas
NUM_WORKERS = int(CONFIG.get("CAPTURA_NUM_WORKERS", "4"))  # Número de workers concurrentes
alert_queue = Queue(maxsize=QUEUE_SIZE)
_STOP_SIGNAL = object()  # Centinela para detener workers (distinto de None)

# Variables globales
_dvr_ip = None
_ultima_captura_por_canal = {}  # {canal: timestamp}
_capturas_inactivas_en_progreso = {}  # {canal: timestamp previo} para reservar cooldown durante la captura

# Locks para thread safety
_stats_lock = threading.Lock()
_cooldown_lock = threading.Lock()

DVR_CHANNELS = tuple(range(1, 9))
CANALES_VALIDOS = DVR_CHANNELS
DVR_ENABLED_CHANNELS = parse_channel_list(CONFIG.get("DVR_ENABLED_CHANNELS", "1,2,3,4"), (1, 2, 3, 4))
BITMASK_CANALES = (
    (0x01, 1),
    (0x02, 2),
    (0x04, 3),
    (0x08, 4),
    (0x10, 5),
    (0x20, 6),
    (0x40, 7),
    (0x80, 8),
)


@dataclass(frozen=True)
class AlertaCaptura:
    """Representa una alerta lista para encolar por canal."""

    canal: int
    origen: str
    byte27: Optional[int] = None
    canales_mask: Tuple[int, ...] = ()

def get_dvr_ip():
    """Obtiene la IP actual del DVR desde el archivo o memoria."""
    global _dvr_ip
    if _dvr_ip:
        return _dvr_ip
    try:
        with open(DVR_IP_FILE, 'r') as f:
            _dvr_ip = f.read().strip()
            return _dvr_ip
    except FileNotFoundError:
        return None

def set_dvr_ip(new_ip):
    """Guarda la nueva IP del DVR."""
    global _dvr_ip
    old_ip = _dvr_ip
    _dvr_ip = new_ip
    with open(DVR_IP_FILE, 'w') as f:
        f.write(new_ip)
    return old_ip

# ============================================
# TELEGRAM
# ============================================

def enviar_telegram(mensaje):
    """Envía un mensaje de texto a Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado en .env (TELEGRAM_BOT_TOKEN/DVR_TELEGRAM_CHAT_ID)")
        return False
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
        log.error(f"Error enviando Telegram: {e}")
        return False

def notificar_cambio_ip(old_ip, new_ip):
    """Notifica por Telegram cuando cambia la IP del DVR."""
    mensaje = (
        f"🔄 <b>IP del DVR actualizada</b>\n\n"
        f"Anterior: <code>{old_ip or 'ninguna'}</code>\n"
        f"Nueva: <code>{new_ip}</code>\n"
        f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    enviar_telegram(mensaje)

# ============================================
# ESTADÍSTICAS
# ============================================

def cargar_stats():
    """Carga las estadísticas desde el archivo JSON."""
    try:
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"dias": {}}

def guardar_stats(stats):
    """Guarda las estadísticas en el archivo JSON."""
    with open(STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def stats_template():
    """Plantilla base de estadísticas por canal del DVR."""
    base = {f"A{canal}": 0 for canal in DVR_CHANNELS}
    base["total"] = 0
    return base

def registrar_captura(canal):
    """Registra una captura en las estadísticas (thread-safe)."""
    with _stats_lock:
        stats = cargar_stats()
        hoy = date.today().isoformat()
        
        if hoy not in stats["dias"]:
            stats["dias"][hoy] = stats_template()
        
        camara = f"A{canal}"
        if camara in stats["dias"][hoy]:
            stats["dias"][hoy][camara] += 1
        stats["dias"][hoy]["total"] += 1
        
        # Limpiar días antiguos (mantener últimos 30)
        dias_ordenados = sorted(stats["dias"].keys(), reverse=True)
        if len(dias_ordenados) > 30:
            for dia in dias_ordenados[30:]:
                del stats["dias"][dia]
        
        guardar_stats(stats)

def obtener_stats_hoy():
    """Obtiene las estadísticas del día actual."""
    stats = cargar_stats()
    hoy = date.today().isoformat()
    return stats["dias"].get(hoy, stats_template())

# ============================================
# HELPERS
# ============================================

def format_canales(canales: List[int]) -> str:
    """Formatea una lista de canales como A1,A2,A3."""
    return ",".join(f"A{canal}" for canal in canales)


def canales_desde_mascara(valor: int) -> List[int]:
    """Expande byte27 como bitmask real para A1-A8."""
    return [canal for bit, canal in BITMASK_CANALES if valor & bit]


# ============================================
# COOLDOWN / CONTROL DE CAPTURAS
# ============================================

def esta_detector_activo():
    """Verifica si el sistema de detección de personas está activo."""
    try:
        # Leer estado del detector
        with open(CAMDETECTOR_STATE_FILE, 'r') as f:
            state = json.load(f)
        
        modo = state.get("modo", "auto")
        
        if modo == "forzado_activado":
            return True
        elif modo == "forzado_desactivado":
            return False
        else:  # modo auto
            # Leer horario del .env
            hora_inicio = "00:00"
            hora_fin = "07:30"
            try:
                with open(CAMDETECTOR_ENV_FILE, 'r') as f:
                    for linea in f:
                        if linea.startswith("DETECCION_INICIO="):
                            hora_inicio = linea.split("=")[1].strip()
                        elif linea.startswith("DETECCION_FIN="):
                            hora_fin = linea.split("=")[1].strip()
            except:
                pass
            
            ahora = datetime.now().strftime("%H:%M")
            # Manejar horario que cruza medianoche
            if hora_inicio <= hora_fin:
                return hora_inicio <= ahora <= hora_fin
            else:
                return ahora >= hora_inicio or ahora <= hora_fin
    except:
        return False

def reservar_captura(canal):
    """
    Reserva una captura para un canal.

    En modo activo siempre permite capturar.
    En modo inactivo aplica cooldown por cámara y evita carreras entre workers
    reservando el canal antes de lanzar ffmpeg.

    Returns:
        (permitido, detector_activo, motivo)
    """
    detector_activo = esta_detector_activo()
    if detector_activo:
        return True, True, "activo"

    with _cooldown_lock:
        ahora = datetime.now().timestamp()
        if canal in _capturas_inactivas_en_progreso:
            return False, False, "captura_en_progreso"

        ultima = _ultima_captura_por_canal.get(canal, 0)
        delta = ahora - ultima
        if delta < COOLDOWN_INACTIVO_SEG:
            restante = int(COOLDOWN_INACTIVO_SEG - delta)
            return False, False, f"cooldown_restante={restante}s"

        _capturas_inactivas_en_progreso[canal] = ultima
        _ultima_captura_por_canal[canal] = ahora
        return True, False, "reservada"


def finalizar_reserva_captura(canal, detector_activo, captura_exitosa):
    """Confirma o revierte la reserva creada por reservar_captura()."""
    if detector_activo:
        return

    with _cooldown_lock:
        ultima_previa = _capturas_inactivas_en_progreso.pop(canal, None)
        if captura_exitosa:
            return
        if ultima_previa:
            _ultima_captura_por_canal[canal] = ultima_previa
        else:
            _ultima_captura_por_canal.pop(canal, None)

def get_rtsp_url(canal):
    """Genera la URL RTSP para el canal especificado."""
    dvr_ip = get_dvr_ip()
    if not dvr_ip:
        return None
    # Canal 1 -> 101, Canal 2 -> 201, Canal 3 -> 301, Canal 4 -> 401
    channel_id = canal * 100 + 1  # Stream principal (01)
    return f"rtsp://{DVR_USER}:{DVR_PASS}@{dvr_ip}:{DVR_RTSP_PORT}/Streaming/Channels/{channel_id}"

def es_paquete_hikvision(data):
    """Verifica si el paquete tiene firma Hikvision válida."""
    if len(data) >= 8:
        # Firma: bytes 4-7 = ff 00 02 59
        return data[4:8] == b'\xff\x00\x02\x59'
    return False

# --- CREAR DIRECTORIOS ---
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- CONFIGURAR LOGGING ---
class ExcludeKeywordsFilter(logging.Filter):
    """Oculta mensajes ruidosos por substring sin perder warnings/errores."""

    def __init__(self, keywords):
        super().__init__()
        self.keywords = [keyword.lower() for keyword in keywords]

    def filter(self, record):
        if record.levelno >= logging.WARNING or not self.keywords:
            return True
        message = record.getMessage().lower()
        return not any(keyword in message for keyword in self.keywords)


def setup_logging():
    """Configura logging con rotación semanal de archivos."""
    logger = logging.getLogger("dvr_alertas")
    logger.setLevel(getattr(logging, CAPTURA_LOG_LEVEL, logging.INFO))
    logger.propagate = False
    logger.handlers.clear()
    
    # Formato de logs
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    noise_filter = ExcludeKeywordsFilter(CAPTURA_LOG_EXCLUDE_KEYWORDS)
    
    # Handler para archivo con rotación semanal
    file_handler = TimedRotatingFileHandler(
        CAPTURA_LOG_FILE,
        when='W0',
        interval=1,
        backupCount=CAPTURA_LOG_WEEKLY_BACKUPS,
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(noise_filter)
    logger.addHandler(file_handler)
    
    # Handler para consola (útil cuando se ejecuta manualmente)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(noise_filter)
    logger.addHandler(console_handler)
    
    return logger

log = setup_logging()
log.info(f"Log captura: nivel={CAPTURA_LOG_LEVEL} | backups semanales={CAPTURA_LOG_WEEKLY_BACKUPS}")
log.info(
    "Filtros captura: %s",
    ", ".join(CAPTURA_LOG_EXCLUDE_KEYWORDS) if CAPTURA_LOG_EXCLUDE_KEYWORDS else "sin filtros"
)


# ============================================
# WORKERS - Procesamiento concurrente
# ============================================

def worker_procesar_alerta(worker_id):
    """Worker que procesa alertas desde la cola."""
    log.info(f"Worker {worker_id} iniciado")
    while True:
        try:
            canal_info = alert_queue.get()
            if canal_info is _STOP_SIGNAL:  # Señal de parada
                log.info(f"Worker {worker_id} detenido")
                break
            try:
                ejecutar_captura_hd(canal_info)
            except Exception as e:
                log.error(f"Worker {worker_id} error: {e}")
            finally:
                alert_queue.task_done()
        except Exception as e:
            log.error(f"Worker {worker_id} error crítico: {e}")


def iniciar_workers():
    """Inicia los workers de procesamiento."""
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=worker_procesar_alerta, args=(i+1,), daemon=True)
        t.start()
    log.info(f"QUEUE | workers={NUM_WORKERS} | maxsize={QUEUE_SIZE}")
    log.info(f"QUEUE | canales_habilitados={format_canales(list(DVR_ENABLED_CHANNELS))}")


def extraer_alertas_de_paquete(data):
    """
    Extrae una o varias alertas por canal desde un paquete Hikvision.

    Byte 27 se interpreta como máscara de bits:
      0x01=A1, 0x02=A2, 0x04=A3, 0x08=A4, 0x10=A5, 0x20=A6, 0x40=A7, 0x80=A8
    """
    try:
        # Verificar firma Hikvision: bytes 4-5 = ff00
        if len(data) >= 6 and data[4:6] == b'\xff\x00':
            if len(data) >= 28:
                valor = data[27]
                canales = canales_desde_mascara(valor)
                if canales:
                    if len(canales) > 1:
                        log.info(f"MASK | byte27={valor} | canales={format_canales(canales)}")
                    return [
                        AlertaCaptura(
                            canal=canal,
                            origen="byte27_mask",
                            byte27=valor,
                            canales_mask=tuple(canales),
                        )
                        for canal in canales
                    ]
            elif len(data) >= 9:
                log.info(
                    "DIAG paquete corto Hikvision (%sb) ignorado para captura; "
                    "esperando payload completo antes de inferir canal",
                    len(data),
                )
                return []

        # Fallback: intentar XML
        texto = data.decode('utf-8', errors='replace')
        match = re.search(r'<(?:dyn)?[Cc]hannel[Ii][Dd]>(\d+)</(?:dyn)?[Cc]hannel[Ii][Dd]>', texto)
        if match:
            canal = int(match.group(1))
            if canal in CANALES_VALIDOS:
                return [AlertaCaptura(canal=canal, origen="xml", canales_mask=(canal,))]
        
        # Log para diagnóstico si no se encontró canal
        log.info(f"DIAG paquete ({len(data)}b) sin canal detectado - byte27={data[27] if len(data) > 27 else 'N/A'}")
        
        # Guardar paquete para análisis
        try:
            import hashlib
            fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
            nombre = f"logs/diag_paquete_{fecha}_{hashlib.md5(data[:50]).hexdigest()[:8]}.bin"
            with open(nombre, "wb") as f:
                f.write(data)
            log.info(f"Paquete guardado en {nombre}")
        except Exception as e:
            log.debug(f"Error guardando paquete: {e}")
        
    except Exception as e:
        log.debug(f"Error parseando alerta: {e}")

    return []


def recibir_payload_completo(conn) -> Tuple[bytes, int]:
    """
    Lee todos los fragmentos disponibles de una conexión TCP del DVR.

    El DVR suele enviar un preámbulo corto y luego el payload real en otro
    segmento. Este helper espera un primer bloque con timeout largo y luego
    sigue leyendo hasta timeout corto por inactividad o cierre remoto.
    """
    chunks = []
    bytes_totales = 0
    chunks_recibidos = 0

    conn.settimeout(5.0)
    try:
        primer_chunk = conn.recv(TCP_READ_CHUNK_SIZE)
    except socket.timeout:
        return b"", 0

    if not primer_chunk:
        return b"", 0

    chunks.append(primer_chunk)
    bytes_totales += len(primer_chunk)
    chunks_recibidos += 1

    conn.settimeout(TCP_IDLE_TIMEOUT)
    while bytes_totales < TCP_MAX_PAYLOAD_BYTES:
        try:
            chunk = conn.recv(TCP_READ_CHUNK_SIZE)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        bytes_totales += len(chunk)
        chunks_recibidos += 1

    return b"".join(chunks), chunks_recibidos


def limpiar_imagenes_antiguas():
    """Elimina las imágenes más antiguas si se supera el límite MAX_IMAGENES."""
    patron = os.path.join(OUTPUT_DIR, "alerta_*.jpg")
    imagenes = glob.glob(patron)
    
    if len(imagenes) <= MAX_IMAGENES:
        return
    
    # Ordenar por fecha de modificación (más antigua primero)
    imagenes.sort(key=os.path.getmtime)
    
    # Calcular cuántas hay que borrar
    a_borrar = len(imagenes) - MAX_IMAGENES
    
    for img in imagenes[:a_borrar]:
        try:
            os.remove(img)
            log.info(f"LIMPIEZA: Eliminada imagen antigua: {os.path.basename(img)}")
        except Exception as e:
            log.error(f"No se pudo eliminar {img}: {e}")


def capturar_frame(filename, canal, intento=1):
    """
    Captura un frame del stream RTSP del canal especificado.
    Retorna True si la captura fue exitosa y válida, False en caso contrario.
    """
    rtsp_url = get_rtsp_url(canal)
    
    # Comando FFmpeg optimizado (2026-01-29):
    # -skip_frame nokey: solo decodifica keyframes (frames I completos)
    # -vsync vfr: sincronización variable para evitar frames duplicados
    # NOTA: Se eliminó -ss 00:00:01 que causaba delay de ~20s por esperar keyframes
    #       Sin -ss la captura tarda ~3s en lugar de ~22s
    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-skip_frame", "nokey",      # Solo keyframes (frames I completos)
        "-i", rtsp_url,
        "-frames:v", "1",
        "-vsync", "vfr",
        "-q:v", "2",                 # Calidad alta (1-31, menor=mejor)
        filename
    ]
    
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_FFMPEG
        )
        
        if not os.path.exists(filename):
            log.warning(f"Intento {intento}: FFmpeg no generó archivo")
            return False
        
        size_kb = os.path.getsize(filename) // 1024
        
        if size_kb < MIN_SIZE_KB:
            log.warning(f"Intento {intento}: Imagen muy pequeña ({size_kb} KB < {MIN_SIZE_KB} KB)")
            os.remove(filename)
            return False
        
        log.debug(f"Intento {intento}: Captura válida ({size_kb} KB)")
        return True
        
    except subprocess.TimeoutExpired:
        log.warning(f"Intento {intento}: Timeout ({TIMEOUT_FFMPEG}s)")
        if os.path.exists(filename):
            os.remove(filename)
        return False
    except Exception as e:
        log.error(f"Intento {intento}: Error inesperado: {e}")
        return False


def ejecutar_captura_hd(alerta=None):
    """Ejecuta la captura con reintentos si es necesario."""
    if alerta is None:
        alerta = AlertaCaptura(canal=1, origen="default", canales_mask=(1,))
        log.warning("Canal no detectado en paquete, usando canal 1 por defecto")
    elif isinstance(alerta, int):
        alerta = AlertaCaptura(canal=alerta, origen="legacy", canales_mask=(alerta,))

    canal = alerta.canal
    puede, detector_activo, motivo = reservar_captura(canal)
    if not puede:
        log.debug(f"COOLDOWN | A{canal} | {motivo} → SKIP")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    # Incluir canal en el nombre: A1, A2, A3, A4
    filename = f"{OUTPUT_DIR}/alerta_A{canal}_{timestamp}.jpg"

    modo = "ACTIVO" if detector_activo else "INACTIVO"
    detalle_mask = ""
    if alerta.byte27 is not None and len(alerta.canales_mask) > 1:
        detalle_mask = f" | mask={alerta.byte27} ({format_canales(list(alerta.canales_mask))})"
    log.info(f"CAPTURA | A{canal} | INICIO | modo={modo}{detalle_mask}")

    captura_exitosa = False
    try:
        # Intentar captura con reintentos
        for intento in range(1, MAX_REINTENTOS + 1):
            if capturar_frame(filename, canal, intento):
                size_kb = os.path.getsize(filename) // 1024
                log.info(f"EXITO: {os.path.basename(filename)} ({size_kb} KB)")
                limpiar_imagenes_antiguas()
                registrar_captura(canal)
                captura_exitosa = True
                return True

            if intento < MAX_REINTENTOS:
                log.info(f"Reintentando... ({intento}/{MAX_REINTENTOS})")

        log.error(f"FALLO: No se pudo capturar después de {MAX_REINTENTOS} intentos")
        return False
    finally:
        finalizar_reserva_captura(canal, detector_activo, captura_exitosa)


def servidor_alertas():
    """Servidor TCP que escucha alertas del DVR."""
    log.info("=" * 60)
    log.info("INICIANDO SISTEMA DE CAPTURA DVR")
    log.info("=" * 60)
    log.info(f"Puerto de escucha: {LISTEN_PORT}")
    log.info(f"Directorio capturas: {OUTPUT_DIR}")
    log.info(f"Config: max={MAX_IMAGENES} imgs, timeout={TIMEOUT_FFMPEG}s, min={MIN_SIZE_KB}KB")
    log.info(f"Cooldown inactivo: {COOLDOWN_INACTIVO_SEG}s ({COOLDOWN_INACTIVO_SEG//60} min)")
    log.info(
        f"TCP payload: chunk={TCP_READ_CHUNK_SIZE}B | max={TCP_MAX_PAYLOAD_BYTES}B | "
        f"idle_timeout={TCP_IDLE_TIMEOUT:.2f}s"
    )
    
    # Mostrar estadísticas del día
    stats_hoy = obtener_stats_hoy()
    stats_str = ", ".join(f"A{canal}={stats_hoy.get(f'A{canal}', 0)}" for canal in DVR_CHANNELS)
    log.info(f"Stats hoy: {stats_str}, Total={stats_hoy['total']}")
    
    log.info("Esperando conexiones del DVR...")
    
    # Iniciar workers antes de escuchar
    iniciar_workers()
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', LISTEN_PORT))
        s.listen(5)
        
        while True:
            try:
                conn, addr = s.accept()
                client_ip = addr[0]
                
                with conn:
                    data, chunks_recibidos = recibir_payload_completo(conn)
                    if not data:
                        continue
                    
                    # Verificar si es un paquete Hikvision válido
                    if not es_paquete_hikvision(data):
                        log.warning(f"Paquete no-Hikvision rechazado de {client_ip}")
                        continue
                    
                    # Auto-detectar IP del DVR
                    dvr_ip_actual = get_dvr_ip()
                    if dvr_ip_actual != client_ip:
                        old_ip = set_dvr_ip(client_ip)
                        if old_ip:
                            log.info(f"IP del DVR actualizada: {old_ip} -> {client_ip}")
                            notificar_cambio_ip(old_ip, client_ip)
                        else:
                            log.info(f"IP del DVR configurada: {client_ip}")
                    
                    log.info(f"Conexión recibida desde {addr[0]}:{addr[1]}")
                    log.debug(
                        "TCP | %s:%s | chunks=%s | bytes=%s",
                        addr[0],
                        addr[1],
                        chunks_recibidos,
                        len(data),
                    )
                    
                    # Logear contenido del paquete para diagnóstico
                    log.debug(f"Paquete raw ({len(data)} bytes): {data[:200]}")
                    # Intentar extraer una o más alertas por canal desde el paquete
                    alertas = extraer_alertas_de_paquete(data)

                    # Encolar cada alerta por cámara para procesamiento concurrente
                    if not alertas:
                        log.warning(f"Canal no detectado en paquete, alerta descartada")
                        continue
                    for alerta in alertas:
                        if alerta.canal not in DVR_ENABLED_CHANNELS:
                            log.info(f"QUEUE | A{alerta.canal} | canal deshabilitado → SKIP")
                            continue
                        try:
                            alert_queue.put_nowait(alerta)
                            log.debug(
                                f"QUEUE | A{alerta.canal} | encolada "
                                f"({alert_queue.qsize()}/{QUEUE_SIZE}) | origen={alerta.origen}"
                            )
                        except Full:
                            log.warning(
                                f"QUEUE | llena ({QUEUE_SIZE}) | "
                                f"descartada A{alerta.canal} | origen={alerta.origen}"
                            )
            except socket.timeout:
                # Timeout en recv, continuar normalmente
                continue
            except KeyboardInterrupt:
                log.info("Servicio detenido por el usuario")
                break
            except Exception as e:
                log.error(f"Error en conexión: {e}")


if __name__ == "__main__":
    servidor_alertas()
