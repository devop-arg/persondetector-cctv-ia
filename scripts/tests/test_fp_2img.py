#!/usr/bin/env python3
"""Test 2 falsos positivos: qwen vs gemini-3.1-flash-lite, 3 resoluciones"""
import json, time, base64, requests
from pathlib import Path

API_KEY_PATH = Path("/home/capw/projects/camdetector/config/.env")
IMAGES_DIR = Path("/home/capw/projects/camdetector/data/dvr_sospechosas")
OUTPUT_FILE = Path("/home/capw/projects/camdetector/data/test_fp_2img_result.json")

MODELS = [
    ("qwen/qwen3.5-flash-02-23", True),
    ("google/gemini-3.1-flash-lite-preview", False),
]

RESOLUTIONS = [100, 75, 50]

# Prompt actual de produccion (DETECTION_PROMPT_SOSPECHOSA de cam_watcher.py)
PROMPT = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie dentro del predio o patio
- 0-49: Persona a pie pasando por la vereda o calle exterior

Reglas estrictas:
- Responde PERSONA: SI solo si ves personas a pie presentes
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0
- No agregues descripción, explicación ni texto extra"""

IMAGES = [
    ("alerta_A3_20260702_074212_682914.jpg", "DVR-A3"),
    ("alerta_A2_20260703_000159_371942.jpg", "DVR-A2"),
]

def load_api_key():
    with open(API_KEY_PATH) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()

def get_image_base64(path, scale):
    if scale == 100:
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode()
    from PIL import Image
    import io
    img = Image.open(path)
    w, h = int(img.width * scale / 100), int(img.height * scale / 100)
    img = img.resize((w, h), 3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()

def query(model, img_b64, api_key, disable_reasoning):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "Analiza esta imagen."},
            ]},
        ],
        "max_tokens": 100,
    }
    if disable_reasoning:
        payload["reasoning"] = {"enabled": False}
    t0 = time.time()
    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
    elapsed = time.time() - t0
    return resp.json(), round(elapsed, 2)

def parse(raw):
    persona, sospechosa = None, 0
    for line in raw.upper().split("\n"):
        line = line.strip()
        if line.startswith("PERSONA:"):
            v = line.split(":", 1)[1].strip().replace("[", "").replace("]", "")
            persona = "SI" in v and "NO" not in v
        elif line.startswith("SOSPECHOSA:"):
            v = line.split(":", 1)[1].strip().replace("[", "").replace("]", "").replace("%", "")
            try: sospechosa = int(v)
            except: pass
    return persona, sospechosa

def main():
    api_key = load_api_key()
    results = []
    n = 0
    total = len(MODELS) * len(RESOLUTIONS) * len(IMAGES)

    for model, disable_reasoning in MODELS:
        for res in RESOLUTIONS:
            for img_name, cam in IMAGES:
                n += 1
                img_path = IMAGES_DIR / img_name
                tag = f"[{n}/{total}] {model.split('/')[-1]} {res}% {img_name[-40:]}"

                try:
                    img_b64 = get_image_base64(img_path, res)
                    data, elapsed = query(model, img_b64, api_key, disable_reasoning)

                    if "error" in data:
                        print(f"{tag} ERROR: {data['error']}")
                        results.append({"model": model, "image": img_name, "camara": cam, "resolution": res, "error": str(data["error"]), "time": elapsed})
                        continue

                    content = data["choices"][0]["message"].get("content", "")
                    usage = data.get("usage", {})
                    persona, sospechosa = parse(content)
                    status = "PERSONA" if persona else "NO"

                    print(f"{tag} {status} sosp={sospechosa} {elapsed}s {usage.get('total_tokens',0)}t")

                    results.append({
                        "model": model, "image": img_name, "camara": cam, "resolution": res,
                        "persona": persona, "sospechosa": sospechosa,
                        "raw": content, "time": elapsed,
                        "tokens_prompt": usage.get("prompt_tokens", 0),
                        "tokens_completion": usage.get("completion_tokens", 0),
                        "tokens_total": usage.get("total_tokens", 0),
                    })
                except Exception as e:
                    print(f"{tag} ERROR: {e}")
                    results.append({"model": model, "image": img_name, "camara": cam, "resolution": res, "error": str(e), "time": 0})
                time.sleep(0.5)

    with open(OUTPUT_FILE, "w") as f:
        json.dump({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*80}")
    print("RESULTADOS - TEST FALSOS POSITIVOS")
    print(f"{'='*80}")
    print()
    print(f"{'Modelo':<35} {'Resol':>5} {'Imagen':<25} {'Pers':>5} {'Sosp':>5} {'T(s)':>6} {'Tokens':>7}")
    print("-"*90)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<35} {r['resolution']:>5}% {r['image'][:24]:<25} ERROR")
        else:
            p = "SI" if r["persona"] else "NO"
            print(f"{r['model']:<35} {r['resolution']:>5}% {r['image'][:24]:<25} {p:>5} {r['sospechosa']:>5} {r['time']:>5.1f}s {r['tokens_total']:>7}")

    print(f"\nJSON guardado en: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
