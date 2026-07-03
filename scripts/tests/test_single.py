#!/usr/bin/env python3
"""Test Gemini solo - sin parametro reasoning"""
import json, time, base64, requests
from pathlib import Path

API_KEY_PATH = Path("/home/capw/projects/camdetector/config/.env")
IMAGES_DIR = Path("/home/capw/projects/camdetector/data/dvr_sospechosas")

def load_api_key():
    with open(API_KEY_PATH) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()

SYSTEM_PROMPT = """Sos un sistema de seguridad. Analiza la imagen de camara de vigilancia.

RESPONDE SOLO CON ESTE FORMATO EXACTO:
PERSONA: [SI/NO]
SOSPECHOSA: [0-100]"""

def query_model(model, image_b64, api_key, disable_reasoning=False):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": "Analiza esta imagen."},
                ],
            }
        ],
        "max_tokens": 500,
    }
    if disable_reasoning:
        payload["reasoning"] = {"enabled": False}

    start = time.time()
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    elapsed = time.time() - start
    return resp.json(), round(elapsed, 2)

api_key = load_api_key()

tests = [
    ("alerta_A1_20260306_000405_419657.jpg", True, "CON persona"),
    ("alerta_A1_20260306_001904_379423.jpg", False, "SIN persona"),
]

models = [
    ("google/gemini-3.1-flash-lite-preview", False),
    ("google/gemini-2.5-flash-lite-preview-09-2025", False),
]

for model, disable_reasoning in models:
    print(f"\n{'='*70}")
    print(f"MODELO: {model}")
    print(f"{'='*70}")
    
    for img_name, expected_persona, desc in tests:
        img_path = IMAGES_DIR / img_name
        with open(img_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()
        
        print(f"\n  {img_name} ({desc})")
        data, elapsed = query_model(model, img_b64, api_key, disable_reasoning)
        
        if "error" in data:
            print(f"  ERROR: {data['error']}")
            continue
        
        content = data["choices"][0]["message"].get("content", "")
        usage = data.get("usage", {})
        
        print(f"  Tiempo: {elapsed}s")
        print(f"  Tokens: prompt={usage.get('prompt_tokens',0)} completion={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}")
        print(f"  Content: {content}")
        
        time.sleep(1)
