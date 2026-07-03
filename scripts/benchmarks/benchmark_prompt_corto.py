#!/usr/bin/env python3
"""
Benchmark de modelos con prompt CORTO (sin descripcion)
- 3 imagenes sin persona + 5 con persona (al azar)
- Resoluciones: nativa, 75%, 50%
- Modelos: qwen3.5-9b, qwen3.5-flash-02-23, gemini-3.1-flash-lite
"""

import json
import os
import sys
import time
import base64
import random
import requests
from pathlib import Path
from datetime import datetime

# Config
API_KEY_PATH = Path("/home/capw/projects/camdetector/config/.env")
IMAGES_DIR = Path("/home/capw/projects/camdetector/data/dvr_sospechosas")
OUTPUT_DIR = Path("/home/capw/projects/camdetector/data")

# Modelos a testear
MODELS = [
    "qwen/qwen3.5-9b",
    "qwen/qwen3.5-flash-02-23",
    "google/gemini-3.1-flash-lite-preview",
]

# Resoluciones (porcentaje del original)
RESOLUTIONS = [100, 75, 50]

# PROMPT CORTO - sin descripcion
SYSTEM_PROMPT = """Sos un sistema de seguridad. Analiza la imagen de camara de vigilancia.

RESPONDE SOLO CON ESTE FORMATO EXACTO, NADA MAS:
PERSONA: [SI/NO]
SOSPECHOSA: [0-100]

Si NO hay persona, responde:
PERSONA: NO
SOSPECHOSA: 0"""

# Imagenes sin persona (confirmadas por user)
SIN_PERSONA = [
    "alerta_A1_20260306_001904_379423.jpg",
    "alerta_A1_20260311_223746_708951.jpg",
    "alerta_A1_20260315_062211_089234.jpg",
]

# Imagenes con persona
CON_PERSONA = [
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
]


def load_api_key():
    """Carga API key del .env"""
    with open(API_KEY_PATH) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise ValueError("No se encontro OPENROUTER_API_KEY")


def get_image_base64(image_path, scale_pct=100):
    """Lee imagen y opcionalmente la escala usando PIL"""
    if scale_pct == 100:
        with open(image_path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")

    from PIL import Image
    import io

    img = Image.open(image_path)
    if scale_pct < 100:
        new_w = int(img.width * scale_pct / 100)
        new_h = int(img.height * scale_pct / 100)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def query_model(model, image_b64, api_key):
    """Envia imagen al modelo y retorna resultado"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {"type": "text", "text": "Analiza esta imagen de camara de vigilancia."},
                ],
            }
        ],
        "max_tokens": 100,  # Muy corto, solo SI/NO
    }

    start = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        elapsed = time.time() - start
        data = resp.json()

        if "error" in data:
            return {"error": data["error"], "time": elapsed}

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return {
            "raw": content,
            "time": round(elapsed, 2),
            "tokens_prompt": usage.get("prompt_tokens", 0),
            "tokens_completion": usage.get("completion_tokens", 0),
            "tokens_total": usage.get("total_tokens", 0),
        }
    except Exception as e:
        elapsed = time.time() - start
        return {"error": str(e), "time": round(elapsed, 2)}


def parse_response(raw):
    """Parsea la respuesta del modelo"""
    persona = None
    sospechosa = 0
    raw_upper = raw.upper()

    for line in raw_upper.split("\n"):
        line = line.strip()
        if line.startswith("PERSONA:"):
            val = line.split(":", 1)[1].strip()
            persona = "SI" in val and "NO" not in val
        elif line.startswith("SOSPECHOSA:"):
            val = line.split(":", 1)[1].strip().replace("%", "")
            try:
                sospechosa = int(val)
            except:
                sospechosa = 0

    return persona, sospechosa


def main():
    random.seed(42)  # Reproducible
    api_key = load_api_key()

    # Seleccionar 5 imagenes con persona al azar
    selected_con_persona = random.sample(CON_PERSONA, 5)
    test_images = [(img, False) for img in SIN_PERSONA] + [(img, True) for img in selected_con_persona]

    print("=" * 80)
    print("BENCHMARK PROMPT CORTO - Solo PERSONA: SI/NO + SOSPECHOSA")
    print("=" * 80)
    print(f"\nModelos: {', '.join(MODELS)}")
    print(f"Resoluciones: {RESOLUTIONS}")
    print(f"\nImagenes SIN persona (3):")
    for img in SIN_PERSONA:
        print(f"  - {img}")
    print(f"\nImagenes CON persona (5 al azar):")
    for img in selected_con_persona:
        print(f"  - {img}")
    print()

    results = []
    total_calls = len(MODELS) * len(RESOLUTIONS) * len(test_images)
    call_num = 0

    for model in MODELS:
        print(f"\n{'='*80}")
        print(f"MODELO: {model}")
        print(f"{'='*80}")

        for resolution in RESOLUTIONS:
            print(f"\n  Resolucion: {resolution}%")
            print(f"  {'-'*70}")

            for img_name, has_persona in test_images:
                call_num += 1
                img_path = IMAGES_DIR / img_name
                expected = "SI" if has_persona else "NO"

                print(f"  [{call_num}/{total_calls}] {img_name} (expected: PERSONA={expected}) ... ", end="", flush=True)

                try:
                    image_b64 = get_image_base64(img_path, resolution)
                    result = query_model(model, image_b64, api_key)

                    if "error" in result:
                        print(f"ERROR: {result['error']}")
                        results.append({
                            "model": model,
                            "image": img_name,
                            "resolution": resolution,
                            "expected_persona": has_persona,
                            "error": result["error"],
                            "time": result.get("time", 0),
                        })
                    else:
                        persona, sospechosa = parse_response(result["raw"])
                        correct = persona == has_persona
                        status = "OK" if correct else "FAIL"

                        print(f"{status} | persona={persona} sospechosa={sospechosa} | {result['time']}s | {result['tokens_total']} tokens")

                        results.append({
                            "model": model,
                            "image": img_name,
                            "resolution": resolution,
                            "expected_persona": has_persona,
                            "persona": persona,
                            "sospechosa": sospechosa,
                            "raw": result["raw"],
                            "time": result["time"],
                            "tokens_prompt": result["tokens_prompt"],
                            "tokens_completion": result["tokens_completion"],
                            "tokens_total": result["tokens_total"],
                            "correct": correct,
                        })
                except Exception as e:
                    print(f"ERROR: {e}")
                    results.append({
                        "model": model,
                        "image": img_name,
                        "resolution": resolution,
                        "expected_persona": has_persona,
                        "error": str(e),
                        "time": 0,
                    })

                # Pequeña pausa para no saturar API
                time.sleep(0.5)

    # Guardar resultados
    output_file = OUTPUT_DIR / "benchmark_prompt_corto.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n\nResultados guardados en: {output_file}")

    # Resumen
    print(f"\n{'='*80}")
    print("RESUMEN POR MODELO Y RESOLUCION")
    print(f"{'='*80}")

    for model in MODELS:
        print(f"\n--- {model} ---")
        for resolution in RESOLUTIONS:
            model_res = [r for r in results if r["model"] == model and r["resolution"] == resolution and "correct" in r]
            if not model_res:
                continue
            correct = sum(1 for r in model_res if r["correct"])
            total = len(model_res)
            times = [r["time"] for r in model_res]
            tokens = [r["tokens_total"] for r in model_res]
            avg_time = sum(times) / len(times)
            avg_tokens = sum(tokens) / len(tokens)
            print(f"  {resolution}%: {correct}/{total} correctos | {avg_time:.2f}s avg | {avg_tokens:.0f} tokens avg")


if __name__ == "__main__":
    main()
