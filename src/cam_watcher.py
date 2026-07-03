#!/usr/bin/env python3
"""
Camera Person Detection Watcher

Monitorea directorios de cámaras y analiza imágenes con IA
para detectar personas. Envía notificaciones por Telegram.

Versión: 2.10.5
Documentación: docs/README.md
Changelog: docs/CHANGELOG.md
"""

import os
import re
import sys
import json
import io
import time
import logging
import subprocess
import threading
import fcntl
from datetime import datetime, time as dtime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image
import numpy as np

# Agregar el directorio actual al path para importar antigravity_client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from antigravity_client import AntigravityClient
from app_version import CAM_WATCHER_VERSION

# ============================================
# CONFIGURACIÓN
# ============================================


def load_env() -> Dict[str, str]:
    """Carga variables de entorno desde .env"""
    env_path = Path(__file__).parent / ".env"
    config = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    return config


def parse_camera_name_list(raw_value: str, fallback: List[str]) -> tuple:
    """Parsea una lista de nombres de cámara separada por coma y sin duplicados."""
    camaras = []
    for part in raw_value.split(","):
        camara = part.strip()
        if camara and camara not in camaras:
            camaras.append(camara)
    return tuple(camaras) if camaras else tuple(fallback)


CONFIG = load_env()

# Telegram
TELEGRAM_BOT_TOKEN = CONFIG.get("TELEGRAM_BOT_TOKEN", "")


def get_telegram_chat_ids() -> list:
    """Obtiene la lista de chat IDs leyendo siempre del .env (recarga dinámica)."""
    config = load_env()
    ids = [
        cid.strip()
        for cid in config.get("TELEGRAM_CHAT_IDS", "").split(",")
        if cid.strip()
    ]
    # Compatibilidad con config anterior
    if not ids and config.get("TELEGRAM_CHAT_ID"):
        ids = [config.get("TELEGRAM_CHAT_ID")]
    return ids


# Para el inicio, cargamos una vez (se recarga dinámicamente en enviar_telegram_*)
TELEGRAM_CHAT_IDS = get_telegram_chat_ids()

# Directorios y archivos
# Soporta múltiples directorios separados por coma
WATCH_DIRS = [
    d.strip()
    for d in CONFIG.get(
        "WATCH_DIRS",
        CONFIG.get("WATCH_DIR", "/home/capw/projects/camdetector/data/ftpcam1"),
    ).split(",")
]
LOG_FILE = CONFIG.get("LOG_FILE", "/home/capw/projects/camdetector/logs/detections.log")
LOG_WEEKLY_BACKUPS = int(CONFIG.get("LOG_WEEKLY_BACKUPS", "12"))
LOG_EXCLUDE_KEYWORDS = [
    kw.strip()
    for kw in CONFIG.get("LOG_EXCLUDE_KEYWORDS", "Nueva imagen:,sin cambio (").split(
        ","
    )
    if kw.strip()
]
AI_AUDIT_LOG_FILE = CONFIG.get(
    "AI_AUDIT_LOG_FILE", "/home/capw/projects/camdetector/logs/ai_audit.jsonl"
)
AI_AUDIT_LOG_WEEKLY_BACKUPS = int(CONFIG.get("AI_AUDIT_LOG_WEEKLY_BACKUPS", "12"))
STATE_FILE = CONFIG.get(
    "STATE_FILE", "/home/capw/projects/camdetector/config/state.json"
)
STATS_HISTORY_FILE = CONFIG.get(
    "STATS_HISTORY_FILE", "/home/capw/projects/camdetector/config/stats_history.json"
)

# Horarios
DETECCION_INICIO = CONFIG.get("DETECCION_INICIO", "00:01")
DETECCION_FIN = CONFIG.get("DETECCION_FIN", "07:45")

# IA
AI_MODEL = CONFIG.get("AI_MODEL", "gemini-3-flash")
OPENROUTER_API_KEY = CONFIG.get("OPENROUTER_API_KEY", "")

# Model OpenRouter que usan vision
OPENROUTER_VISION_MODELS = [
    "google/gemma-3-12b-it:free",
    "google/gemma-3-27b-it:free",
    "google/gemini-2.5-flash-lite-preview-09-2025",
    "google/gemini-3.1-flash-lite-preview",
    "qwen/qwen3.5-9b",
    "qwen/qwen3.5-flash-02-23",
]


def usa_openrouter(model: str) -> bool:
    """Determina si el modelo usa OpenRouter en lugar de Antigravity."""
    if not OPENROUTER_API_KEY:
        return False
    model_lower = model.lower()
    # Modelos que usan OpenRouter (sin prefijo google/)
    vision_models = [
        "gemma-3-12b",
        "gemma-3-27b",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-lite",
        "qwen3.5-9b",
        "qwen3.5-flash-02-23",
    ]
    return any(m in model_lower for m in vision_models)


# Configuración avanzada
WAIT_BEFORE_ANALYZE = float(CONFIG.get("WAIT_BEFORE_ANALYZE", "1.0"))

# Rate limiting: máximo N imágenes por minuto
RATE_LIMIT_MAX = int(CONFIG.get("RATE_LIMIT_MAX", "10"))
RATE_LIMIT_COOLDOWN = float(CONFIG.get("RATE_LIMIT_COOLDOWN", "30.0"))

# Lógica de eventos por cámara
EVENTO_GAP_SEGUNDOS = float(CONFIG.get("EVENTO_GAP_SEGUNDOS", "8.0"))
MAX_ANALISIS_POR_EVENTO = int(CONFIG.get("MAX_ANALISIS_POR_EVENTO", "3"))

# Fondo subtraction: detectar cambios vs imagen de referencia
FONDO_SUBTRACTION_ENABLED = (
    CONFIG.get("FONDO_SUBTRACTION_ENABLED", "true").lower() == "true"
)
FONDO_UMBRAL = int(CONFIG.get("FONDO_UMBRAL", "30"))  # Diferencia por pixel (0-255)
FONDO_PORCENTAJE_CAMBIO = float(
    CONFIG.get("FONDO_PORCENTAJE_CAMBIO", "5.0")
)  # % mínimo para considerar cambio
FONDO_RESIZE = (960, 540)  # 50% de resolución nativa (más rápido)
FONDO_NO_PERSONA_CONSECUTIVOS = int(CONFIG.get("FONDO_NO_PERSONA_CONSECUTIVOS", "3"))
AI_IMAGE_SCALE = int(CONFIG.get("AI_IMAGE_SCALE", "100"))

# Pool de threads para análisis de imágenes (evita bloqueos)
ANALYSIS_POOL = ThreadPoolExecutor(max_workers=3)

# Variable global para el estado anterior (para detectar cambios)
_estado_anterior = None

# Estado de evento por cámara (reemplaza el throttle anterior)
_eventos_lock = threading.Lock()
_eventos: Dict[str, Dict] = {}
# Estructura por cámara:
# {
#   'ultimo_ts': float,        # time.time() de la última imagen recibida
#   'buffer': [],              # paths acumulados del evento actual
#   'analisis_count': int,     # cuántos análisis se lanzaron en este evento
#   'analizando': bool,        # hay un Future en vuelo actualmente
#   'ultima_revisada': str,    # último path ya decidido (analizado o skip por fondo)
#   'ultima_analizada': str,   # path de la última imagen enviada a la API
# }

# Referencia de fondo por cámara para detección de cambios
_fondo_lock = threading.Lock()
_fondo_referencias: Dict[
    str, np.ndarray
] = {}  # camara -> array numpy (escala de grises, 50% resolución)
_fondo_no_persona_consecutivos: Dict[str, int] = {}
FONDO_CAMARAS_ACTIVAS = parse_camera_name_list(
    CONFIG.get("FONDO_CAMARAS_ACTIVAS", "DVR-A1,DVR-A2,DVR-A3,DVR-A4"),
    ["DVR-A1", "DVR-A2", "DVR-A3", "DVR-A4"],
)

# ============================================
# COMPARACIÓN DE FONDO (background subtraction)
# ============================================


def _cargar_imagen_gris(filepath: str) -> Optional[np.ndarray]:
    """Carga imagen en escala de grises redimensionada a 50%."""
    try:
        img = Image.open(filepath).convert("L").resize(FONDO_RESIZE, Image.LANCZOS)
        return np.array(img, dtype=np.int16)
    except Exception as e:
        logger.error(f"Error cargando imagen para fondo: {e}")
        return None


def comparar_fondo(filepath: str, camara: str) -> tuple:
    """
    Compara imagen nueva con referencia de fondo de la cámara.

    Returns:
        (bool hay_cambio, float porcentaje_diferencia)
        - hay_cambio: True si diferencia > FONDO_PORCENTAJE_CAMBIO
        - porcentaje_diferencia: % de píxeles diferentes
    """
    if not FONDO_SUBTRACTION_ENABLED or camara not in FONDO_CAMARAS_ACTIVAS:
        return True, 0.0  # Sin filtro, siempre procesar

    with _fondo_lock:
        referencia = _fondo_referencias.get(camara)

    if referencia is None:
        # Primera imagen, no hay referencia aún
        return True, 0.0

    nueva = _cargar_imagen_gris(filepath)
    if nueva is None:
        return True, 0.0  # Error cargando, procesar por seguridad

    # Diferencia absoluta por pixel
    diff = np.abs(nueva - referencia)
    pxeles_cambiados = np.sum(diff > FONDO_UMBRAL)
    porcentaje = (pxeles_cambiados / diff.size) * 100

    hay_cambio = porcentaje > FONDO_PORCENTAJE_CAMBIO
    return hay_cambio, round(porcentaje, 2)


def resetear_no_persona_fondo(camara: str):
    """Resetea el contador de NO_PERSONA consecutivos usado para actualizar el fondo."""
    if not FONDO_SUBTRACTION_ENABLED or camara not in FONDO_CAMARAS_ACTIVAS:
        return

    with _fondo_lock:
        _fondo_no_persona_consecutivos[camara] = 0


def actualizar_referencia_fondo(filepath: str, camara: str):
    """
    Actualiza la referencia de fondo para una cámara.
    Si la IA validó NO_PERSONA, solo usamos esa imagen como nueva referencia
    después de FONDO_NO_PERSONA_CONSECUTIVOS análisis consecutivos sin persona.
    Se loguea la desviación respecto a la referencia previa para diagnóstico.
    """
    resultado = {
        "actualizada": False,
        "inicializada": False,
        "porcentaje": None,
        "consecutivos": 0,
    }
    if not FONDO_SUBTRACTION_ENABLED or camara not in FONDO_CAMARAS_ACTIVAS:
        return resultado

    nueva = _cargar_imagen_gris(filepath)
    if nueva is None:
        return resultado

    mensaje_log = None

    with _fondo_lock:
        referencia_anterior = _fondo_referencias.get(camara)
        consecutivos = _fondo_no_persona_consecutivos.get(camara, 0) + 1
        _fondo_no_persona_consecutivos[camara] = consecutivos
        porcentaje = None

        if referencia_anterior is not None:
            diff = np.abs(nueva - referencia_anterior)
            pxeles_cambiados = np.sum(diff > FONDO_UMBRAL)
            porcentaje = (pxeles_cambiados / diff.size) * 100
            porcentaje = round(porcentaje, 2)

        resultado["porcentaje"] = porcentaje
        resultado["consecutivos"] = consecutivos

        if consecutivos < FONDO_NO_PERSONA_CONSECUTIVOS:
            if porcentaje is None:
                mensaje_log = (
                    f"FONDO | {camara} | NO_PERSONA {consecutivos}/{FONDO_NO_PERSONA_CONSECUTIVOS} "
                    f"| referencia pendiente de inicialización"
                )
            else:
                mensaje_log = (
                    f"FONDO | {camara} | NO_PERSONA {consecutivos}/{FONDO_NO_PERSONA_CONSECUTIVOS} "
                    f"| sin actualizar referencia (desviación {porcentaje}%)"
                )
        else:
            _fondo_referencias[camara] = nueva
            _fondo_no_persona_consecutivos[camara] = 0
            resultado["actualizada"] = True
            resultado["inicializada"] = referencia_anterior is None

            if referencia_anterior is None:
                mensaje_log = (
                    f"FONDO | {camara} | referencia inicializada "
                    f"tras {consecutivos} NO_PERSONA consecutivos"
                )
            else:
                mensaje_log = (
                    f"FONDO | {camara} | referencia actualizada "
                    f"tras {consecutivos} NO_PERSONA consecutivos "
                    f"(desviación {porcentaje}%)"
                )

    if mensaje_log:
        logger.info(mensaje_log)

    if resultado["actualizada"]:
        archivo = os.path.basename(filepath)
        guardar_imagen_en_dir(
            filepath,
            archivo,
            FONDO_REFERENCIAS_DIR,
            MAX_IMAGENES_FONDO_REFERENCIAS,
        )
        registrar_auditoria_ia(
            "fondo_referencia_actualizada",
            camara=camara,
            archivo=archivo,
            consecutivos_no_persona=consecutivos,
            desviacion_pct=resultado["porcentaje"],
            inicializada=resultado["inicializada"],
        )

    return resultado


# ============================================
# RATE LIMITER SIMPLE (UNA SOLA CUENTA)
# ============================================


class SimpleRateLimiter:
    """
    Rate limiter simple para una sola cuenta.
    Limita a N imágenes por minuto con cooldown cuando se excede.
    """

    def __init__(self, max_per_minute: int = 10, cooldown_seconds: float = 30.0):
        self.max_per_minute = max_per_minute
        self.cooldown_seconds = cooldown_seconds
        self.timestamps: List[float] = []
        self.lock = threading.Lock()
        self.in_cooldown = False
        self.cooldown_until = 0
        self.pending_image = None
        self.cooldown_timer: Optional[threading.Timer] = None
        self._process_callback: Optional[callable] = None

    def set_process_callback(self, callback: callable):
        self._process_callback = callback

    def _on_cooldown_end(self):
        with self.lock:
            if not self.in_cooldown:
                return
            now = time.time()
            if now < self.cooldown_until:
                return
            self.in_cooldown = False
            logger.info("Rate limit: Cooldown terminado")
            if self.pending_image and self._process_callback:
                img = self.pending_image
                self.pending_image = None
                self.timestamps.append(now)
                logger.info("Rate limit: Procesando imagen pendiente")
                try:
                    self._process_callback(img)
                except Exception as e:
                    logger.error(f"Error procesando imagen pendiente: {e}")

    def can_process(self, filepath: str) -> tuple:
        """
        Verifica si se puede procesar una imagen.

        Returns:
            (bool, str): (puede_procesar, filepath_a_procesar)
        """
        with self.lock:
            now = time.time()

            if self.in_cooldown:
                if now < self.cooldown_until:
                    self.pending_image = filepath
                    return False, None
                else:
                    self.in_cooldown = False
                    logger.info("Rate limit: Cooldown terminado")

            # Limpiar timestamps antiguos
            self.timestamps = [t for t in self.timestamps if now - t < 60]

            if len(self.timestamps) < self.max_per_minute:
                self.timestamps.append(now)
                if self.pending_image:
                    img = self.pending_image
                    self.pending_image = None
                    logger.info("Rate limit: Procesando imagen pendiente")
                    return True, img
                return True, filepath

            # Límite excedido - entrar en cooldown
            self.in_cooldown = True
            self.cooldown_until = now + self.cooldown_seconds
            self.pending_image = filepath

            if self.cooldown_timer:
                self.cooldown_timer.cancel()
            self.cooldown_timer = threading.Timer(
                self.cooldown_seconds, self._on_cooldown_end
            )
            self.cooldown_timer.daemon = True
            self.cooldown_timer.start()

            logger.warning(
                f"Rate limit: Límite alcanzado ({self.max_per_minute}/min). "
                f"Cooldown {self.cooldown_seconds}s"
            )
            return False, None

    def get_stats(self) -> dict:
        with self.lock:
            now = time.time()
            recent = len([t for t in self.timestamps if now - t < 60])
            return {
                "images_last_minute": recent,
                "max_per_minute": self.max_per_minute,
                "in_cooldown": self.in_cooldown,
                "pending_image": self.pending_image is not None,
            }


RATE_LIMITER = SimpleRateLimiter(
    max_per_minute=RATE_LIMIT_MAX, cooldown_seconds=RATE_LIMIT_COOLDOWN
)

LOG_LEVEL = CONFIG.get("LOG_LEVEL", "INFO").upper()


class ExcludeKeywordsFilter(logging.Filter):
    """Oculta mensajes ruidosos por substring sin perder warnings/errores."""

    def __init__(self, keywords: List[str]):
        super().__init__()
        self.keywords = [keyword.lower() for keyword in keywords]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING or not self.keywords:
            return True
        message = record.getMessage().lower()
        return not any(keyword in message for keyword in self.keywords)


def setup_logging() -> logging.Logger:
    """Configura logging con rotación semanal propia del proceso."""
    logger = logging.getLogger(__name__)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    logger.handlers.clear()

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    noise_filter = ExcludeKeywordsFilter(LOG_EXCLUDE_KEYWORDS)

    file_handler = TimedRotatingFileHandler(
        LOG_FILE,
        when="W0",
        interval=1,
        backupCount=LOG_WEEKLY_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(noise_filter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(noise_filter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logging()
logger.info(f"Log level: {LOG_LEVEL}")
logger.info(f"Rotación semanal de logs: backups={LOG_WEEKLY_BACKUPS}")
logger.info(
    f"Filtros de ruido: {', '.join(LOG_EXCLUDE_KEYWORDS) if LOG_EXCLUDE_KEYWORDS else 'sin filtros'}"
)


def setup_audit_logger() -> logging.Logger:
    """Configura un log separado y estructurado para auditoría de IA."""
    audit_logger = logging.getLogger("camdetector.ai_audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False
    audit_logger.handlers.clear()

    os.makedirs(os.path.dirname(AI_AUDIT_LOG_FILE), exist_ok=True)
    audit_handler = TimedRotatingFileHandler(
        AI_AUDIT_LOG_FILE,
        when="W0",
        interval=1,
        backupCount=AI_AUDIT_LOG_WEEKLY_BACKUPS,
        encoding="utf-8",
    )
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(audit_handler)
    return audit_logger


audit_logger = setup_audit_logger()


def registrar_auditoria_ia(evento: str, **payload):
    """Escribe una línea JSON en el log de auditoría IA."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "evento": evento,
        "modelo": AI_MODEL,
        **payload,
    }
    try:
        audit_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Error escribiendo auditoría IA: {e}")


# ============================================
# PROMPT PARA DETECCIÓN DE PERSONAS
# ============================================

# Prompt estándar para cámaras A3, A4 y FTP
DETECTION_PROMPT = """Analiza esta imagen de una cámara de seguridad. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO

Reglas estrictas:
- Si hay una o más personas a pie visibles responde PERSONA: SI
- Para cualquier otra cosa que veas, responde PERSONA: NO
"""

# Prompt especial para cámaras A1 y A2 (con evaluación de actividad sospechosa)
DETECTION_PROMPT_SOSPECHOSA = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie dentro del predio o patio
- 0-49: Persona a pie pasando por la vereda o calle exterior

Reglas estrictas:
- Responde PERSONA: SI solo si ves personas a pie presentes
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0
- No agregues descripción, explicación ni texto extra
"""

# Directorio para guardar imágenes de personas no sospechosas
# NOTA: Debe estar FUERA de los directorios monitoreados para evitar reprocesamiento.
# También funciona como carpeta de auditoría: guarda todas las imágenes enviadas a la IA.
NO_SOSPECHOSAS_DIR = "/home/capw/projects/camdetector/data/dvr_no_sospechosas"
SOSPECHOSAS_DIR = "/home/capw/projects/camdetector/data/dvr_sospechosas"
FONDO_REFERENCIAS_DIR = CONFIG.get(
    "FONDO_REFERENCIAS_DIR",
    "/home/capw/projects/camdetector/data/dvr_fondo_referencias",
)
GUARDAR_IMAGENES_ANALIZADAS = (
    CONFIG.get("GUARDAR_IMAGENES_ANALIZADAS", "true").lower() == "true"
)
MAX_IMAGENES_REFERENCIA_IA = int(CONFIG.get("MAX_IMAGENES_REFERENCIA_IA", "2000"))
MAX_IMAGENES_SOSPECHOSAS = int(CONFIG.get("MAX_IMAGENES_SOSPECHOSAS", "500"))
MAX_IMAGENES_FONDO_REFERENCIAS = int(
    CONFIG.get("MAX_IMAGENES_FONDO_REFERENCIAS", "100")
)

# ============================================
# FUNCIONES DE ESTADO
# ============================================

_state_lock = threading.Lock()


def cargar_estado() -> Dict[str, Any]:
    """Carga el estado persistente desde el archivo JSON (thread-safe + process-safe)."""
    with _state_lock:
        try:
            with open(STATE_FILE, "r") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Lock shared para lectura
                data = json.load(f)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "modo": "auto",
                "ultima_deteccion": None,
                "stats_hoy": {
                    "fecha": None,
                    "imagenes_recibidas": 0,
                    "imagenes_analizadas": 0,
                    "personas_detectadas": 0,
                },
            }


def guardar_estado(state: Dict[str, Any]):
    """Guarda el estado persistente en el archivo JSON (thread-safe + process-safe)."""
    with _state_lock:
        # Abrir con 'a+' para no truncar antes de tener el lock
        with open(STATE_FILE, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Lock exclusivo PRIMERO
            f.seek(0)  # Ir al inicio
            f.truncate()  # Ahora truncar
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()  # Asegurar que se escriba antes de liberar el lock
            os.fsync(f.fileno())  # Forzar escritura a disco
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock


def _guardar_estado_directo(state: Dict[str, Any]):
    """Guarda estado sin adquirir lock (para uso interno cuando ya se tiene el lock)."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())


def actualizar_stats(analizado: bool, persona_detectada: bool):
    """Actualiza las estadísticas del día (atomic - protegido contra TOCTOU)."""
    hoy = datetime.now().strftime("%Y-%m-%d")

    # Usar lock durante toda la operación read-modify-write
    with _state_lock:
        try:
            with open(STATE_FILE, "r+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Lock exclusivo
                try:
                    state = json.load(f)
                except json.JSONDecodeError:
                    state = {}

                # Resetear stats si es un nuevo día
                if state.get("stats_hoy", {}).get("fecha") != hoy:
                    state["stats_hoy"] = {
                        "fecha": hoy,
                        "imagenes_recibidas": 0,
                        "imagenes_analizadas": 0,
                        "personas_detectadas": 0,
                    }

                state["stats_hoy"]["imagenes_recibidas"] += 1
                if analizado:
                    state["stats_hoy"]["imagenes_analizadas"] += 1
                if persona_detectada:
                    state["stats_hoy"]["personas_detectadas"] += 1

                # Guardar
                f.seek(0)
                f.truncate()
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            # Si no existe el archivo, crearlo (sin llamar a guardar_estado para evitar deadlock)
            state = {
                "modo": "auto",
                "ultima_deteccion": None,
                "stats_hoy": {
                    "fecha": hoy,
                    "imagenes_recibidas": 1,
                    "imagenes_analizadas": 1 if analizado else 0,
                    "personas_detectadas": 1 if persona_detectada else 0,
                },
            }
            _guardar_estado_directo(state)


def guardar_ultima_deteccion(
    archivo: str, camara: str, descripcion: str, fecha_hora: str
):
    """Guarda información de la última detección de persona."""
    state = cargar_estado()
    state["ultima_deteccion"] = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "archivo": archivo,
        "camara": camara,
        "descripcion": descripcion,
        "fecha_imagen": fecha_hora,
    }
    guardar_estado(state)


# ============================================
# FUNCIONES DE HISTÓRICO
# ============================================

_history_lock = threading.Lock()


def _nuevo_registro_historico_dia() -> Dict[str, Any]:
    """Estructura base de métricas diarias."""
    return {
        "imagenes_recibidas": 0,
        "imagenes_analizadas": 0,
        "personas_detectadas": 0,
        "personas_sospechosas_notificadas": 0,
        "personas_no_sospechosas": 0,
        "analisis_sin_persona_count": 0,
        "skips_fondo": 0,
        "fondo_actualizaciones": 0,
        "analisis_ms_total": 0,
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": 0,
    }


def _asegurar_campos_historico_dia(dia: Dict[str, Any]) -> Dict[str, Any]:
    """Completa campos faltantes para compatibilidad hacia atrás."""
    defaults = _nuevo_registro_historico_dia()
    for key, value in defaults.items():
        dia.setdefault(key, value)
    return dia


def _aplicar_evento_historico(
    history: Dict[str, Any],
    hoy: str,
    hora_actual: str,
    analizado: bool,
    persona_detectada: bool,
    tokens_prompt: int,
    tokens_completion: int,
    tokens_total: int,
    descripcion: str,
    camara: str,
    archivo: str,
    sospechosa: int,
    notificado: bool,
    skip_fondo: bool,
    fondo_actualizado: bool,
    analisis_ms: int,
):
    """Aplica un evento al histórico en memoria."""
    if "dias" not in history:
        history["dias"] = {}
    if "analisis_sin_persona" not in history:
        history["analisis_sin_persona"] = {}

    if hoy not in history["dias"]:
        history["dias"][hoy] = _nuevo_registro_historico_dia()

    dia = _asegurar_campos_historico_dia(history["dias"][hoy])
    dia["imagenes_recibidas"] += 1

    if skip_fondo:
        dia["skips_fondo"] += 1

    if fondo_actualizado:
        dia["fondo_actualizaciones"] += 1

    if analizado:
        dia["imagenes_analizadas"] += 1
        dia["tokens_prompt"] += tokens_prompt
        dia["tokens_completion"] += tokens_completion
        dia["tokens_total"] += tokens_total
        dia["analisis_ms_total"] += max(0, int(analisis_ms))

        if persona_detectada:
            dia["personas_detectadas"] += 1

            if notificado:
                dia["personas_sospechosas_notificadas"] += 1

            if sospechosa < 50:
                dia["personas_no_sospechosas"] += 1

                if "personas_no_sospechosas_detalle" not in history:
                    history["personas_no_sospechosas_detalle"] = {}
                if hoy not in history["personas_no_sospechosas_detalle"]:
                    history["personas_no_sospechosas_detalle"][hoy] = []

                history["personas_no_sospechosas_detalle"][hoy].append(
                    {
                        "hora": hora_actual,
                        "camara": camara,
                        "archivo": archivo,
                        "descripcion": descripcion,
                        "sospechosa": sospechosa,
                    }
                )
        else:
            dia["analisis_sin_persona_count"] += 1

            if hoy not in history["analisis_sin_persona"]:
                history["analisis_sin_persona"][hoy] = []

            history["analisis_sin_persona"][hoy].append(
                {
                    "hora": hora_actual,
                    "camara": camara,
                    "archivo": archivo,
                    "descripcion": descripcion,
                }
            )


def cargar_historico() -> Dict[str, Any]:
    """Carga el histórico de estadísticas desde el archivo JSON (thread-safe + process-safe)."""
    with _history_lock:
        try:
            with open(STATS_HISTORY_FILE, "r") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Lock shared para lectura
                data = json.load(f)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {"dias": {}, "analisis_sin_persona": {}}


def guardar_historico(history: Dict[str, Any]):
    """Guarda el histórico de estadísticas en el archivo JSON (thread-safe + process-safe)."""
    with _history_lock:
        # Abrir con 'a+' para no truncar antes de tener el lock
        with open(STATS_HISTORY_FILE, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Lock exclusivo PRIMERO
            f.seek(0)  # Ir al inicio
            f.truncate()  # Ahora truncar
            json.dump(history, f, indent=2, ensure_ascii=False)
            f.flush()  # Asegurar que se escriba antes de liberar el lock
            os.fsync(f.fileno())  # Forzar escritura a disco
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock


def _guardar_historico_directo(history: Dict[str, Any]):
    """Guarda histórico sin adquirir lock (para uso interno cuando ya se tiene el lock)."""
    with open(STATS_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())


def actualizar_historico(
    analizado: bool,
    persona_detectada: bool,
    tokens_prompt: int = 0,
    tokens_completion: int = 0,
    tokens_total: int = 0,
    descripcion: str = "",
    camara: str = "",
    archivo: str = "",
    sospechosa: int = 100,
    notificado: bool = True,
    skip_fondo: bool = False,
    fondo_actualizado: bool = False,
    analisis_ms: int = 0,
):
    """
    Actualiza el histórico diario con estadísticas y tokens (atomic - protegido contra TOCTOU).
    También guarda detalles de análisis sin persona y personas no sospechosas para el resumen diario.
    """
    hoy = datetime.now().strftime("%Y-%m-%d")
    hora_actual = datetime.now().strftime("%H:%M:%S")

    # Usar lock durante toda la operación read-modify-write
    with _history_lock:
        try:
            with open(STATS_HISTORY_FILE, "r+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Lock exclusivo
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = {"dias": {}, "analisis_sin_persona": {}}

                # Asegurar estructura base
                if "dias" not in history:
                    history["dias"] = {}
                if "analisis_sin_persona" not in history:
                    history["analisis_sin_persona"] = {}
                _aplicar_evento_historico(
                    history,
                    hoy,
                    hora_actual,
                    analizado,
                    persona_detectada,
                    tokens_prompt,
                    tokens_completion,
                    tokens_total,
                    descripcion,
                    camara,
                    archivo,
                    sospechosa,
                    notificado,
                    skip_fondo,
                    fondo_actualizado,
                    analisis_ms,
                )

                # Limpiar datos antiguos (más de 35 días)
                fecha_limite = (datetime.now() - timedelta(days=35)).strftime(
                    "%Y-%m-%d"
                )
                history["dias"] = {
                    k: v for k, v in history["dias"].items() if k >= fecha_limite
                }
                history["analisis_sin_persona"] = {
                    k: v
                    for k, v in history["analisis_sin_persona"].items()
                    if k >= fecha_limite
                }
                if "personas_no_sospechosas_detalle" in history:
                    history["personas_no_sospechosas_detalle"] = {
                        k: v
                        for k, v in history["personas_no_sospechosas_detalle"].items()
                        if k >= fecha_limite
                    }

                # Guardar
                f.seek(0)
                f.truncate()
                json.dump(history, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            # Si no existe el archivo, crearlo con el evento actual aplicado.
            history = {"dias": {}, "analisis_sin_persona": {}}
            _aplicar_evento_historico(
                history,
                hoy,
                hora_actual,
                analizado,
                persona_detectada,
                tokens_prompt,
                tokens_completion,
                tokens_total,
                descripcion,
                camara,
                archivo,
                sospechosa,
                notificado,
                skip_fondo,
                fondo_actualizado,
                analisis_ms,
            )
            _guardar_historico_directo(history)


def format_tokens(n: int) -> str:
    """Formatea tokens para mensajes resumidos."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def obtener_metricas_dia(fecha: Optional[str] = None) -> Dict[str, Any]:
    """Obtiene las métricas agregadas de un día desde el histórico."""
    fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    history = cargar_historico()
    dia = _asegurar_campos_historico_dia(history.get("dias", {}).get(fecha, {}).copy())
    analizadas = dia.get("imagenes_analizadas", 0)
    promedio_ms = (
        round(dia.get("analisis_ms_total", 0) / analizadas) if analizadas else 0
    )
    return {
        **dia,
        "analisis_ms_promedio": promedio_ms,
        "tokens_total_fmt": format_tokens(dia.get("tokens_total", 0)),
    }


# ============================================
# LÓGICA DE MODO DETECCIÓN
# ============================================


def parse_time(time_str: str) -> dtime:
    """Convierte string HH:MM a objeto time."""
    h, m = map(int, time_str.split(":"))
    return dtime(h, m)


def debe_analizar() -> tuple:
    """
    Determina si se debe analizar la imagen según el modo actual.
    Soporta horarios que cruzan la medianoche (ej: 22:00 - 06:00).

    Returns:
        (bool, str): (debe_analizar, razón)
    """
    state = cargar_estado()
    modo = state.get("modo", "auto")

    # Modo forzado tiene prioridad
    if modo == "forzado_activado":
        return True, "Modo forzado ACTIVADO"
    if modo == "forzado_desactivado":
        return False, "Modo forzado DESACTIVADO"

    # Modo automático: ACTIVO durante horario configurado
    hora_actual = datetime.now().time()
    inicio = parse_time(DETECCION_INICIO)
    fin = parse_time(DETECCION_FIN)

    # Verificar si estamos dentro del horario de detección
    # Soporta cruce de medianoche (ej: 22:00 - 06:00)
    if inicio < fin:
        # Horario normal (ej: 00:01 - 07:45)
        en_horario = inicio <= hora_actual <= fin
    else:
        # Horario que cruza medianoche (ej: 22:00 - 06:00)
        en_horario = hora_actual >= inicio or hora_actual <= fin

    if en_horario:
        return True, f"Horario activo ({DETECCION_INICIO} - {DETECCION_FIN})"

    return False, f"Fuera del horario ({DETECCION_INICIO} - {DETECCION_FIN})"


# ============================================
# TELEGRAM
# ============================================


def enviar_telegram_mensaje(texto: str, chat_ids: Optional[List[str]] = None) -> int:
    """
    Envía un mensaje de texto a Telegram.

    Args:
        texto: Mensaje a enviar
        chat_ids: Lista de chat IDs (usa get_telegram_chat_ids() si no se especifica)

    Returns:
        Número de envíos exitosos
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram no configurado (sin token)")
        return 0

    # Recargar chat IDs dinámicamente para que /agregar aplique sin reiniciar
    ids = chat_ids or get_telegram_chat_ids()
    if not ids:
        logger.warning("Telegram no configurado (sin chat IDs)")
        return 0

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    exitos = 0

    for chat_id in ids:
        payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.json().get("ok", False):
                exitos += 1
            else:
                logger.warning(
                    f"Error enviando a {chat_id}: {response.json().get('description', 'Unknown')}"
                )
        except Exception as e:
            logger.error(f"Error enviando mensaje Telegram a {chat_id}: {e}")

    return exitos


def enviar_telegram_foto(
    imagen_path: str, caption: str, chat_ids: Optional[List[str]] = None
) -> int:
    """
    Envía una foto con caption a Telegram.

    Args:
        imagen_path: Ruta a la imagen
        caption: Texto del caption
        chat_ids: Lista de chat IDs (usa get_telegram_chat_ids() si no se especifica)

    Returns:
        Número de envíos exitosos
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram no configurado (sin token)")
        return 0

    # Recargar chat IDs dinámicamente para que /agregar aplique sin reiniciar
    ids = chat_ids or get_telegram_chat_ids()
    if not ids:
        logger.warning("Telegram no configurado (sin chat IDs)")
        return 0

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    exitos = 0

    for chat_id in ids:
        try:
            with open(imagen_path, "rb") as photo:
                files = {"photo": photo}
                data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
                response = requests.post(url, files=files, data=data, timeout=30)
                if response.json().get("ok", False):
                    exitos += 1
                else:
                    logger.warning(
                        f"Error enviando foto a {chat_id}: {response.json().get('description', 'Unknown')}"
                    )
        except Exception as e:
            logger.error(f"Error enviando foto Telegram a {chat_id}: {e}")

    return exitos


# ============================================
# ANÁLISIS CON IA - OpenRouter
# ============================================


def _analizar_con_openrouter(image_data: bytes, image_type: str, prompt: str) -> dict:
    """Analiza imagen usando OpenRouter API. Retorna dict con text y tokens."""
    import base64

    image_base64 = base64.b64encode(image_data).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://camdetector.local",
        "X-Title": "CamDetector",
    }

    payload = {
        "model": AI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_type};base64,{image_base64}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 100,
    }

    if "qwen/" in AI_MODEL.lower():
        payload["reasoning"] = {"enabled": False}

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if response.status_code != 200:
        raise Exception(
            f"OpenRouter error {response.status_code}: {response.text[:200]}"
        )

    data = response.json()
    usage = data.get("usage", {})
    return {
        "text": data["choices"][0]["message"]["content"],
        "tokens_prompt": usage.get("prompt_tokens", 0),
        "tokens_completion": usage.get("completion_tokens", 0),
        "tokens_total": usage.get("total_tokens", 0),
    }


# ============================================
# ANÁLISIS CON IA
# ============================================


def es_camara_con_sospecha(camara: str) -> bool:
    """
    Determina si la cámara requiere evaluación de actividad sospechosa.
    Solo DVR-A1 y DVR-A2 requieren esta evaluación.
    """
    return camara in ("DVR-A1", "DVR-A2")


def analizar_imagen(imagen_path: str, camara: str = "") -> Dict[str, Any]:
    """
    Analiza una imagen con la IA para detectar personas.

    Args:
        imagen_path: Ruta a la imagen
        camara: Identificador de la cámara (ej: 'DVR-A1', 'DVR-A2', etc.)

    Returns:
        Dict con keys: persona (bool), sospechosa (int 0-100),
                       descripcion (str, reservado), error (str o None),
                       tokens_prompt, tokens_completion, tokens_total
    """
    _resultado_error = {
        "persona": False,
        "sospechosa": 0,
        "descripcion": "",
        "error": None,
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": 0,
    }
    try:
        with open(imagen_path, "rb") as f:
            image_data = f.read()

        if len(image_data) < 1000:
            return {**_resultado_error, "error": "Imagen muy pequeña o corrupta"}

        if 1 <= AI_IMAGE_SCALE < 100:
            img = Image.open(io.BytesIO(image_data))
            new_w = max(1, int(img.width * AI_IMAGE_SCALE / 100))
            new_h = max(1, int(img.height * AI_IMAGE_SCALE / 100))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=100)
            image_data = buf.getvalue()
            image_type = "image/jpeg"
        else:
            image_type = (
                "image/png" if imagen_path.lower().endswith(".png") else "image/jpeg"
            )

        usar_prompt_sospecha = es_camara_con_sospecha(camara)
        prompt = (
            DETECTION_PROMPT_SOSPECHOSA if usar_prompt_sospecha else DETECTION_PROMPT
        )

        or_result = _analizar_con_openrouter(image_data, image_type, prompt)
        text = or_result["text"]
        result = {
            **_resultado_error,
            "raw_response": text,
            "tokens_prompt": or_result["tokens_prompt"],
            "tokens_completion": or_result["tokens_completion"],
            "tokens_total": or_result["tokens_total"],
        }

        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("PERSONA:"):
                result["persona"] = line.split(":", 1)[1].strip().upper() == "SI"
            elif line.upper().startswith("SOSPECHOSA:"):
                try:
                    result["sospechosa"] = int(
                        line.split(":", 1)[1].strip().replace("%", "")
                    )
                except ValueError:
                    result["sospechosa"] = 0
        # Cámaras sin evaluación de sospecha → si hay persona = 100%
        if not usar_prompt_sospecha and result["persona"]:
            result["sospechosa"] = 100

        return result

    except Exception as e:
        error_str = str(e)
        if "429" in error_str:
            logger.error(f"Error 429 (quota agotada): {error_str}")
        else:
            logger.error(f"Error analizando imagen: {e}")
        return {**_resultado_error, "error": error_str}


# ============================================
# GESTIÓN DE EVENTOS POR CÁMARA
# ============================================


def _get_estado_evento(camara: str) -> Dict:
    """Retorna el estado actual del evento para la cámara, inicializando si no existe."""
    return _eventos.get(
        camara,
        {
            "ultimo_ts": 0.0,
            "buffer": [],  # todas las imágenes del evento
            "analisis_count": 0,  # análisis lanzados en el evento
            "imagenes_recibidas": 0,  # contador de imgs recibidas DESPUÉS del trigger (img1)
            "analizando": False,
            "ultima_revisada": None,
            "ultima_analizada": None,
            "cerrado": False,  # True cuando se detectó persona sospechosa
        },
    )


def _lanzar_analisis_evento(camara: str, estado: Dict, filepath: str):
    """Lanza análisis de filepath en el pool y registra el callback."""
    estado["analizando"] = True
    estado["ultima_analizada"] = filepath
    future = ANALYSIS_POOL.submit(procesar_imagen, filepath)
    future.add_done_callback(lambda f: _on_analisis_completo(f, camara, filepath))
    logger.info(
        f"EVENTO | {camara} | img#{estado['imagenes_recibidas']} | ANALIZA {os.path.basename(filepath)} (análisis #{estado['analisis_count'] + 1})"
    )


def _siguiente_a_analizar(buf: list, filepath_analizado: str) -> Optional[str]:
    """Retorna la siguiente imagen pendiente del buffer después de la última analizada."""
    try:
        idx_analizada = buf.index(filepath_analizado)
    except ValueError:
        return None
    for i in range(idx_analizada + 1, len(buf)):
        return buf[i]
    return None


def _continuar_evento_con_filtro(camara: str):
    """
    Continúa un evento pendiente revalidando el comparador de fondo
    antes de cada análisis, incluso para imágenes ya acumuladas.
    """
    while True:
        with _eventos_lock:
            estado = _eventos.get(camara)
            if (
                not estado
                or estado.get("cerrado", False)
                or estado.get("analizando", False)
            ):
                return

            siguiente = _siguiente_a_analizar(
                estado["buffer"], estado.get("ultima_revisada")
            )
            if not siguiente:
                logger.debug(
                    f"EVENTO | {camara} | esperando próxima imagen para análisis #{estado['analisis_count'] + 1}"
                )
                _eventos[camara] = estado
                return

        hay_cambio, porcentaje = comparar_fondo(siguiente, camara)
        archivo = os.path.basename(siguiente)

        if not hay_cambio:
            logger.info(
                f"FONDO | {camara} | {archivo} | sin cambio ({porcentaje}%) → SKIP"
            )
            actualizar_stats(analizado=False, persona_detectada=False)
            actualizar_historico(
                analizado=False,
                persona_detectada=False,
                camara=camara,
                archivo=archivo,
                skip_fondo=True,
            )

            with _eventos_lock:
                estado = _eventos.get(camara)
                if not estado:
                    return

                if siguiente in estado.get("buffer", []):
                    estado["ultima_revisada"] = siguiente
                    _eventos[camara] = estado
            continue

        with _eventos_lock:
            estado = _eventos.get(camara)
            if (
                not estado
                or estado.get("cerrado", False)
                or estado.get("analizando", False)
            ):
                return

            siguiente_actual = _siguiente_a_analizar(
                estado["buffer"], estado.get("ultima_revisada")
            )
            if siguiente_actual != siguiente:
                continue

            estado["ultima_revisada"] = siguiente
            _lanzar_analisis_evento(camara, estado, siguiente)
            _eventos[camara] = estado
            return


def _on_analisis_completo(future, camara: str, filepath_analizado: str):
    """
    Callback ejecutado cuando termina un análisis.
    Cierra el evento solo si se detectó persona sospechosa (>=50%).
    De lo contrario, lanza el siguiente análisis pendiente del evento.
    """
    try:
        resultado = future.result()
        if resultado is None:
            resultado = {
                "cerrar_evento": False,
                "persona_detectada": False,
                "sospechosa": 0,
            }
        cerrar_evento = resultado.get("cerrar_evento", False)
        persona_detectada = resultado.get("persona_detectada", False)
        sospechosa = resultado.get("sospechosa", 0)
    except Exception as e:
        logger.error(f"EVENTO | {camara} | error en callback de análisis: {e}")
        cerrar_evento = False
        persona_detectada = False
        sospechosa = 0

    continuar_evento = False

    with _eventos_lock:
        estado = _eventos.get(camara)
        if not estado:
            return

        estado["analizando"] = False
        estado["analisis_count"] += 1

        if cerrar_evento:
            logger.info(
                f"EVENTO | {camara} | CERRADO | persona sospechosa {sospechosa}% "
                f"en análisis #{estado['analisis_count']} | {os.path.basename(filepath_analizado)}"
            )
            estado["cerrado"] = True
            _eventos[camara] = estado
            return

        if estado["analisis_count"] >= MAX_ANALISIS_POR_EVENTO:
            logger.info(
                f"EVENTO | {camara} | CERRADO | techo de {MAX_ANALISIS_POR_EVENTO} análisis alcanzado"
            )
            estado["cerrado"] = True
            _eventos[camara] = estado
            return

        _eventos[camara] = estado
        continuar_evento = True

    if continuar_evento:
        _continuar_evento_con_filtro(camara)


def on_imagen_recibida(filepath: str, camara: str):
    """
    Punto de entrada para cada imagen recibida del watcher.

    Lógica del evento:
      - img1 (gap > EVENTO_GAP_SEGUNDOS): trigger, no se analiza, resetea estado
      - img2+ : se acumulan y se analizan en orden de llegada

    El evento solo se cierra cuando se detecta persona sospechosa (>=50%)
    o se alcanza MAX_ANALISIS_POR_EVENTO.
    """
    now = time.time()

    continuar_evento = False

    with _eventos_lock:
        estado = _get_estado_evento(camara)
        gap = now - estado["ultimo_ts"]
        estado["ultimo_ts"] = now

        if gap > EVENTO_GAP_SEGUNDOS:
            # img1 = trigger → resetear estado, no analizar
            logger.info(
                f"EVENTO | {camara} | INICIO | img1=trigger {os.path.basename(filepath)}"
            )
            estado["buffer"] = [filepath]
            estado["analisis_count"] = 0
            estado["imagenes_recibidas"] = 0
            estado["analizando"] = False
            estado["ultima_revisada"] = filepath
            estado["ultima_analizada"] = None
            estado["cerrado"] = False
            _eventos[camara] = estado
            actualizar_stats(analizado=False, persona_detectada=False)
            actualizar_historico(
                analizado=False,
                persona_detectada=False,
                camara=camara,
                archivo=os.path.basename(filepath),
            )
            return

        # Imagen del evento actual
        estado["imagenes_recibidas"] += 1
        img_num = estado["imagenes_recibidas"] + 1  # +1 porque img1 fue el trigger
        estado["buffer"].append(filepath)

        # Evento cerrado por persona sospechosa o techo de análisis
        if estado.get("cerrado", False):
            logger.debug(
                f"EVENTO | {camara} | img#{img_num} | ignorada (evento cerrado)"
            )
            actualizar_stats(analizado=False, persona_detectada=False)
            actualizar_historico(
                analizado=False,
                persona_detectada=False,
                camara=camara,
                archivo=os.path.basename(filepath),
            )
            _eventos[camara] = estado
            return

        if estado["analizando"]:
            # Hay un análisis en curso → acumular, el callback elegirá la siguiente
            logger.debug(
                f"EVENTO | {camara} | img#{img_num} | acumulada (análisis en curso)"
            )
            actualizar_stats(analizado=False, persona_detectada=False)
            actualizar_historico(
                analizado=False,
                persona_detectada=False,
                camara=camara,
                archivo=os.path.basename(filepath),
            )
            _eventos[camara] = estado
            return

        _eventos[camara] = estado
        continuar_evento = True

    if continuar_evento:
        _continuar_evento_con_filtro(camara)


# ============================================
# PROCESAMIENTO DE IMAGEN
# ============================================


def guardar_imagen_en_dir(
    imagen_path: str, archivo: str, directorio: str, max_imagenes: int
):
    """
    Guarda una copia de la imagen en el directorio indicado.
    Mantiene solo las últimas max_imagenes imágenes.

    Args:
        imagen_path: Ruta completa a la imagen original
        archivo: Nombre del archivo
        directorio: Directorio destino (NO_SOSPECHOSAS_DIR o SOSPECHOSAS_DIR)
        max_imagenes: Máximo de archivos a mantener en el directorio
    """
    import shutil
    import glob as glob_module

    os.makedirs(directorio, exist_ok=True)

    destino = os.path.join(directorio, archivo)
    try:
        shutil.copy2(imagen_path, destino)
        logger.debug(f"Imagen archivada en {os.path.basename(directorio)}: {archivo}")
    except Exception as e:
        logger.error(f"Error guardando imagen en {directorio}: {e}")
        return

    # Limpiar imágenes antiguas si excede el límite
    imagenes = []
    for patron in ("*.jpg", "*.jpeg", "*.png"):
        imagenes.extend(glob_module.glob(os.path.join(directorio, patron)))

    if len(imagenes) > max_imagenes:
        imagenes.sort(key=os.path.getmtime)
        a_borrar = len(imagenes) - max_imagenes
        for img in imagenes[:a_borrar]:
            try:
                os.remove(img)
                logger.debug(
                    f"Limpieza {os.path.basename(directorio)}: eliminada {os.path.basename(img)}"
                )
            except Exception as e:
                logger.error(f"Error eliminando {img}: {e}")


def archivar_imagen_analizada(imagen_path: str, archivo: str):
    """Guarda una copia de toda imagen que fue a análisis IA para auditoría posterior."""
    if not GUARDAR_IMAGENES_ANALIZADAS:
        return
    guardar_imagen_en_dir(
        imagen_path,
        archivo,
        NO_SOSPECHOSAS_DIR,
        MAX_IMAGENES_REFERENCIA_IA,
    )


def extraer_info_camara(archivo_path: str) -> str:
    """
    Extrae información de la cámara del path del archivo.

    Soporta:
    - FTP: /ftpcam1/uploads/192.168.1.10_MAC/fecha/hora/pic/archivo.jpg -> "192.168.1.10"
    - DVR: /dvr/capturas/alerta_A1_20260127_101234.jpg -> "DVR-A1"
    """
    path = Path(archivo_path)
    filename = path.name

    # Detectar si es del DVR (formato: alerta_A1_YYYYMMDD_HHMMSS.jpg)
    if filename.startswith("alerta_A") and "_" in filename:
        # Extraer canal: alerta_A1_... -> DVR-A1
        parts = filename.split("_")
        if len(parts) >= 2:
            canal = parts[1]  # "A1" (segundo elemento después de "alerta")
            return f"DVR-{canal}"

    # Patrón FTP: IP_MAC en el path
    for part in path.parts:
        if "." in part and "_" in part:  # Patrón IP_MAC
            return part.split("_")[0]  # Retorna solo la IP

    return "desconocida"


def extraer_fecha_hora(archivo_path: str) -> str:
    """
    Extrae fecha y hora del nombre/path del archivo.

    Soporta:
    - FTP: /ftpcam1/uploads/IP_MAC/YYYY-MM-DD/HH/pic/HHMMSSmmm.jpg -> "YYYY-MM-DD HH:MM:SS"
    - DVR: /dvr/capturas/alerta_A1_YYYYMMDD_HHMMSS.jpg -> "YYYY-MM-DD HH:MM:SS"

    Returns:
        Fecha-hora en formato "YYYY-MM-DD HH:MM:SS" o "desconocida" si no se puede extraer
    """
    path = Path(archivo_path)
    filename = path.name
    parts = path.parts

    # DVR: formato alerta_A1_YYYYMMDD_HHMMSS.jpg
    if filename.startswith("alerta_A") and "_" in filename:
        try:
            # parts: [..., alerta_A1_YYYYMMDD_HHMMSS.jpg]
            dvr_name = filename  # alerta_A1_20260213_000110.jpg
            # Extraer fecha y hora del nombre
            # Formato: alerta_A1_YYYYMMDD_HHMMSS
            match = re.search(r"alerta_A\d+_(\d{8})_(\d{6})", filename)
            if match:
                fecha = match.group(1)  # YYYYMMDD
                hora = match.group(2)  # HHMMSS
                return f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:8]} {hora[:2]}:{hora[2:4]}:{hora[4:6]}"
        except Exception:
            pass

    # FTP: formato /.../YYYY-MM-DD/HH/pic/HHMMSSmmm.jpg
    # Buscar componentes de fecha en el path
    try:
        fecha_str = None
        hora_str = None

        for i, part in enumerate(parts):
            # Buscar fecha en formato YYYY-MM-DD
            if re.match(r"\d{4}-\d{2}-\d{2}", part):
                fecha_str = part
                # La hora está en el siguiente nivel: .../YYYY-MM-DD/HH/...
                if i + 1 < len(parts):
                    next_part = parts[i + 1]
                    if re.match(r"\d{2}$", next_part):  # HH
                        hora_str = next_part
                break

        if fecha_str and hora_str:
            return f"{fecha_str} {hora_str}:00"
    except Exception:
        pass

    return "desconocida"


def procesar_imagen(imagen_path: str) -> dict:
    """Procesa una imagen: la analiza con IA y actúa según el resultado.

    Returns:
        Dict con keys: cerrar_evento (bool), persona_detectada (bool), sospechosa (int)

    Lógica de notificación:
        - Persona sospechosa (>=50%): notificar Telegram + guardar en dvr_sospechosas
        - Persona no sospechosa (<50%): guardar en dvr_no_sospechosas, sin alerta
        - Sin persona: no notificar, evento continúa
    """
    archivo = os.path.basename(imagen_path)
    camara = extraer_info_camara(imagen_path)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _resultado_vacio = {
        "cerrar_evento": False,
        "persona_detectada": False,
        "sospechosa": 0,
    }

    if not os.path.exists(imagen_path):
        logger.warning(f"Archivo no existe: {imagen_path}")
        return _resultado_vacio

    if os.path.getsize(imagen_path) < 1000:
        logger.warning(f"Archivo muy pequeño, posiblemente corrupto: {imagen_path}")
        return _resultado_vacio

    inicio_analisis = time.time()
    resultado = analizar_imagen(imagen_path, camara=camara)
    analisis_ms = round((time.time() - inicio_analisis) * 1000)

    archivar_imagen_analizada(imagen_path, archivo)

    if resultado.get("error"):
        registrar_auditoria_ia(
            "analisis_error",
            camara=camara,
            archivo=archivo,
            duracion_ms=analisis_ms,
            error=resultado["error"],
        )
        logger.error(f"ANALISIS | {camara} | {archivo} | ERROR: {resultado['error']}")
        actualizar_stats(analizado=False, persona_detectada=False)
        actualizar_historico(
            analizado=False, persona_detectada=False, camara=camara, archivo=archivo
        )
        return _resultado_vacio

    persona = resultado["persona"]
    sospechosa = resultado.get("sospechosa", 100)
    fecha_hora = extraer_fecha_hora(imagen_path)
    descripcion = resultado["descripcion"]
    tokens_prompt = resultado.get("tokens_prompt", 0)
    tokens_completion = resultado.get("tokens_completion", 0)
    tokens_total = resultado.get("tokens_total", 0)
    es_camara_sospecha = es_camara_con_sospecha(camara)
    fondo_info = {
        "actualizada": False,
        "inicializada": False,
        "porcentaje": None,
        "consecutivos": 0,
    }

    tokens_str = f"tokens={tokens_total} (p:{tokens_prompt}/c:{tokens_completion})"

    if persona:
        resetear_no_persona_fondo(camara)
        debe_notificar = sospechosa >= 50

        if debe_notificar:
            guardar_imagen_en_dir(
                imagen_path, archivo, SOSPECHOSAS_DIR, MAX_IMAGENES_SOSPECHOSAS
            )
            guardar_ultima_deteccion(archivo, camara, descripcion, fecha_hora)

            if es_camara_sospecha:
                caption = (
                    f"🚨 <b>PERSONA DETECTADA</b>\n\n"
                    f"📅 {fecha_hora if fecha_hora != 'desconocida' else timestamp}\n"
                    f"📷 Cámara: {camara}\n"
                    f"⚠️ Sospechosa: {sospechosa}%\n"
                    f"📁 {archivo}"
                )
            else:
                caption = (
                    f"🚨 <b>PERSONA DETECTADA</b>\n\n"
                    f"📅 {fecha_hora if fecha_hora != 'desconocida' else timestamp}\n"
                    f"📷 Cámara: {camara}\n"
                    f"📁 {archivo}"
                )

            enviados = enviar_telegram_foto(imagen_path, caption)
            destinatarios = len(get_telegram_chat_ids())
            if enviados > 0:
                notif_str = f"NOTIFICADO ({enviados}/{destinatarios})"
            else:
                notif_str = "ERROR_NOTIF"

            if es_camara_sospecha:
                logger.info(
                    f"ANALIZADO | {camara} | {archivo} | PERSONA sospechosa={sospechosa}% | {tokens_str} → {notif_str}"
                )
            else:
                logger.info(
                    f"ANALIZADO | {camara} | {archivo} | PERSONA | {tokens_str} → {notif_str}"
                )

        else:
            logger.info(
                f"ANALIZADO | {camara} | {archivo} | PERSONA sospechosa={sospechosa}% | {tokens_str} → NO_SOSPECHOSA"
            )

    else:
        logger.info(f"ANALIZADO | {camara} | {archivo} | NO_PERSONA | {tokens_str}")
        # Actualizar referencia de fondo solo tras varios NO_PERSONA consecutivos.
        fondo_info = actualizar_referencia_fondo(imagen_path, camara)

    actualizar_stats(analizado=True, persona_detectada=persona)
    fue_notificado = persona and sospechosa >= 50
    registrar_auditoria_ia(
        "analisis_ia",
        camara=camara,
        archivo=archivo,
        persona=persona,
        sospechosa=sospechosa if persona else 0,
        notificado=fue_notificado,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        tokens_total=tokens_total,
        duracion_ms=analisis_ms,
        fondo_actualizado=fondo_info.get("actualizada", False),
        fondo_inicializado=fondo_info.get("inicializada", False),
        fondo_desviacion_pct=fondo_info.get("porcentaje"),
        fondo_no_persona_consecutivos=fondo_info.get("consecutivos", 0),
    )
    actualizar_historico(
        analizado=True,
        persona_detectada=persona,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        tokens_total=tokens_total,
        descripcion=descripcion,
        camara=camara,
        archivo=archivo,
        sospechosa=sospechosa,
        notificado=fue_notificado,
        fondo_actualizado=fondo_info.get("actualizada", False),
        analisis_ms=analisis_ms,
    )

    cerrar = persona and sospechosa >= 50
    return {
        "cerrar_evento": cerrar,
        "persona_detectada": persona,
        "sospechosa": sospechosa,
    }


# ============================================
# WATCHER CON INOTIFY
# ============================================


def watch_directory():
    """Monitorea múltiples directorios con inotifywait."""
    logger.info(f"Iniciando monitoreo de: {', '.join(WATCH_DIRS)}")
    logger.info(
        f"Horario de detección automática: {DETECCION_INICIO} - {DETECCION_FIN}"
    )
    logger.info(f"Destinatarios Telegram: {len(get_telegram_chat_ids())}")
    logger.info(f"Rate limit: {RATE_LIMIT_MAX} img/min")
    logger.info(f"Gap de evento por cámara: {EVENTO_GAP_SEGUNDOS:.1f}s")

    try:
        subprocess.run(["which", "inotifywait"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        logger.error(
            "inotifywait no está instalado. Ejecutar: sudo apt install inotify-tools"
        )
        sys.exit(1)

    for watch_dir in WATCH_DIRS:
        if not os.path.isdir(watch_dir):
            logger.warning(f"Directorio no existe: {watch_dir}")

    cmd = [
        "inotifywait",
        "-m",
        "-r",
        "-e",
        "close_write",
        "--format",
        "%w%f",
    ] + WATCH_DIRS

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    logger.info("Watcher iniciado, esperando imágenes...")

    if process.stdout is None:
        logger.error("Error: No se pudo obtener stdout del proceso inotifywait")
        return

    try:
        for line in process.stdout:
            filepath = line.strip()

            if not filepath.lower().endswith(".jpg"):
                continue

            time.sleep(WAIT_BEFORE_ANALYZE)
            archivo = os.path.basename(filepath)
            camara = extraer_info_camara(filepath)

            analizar, razon = debe_analizar()

            if not analizar:
                logger.info(f"Nueva imagen: {archivo} de cámara {camara}")
                logger.info(f"IGNORADO | - | {camara} | {archivo} | {razon}")
                actualizar_stats(analizado=False, persona_detectada=False)
                actualizar_historico(
                    analizado=False,
                    persona_detectada=False,
                    camara=camara,
                    archivo=archivo,
                )
                continue

            # Verificar rate limit global antes de encolar
            can_process, image_to_process = RATE_LIMITER.can_process(filepath)
            if not can_process:
                continue  # Rate limiter guarda pending internamente

            # Comparación de fondo: detectar si hay cambio vs referencia
            hay_cambio, porcentaje = comparar_fondo(image_to_process, camara)
            if not hay_cambio:
                logger.info(
                    f"FONDO | {camara} | {archivo} | sin cambio ({porcentaje}%) → SKIP"
                )
                actualizar_stats(analizado=False, persona_detectada=False)
                actualizar_historico(
                    analizado=False,
                    persona_detectada=False,
                    camara=camara,
                    archivo=archivo,
                    skip_fondo=True,
                )
                continue

            # Loguear solo cuando la imagen se procesa realmente (no cuando llega al rate limiter)
            logger.info(f"Nueva imagen: {archivo} de cámara {camara}")

            # Delegar al gestor de eventos por cámara
            on_imagen_recibida(image_to_process, camara)

    except KeyboardInterrupt:
        logger.info("Watcher detenido por usuario")
    finally:
        process.terminate()
        ANALYSIS_POOL.shutdown(wait=True)


# ============================================
# MONITOR DE CAMBIOS DE ESTADO (modo auto)
# ============================================


def monitor_estado_auto():
    """
    Thread que espera hasta el próximo cambio de estado en modo auto.
    Usa temporizador exacto en vez de polling (más eficiente).

    En vez de verificar cada 30 segundos, calcula cuánto falta para
    el próximo cambio de horario y hace un solo sleep largo.
    """
    global _estado_anterior

    while True:
        try:
            state = cargar_estado()
            modo = state.get("modo", "auto")

            if modo != "auto":
                # En modo forzado, esperar y reintentar
                time.sleep(60)
                continue

            ahora = datetime.now()
            inicio = parse_time(DETECCION_INICIO)  # 00:01
            fin = parse_time(DETECCION_FIN)  # 07:45

            # Calcular próximo cambio de estado
            hora_inicio_hoy = datetime.combine(ahora.date(), inicio)
            hora_fin_hoy = datetime.combine(ahora.date(), fin)

            if ahora < hora_inicio_hoy:
                # Antes de 00:01, próximo cambio = 00:01 (activación)
                proximo = hora_inicio_hoy
                proximo_estado = True
            elif ahora < hora_fin_hoy:
                # Entre 00:01 y 07:45, próximo cambio = 07:45 (desactivación)
                proximo = hora_fin_hoy
                proximo_estado = False
            else:
                # Después de 07:45, próximo cambio = 00:01 mañana
                proximo = hora_inicio_hoy + timedelta(days=1)
                proximo_estado = True

            # Calcular segundos hasta el cambio
            segundos = (proximo - ahora).total_seconds()
            horas = segundos / 3600
            logger.info(
                f"Próximo cambio de estado en {horas:.1f}h ({proximo.strftime('%Y-%m-%d %H:%M')})"
            )

            # Dormir hasta el momento exacto (+2 seg de margen)
            time.sleep(segundos + 2)

            # Verificar que seguimos en modo auto (pudo cambiar durante el sleep)
            state = cargar_estado()
            if state.get("modo", "auto") != "auto":
                continue

            # Validar que realmente hubo un cambio de estado (evita notifs duplicadas por NTP/drift)
            if _estado_anterior is not None and _estado_anterior == proximo_estado:
                logger.debug(
                    f"Estado sin cambio ({proximo_estado}), omitiendo notificación duplicada"
                )
                continue

            # Preparar y enviar notificación
            hora_actual = datetime.now().strftime("%H:%M")

            if proximo_estado:
                # Se activó
                mensaje = (
                    f"🟢 <b>Sistema ACTIVADO</b>\n\n"
                    f"⏰ Hora: {hora_actual}\n"
                    f"📅 Horario: {DETECCION_INICIO} - {DETECCION_FIN}\n"
                    f"🔍 Las imágenes serán analizadas por IA"
                )
                logger.info("Estado cambiado: ACTIVADO (horario automático)")
            else:
                # Se desactivó - incluir resumen
                metricas = obtener_metricas_dia()

                mensaje = (
                    f"🔴 <b>Sistema DESACTIVADO</b>\n\n"
                    f"⏰ Hora: {hora_actual}\n"
                    f"📅 Próxima activación: {DETECCION_INICIO}\n\n"
                    f"📊 <b>Resumen de la sesión:</b>\n"
                    f"  • Imágenes recibidas: {metricas['imagenes_recibidas']}\n"
                    f"  • Analizadas con IA: {metricas['imagenes_analizadas']}\n"
                    f"  • Skips por fondo: {metricas['skips_fondo']}\n"
                    f"  • Reemplazos de fondo: {metricas['fondo_actualizaciones']}\n"
                    f"  • Personas detectadas: {metricas['personas_detectadas']}\n"
                    f"  • Tokens IA: {metricas['tokens_total_fmt']}\n"
                    f"  • Tiempo prom. IA: {metricas['analisis_ms_promedio']} ms"
                )
                logger.info("Estado cambiado: DESACTIVADO (fuera de horario)")

            enviar_telegram_mensaje(mensaje)
            _estado_anterior = proximo_estado

        except Exception as e:
            logger.error(f"Error en monitor de estado: {e}")
            time.sleep(60)


# ============================================
# MAIN
# ============================================


def main():
    """Punto de entrada principal."""

    def procesar_pendiente(filepath: str):
        camara = extraer_info_camara(filepath)
        on_imagen_recibida(filepath, camara)

    RATE_LIMITER.set_process_callback(procesar_pendiente)

    logger.info("=" * 60)
    logger.info(f"Camera Person Detection System v{CAM_WATCHER_VERSION} - Iniciando")
    logger.info(f"Rate limit: {RATE_LIMIT_MAX} img/min")
    logger.info(f"Gap de evento por cámara: {EVENTO_GAP_SEGUNDOS:.1f}s")
    logger.info(f"Máx análisis por evento: {MAX_ANALISIS_POR_EVENTO}")
    logger.info(
        f"Fondo: referencia tras {FONDO_NO_PERSONA_CONSECUTIVOS} NO_PERSONA consecutivos"
    )
    logger.info(f"Fondo cámaras activas: {', '.join(FONDO_CAMARAS_ACTIVAS)}")
    logger.info(f"Auditoría IA: {AI_AUDIT_LOG_FILE}")
    logger.info("=" * 60)

    if not TELEGRAM_BOT_TOKEN:
        logger.warning(
            "TELEGRAM_BOT_TOKEN no configurado - las notificaciones no funcionarán"
        )

    destinatarios = get_telegram_chat_ids()
    if not destinatarios:
        logger.warning(
            "TELEGRAM_CHAT_IDS no configurado - las notificaciones no funcionarán"
        )
    else:
        logger.info(
            f"Configurados {len(destinatarios)} destinatario(s) para notificaciones"
        )

    if not os.path.isdir(WATCH_DIRS[0]):
        logger.warning(f"Directorio principal no existe: {WATCH_DIRS[0]}")

    state = cargar_estado()
    modo = state.get("modo", "auto")
    analizar, razon = debe_analizar()
    estado_actual = "ACTIVADO" if analizar else "DESACTIVADO"

    global _estado_anterior
    _estado_anterior = analizar

    dirs_str = ", ".join([os.path.basename(d) for d in WATCH_DIRS])
    enviar_telegram_mensaje(
        f"🟢 <b>Sistema de Detección Iniciado</b>\n\n"
        f"📍 Modo: {modo.upper()}\n"
        f"🔔 Estado actual: {estado_actual}\n"
        f"⏰ Horario: {DETECCION_INICIO} - {DETECCION_FIN}\n"
        f"📂 Monitoreando: {dirs_str}\n"
        f"👥 Destinatarios: {len(get_telegram_chat_ids())}"
    )

    monitor_thread = threading.Thread(target=monitor_estado_auto, daemon=True)
    monitor_thread.start()
    logger.info("Monitor de cambios de estado iniciado")

    watch_directory()


if __name__ == "__main__":
    main()
