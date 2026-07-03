#!/usr/bin/env python3
"""Benchmark: 1 sin persona + 3 con persona, 3 resoluciones, 3 modelos"""
import json, time, base64, random, requests
from pathlib import Path

API_KEY_PATH = Path("/home/capw/projects/camdetector/config/.env")
IMAGES_DIR = Path("/home/capw/projects/camdetector/data/dvr_sospechosas")
OUTPUT_FILE = Path("/home/capw/projects/camdetector/data/benchmark_p4.json")

random.seed(42)

MODELS = [
    ("qwen/qwen3.5-9b", True),  # (modelo, needs_disable_reasoning)
    ("qwen/qwen3.5-flash-02-23", True),
    ("google/gemini-3.1-flash-lite-preview", False),
]

RESOLUTIONS = [100, 75, 50]

SIN_PERSONA = ["alerta_A1_20260315_062211_089234.jpg"]
CON_PERSONA_ALL = [
    "alerta_A1_20260306_000405_419657.jpg",
    "alerta_A1_20260306_072548_925857.jpg",
    "alerta_A1_20260307_012537_278113.jpg",
    "alerta_A1_20260307_102140_473164.jpg",
    "alerta_A1_20260313_063747_453489.jpg",
    "alerta_A1_20260313_131822_293904.jpg",
    "alerta_A1_20260313_131849_344431.jpg",
    "alerta_A1_20260315_021220_451403.jpg",
    "alerta_A1_20260316_072023_264093.jpg",
    "alerta_A1_20260317_072453_810107.jpg",
    "alerta_A1_20260306_001904_379423.jpg",  # tiene persona en moto
]

SYSTEM_PROMPT = """Sos un sistema de seguridad. Analiza la imagen de camara de vigilancia.

RESPONDE SOLO CON ESTE FORMATO EXACTO:
PERSONA: [SI/NO]
SOSPECHOSA: [0-100]"""


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
    img = img.resize((w, h), 3)  # LANCZOS
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def query(model, img_b64, api_key, disable_reasoning):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "Analiza esta imagen."},
            ]},
        ],
        "max_tokens": 500,
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
    sel_con = random.sample(CON_PERSONA_ALL, 3)
    images = [(img, False) for img in SIN_PERSONA] + [(img, True) for img in sel_con]

    print(f"Imágenes: 1 sin persona + 3 con persona al azar")
    print(f"Sin persona: {SIN_PERSONA[0]}")
    print(f"Con persona: {', '.join(sel_con)}")
    print(f"Modelos: {', '.join(m[0] for m in MODELS)}")
    print(f"Resoluciones: {RESOLUTIONS}")
    print(f"Total llamadas: {len(MODELS) * len(RESOLUTIONS) * len(images)}\n")

    results = []
    n = 0
    total = len(MODELS) * len(RESOLUTIONS) * len(images)

    for model, disable_reasoning in MODELS:
        for res in RESOLUTIONS:
            for img_name, has_persona in images:
                n += 1
                expected = "SI" if has_persona else "NO"
                img_path = IMAGES_DIR / img_name
                tag = f"[{n}/{total}] {model.split('/')[-1]} {res}% {img_name[-30:]}"

                try:
                    img_b64 = get_image_base64(img_path, res)
                    data, elapsed = query(model, img_b64, api_key, disable_reasoning)

                    if "error" in data:
                        print(f"{tag} ERROR: {data['error']}")
                        results.append({"model": model, "image": img_name, "resolution": res, "expected": expected, "error": str(data["error"]), "time": elapsed})
                        continue

                    content = data["choices"][0]["message"].get("content", "")
                    usage = data.get("usage", {})
                    persona, sospechosa = parse(content)
                    correct = persona == has_persona
                    status = "OK" if correct else "FAIL"

                    print(f"{tag} {status} persona={persona} sosp={sospechosa} {elapsed}s {usage.get('total_tokens',0)}t")

                    results.append({
                        "model": model, "image": img_name, "resolution": res,
                        "expected": expected, "persona": persona, "sospechosa": sospechosa,
                        "raw": content, "time": elapsed,
                        "tokens_prompt": usage.get("prompt_tokens", 0),
                        "tokens_completion": usage.get("completion_tokens", 0),
                        "tokens_total": usage.get("total_tokens", 0),
                        "correct": correct,
                    })
                except Exception as e:
                    print(f"{tag} ERROR: {e}")
                    results.append({"model": model, "image": img_name, "resolution": res, "expected": expected, "error": str(e), "time": 0})
                time.sleep(0.5)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Resumen
    print(f"\n{'='*80}")
    print("RESUMEN")
    print(f"{'='*80}")
    for model, _ in MODELS:
        mname = model.split("/")[-1]
        print(f"\n--- {mname} ---")
        for res in RESOLUTIONS:
            rr = [r for r in results if r["model"] == model and r["resolution"] == res and "correct" in r]
            if not rr: continue
            ok = sum(1 for r in rr if r["correct"])
            t = [r["time"] for r in rr]
            tk = [r["tokens_total"] for r in rr]
            print(f"  {res}%: {ok}/{len(rr)} ok | {sum(t)/len(t):.2f}s avg | {sum(tk)/len(tk):.0f} tok avg")


if __name__ == "__main__":
    main()
