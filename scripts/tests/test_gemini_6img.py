#!/usr/bin/env python3
"""Test 6 imagenes A2 con gemini-3.1-flash-lite al 50%, 10s delay"""
import json, time, base64, requests
from pathlib import Path

API_KEY_PATH = Path("/home/capw/projects/camdetector/config/.env")
IMAGES_DIR = Path("/home/capw/projects/camdetector/data/dvr_sospechosas")
OUTPUT_FILE = Path("/home/capw/projects/camdetector/data/test_gemini_6img.json")

MODEL = "google/gemini-3.1-flash-lite-preview"
RES = 50
DELAY = 10

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
    ("alerta_A2_20260605_064346_286636.jpg", "100%", "05/06 06:43"),
    ("alerta_A2_20260605_065737_934202.jpg", "100%", "05/06 06:57"),
    ("alerta_A2_20260615_004037_656122.jpg", "90%",  "15/06 00:40"),
    ("alerta_A2_20260622_073216_269692.jpg", "95%",  "22/06 07:32"),
    ("alerta_A2_20260622_073822_141226.jpg", "80%",  "22/06 07:38"),
    ("alerta_A2_20260629_073028_407317.jpg", "55%",  "29/06 07:30"),
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
    total = len(IMAGES)

    print(f"Modelo: {MODEL}")
    print(f"Resolucion: {RES}%")
    print(f"Delay: {DELAY}s entre requests")
    print(f"Total: {total} imagenes\n")

    for i, (img_name, orig_sosp, fecha) in enumerate(IMAGES, 1):
        img_path = IMAGES_DIR / img_name
        tag = f"[{i}/{total}] {img_name} (original: {orig_sosp})"
        print(f"{tag}...", end=" ", flush=True)

        try:
            img_b64 = get_image_base64(img_path, RES)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": "Analiza esta imagen."},
                    ]},
                ],
                "max_tokens": 100,
            }
            t0 = time.time()
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
            elapsed = time.time() - t0
            data = resp.json()

            if "error" in data:
                print(f"ERROR: {data['error']}")
                results.append({"error": str(data["error"]), "time": round(elapsed, 2)})
            else:
                content = data["choices"][0]["message"].get("content", "")
                usage = data.get("usage", {})
                persona, sospechosa = parse(content)
                status = f"PERSONA sosp={sospechosa}%" if persona else "NO"
                print(f"{status} | {elapsed:.1f}s | {usage.get('total_tokens',0)}t | RAW: {content[:80]}")
                results.append({
                    "image": img_name, "fecha": fecha, "sospecha_original": orig_sosp,
                    "persona": persona, "sospechosa": sospechosa,
                    "raw": content, "time": round(elapsed, 2),
                    "tokens_total": usage.get("total_tokens", 0),
                })
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"error": str(e)})

        if i < total:
            print(f"  (esperando {DELAY}s...)")
            time.sleep(DELAY)

    with open(OUTPUT_FILE, "w") as f:
        json.dump({"model": MODEL, "resolution": RES, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*80}")
    print(f"RESUMEN - Gemini 3.1 Flash Lite al {RES}%")
    print(f"{'='*80}")
    print(f"\n{'Imagen':<45} {'Orig':<6} {'Gemini':<15} {'T(s)':>6}")
    print("-"*80)
    for r in results:
        if "error" in r:
            print(f"{r.get('image','?'):<45} ERROR")
        else:
            g = "PERSONA" if r["persona"] else "NO"
            if r["persona"]:
                g += f" ({r['sospechosa']}%)"
            print(f"{r['image']:<45} {r['sospecha_original']:<6} {g:<20} {r['time']:>5.1f}s")
    print(f"\nJSON: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
