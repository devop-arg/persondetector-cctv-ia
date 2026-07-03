#!/usr/bin/env python3
"""
Analiza imágenes sospechosas con modelos adicionales (gemini-2.5 y grok-4.1-fast).
Combina con resultados anteriores para ranking final.
"""
import os, sys, json, base64, time
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
    'google/gemini-2.5-flash-lite-preview-09-2025',
    'x-ai/grok-4.1-fast',
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

OUTPUT_PATH = '/home/capw/projects/camdetector/data/suspect_reanalyze_v2.json'

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
    tasks = [(os.path.join(IMAGES_DIR, img), model) for img in IMAGES for model in MODELS]
    total = len(tasks)
    results = []
    done = 0
    
    print(f"Modelos nuevos: {len(MODELS)}, Imágenes: {len(IMAGES)}, Combinaciones: {total}")
    print("=" * 140)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(analyze_one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1
            with open(OUTPUT_PATH, 'w') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            p = "SI" if result.get('persona') else "NO" if result.get('persona') is not None else "ERR"
            s = result.get('sospechosa', '?')
            t = result.get('time', '?')
            tok = result.get('tokens_total', 0)
            m = result['model'].split('/')[-1][:18]
            img = result['image'][-30:]
            err = result.get('error', '')
            if err:
                print(f"[{done}/{total}] {m:<19} {img} → ERR: {err[:45]} ({t}s)")
            else:
                d = result.get('descripcion', '')[:30]
                print(f"[{done}/{total}] {m:<19} {img} → P={p} S={s}% {t}s tok={tok} | {d}")
    
    # Load previous results and combine
    prev_path = '/home/capw/projects/camdetector/data/suspect_reanalyze_results.json'
    all_results = results.copy()
    if os.path.exists(prev_path):
        with open(prev_path) as f:
            prev = json.load(f)
        # Filter to only keep previous models (not the new ones)
        prev_filtered = [r for r in prev if r['model'] not in [
            'google/gemini-2.5-flash-lite-preview-09-2025',
            'x-ai/grok-4.1-fast'
        ]]
        all_results.extend(prev_filtered)
    
    # ============================================================
    # SUMMARY
    # ============================================================
    print("\n" + "=" * 140)
    print("RESUMEN COMPLETO - TODOS LOS MODELOS")
    print("=" * 140)
    print(f"{'#':>3} {'Modelo':<35} {'SI':>4} {'NO':>4} {'ERR':>4} {'Avg(s)':>8} {'Total tok':>11}")
    print("-" * 140)
    
    model_stats = []
    all_models_seen = set()
    for r in all_results:
        if r['model'] not in all_models_seen:
            all_models_seen.add(r['model'])
            m = r['model']
            ms = m.split('/')[-1]
            mr = [x for x in all_results if x['model'] == m and 'error' not in x]
            errs = [x for x in all_results if x['model'] == m and 'error' in x]
            si = sum(1 for x in mr if x.get('persona'))
            no = sum(1 for x in mr if not x.get('persona'))
            times = [x['time'] for x in mr]
            avg_t = round(sum(times)/len(times), 2) if times else 999
            total_tok = sum(x.get('tokens_total', 0) for x in mr)
            model_stats.append({
                'model': m, 'short': ms,
                'si': si, 'no': no, 'err': len(errs),
                'avg_time': avg_t, 'total_tokens': total_tok,
                'correct': min(si, 10) + min(no, 3)  # 10 images have persona, 3 don't
            })
    
    # Sort by correctness then speed
    model_stats.sort(key=lambda x: (-x['correct'], x['avg_time']))
    
    for i, s in enumerate(model_stats, 1):
        print(f"{i:>3} {s['short']:<35} {s['si']:>4} {s['no']:>4} {s['err']:>4} {s['avg_time']:>7.2f}s {s['total_tokens']:>11,}")
    
    # Per-image detail for new models
    print("\n" + "=" * 140)
    print("DETALLE POR IMAGEN - MODELOS NUEVOS")
    print("=" * 140)
    
    for img_name in IMAGES:
        print(f"\n📷 {img_name}")
        for model in MODELS:
            ms = model.split('/')[-1][:28]
            mr = [r for r in results if r['image'] == img_name and r['model'] == model and 'error' not in r]
            if not mr:
                print(f"  {ms:<30} → ERROR")
                continue
            r = mr[0]
            p = "SI" if r.get('persona') else "NO"
            s = r.get('sospechosa', 0)
            t = r.get('time', 0)
            tok = r.get('tokens_total', 0)
            tp = r.get('tokens_prompt', 0)
            tc = r.get('tokens_completion', 0)
            desc = r.get('descripcion', '')[:35]
            print(f"  {ms:<30} → {p}/{s}% | {t:>5.1f}s | tok={tok:>5,} (p:{tp:,} c:{tc:,}) | {desc}")

if __name__ == '__main__':
    main()
