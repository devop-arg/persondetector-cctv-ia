#!/usr/bin/env python3
"""
Telegram Bot para Control Remoto del Sistema de Detección

Modo: Webhook (respuesta instantánea)
Endpoint: https://server1.dev0p.com/webhook/camdetector

Comandos disponibles:
  /activar    - Fuerza modo detección ON
  /desactivar - Fuerza modo detección OFF
  /auto       - Vuelve al modo automático (horario)
  /estado     - Muestra estado actual
  /ultimo     - Muestra última detección de persona
  /stats      - Estadísticas del día
  /historial  - Historial con tokens
  /espacio    - Espacio en disco
  /camaras    - Estadísticas DVR por cámara
  /destinatarios - Muestra chat IDs configurados (solo admin)
  /agregar    - Agregar destinatario (solo admin)
  /limpiar    - Limpiar imágenes antiguas (solo admin)
  /test       - Enviar notificación de prueba (solo admin)

Autor: OpenCode
Fecha: 2026-02-13
Versión: 2.6.2
"""

import os
import sys
import json
import logging
import threading
import fcntl
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, Any, Optional, List

import requests
from flask import Flask, request, jsonify
from app_version import TELEGRAM_BOT_VERSION

# ============================================
# CONFIGURACIÓN
# ============================================

def load_env() -> Dict[str, str]:
    """Carga variables de entorno desde .env"""
    env_path = Path(__file__).parent / '.env'
    config = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
    return config

CONFIG = load_env()

# Telegram
TELEGRAM_BOT_TOKEN = CONFIG.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_IDS = [cid.strip() for cid in CONFIG.get('TELEGRAM_CHAT_IDS', '').split(',') if cid.strip()]
TELEGRAM_ADMIN_ID = CONFIG.get('TELEGRAM_ADMIN_ID', '')

# Compatibilidad con config anterior
if not TELEGRAM_CHAT_IDS and CONFIG.get('TELEGRAM_CHAT_ID'):
    TELEGRAM_CHAT_IDS = [CONFIG.get('TELEGRAM_CHAT_ID')]
if not TELEGRAM_ADMIN_ID and TELEGRAM_CHAT_IDS:
    TELEGRAM_ADMIN_ID = TELEGRAM_CHAT_IDS[0]

# Horarios
DETECCION_INICIO = CONFIG.get('DETECCION_INICIO', '00:01')
DETECCION_FIN = CONFIG.get('DETECCION_FIN', '07:45')

# Estado
STATE_FILE = CONFIG.get('STATE_FILE', '/home/capw/projects/camdetector/config/state.json')
STATS_HISTORY_FILE = CONFIG.get('STATS_HISTORY_FILE', '/home/capw/projects/camdetector/config/stats_history.json')

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================
# FUNCIONES DE ESTADO
# ============================================

def cargar_estado() -> Dict[str, Any]:
    """Carga el estado persistente desde el archivo JSON (process-safe)."""
    try:
        with open(STATE_FILE, 'r') as f:
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
                "personas_detectadas": 0
            }
        }

def guardar_estado(state: Dict[str, Any]):
    """Guarda el estado persistente en el archivo JSON (process-safe)."""
    # Abrir con 'a+' para no truncar antes de tener el lock
    with open(STATE_FILE, 'a+') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Lock exclusivo PRIMERO
        f.seek(0)  # Ir al inicio
        f.truncate()  # Ahora truncar
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()  # Asegurar que se escriba antes de liberar el lock
        os.fsync(f.fileno())  # Forzar escritura a disco
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock

def parse_time(time_str: str) -> dtime:
    """Convierte string HH:MM a objeto time."""
    h, m = map(int, time_str.split(':'))
    return dtime(h, m)

def esta_en_horario_activo() -> bool:
    """Verifica si estamos dentro del horario de detección automática.
    Soporta horarios que cruzan la medianoche (ej: 22:00 - 06:00)."""
    hora_actual = datetime.now().time()
    inicio = parse_time(DETECCION_INICIO)
    fin = parse_time(DETECCION_FIN)
    
    # Soporta cruce de medianoche
    if inicio < fin:
        # Horario normal (ej: 00:01 - 07:45)
        return inicio <= hora_actual <= fin
    else:
        # Horario que cruza medianoche (ej: 22:00 - 06:00)
        return hora_actual >= inicio or hora_actual <= fin

def obtener_estado_actual() -> tuple:
    """
    Obtiene el estado actual del sistema.
    
    Returns:
        (modo, esta_activo): modo es 'auto'/'forzado_activado'/'forzado_desactivado',
                           esta_activo indica si está analizando ahora
    """
    state = cargar_estado()
    modo = state.get('modo', 'auto')
    
    if modo == 'forzado_activado':
        return modo, True
    elif modo == 'forzado_desactivado':
        return modo, False
    else:  # auto
        return modo, esta_en_horario_activo()

# ============================================
# TELEGRAM API
# ============================================

def enviar_mensaje(chat_id: str, texto: str) -> bool:
    """Envía un mensaje de texto a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': texto,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        logger.error(f"Error enviando mensaje: {e}")
        return False

def enviar_a_todos(texto: str) -> int:
    """Envía un mensaje a todos los chat IDs configurados."""
    exitos = 0
    for chat_id in TELEGRAM_CHAT_IDS:
        if enviar_mensaje(chat_id, texto):
            exitos += 1
    return exitos

# ============================================
# HANDLERS DE COMANDOS
# ============================================

def cmd_activar(chat_id: str) -> str:
    """Activa el modo detección (forzado)."""
    state = cargar_estado()
    state['modo'] = 'forzado_activado'
    guardar_estado(state)
    
    # Notificar a todos en background (no bloquea la respuesta)
    threading.Thread(
        target=enviar_a_todos,
        args=("🔔 <b>Modo Detección ACTIVADO</b> por comando remoto.",),
        daemon=True
    ).start()
    
    return """✅ <b>Modo Detección ACTIVADO</b>

El sistema analizará TODAS las imágenes hasta que uses /desactivar o /auto.

📍 Estado: <b>ACTIVADO (forzado)</b>"""

def cmd_desactivar(chat_id: str) -> str:
    """Desactiva el modo detección (forzado)."""
    state = cargar_estado()
    state['modo'] = 'forzado_desactivado'
    guardar_estado(state)
    
    # Notificar a todos en background
    threading.Thread(
        target=enviar_a_todos,
        args=("🔕 <b>Modo Detección DESACTIVADO</b> por comando remoto.",),
        daemon=True
    ).start()
    
    return """🔕 <b>Modo Detección DESACTIVADO</b>

El sistema NO analizará imágenes hasta que uses /activar o /auto.

📍 Estado: <b>DESACTIVADO (forzado)</b>"""

def cmd_auto(chat_id: str) -> str:
    """Vuelve al modo automático."""
    state = cargar_estado()
    state['modo'] = 'auto'
    guardar_estado(state)
    
    en_horario = esta_en_horario_activo()
    estado_actual = "ACTIVADO" if en_horario else "DESACTIVADO"
    hora_actual = datetime.now().strftime('%H:%M')
    
    # Notificar a todos en background
    threading.Thread(
        target=enviar_a_todos,
        args=(f"🔄 <b>Modo AUTOMÁTICO</b> activado. Estado actual: {estado_actual}",),
        daemon=True
    ).start()
    
    return f"""🔄 <b>Modo AUTOMÁTICO activado</b>

El sistema analizará imágenes según el horario configurado.

⏰ Horario de detección: {DETECCION_INICIO} - {DETECCION_FIN}
🕐 Hora actual: {hora_actual}
📍 Estado actual: <b>{estado_actual}</b>"""

def cmd_estado(chat_id: str) -> str:
    """Muestra el estado actual del sistema."""
    state = cargar_estado()
    modo, activo = obtener_estado_actual()
    hora_actual = datetime.now().strftime('%H:%M')
    
    # Determinar texto del modo
    if modo == 'auto':
        modo_texto = "AUTOMÁTICO"
        emoji_modo = "🔄"
    elif modo == 'forzado_activado':
        modo_texto = "FORZADO ACTIVADO"
        emoji_modo = "🔒"
    else:
        modo_texto = "FORZADO DESACTIVADO"
        emoji_modo = "🔓"
    
    estado_actual = "ACTIVADO ✅" if activo else "DESACTIVADO 🔕"
    
    # Stats del día
    stats = state.get('stats_hoy', {})
    stats_fecha = stats.get('fecha', 'N/A')
    hoy = datetime.now().strftime('%Y-%m-%d')
    
    if stats_fecha == hoy:
        recibidas = stats.get('imagenes_recibidas', 0)
        analizadas = stats.get('imagenes_analizadas', 0)
        personas = stats.get('personas_detectadas', 0)
    else:
        recibidas = analizadas = personas = 0
    
    return f"""📊 <b>Estado del Sistema</b>

{emoji_modo} Modo: <b>{modo_texto}</b>
⏰ Horario activo: {DETECCION_INICIO} - {DETECCION_FIN}
🕐 Hora actual: {hora_actual}
📍 Estado actual: <b>{estado_actual}</b>

📈 <b>Estadísticas de Hoy:</b>
  • Imágenes recibidas: {recibidas}
  • Analizadas con IA: {analizadas}
  • Personas detectadas: {personas}

👥 Destinatarios: {len(TELEGRAM_CHAT_IDS)}

<b>Comandos disponibles:</b>
/activar - Forzar detección ON
/desactivar - Forzar detección OFF
/auto - Modo automático
/ultimo - Última detección
/stats - Estadísticas"""

def cmd_ultimo(chat_id: str) -> str:
    """Muestra la última detección de persona."""
    state = cargar_estado()
    ultima = state.get('ultima_deteccion')
    
    if not ultima:
        return "📭 No hay detecciones de personas registradas todavía."
    
    descripcion = (ultima.get('descripcion') or '').strip()
    detalle = f"\n\n📝 {descripcion}" if descripcion else ""

    return f"""🔍 <b>Última Detección de Persona</b>

📅 Fecha sistema: {ultima.get('fecha', 'N/A')}
📸 Fecha imagen: {ultima.get('fecha_imagen', 'N/A')}
📷 Cámara: {ultima.get('camara', 'N/A')}
📁 Archivo: {ultima.get('archivo', 'N/A')}{detalle}"""

def cmd_stats(chat_id: str) -> str:
    """Muestra estadísticas del día."""
    state = cargar_estado()
    stats = state.get('stats_hoy', {})
    stats_fecha = stats.get('fecha', 'N/A')
    hoy = datetime.now().strftime('%Y-%m-%d')
    
    if stats_fecha != hoy:
        return "📊 No hay estadísticas para hoy todavía."
    
    recibidas = stats.get('imagenes_recibidas', 0)
    analizadas = stats.get('imagenes_analizadas', 0)
    personas = stats.get('personas_detectadas', 0)
    
    # Calcular porcentajes
    pct_analizadas = (analizadas / recibidas * 100) if recibidas > 0 else 0
    pct_personas = (personas / analizadas * 100) if analizadas > 0 else 0
    
    return f"""📊 <b>Estadísticas de Hoy</b>
📅 {hoy}

📥 Imágenes recibidas: <b>{recibidas}</b>
🔍 Analizadas con IA: <b>{analizadas}</b> ({pct_analizadas:.0f}%)
👤 Personas detectadas: <b>{personas}</b> ({pct_personas:.0f}%)

💡 Las imágenes no analizadas fueron recibidas cuando el modo de detección estaba desactivado."""

def cargar_historico() -> Dict[str, Any]:
    """Carga el histórico de estadísticas desde el archivo JSON (process-safe)."""
    try:
        with open(STATS_HISTORY_FILE, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Lock shared para lectura
            data = json.load(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Liberar lock
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"dias": {}, "analisis_sin_persona": {}}


def format_tokens(n: int) -> str:
    """Formatea número de tokens de forma legible."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def cmd_historial(chat_id: str, periodo: str = "dia") -> str:
    """Muestra estadísticas históricas por día, semana o mes."""
    from datetime import timedelta
    
    history = cargar_historico()
    dias_data = history.get('dias', {})
    analisis_sin_persona = history.get('analisis_sin_persona', {})
    personas_no_sospechosas = history.get('personas_no_sospechosas_detalle', {})
    
    if not dias_data:
        return "📊 No hay datos históricos todavía."
    
    hoy = datetime.now()
    
    # Determinar rango de fechas según período
    if periodo == "semana":
        dias_atras = 7
        titulo = "Últimos 7 días"
    elif periodo == "mes":
        dias_atras = 30
        titulo = "Últimos 30 días"
    else:  # dia
        dias_atras = 1
        titulo = "Hoy"
    
    # Calcular totales
    total_recibidas = 0
    total_analizadas = 0
    total_personas = 0
    total_no_sospechosas = 0
    total_sospechosas = 0
    total_skips_fondo = 0
    total_fondo_actualizaciones = 0
    total_analisis_sin = 0
    total_analisis_ms = 0
    total_tokens_prompt = 0
    total_tokens_completion = 0
    total_tokens = 0
    dias_con_datos = 0
    fechas_con_datos = []
    
    for i in range(dias_atras):
        fecha = (hoy - timedelta(days=i)).strftime('%Y-%m-%d')
        if fecha in dias_data:
            d = dias_data[fecha]
            fechas_con_datos.append(fecha)
            total_recibidas += d.get('imagenes_recibidas', 0)
            total_analizadas += d.get('imagenes_analizadas', 0)
            total_personas += d.get('personas_detectadas', 0)
            total_no_sospechosas += d.get('personas_no_sospechosas', 0)
            total_sospechosas += d.get(
                'personas_sospechosas_notificadas',
                d.get('personas_detectadas', 0) - d.get('personas_no_sospechosas', 0),
            )
            total_skips_fondo += d.get('skips_fondo', 0)
            total_fondo_actualizaciones += d.get('fondo_actualizaciones', 0)
            total_analisis_sin += d.get(
                'analisis_sin_persona_count',
                d.get('imagenes_analizadas', 0) - d.get('personas_detectadas', 0),
            )
            total_analisis_ms += d.get('analisis_ms_total', 0)
            total_tokens_prompt += d.get('tokens_prompt', 0)
            total_tokens_completion += d.get('tokens_completion', 0)
            total_tokens += d.get('tokens_total', 0)
            dias_con_datos += 1
    
    if dias_con_datos == 0:
        return f"📊 No hay datos para {titulo.lower()}."
    
    # Calcular porcentajes
    pct_analizadas = (total_analizadas / total_recibidas * 100) if total_recibidas > 0 else 0
    pct_personas = (total_personas / total_analizadas * 100) if total_analizadas > 0 else 0
    pct_skips_fondo = (total_skips_fondo / total_recibidas * 100) if total_recibidas > 0 else 0
    promedio_ms = round(total_analisis_ms / total_analizadas) if total_analizadas > 0 else 0

    def format_momento(fecha: str, item: Dict[str, Any]) -> str:
        hora = item.get('hora', '??:??')
        if periodo == "dia":
            return hora
        return f"{fecha[8:10]}/{fecha[5:7]} {hora}"

    detalle_no_sospechosas = []
    detalle_sin_persona = []
    for fecha in fechas_con_datos:
        for item in personas_no_sospechosas.get(fecha, []):
            detalle_no_sospechosas.append((fecha, item))
        for item in analisis_sin_persona.get(fecha, []):
            detalle_sin_persona.append((fecha, item))

    detalle_no_sospechosas.sort(key=lambda x: (x[0], x[1].get('hora', '')), reverse=True)
    detalle_sin_persona.sort(key=lambda x: (x[0], x[1].get('hora', '')), reverse=True)

    mensaje = f"""📊 <b>Historial - {titulo}</b>

📥 Imágenes recibidas: <b>{total_recibidas:,}</b>
🔍 Analizadas con IA: <b>{total_analizadas:,}</b> ({pct_analizadas:.0f}%)
🪟 Skips por fondo: <b>{total_skips_fondo:,}</b> ({pct_skips_fondo:.0f}%)
🖼️ Reemplazos de fondo: <b>{total_fondo_actualizaciones:,}</b>
👤 Personas detectadas: <b>{total_personas:,}</b> ({pct_personas:.0f}%)
  ├ 🚨 Sospechosas (notificadas): <b>{total_sospechosas:,}</b>
  └ ✅ No sospechosas: <b>{total_no_sospechosas:,}</b>
❌ Sin persona: <b>{total_analisis_sin:,}</b>
⚡ Tiempo prom. análisis IA: <b>{promedio_ms} ms</b>

🎯 <b>Consumo de Tokens:</b>
  • Prompt: <b>{format_tokens(total_tokens_prompt)}</b>
  • Completion: <b>{format_tokens(total_tokens_completion)}</b>
  • Total: <b>{format_tokens(total_tokens)}</b>

📅 Días con datos: {dias_con_datos}"""

    if detalle_no_sospechosas:
        mensaje += f"\n\n👤 <b>Personas no sospechosas ({len(detalle_no_sospechosas)} total):</b>"
        for fecha, item in detalle_no_sospechosas[:10]:
            momento = format_momento(fecha, item)
            camara = item.get('camara', '?')
            pct = item.get('sospechosa', 0)
            desc = (item.get('descripcion') or '').strip()
            if desc:
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                mensaje += f"\n  • {momento} [{camara}] {pct}%: {desc}"
            else:
                mensaje += f"\n  • {momento} [{camara}] {pct}%"
        if len(detalle_no_sospechosas) > 10:
            mensaje += f"\n  ... y {len(detalle_no_sospechosas) - 10} más"

    if detalle_sin_persona:
        mensaje += f"\n\n📝 <b>Análisis sin detección ({len(detalle_sin_persona)} total):</b>"
        for fecha, item in detalle_sin_persona[:10]:
            momento = format_momento(fecha, item)
            desc = (item.get('descripcion') or '').strip()
            if desc:
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                mensaje += f"\n  • {momento}: {desc}"
            else:
                mensaje += f"\n  • {momento}"
        if len(detalle_sin_persona) > 10:
            mensaje += f"\n  ... y {len(detalle_sin_persona) - 10} más"

    mensaje += "\n\n💡 Usa: /historial dia | semana | mes"
    return mensaje

def cmd_destinatarios(chat_id: str) -> str:
    """Muestra los chat IDs configurados (solo admin)."""
    if chat_id != TELEGRAM_ADMIN_ID:
        return "⛔ Este comando solo está disponible para el administrador."
    
    lista = "\n".join([f"  • {cid}" for cid in TELEGRAM_CHAT_IDS])
    return f"""👥 <b>Destinatarios Configurados</b>

{lista}

🔑 Admin: {TELEGRAM_ADMIN_ID}

💡 Usa /agregar [ID] para agregar un nuevo destinatario"""

def cmd_agregar(chat_id: str, nuevo_id: str = "") -> str:
    """Agrega un nuevo chat ID a los destinatarios (solo admin)."""
    if chat_id != TELEGRAM_ADMIN_ID:
        return "⛔ Este comando solo está disponible para el administrador."
    
    if not nuevo_id:
        return """📝 <b>Agregar Destinatario</b>

Uso: /agregar [CHAT_ID]

Ejemplo: /agregar 123456789

💡 Para obtener el ID de un usuario:
  1. El usuario debe enviar /start al bot
  2. Verás su ID en los logs del bot
  3. O puede usar @userinfobot en Telegram"""
    
    # Validar que es un número
    nuevo_id = nuevo_id.strip()
    if not nuevo_id.lstrip('-').isdigit():
        return f"❌ ID inválido: {nuevo_id}\n\nEl ID debe ser un número (puede ser negativo para grupos)."
    
    # Verificar si ya existe
    if nuevo_id in TELEGRAM_CHAT_IDS:
        return f"ℹ️ El ID {nuevo_id} ya está en la lista de destinatarios."
    
    # Leer archivo .env
    env_path = Path(__file__).parent / '.env'
    try:
        with open(env_path, 'r') as f:
            contenido = f.read()
        
        # Buscar y actualizar TELEGRAM_CHAT_IDS
        import re
        patron = r'(TELEGRAM_CHAT_IDS=)([^\n]*)'
        match = re.search(patron, contenido)
        
        if match:
            ids_actuales = match.group(2).strip()
            if ids_actuales:
                nuevos_ids = f"{ids_actuales},{nuevo_id}"
            else:
                nuevos_ids = nuevo_id
            contenido = re.sub(patron, f'TELEGRAM_CHAT_IDS={nuevos_ids}', contenido)
        else:
            contenido += f"\nTELEGRAM_CHAT_IDS={nuevo_id}\n"
        
        # Guardar
        with open(env_path, 'w') as f:
            f.write(contenido)
        
        # Actualizar lista en memoria
        TELEGRAM_CHAT_IDS.append(nuevo_id)
        
        # Cantidad actual de destinatarios
        total = len(TELEGRAM_CHAT_IDS)
        
        return f"""✅ <b>Destinatario Agregado</b>

ID: <code>{nuevo_id}</code>

📢 El nuevo destinatario recibirá las alertas inmediatamente.
👥 Total destinatarios: <b>{total}</b>

💡 No es necesario reiniciar los servicios."""
    
    except Exception as e:
        return f"❌ Error guardando: {e}"

def cmd_limpiar(chat_id: str, dias: str = "7") -> str:
    """Elimina imágenes antiguas de los directorios monitoreados (solo admin)."""
    if chat_id != TELEGRAM_ADMIN_ID:
        return "⛔ Este comando solo está disponible para el administrador."
    
    import subprocess
    import shutil
    
    # Validar parámetro
    try:
        dias_num = int(dias)
        if dias_num < 0:
            dias_num = 0
        if dias_num > 365:
            dias_num = 365
    except ValueError:
        return """📝 <b>Limpiar Imágenes</b>

Uso: /limpiar [DIAS]

Elimina imágenes con más de X días de antigüedad.

Ejemplos:
  /limpiar 0   - Elimina TODAS las imágenes (incluyendo hoy)
  /limpiar 1   - Elimina imágenes de ayer y anteriores
  /limpiar 7   - Elimina imágenes de más de 7 días
  /limpiar 30  - Elimina imágenes de más de 30 días

⚠️ Esta acción es IRREVERSIBLE."""
    
    # Usar WATCH_DIRS (plural) - soporta múltiples directorios
    watch_dirs_str = CONFIG.get('WATCH_DIRS', '/home/capw/projects/camdetector/data/ftpcam1')
    watch_dirs = [d.strip() for d in watch_dirs_str.split(',') if d.strip()]
    
    total_antes = 0
    total_eliminados = 0
    total_errores = 0
    total_espacio = 0
    
    try:
        for watch_dir in watch_dirs:
            if not os.path.isdir(watch_dir):
                continue
            
            # Contar imágenes antes en este directorio
            resultado = subprocess.run(
                ['find', watch_dir, '-name', '*.jpg', '-type', 'f'],
                capture_output=True, text=True, timeout=30
            )
            total_antes += len([f for f in resultado.stdout.strip().split('\n') if f])
            
            # Buscar imágenes a eliminar
            if dias_num == 0:
                find_cmd = ['find', watch_dir, '-name', '*.jpg', '-type', 'f']
            else:
                find_cmd = ['find', watch_dir, '-name', '*.jpg', '-type', 'f', '-mtime', f'+{dias_num - 1}']
            
            resultado = subprocess.run(
                find_cmd,
                capture_output=True, text=True, timeout=30
            )
            archivos = [f for f in resultado.stdout.strip().split('\n') if f]
            
            # Calcular espacio y eliminar
            for archivo in archivos:
                try:
                    total_espacio += os.path.getsize(archivo)
                    os.remove(archivo)
                    total_eliminados += 1
                except Exception as e:
                    total_errores += 1
            
            # Limpiar directorios vacíos
            subprocess.run(
                ['find', watch_dir, '-type', 'd', '-empty', '-delete'],
                capture_output=True, timeout=30
            )
        
        espacio_mb = total_espacio / (1024 * 1024)
        
        if total_eliminados == 0:
            msg_dias = "todas las" if dias_num == 0 else f"con más de {dias_num} días de antigüedad"
            return f"""🗑️ <b>Limpiar Imágenes</b>

No hay imágenes {msg_dias}.

📊 Total de imágenes: {total_antes}"""
        
        return f"""🗑️ <b>Limpieza Completada</b>

✅ Eliminadas: <b>{total_eliminados}</b> imágenes
📦 Espacio liberado: <b>{espacio_mb:.1f} MB</b>
❌ Errores: {total_errores}

📊 Imágenes restantes: {total_antes - total_eliminados}"""
        
    except subprocess.TimeoutExpired:
        return "❌ Timeout ejecutando limpieza. Hay demasiados archivos."
    except Exception as e:
        return f"❌ Error: {e}"

def cmd_espacio(chat_id: str) -> str:
    """Muestra el espacio usado en los directorios monitoreados."""
    import subprocess
    import shutil
    
    # Usar WATCH_DIRS (plural) - soporta múltiples directorios
    watch_dirs_str = CONFIG.get('WATCH_DIRS', '/home/capw/projects/camdetector/data/ftpcam1')
    watch_dirs = [d.strip() for d in watch_dirs_str.split(',') if d.strip()]
    
    try:
        # Usar el primer directorio para calcular espacio de disco
        primer_dir = watch_dirs[0] if watch_dirs else '/home/capw'
        total, usado, libre = shutil.disk_usage(primer_dir)
        total_gb = total / (1024**3)
        usado_gb = usado / (1024**3)
        libre_gb = libre / (1024**3)
        pct_usado = (usado / total) * 100
        
        # Calcular espacio e imágenes de todos los directorios
        espacio_total = 0
        num_imagenes = 0
        detalles_dirs = []
        
        for watch_dir in watch_dirs:
            if not os.path.isdir(watch_dir):
                detalles_dirs.append(f"  • {watch_dir}: No existe")
                continue
            
            # Espacio usado por este directorio
            resultado = subprocess.run(
                ['du', '-sh', watch_dir],
                capture_output=True, text=True, timeout=30
            )
            espacio_dir = resultado.stdout.split()[0] if resultado.stdout else "N/A"
            
            # Contar archivos jpg
            resultado = subprocess.run(
                ['find', watch_dir, '-name', '*.jpg', '-type', 'f'],
                capture_output=True, text=True, timeout=30
            )
            imgs_dir = len([f for f in resultado.stdout.strip().split('\n') if f])
            
            espacio_total += imgs_dir  # Aproximación
            num_imagenes += imgs_dir
            dir_name = os.path.basename(watch_dir)
            detalles_dirs.append(f"  • {dir_name}: {espacio_dir} ({imgs_dir:,} imgs)")
        
        # Barra de progreso
        barra_len = 10
        lleno = int(pct_usado / 100 * barra_len)
        barra = "█" * lleno + "░" * (barra_len - lleno)
        
        dirs_text = '\n'.join(detalles_dirs)
        
        return f"""💾 <b>Espacio en Disco</b>

📁 <b>Directorios monitoreados:</b>
{dirs_text}

🖼️ <b>Total imágenes JPG:</b> {num_imagenes:,}

💿 <b>Disco:</b>
[{barra}] {pct_usado:.1f}%
  • Total: {total_gb:.1f} GB
  • Usado: {usado_gb:.1f} GB
  • Libre: <b>{libre_gb:.1f} GB</b>

💡 Usa /limpiar [DIAS] para liberar espacio"""
    
    except Exception as e:
        return f"❌ Error obteniendo espacio: {e}"

def cmd_camaras(chat_id: str) -> str:
    """Muestra estadísticas de capturas del DVR por cámara."""
    import json
    from datetime import date
    
    DVR_STATS_FILE = "/home/capw/projects/camdetector/data/stats.json"
    DVR_IP_FILE = "/home/capw/projects/camdetector/data/dvr_ip.txt"
    DVR_CAPTURAS_DIR = "/home/capw/projects/camdetector/data/dvr_capturas"
    dvr_channels = tuple(range(1, 9))

    def stats_template():
        base = {f"A{canal}": 0 for canal in dvr_channels}
        base["total"] = 0
        return base
    
    try:
        # Leer estadísticas
        try:
            with open(DVR_STATS_FILE, 'r') as f:
                stats = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            stats = {"dias": {}}
        
        hoy = date.today().isoformat()
        stats_hoy = stats.get("dias", {}).get(hoy, stats_template())
        
        # Leer IP actual del DVR
        try:
            with open(DVR_IP_FILE, 'r') as f:
                dvr_ip = f.read().strip()
        except FileNotFoundError:
            dvr_ip = "No configurada"
        
        # Contar imágenes actuales en el directorio
        import os
        try:
            imagenes = [f for f in os.listdir(DVR_CAPTURAS_DIR) if f.endswith('.jpg')]
            total_imgs = len(imagenes)
            # Contar por cámara
            imgs_por_cam = {f"A{canal}": 0 for canal in dvr_channels}
            for img in imagenes:
                for cam in imgs_por_cam:
                    if f"_{cam}_" in img:
                        imgs_por_cam[cam] += 1
                        break
        except:
            total_imgs = 0
            imgs_por_cam = {f"A{canal}": 0 for canal in dvr_channels}
        
        # Estadísticas de ayer
        from datetime import timedelta
        ayer = (date.today() - timedelta(days=1)).isoformat()
        stats_ayer = stats.get("dias", {}).get(ayer, {"total": 0})
        
        return f"""📷 <b>Estadísticas DVR</b>

<b>Capturas Hoy ({hoy}):</b>
  • A1: <b>{stats_hoy.get('A1', 0)}</b>
  • A2: <b>{stats_hoy.get('A2', 0)}</b>
  • A3: <b>{stats_hoy.get('A3', 0)}</b>
  • A4: <b>{stats_hoy.get('A4', 0)}</b>
  • A5: <b>{stats_hoy.get('A5', 0)}</b>
  • A6: <b>{stats_hoy.get('A6', 0)}</b>
  • A7: <b>{stats_hoy.get('A7', 0)}</b>
  • A8: <b>{stats_hoy.get('A8', 0)}</b>
  • Total: <b>{stats_hoy.get('total', 0)}</b>

📅 Capturas ayer: {stats_ayer.get('total', 0)}

💾 <b>Imágenes en disco:</b> {total_imgs}
  • A1: {imgs_por_cam['A1']} | A2: {imgs_por_cam['A2']}
  • A3: {imgs_por_cam['A3']} | A4: {imgs_por_cam['A4']}
  • A5: {imgs_por_cam['A5']} | A6: {imgs_por_cam['A6']}
  • A7: {imgs_por_cam['A7']} | A8: {imgs_por_cam['A8']}

🌐 <b>IP DVR:</b> <code>{dvr_ip}</code>"""
    
    except Exception as e:
        return f"❌ Error obteniendo stats DVR: {e}"

def cmd_test(chat_id: str) -> str:
    """Envía una notificación de prueba a todos los destinatarios."""
    if chat_id != TELEGRAM_ADMIN_ID:
        return "⛔ Este comando solo está disponible para el administrador."
    
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    mensaje_test = f"""🔔 <b>Notificación de Prueba</b>

📅 {timestamp}
✅ El sistema está funcionando correctamente.

Este mensaje fue enviado usando /test"""
    
    # Enviar a todos en background
    def enviar():
        exitos = 0
        for cid in TELEGRAM_CHAT_IDS:
            if cid != chat_id:  # No enviar al admin que ejecutó el comando
                if enviar_mensaje(cid, mensaje_test):
                    exitos += 1
        return exitos
    
    import threading
    t = threading.Thread(target=enviar, daemon=True)
    t.start()
    
    otros = len(TELEGRAM_CHAT_IDS) - 1 if chat_id in TELEGRAM_CHAT_IDS else len(TELEGRAM_CHAT_IDS)
    
    return f"""✅ <b>Test Enviado</b>

📤 Enviando notificación de prueba a {otros} destinatario(s)...

El mensaje se envía en background."""

def cmd_logs(chat_id: str, servicio: str = "watcher", lineas: str = "15") -> str:
    """Muestra las últimas líneas de logs del sistema."""
    if chat_id != TELEGRAM_ADMIN_ID:
        return "⛔ Este comando solo está disponible para el administrador."
    
    import subprocess
    
    # Validar número de líneas (máximo 50)
    try:
        n_lineas = min(int(lineas), 50)
        if n_lineas < 1:
            n_lineas = 15
    except ValueError:
        n_lineas = 15
    
    # Determinar servicio
    servicio = servicio.lower()
    if servicio in ['watcher', 'cam', 'cam_watcher', 'w']:
        service_name = 'cam_watcher'
        titulo = 'Cam Watcher'
    elif servicio in ['bot', 'telegram', 'telegram_bot', 'b']:
        service_name = 'telegram_bot'
        titulo = 'Telegram Bot'
    else:
        return """⚠️ <b>Uso:</b> /logs [servicio] [lineas]

<b>Servicios:</b>
• watcher (o w, cam) - Logs del detector
• bot (o b, telegram) - Logs del bot

<b>Ejemplos:</b>
/logs watcher 20
/logs bot 10
/logs w
/logs b 30"""
    
    try:
        # Ejecutar journalctl
        result = subprocess.run(
            ['journalctl', '-u', service_name, '-n', str(n_lineas), '--no-pager', '-o', 'short-iso'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return f"❌ Error al obtener logs: {result.stderr}"
        
        logs = result.stdout.strip()
        
        if not logs:
            return f"📋 No hay logs disponibles para {titulo}"
        
        # Procesar logs para hacerlos más legibles
        lineas_log = logs.split('\n')
        logs_formateados = []
        
        for linea in lineas_log:
            # Formato journalctl: 2026-01-24T18:28:16-03:00 hostname python3[PID]: 2026-01-24 18:28:16,123 | INFO | mensaje
            if '|' in linea:
                partes = linea.split('|', 2)
                if len(partes) >= 3:
                    nivel = partes[1].strip()
                    mensaje = partes[2].strip()[:60]  # Limitar largo
                    emoji = '🔵' if nivel == 'INFO' else '🟡' if nivel == 'WARNING' else '🔴' if nivel == 'ERROR' else '⚪'
                    
                    # Extraer timestamp del log (formato: 2026-01-24 18:28:16,123)
                    timestamp = ""
                    try:
                        # Buscar patrón HH:MM:SS en la parte antes del primer |
                        import re
                        match = re.search(r'(\d{2}:\d{2}:\d{2})', partes[0])
                        if match:
                            timestamp = match.group(1)
                    except:
                        pass
                    
                    if timestamp:
                        logs_formateados.append(f"{emoji} [{timestamp}] {mensaje}")
                    else:
                        logs_formateados.append(f"{emoji} {mensaje}")
                else:
                    logs_formateados.append(linea[-80:])
            else:
                logs_formateados.append(linea[-80:])
        
        # Limitar total de caracteres (Telegram max 4096)
        logs_texto = '\n'.join(logs_formateados[-n_lineas:])
        if len(logs_texto) > 3500:
            logs_texto = logs_texto[-3500:]
        
        return f"""📋 <b>Logs: {titulo}</b> (últimas {n_lineas} líneas)

<code>{logs_texto}</code>"""
        
    except subprocess.TimeoutExpired:
        return "❌ Timeout al obtener logs"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def cmd_help(chat_id: str) -> str:
    """Muestra ayuda."""
    es_admin = chat_id == TELEGRAM_ADMIN_ID
    
    comandos_base = f"""🤖 <b>Sistema de Detección de Personas v{TELEGRAM_BOT_VERSION}</b>

<b>Comandos de estado:</b>
/estado - Estado actual del sistema
/ultimo - Última detección de persona
/stats - Estadísticas del día
/historial [dia|semana|mes] - Historial con tokens
/espacio - Espacio en disco
/camaras - Estadísticas DVR por cámara

<b>Comandos de control:</b>
/activar - Fuerza modo detección ON
/desactivar - Fuerza modo detección OFF
/auto - Modo automático (horario)

/help - Esta ayuda"""

    if es_admin:
        comandos_base += """

<b>Comandos de administrador:</b>
/destinatarios - Ver chat IDs configurados
/agregar [ID] - Agregar nuevo destinatario
/limpiar [DIAS] - Eliminar imágenes antiguas
  • /limpiar 0 = elimina TODAS
  • /limpiar 7 = más de 7 días (default)
/test - Enviar notificación de prueba
/logs [watcher|bot] [N] - Ver últimas N líneas de logs"""
    
    return comandos_base

# ============================================
# PROCESAMIENTO DE COMANDOS
# ============================================

COMMANDS = {
    '/activar': cmd_activar,
    '/desactivar': cmd_desactivar,
    '/auto': cmd_auto,
    '/estado': cmd_estado,
    '/status': cmd_estado,
    '/ultimo': cmd_ultimo,
    '/stats': cmd_stats,
    '/historial': cmd_historial,
    '/destinatarios': cmd_destinatarios,
    '/agregar': cmd_agregar,
    '/limpiar': cmd_limpiar,
    '/espacio': cmd_espacio,
    '/camaras': cmd_camaras,
    '/test': cmd_test,
    '/logs': cmd_logs,
    '/help': cmd_help,
    '/start': cmd_help,
}

def procesar_mensaje(update: Dict[str, Any]):
    """Procesa un mensaje recibido."""
    message = update.get('message', {})
    chat_id = str(message.get('chat', {}).get('id', ''))
    text = message.get('text', '').strip()
    user = message.get('from', {})
    username = user.get('username', user.get('first_name', 'desconocido'))
    
    if not chat_id or not text:
        return
    
    # Verificar que es un chat autorizado (admin o destinatario)
    if chat_id != TELEGRAM_ADMIN_ID and chat_id not in TELEGRAM_CHAT_IDS:
        logger.warning(f"Mensaje de chat no autorizado: {chat_id} ({username})")
        return
    
    # Solo el admin puede ejecutar comandos de control
    comando_control = text.split()[0].split('@')[0].lower() in ['/activar', '/desactivar', '/auto']
    if comando_control and chat_id != TELEGRAM_ADMIN_ID:
        enviar_mensaje(chat_id, "⛔ Solo el administrador puede ejecutar este comando.")
        return
    
    # Extraer comando y argumentos (puede tener @botname al final)
    parts = text.split()
    command = parts[0].split('@')[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    
    logger.info(f"Comando recibido: {command} de {username} ({chat_id})")
    
    # Buscar handler
    handler = COMMANDS.get(command)
    if handler:
        # Manejar comandos con argumentos
        if command == '/historial':
            periodo = args[0].lower() if args else 'dia'
            if periodo not in ['dia', 'semana', 'mes']:
                periodo = 'dia'
            respuesta = handler(chat_id, periodo)
        elif command == '/agregar':
            nuevo_id = args[0] if args else ""
            respuesta = handler(chat_id, nuevo_id)
        elif command == '/limpiar':
            dias = args[0] if args else "7"
            logger.info(f"Comando /limpiar con argumento: '{dias}'")
            respuesta = handler(chat_id, dias)
        elif command == '/logs':
            servicio = args[0] if len(args) > 0 else "watcher"
            lineas = args[1] if len(args) > 1 else "15"
            respuesta = handler(chat_id, servicio, lineas)
        else:
            respuesta = handler(chat_id)
        enviar_mensaje(chat_id, respuesta)
    else:
        # Comando no reconocido
        enviar_mensaje(chat_id, "❓ Comando no reconocido. Usa /help para ver comandos disponibles.")

# ============================================
# FLASK APP (WEBHOOK MODE)
# ============================================

app = Flask(__name__)

# Configuración del servidor
WEBHOOK_HOST = '127.0.0.1'
WEBHOOK_PORT = 8085

@app.route('/', methods=['POST'])
def webhook():
    """Endpoint que recibe updates de Telegram via webhook."""
    try:
        update = request.get_json()
        if update:
            # Procesar en el mismo thread para responder rápido
            procesar_mensaje(update)
        return jsonify({'ok': True}), 200
    except Exception as e:
        logger.error(f"Error procesando webhook: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'bot': 'CamDetector Telegram Bot',
        'version': TELEGRAM_BOT_VERSION,
        'mode': 'webhook'
    }), 200

def setup_bot_commands():
    """Configura los comandos del menú del bot en Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No se pueden configurar comandos: TELEGRAM_BOT_TOKEN no configurado")
        return
    
    commands = [
        {"command": "activar", "description": "Activa el modo detección forzado 24/7"},
        {"command": "desactivar", "description": "Desactiva el modo detección"},
        {"command": "auto", "description": "Vuelve al modo automático (horario 00:01-07:45)"},
        {"command": "estado", "description": "Muestra el estado actual del sistema"},
        {"command": "stats", "description": "Muestra estadísticas del día"},
        {"command": "logs", "description": "Muestra logs (uso: /logs watcher 15)"},
        {"command": "help", "description": "Muestra ayuda con todos los comandos disponibles"}
    ]
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    payload = {"commands": commands}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.json().get('ok'):
            logger.info(f"✅ Comandos del bot configurados: {len(commands)} comandos")
        else:
            logger.warning(f"⚠️ No se pudieron configurar comandos: {response.json().get('description')}")
    except Exception as e:
        logger.warning(f"⚠️ Error configurando comandos: {e}")

def main():
    """Punto de entrada principal."""
    logger.info("=" * 60)
    logger.info(f"Telegram Bot v{TELEGRAM_BOT_VERSION} - Modo Webhook - Iniciando")
    logger.info("=" * 60)
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no configurado")
        sys.exit(1)
    
    if not TELEGRAM_ADMIN_ID:
        logger.error("TELEGRAM_ADMIN_ID no configurado")
        sys.exit(1)
    
    logger.info(f"Admin ID: {TELEGRAM_ADMIN_ID}")
    logger.info(f"Destinatarios: {len(TELEGRAM_CHAT_IDS)}")
    logger.info(f"Escuchando en {WEBHOOK_HOST}:{WEBHOOK_PORT}")
    logger.info("Webhook URL: https://server1.dev0p.com/webhook/camdetector")
    
    # Configurar comandos del menú
    setup_bot_commands()
    
    # Ejecutar Flask
    app.run(
        host=WEBHOOK_HOST,
        port=WEBHOOK_PORT,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    main()
