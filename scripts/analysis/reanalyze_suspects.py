#!/usr/bin/env python3
"""
Reanaliza imágenes sospechosas con múltiples modelos al 50% de resolución.
Guardado incremental y timeout agresivo.
"""
import os, sys, json, base64, time, signal
import concurrent.futures
from PIL import Image
from io import BytesIO
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from cam_watcher import load_env

CONFIG = load_env()
API_KEY = CONFIG.get('OPENROUTER_API_KEY', '')

IMAGES_DIR = '/home/capw/projects/camdetector/data/dvr_sospechosas'
IMAGES = sorted([f for f in os.listdir(IMAGES_DIR) if f.endswith('.jpg')])

MODELS = [
    'openrouter/healer-alpha',
    'bytedance-seed/seed-2.0-mini',
    'qwen/qwen3.5-9b',
    'qwen/qwen3.5-35b-a3b',
    'qwen/qwen3.5-flash-02-23',
    'mistralai/mistral-small-2603',
    'google/gemini-3.1-flash-lite-preview',
]

PROMPT = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar ÚNICAMENTE personas a pie.

IMPORTANTE: Responde EXACTAMENTE en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100] (porcentaje de actividad sospechosa)
DESCRIPCION: [descripción breve solo si hay PERSONA, sino dejar vacío]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie DENTRO del predio o patio.
- 0-49: Persona a pie pasando por la vereda o calle exterior.

Reglas estrictas:
- Responde PERSONA: SI **solo** si ves personas a pie presentes.
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0.
- No busques objetos pequeños.
"""

OUTPUT_PATH = '/home/capw/projects/camdetector/data/suspect_reanalyze_results.json'

def resize_image_50(img):
    w, h = img.size
    resized = img.resize((int(w*0.5), int(h*0.5)), Image.LANCZOS)
    buf = BytesIO()
    resized.save(buf, format='JPEG', quality=85)
    return buf.getvalue()

def call_api(model, image_bytes):
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://camdetector.local",
        "X-Title": "CamDetector-Benchmark"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
        ]}]
    }
    
    t0 = time.time()
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                            headers=headers, json=payload, timeout=90)
        elapsed = time.time() - t0
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:100]}", "time": round(elapsed,2)}
        
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        
        persona, sospechosa, descripcion = None, 0, ""
        for line in text.split('\n'):
            line = line.strip()
            if line.upper().startswith('PERSONA:'):
                persona = line.split(':', 1)[1].strip().upper() == 'SI'
            elif line.upper().startswith('SOSPECHOSA:'):
                try: sospechosa = int(line.split(':', 1)[1].strip().replace('%', ''))
                except: pass
            elif line.upper().startswith('DESCRIPCION:'):
                descripcion = line.split(':', 1)[1].strip()
        
        return {
            "persona": persona, "sospechosa": sospechosa, "descripcion": descripcion,
            "raw": text.strip(), "time": round(elapsed, 2),
            "tokens_prompt": usage.get("prompt_tokens", 0),
            "tokens_completion": usage.get("completion_tokens", 0),
            "tokens_total": usage.get("total_tokens", 0),
        }
    except Exception as e:
        return {"error": str(e)[:100], "time": round(time.time()-t0, 2)}

def analyze_one(args):
    img_path, model = args
    filename = os.path.basename(img_path)
    try:
        img = Image.open(img_path).convert('RGB')
        image_bytes = resize_image_50(img)
        result = call_api(model, image_bytes)
        result['model'] = model
        result['image'] = filename
        return result
    except Exception as e:
        return {"model": model, "image": filename, "error": str(e), "time": 0,
                "tokens_prompt": 0, "tokens_completion": 0, "tokens_total": 0}

def main():
    if not API_KEY:
        print("ERROR: No API KEY"); sys.exit(1)
    
    # Load existing results for resume
    done_set = set()
    results = []
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            results = json.load(f)
        for r in results:
            done_set.add((r['image'], r['model']))
        print(f"Reanudando: {len(results)} ya completados")
    
    tasks = []
    for img_name in IMAGES:
        img_path = os.path.join(IMAGES_DIR, img_name)
        for model in MODELS:
            if (img_name, model) not in done_set:
                tasks.append((img_path, model))
    
    total = len(IMAGES) * len(MODELS)
    remaining = len(tasks)
    print(f"Imágenes: {len(IMAGES)}, Modelos: {len(MODELS)}")
    print(f"Total: {total}, Pendientes: {remaining}")
    print("=" * 140)
    
    done = len(results)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        futures = {executor.submit(analyze_one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1
            
            # Save incrementally every result
            with open(OUTPUT_PATH, 'w') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            persona_str = "SI" if result.get('persona') else "NO" if result.get('persona') is not None else "ERR"
            s = result.get('sospechosa', '?')
            t = result.get('time', '?')
            tok = result.get('tokens_total', 0)
            m = result['model'].split('/')[-1][:16]
            img = result['image'][-30:]
            err = result.get('error', '')
            
            if err:
                print(f"[{done}/{total}] {m:<17} {img} → ERR: {err[:45]} ({t}s)")
            else:
                d = result.get('descripcion', '')[:30]
                print(f"[{done}/{total}] {m:<17} {img} → P={persona_str} S={s}% {t}s tok={tok} | {d}")
    
    # ============================================================
    # SUMMARY
    # ============================================================
    print("\n" + "=" * 140)
    print("RESUMEN POR MODELO")
    print("=" * 140)
    print(f"{'Modelo':<32} {'SI':>4} {'NO':>4} {'ERR':>4} {'Avg(s)':>8} {'Total tokens':>13}")
    print("-" * 140)
    
    for model in MODELS:
        ms = model.split('/')[-1]
        mr = [r for r in results if r['model'] == model and 'error' not in r]
        errs = [r for r in results if r['model'] == model and 'error' in r]
        si = sum(1 for r in mr if r.get('persona'))
        no = sum(1 for r in mr if not r.get('persona'))
        times = [r['time'] for r in mr]
        avg_t = round(sum(times)/len(times), 2) if times else 0
        total_tok = sum(r.get('tokens_total', 0) for r in mr)
        print(f"  {ms:<30} {si:>4} {no:>4} {len(errs):>4} {avg_t:>7.2f}s {total_tok:>13,}")
    
    print("\n" + "=" * 140)
    print("DETALLE POR IMAGEN (50% resolución)")
    print("=" * 140)
    
    for img_name in IMAGES:
        print(f"\n📷 {img_name}")
        img_results = [r for r in results if r['image'] == img_name and 'error' not in r]
        for model in MODELS:
            ms = model.split('/')[-1][:22]
            mr = [r for r in img_results if r['model'] == model]
            if not mr:
                print(f"  {ms:<26} → ERROR")
                continue
            r = mr[0]
            p = "SI" if r.get('persona') else "NO"
            s = r.get('sospechosa', 0)
            t = r.get('time', 0)
            tok = r.get('tokens_total', 0)
            tp = r.get('tokens_prompt', 0)
            tc = r.get('tokens_completion', 0)
            desc = r.get('descripcion', '')[:35]
            print(f"  {ms:<26} → {p}/{s}% | {t:>5.1f}s | tok={tok:>5,} (p:{tp:,} c:{tc:,}) | {desc}")
    
    print(f"\nResultados en: {OUTPUT_PATH}")

if __name__ == '__main__':
    main()
