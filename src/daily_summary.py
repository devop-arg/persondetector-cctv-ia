#!/usr/bin/env python3
"""
Daily Summary - Resumen Diario de Detecciones

Envía un resumen de las estadísticas del día actual al finalizar la franja de detección (8:00 AM).
Incluye:
  - Imágenes recibidas y analizadas
  - Skips por fondo y reemplazos de referencia
  - Personas detectadas (sospechosas y no sospechosas)
  - Análisis sin detección de persona (descripción)
  - Tokens consumidos y tiempo promedio por análisis IA

Ejecutar con cron:
  0 8 * * * /usr/bin/python3 /home/capw/projects/camdetector/src/daily_summary.py

Autor: OpenCode
Fecha: 2026-01-30
Versión: 2.6.0

Changelog v1.1.0:
- Incluye desglose de personas sospechosas vs no sospechosas
- Muestra detalle de personas no sospechosas con porcentaje
"""

import json
import sys
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

import requests

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

# Archivos
STATS_HISTORY_FILE = CONFIG.get('STATS_HISTORY_FILE', '/home/capw/projects/camdetector/config/stats_history.json')

# ============================================
# FUNCIONES
# ============================================

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


def enviar_mensaje(texto: str) -> int:
    """Envía un mensaje a todos los chat IDs configurados."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("Error: Telegram no configurado")
        return 0
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    exitos = 0
    
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            'chat_id': chat_id,
            'text': texto,
            'parse_mode': 'HTML'
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.json().get('ok', False):
                exitos += 1
            else:
                print(f"Error enviando a {chat_id}: {response.json().get('description', 'Unknown')}")
        except Exception as e:
            print(f"Error enviando mensaje a {chat_id}: {e}")
    
    return exitos


def format_tokens(n: int) -> str:
    """Formatea número de tokens de forma legible."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def generar_resumen_diario() -> str:
    """Genera el resumen del día actual (se ejecuta al finalizar la franja de detección)."""
    history = cargar_historico()
    
    # Fecha de hoy (el resumen se ejecuta al cierre de la franja horaria del mismo día)
    hoy = datetime.now().strftime('%Y-%m-%d')
    hoy_fmt = datetime.now().strftime('%d/%m/%Y')
    
    # Obtener datos del día actual
    dias_data = history.get('dias', {})
    analisis_sin_persona = history.get('analisis_sin_persona', {})
    personas_no_sospechosas = history.get('personas_no_sospechosas_detalle', {})
    
    if hoy not in dias_data:
        return None  # No hay datos de hoy
    
    datos = dias_data[hoy]
    recibidas = datos.get('imagenes_recibidas', 0)
    analizadas = datos.get('imagenes_analizadas', 0)
    personas = datos.get('personas_detectadas', 0)
    no_sospechosas = datos.get('personas_no_sospechosas', 0)
    personas_sospechosas = datos.get('personas_sospechosas_notificadas', personas - no_sospechosas)
    skips_fondo = datos.get('skips_fondo', 0)
    fondo_actualizaciones = datos.get('fondo_actualizaciones', 0)
    analisis_ms_total = datos.get('analisis_ms_total', 0)
    tokens_prompt = datos.get('tokens_prompt', 0)
    tokens_completion = datos.get('tokens_completion', 0)
    tokens_total = datos.get('tokens_total', 0)
    
    # Porcentajes
    pct_analizadas = (analizadas / recibidas * 100) if recibidas > 0 else 0
    pct_personas = (personas / analizadas * 100) if analizadas > 0 else 0
    pct_skips_fondo = (skips_fondo / recibidas * 100) if recibidas > 0 else 0
    analisis_sin = datos.get('analisis_sin_persona_count', analizadas - personas)
    promedio_ms = round(analisis_ms_total / analizadas) if analizadas > 0 else 0
    
    # Construir mensaje base
    mensaje = f"""📊 <b>Resumen Diario - {hoy_fmt}</b>

📥 Imágenes recibidas: <b>{recibidas:,}</b>
🔍 Analizadas con IA: <b>{analizadas:,}</b> ({pct_analizadas:.0f}%)
🪟 Skips por fondo: <b>{skips_fondo:,}</b> ({pct_skips_fondo:.0f}%)
🖼️ Reemplazos de fondo: <b>{fondo_actualizaciones:,}</b>
👤 Personas detectadas: <b>{personas:,}</b> ({pct_personas:.0f}%)
  ├ 🚨 Sospechosas (notificadas): <b>{personas_sospechosas:,}</b>
  └ ✅ No sospechosas: <b>{no_sospechosas:,}</b>
❌ Sin persona: <b>{analisis_sin:,}</b>
⚡ Tiempo prom. análisis IA: <b>{promedio_ms} ms</b>

🎯 <b>Consumo de Tokens:</b>
  • Prompt: <b>{format_tokens(tokens_prompt)}</b>
  • Completion: <b>{format_tokens(tokens_completion)}</b>
  • Total: <b>{format_tokens(tokens_total)}</b>"""
    
    # Agregar detalle de personas no sospechosas (máximo 10)
    if hoy in personas_no_sospechosas and personas_no_sospechosas[hoy]:
        lista = personas_no_sospechosas[hoy]
        mensaje += f"\n\n👤 <b>Personas no sospechosas ({len(lista)} total):</b>"
        
        for item in lista[:10]:  # Máximo 10
            hora = item.get('hora', '??:??')
            camara = item.get('camara', '?')
            pct = item.get('sospechosa', 0)
            desc = (item.get('descripcion') or '').strip()
            if desc:
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                mensaje += f"\n  • {hora} [{camara}] {pct}%: {desc}"
            else:
                mensaje += f"\n  • {hora} [{camara}] {pct}%"
        
        if len(lista) > 10:
            mensaje += f"\n  ... y {len(lista) - 10} más"
    
    # Agregar detalle de análisis sin persona (máximo 10 para no hacer el mensaje muy largo)
    if hoy in analisis_sin_persona and analisis_sin_persona[hoy]:
        lista = analisis_sin_persona[hoy]
        mensaje += f"\n\n📝 <b>Análisis sin detección ({len(lista)} total):</b>"
        
        for item in lista[:10]:  # Máximo 10
            hora = item.get('hora', '??:??')
            desc = (item.get('descripcion') or '').strip()
            if desc:
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                mensaje += f"\n  • {hora}: {desc}"
            else:
                mensaje += f"\n  • {hora}"
        
        if len(lista) > 10:
            mensaje += f"\n  ... y {len(lista) - 10} más"
    
    mensaje += "\n\n💡 Usa /historial para ver más estadísticas."
    
    return mensaje


def main():
    """Punto de entrada principal."""
    print(f"[{datetime.now()}] Generando resumen diario...")
    
    resumen = generar_resumen_diario()
    
    if not resumen:
        print("No hay datos de hoy. Saltando resumen.")
        return 0
    
    enviados = enviar_mensaje(resumen)
    print(f"Resumen enviado a {enviados}/{len(TELEGRAM_CHAT_IDS)} destinatarios")
    
    return 0 if enviados > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
