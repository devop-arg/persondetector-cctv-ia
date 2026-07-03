#!/usr/bin/env python3
"""
Reanaliza imágenes con múltiples modelos y resoluciones.
"""
import os
import sys
import json
import base64
import time
import concurrent.futures
from pathlib import Path
from PIL import Image
from io import BytesIO
import requests

# Config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from cam_watcher import load_env

CONFIG = load_env()
API_KEY = CONFIG.get('OPENROUTER_API_KEY', '')

IMAGES_DIR = '/home/capw/projects/camdetector/data/dvr_no_sospechosas'
IMAGES = [
    'alerta_A1_20260317_002355_960317.jpg',
    'alerta_A1_20260317_012334_078034.jpg',
    'alerta_A1_20260317_021404_508726.jpg',
    'alerta_A1_20260317_043639_032185.jpg',
    'alerta_A1_20260317_044725_347014.jpg',
    'alerta_A1_20260317_055735_265537.jpg',
]

MODELS = [
    'openrouter/healer-alpha',
    'bytedance-seed/seed-2.0-mini',
    'qwen/qwen3.5-9b',
    'qwen/qwen3.5-35b-a3b',
    'mistralai/mistral-small-2603',
    'google/gemini-3.1-flash-lite-preview',
]

RESOLUTIONS = {
    'nativa': None,  # sin redimensionar
    '75%': 0.75,
    '50%': 0.50,
}

PROMPT = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar ÚNICAMENTE personas a pie.

IMPORTANTE: Responde EXACTAMENTE en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100] (porcentaje de actividad sospechosa)
DESCRIPCION: [descripción breve solo si hay PERSONA, sino dejar vacío]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie DENTRO del predio o patio.
- 0-49: Persona a pie pasando por la vereda o calle exterior.

Ejemplos:

PERSONA: SI
SOSPECHOSA: 80
DESCRIPCION: Persona a pie ingresando al patio.

PERSONA: NO
SOSPECHOSA: 0
DESCRIPCION:

Reglas estrictas:
- Responde PERSONA: SI **solo** si ves personas a pie presentes.
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0.
- No busques objetos pequeños.
"""


def resize_image(img: Image.Image, scale: float) -> bytes:
    """Redimensiona imagen y retorna bytes JPEG."""
    if scale is None:
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=90)
        return buf.getvalue()
    
    w, h = img.size
    new_w, new_h = int(w * scale), int(h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    buf = BytesIO()
    resized.save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def call_api(model: str, image_bytes: bytes) -> dict:
    """Llama a OpenRouter y retorna resultado con tiempo."""
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://camdetector.local",
        "X-Title": "CamDetector-Benchmark"
    }
    
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }]
    }
    
    t0 = time.time()
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        elapsed = time.time() - t0
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:100]}", "time": elapsed}
        
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        
        # Parse response
        persona = None
        sospechosa = 0
        descripcion = ""
        for line in text.split('\n'):
            line = line.strip()
            if line.upper().startswith('PERSONA:'):
                persona = line.split(':', 1)[1].strip().upper() == 'SI'
            elif line.upper().startswith('SOSPECHOSA:'):
                try:
                    sospechosa = int(line.split(':', 1)[1].strip().replace('%', ''))
                except ValueError:
                    sospechosa = 0
            elif line.upper().startswith('DESCRIPCION:'):
                descripcion = line.split(':', 1)[1].strip()
        
        return {
            "persona": persona,
            "sospechosa": sospechosa,
            "descripcion": descripcion,
            "raw": text.strip(),
            "time": round(elapsed, 2)
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {"error": str(e), "time": round(elapsed, 2)}


def analyze_one(args):
    """Analiza una imagen con un modelo y resolución."""
    img_path, model, res_name, scale = args
    filename = os.path.basename(img_path)
    
    try:
        img = Image.open(img_path).convert('RGB')
        image_bytes = resize_image(img, scale)
        size_kb = len(image_bytes) / 1024
        
        result = call_api(model, image_bytes)
        result['model'] = model
        result['image'] = filename
        result['resolution'] = res_name
        result['size_kb'] = round(size_kb, 1)
        return result
    except Exception as e:
        return {
            "model": model,
            "image": filename,
            "resolution": res_name,
            "error": str(e),
            "time": 0
        }


def main():
    if not API_KEY:
        print("ERROR: No OPENROUTER_API_KEY")
        sys.exit(1)
    
    # Build all tasks
    tasks = []
    for img_name in IMAGES:
        img_path = os.path.join(IMAGES_DIR, img_name)
        if not os.path.exists(img_path):
            print(f"SKIP: {img_name} no existe")
            continue
        for model in MODELS:
            for res_name, scale in RESOLUTIONS.items():
                tasks.append((img_path, model, res_name, scale))
    
    total = len(tasks)
    print(f"Total combinaciones: {total} ({len(IMAGES)} imágenes × {len(MODELS)} modelos × {len(RESOLUTIONS)} resoluciones)")
    print(f"Concurrencia: 6 workers")
    print("=" * 120)
    
    results = []
    done = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(analyze_one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            result = future.result()
            results.append(result)
            
            # Progress
            persona_str = "SI" if result.get('persona') else "NO" if result.get('persona') is not None else "ERR"
            sospechosa = result.get('sospechosa', '?')
            tiempo = result.get('time', '?')
            error = result.get('error', '')
            
            status = f"[{done}/{total}]"
            model_short = result['model'].split('/')[-1][:15]
            img_short = result['image'][-25:]
            res = result['resolution']
            
            if error:
                print(f"{status} {model_short:<16} {res:<5} {img_short} → ERROR: {error[:50]} ({tiempo}s)")
            else:
                desc = result.get('descripcion', '')[:40]
                print(f"{status} {model_short:<16} {res:<5} {img_short} → PERSONA={persona_str} SOSPECHOSA={sospechosa}% {tiempo}s | {desc}")
    
    # Save raw results
    output_path = '/home/capw/projects/camdetector/data/reanalyze_results.json'
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResultados guardados en: {output_path}")
    
    # Summary table
    print("\n" + "=" * 120)
    print("RESUMEN POR MODELO (todas las imágenes)")
    print("=" * 120)
    
    for model in MODELS:
        model_short = model.split('/')[-1]
        model_results = [r for r in results if r['model'] == model]
        
        for res_name in RESOLUTIONS:
            res_results = [r for r in model_results if r['resolution'] == res_name and 'error' not in r]
            errors = [r for r in model_results if r['resolution'] == res_name and 'error' in r]
            
            if not res_results and not errors:
                continue
            
            personas_si = sum(1 for r in res_results if r.get('persona'))
            personas_no = sum(1 for r in res_results if not r.get('persona'))
            tiempos = [r['time'] for r in res_results]
            avg_time = round(sum(tiempos) / len(tiempos), 2) if tiempos else 0
            
            line = f"  {model_short:<30} {res_name:<6} → PERSONA: SI={personas_si} NO={personas_no} ERR={len(errors)}  tiempo_avg={avg_time}s"
            print(line)
    
    # Per-image summary
    print("\n" + "=" * 120)
    print("RESUMEN POR IMAGEN (todos los modelos)")
    print("=" * 120)
    
    for img_name in IMAGES:
        print(f"\n📷 {img_name}")
        img_results = [r for r in results if r['image'] == img_name and 'error' not in r]
        
        for model in MODELS:
            model_short = model.split('/')[-1][:20]
            model_res = [r for r in img_results if r['model'] == model]
            
            if not model_res:
                print(f"  {model_short:<25} → ERROR")
                continue
            
            parts = []
            for r in sorted(model_res, key=lambda x: x['resolution']):
                p = "SI" if r.get('persona') else "NO"
                s = r.get('sospechosa', 0)
                t = r.get('time', 0)
                parts.append(f"{r['resolution']}={p}/{s}%/{t}s")
            
            print(f"  {model_short:<25} → {' | '.join(parts)}")


if __name__ == '__main__':
    main()
