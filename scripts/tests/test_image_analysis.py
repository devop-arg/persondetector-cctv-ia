#!/usr/bin/env python3
"""
Script para analizar una imagen específica con el modelo actual de CamDetector.
Uso: python3 test_image_analysis.py [ruta_imagen]
"""

import sys
import os
import json
import base64
import requests
from pathlib import Path

# Agregar el directorio del proyecto al path
project_root = Path("/home/capw/projects/camdetector")
sys.path.insert(0, str(project_root / "src"))

# Cargar configuración
def load_env():
    """Carga variables de entorno desde .env"""
    env_path = project_root / "config" / ".env"
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

# Configuración de IA
AI_MODEL = CONFIG.get('AI_MODEL', 'qwen/qwen3.5-flash-02-23')
OPENROUTER_API_KEY = CONFIG.get('OPENROUTER_API_KEY', '')
AI_IMAGE_SCALE = int(CONFIG.get('AI_IMAGE_SCALE', '100'))

# Prompt del sistema (mismo que usa cam_watcher.py)
DETECTION_PROMPT = """Analiza esta imagen de una cámara de seguridad. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO

Reglas estrictas:
- Si hay una o más personas a pie visibles responde PERSONA: SI
- Para cualquier otra cosa que veas, responde PERSONA: NO
"""

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

def es_camara_con_sospecha(camara: str) -> bool:
    """Determina si la cámara requiere evaluación de actividad sospechosa."""
    return camara in ('DVR-A1', 'DVR-A2')

def analizar_imagen(imagen_path: str, camara: str = "DVR-A3") -> dict:
    """
    Analiza una imagen con la IA.
    
    Args:
        imagen_path: Ruta a la imagen
        camara: Identificador de la cámara (por defecto DVR-A3)
    
    Returns:
        Dict con keys: persona (bool), sospechosa (int 0-100), 
                       descripcion (str), error (str o None), 
                       tokens_prompt, tokens_completion, tokens_total
    """
    try:
        with open(imagen_path, 'rb') as f:
            image_data = f.read()
        
        if len(image_data) < 1000:
            return {
                'persona': False,
                'sospechosa': 0,
                'descripcion': 'Imagen muy pequeña o corrupta',
                'error': 'Imagen muy pequeña o corrupta',
                'tokens_prompt': 0,
                'tokens_completion': 0,
                'tokens_total': 0,
            }
        
        # Escalar imagen si es necesario
        if 1 <= AI_IMAGE_SCALE < 100:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(image_data))
            new_w = max(1, int(img.width * AI_IMAGE_SCALE / 100))
            new_h = max(1, int(img.height * AI_IMAGE_SCALE / 100))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            elif img.mode == 'L':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=100)
            image_data = buf.getvalue()
            image_type = 'image/jpeg'
        else:
            image_type = 'image/jpeg' if imagen_path.lower().endswith('.jpg') or imagen_path.lower().endswith('.jpeg') else 'image/png'
        
        # Seleccionar prompt según cámara
        usar_prompt_sospecha = es_camara_con_sospecha(camara)
        prompt = DETECTION_PROMPT_SOSPECHOSA if usar_prompt_sospecha else DETECTION_PROMPT
        
        # Codificar imagen en base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Preparar payload para OpenRouter
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://camdetector.local",
            "X-Title": "CamDetector"
        }
        
        payload = {
            "model": AI_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}}
                    ]
                }
            ],
            "max_tokens": 100
        }
        
        # Deshabilitar reasoning para modelos qwen
        if 'qwen/' in AI_MODEL.lower():
            payload["reasoning"] = {"enabled": False}
        
        # Enviar solicitud
        import time
        start_time = time.time()
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code != 200:
            raise Exception(f"OpenRouter error {response.status_code}: {response.text[:200]}")
        
        data = response.json()
        usage = data.get("usage", {})
        text = data["choices"][0]["message"]["content"]
        
        # Parsear respuesta
        result = {
            'persona': False,
            'sospechosa': 0,
            'descripcion': '',
            'error': None,
            'tokens_prompt': usage.get("prompt_tokens", 0),
            'tokens_completion': usage.get("completion_tokens", 0),
            'tokens_total': usage.get("total_tokens", 0),
            'tiempo_segundos': round(elapsed_time, 2),
            'raw_response': text,
        }
        
        for line in text.split('\n'):
            line = line.strip()
            if line.upper().startswith('PERSONA:'):
                result['persona'] = line.split(':', 1)[1].strip().upper() == 'SI'
            elif line.upper().startswith('SOSPECHOSA:'):
                try:
                    result['sospechosa'] = int(line.split(':', 1)[1].strip().replace('%', ''))
                except ValueError:
                    result['sospechosa'] = 0
        
        # Cámaras sin evaluación de sospecha → si hay persona = 100%
        if not usar_prompt_sospecha and result['persona']:
            result['sospechosa'] = 100
        
        return result
        
    except Exception as e:
        error_str = str(e)
        if '429' in error_str:
            print(f"Error 429 (quota agotada): {error_str}")
        else:
            print(f"Error analizando imagen: {e}")
        return {
            'persona': False,
            'sospechosa': 0,
            'descripcion': '',
            'error': error_str,
            'tokens_prompt': 0,
            'tokens_completion': 0,
            'tokens_total': 0,
            'tiempo_segundos': 0,
            'raw_response': '',
        }

def main():
    """Función principal."""
    if len(sys.argv) < 2:
        print("Uso: python3 test_image_analysis.py <ruta_imagen> [camara]")
        print("\nImágenes disponibles:")
        for dir_name in ['dvr_capturas', 'dvr_sospechosas', 'dvr_no_sospechosas']:
            dir_path = project_root / 'data' / dir_name
            if dir_path.exists():
                images = list(dir_path.glob('alerta_A3*.jpg'))
                if images:
                    print(f"\n  {dir_name}:")
                    for img in sorted(images):
                        print(f"    - {img}")
        sys.exit(1)
    
    imagen_path = sys.argv[1]
    camara = sys.argv[2] if len(sys.argv) > 2 else "DVR-A3"
    
    if not os.path.exists(imagen_path):
        print(f"Error: La imagen no existe: {imagen_path}")
        sys.exit(1)
    
    print(f"Analizando imagen: {imagen_path}")
    print(f"Cámara: {camara}")
    print(f"Modelo: {AI_MODEL}")
    print(f"Escala: {AI_IMAGE_SCALE}%")
    print("-" * 50)
    
    resultado = analizar_imagen(imagen_path, camara)
    
    print("\n=== RESULTADO DEL ANÁLISIS ===")
    print(f"Persona detectada: {'SI' if resultado['persona'] else 'NO'}")
    print(f"Nivel de sospecha: {resultado['sospechosa']}%")
    print(f"Tiempo de análisis: {resultado.get('tiempo_segundos', 0)} segundos")
    print(f"Tokens usados: {resultado['tokens_prompt']} prompt + {resultado['tokens_completion']} completion = {resultado['tokens_total']} total")
    
    if resultado['error']:
        print(f"Error: {resultado['error']}")
    
    print(f"\nRespuesta cruda del modelo:")
    print(resultado.get('raw_response', 'No disponible'))
    
    # Recomendación basada en el resultado
    print("\n=== RECOMENDACIÓN ===")
    if resultado['persona']:
        if resultado['sospechosa'] >= 50:
            print("ALERTA: Persona sospechosa detectada. Se enviaría notificación por Telegram.")
        else:
            print("INFO: Persona detectada pero no sospechosa. No se enviaría alerta.")
    else:
        print("INFO: No se detectaron personas. Imagen descartada.")

if __name__ == "__main__":
    main()