#!/usr/bin/env python3
"""
Comparación de modelos OpenRouter para detección de personas.
Usa el mismo prompt y parámetros del sistema en producción.

Modelo actual en producción: qwen/qwen3.5-flash-02-23

IMPORTANTE: Para resultados consistentes, usar:
- max_tokens: 100
- reasoning: {"enabled": False}  (para modelos qwen)
- Mismo prompt EXACTO del sistema (sin DESCRIPCION)
"""

import os
import sys
import json
import base64
import time
import re
from pathlib import Path
from datetime import datetime

# Intentar usar requests, sino urllib
try:
    import requests
    USE_REQUESTS = True
except ImportError:
    import urllib.request
    USE_REQUESTS = False

# Cargar .env
CONFIG = {}
config_path = Path("/home/capw/projects/camdetector/config/.env")
if config_path.exists():
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                CONFIG[key.strip()] = value.strip()

OPENROUTER_API_KEY = CONFIG.get("OPENROUTER_API_KEY", "")

# Prompt exacto del sistema (cam_watcher.py) -SIN DESCRIPCION-
DETECTION_PROMPT = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie dentro del predic o patio
- 0-49: Persona a pie pasando por la vereda o calle exterior

Reglas estrictas:
- Responde PERSONA: SI solo si ves personas a pie presentes
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0
- No agregues descripción, explicación ni texto extra
"""

MODELOS = [
    "qwen/qwen3.5-flash-02-23",           # Modelo actual en producción
    "mistralai/mistral-small-2603",       # Alternativo
    "reka/reka-edge",                     # Para probar
]

BASE_DIR = Path("/home/capw/projects/camdetector/data")


def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analizar_imagen(image_path, modelo):
    """Analiza imagen con OpenRouter. Usa parámetros del sistema para consistencia."""
    try:
        b64 = encode_image(image_path)
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://camdetector.local",
            "X-Title": "CamDetector-Compare"
        }
        payload = {
            "model": modelo,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": DETECTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }],
            "max_tokens": 100
        }
        
        # Desactivar reasoning para modelos qwen (igual que producción)
        if "qwen/" in modelo.lower():
            payload["reasoning"] = {"enabled": False}

        if USE_REQUESTS:
            start = time.time()
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload, timeout=120
            )
            elapsed = time.time() - start
            if resp.status_code != 200:
                return f"ERROR: HTTP {resp.status_code}: {resp.text[:200]}", 0, elapsed
            data = resp.json()
            usage = data.get("usage", {})
            return (
                data["choices"][0]["message"]["content"],
                usage.get("total_tokens", 0),
                round(elapsed, 2)
            )
        else:
            import urllib.request
            start = time.time()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start
            usage = data.get("usage", {})
            return (
                data["choices"][0]["message"]["content"],
                usage.get("total_tokens", 0),
                round(elapsed, 2)
            )
    except Exception as e:
        return f"ERROR: {e}", 0, 0


def parse_result(texto):
    """Parsea PERSONA/SOSPECHOSA/DESCRIPCION del texto."""
    persona = False
    sospecha = 0
    descripcion = ""

    # Intentar JSON primero
    texto_limpio = texto.strip()
    if texto_limpio.startswith("{"):
        try:
            j = json.loads(texto_limpio)
            p = str(j.get("persona", j.get("PERSONA", "NO"))).upper()
            persona = p in ["SI", "SÍ", "YES", "TRUE"]
            sospecha = int(j.get("sospechosa", j.get("SOSPECHOSA", 0)))
            descripcion = str(j.get("descripcion", j.get("DESCRIPCION", "")))
            return persona, sospecha, descripcion
        except:
            pass

    for line in texto_limpio.split("\n"):
        line = line.strip()
        if line.upper().startswith("PERSONA:"):
            val = line.split(":", 1)[1].strip().upper()
            persona = val in ["SI", "SÍ", "YES"]
        elif line.upper().startswith("SOSPECHOSA:"):
            try:
                val_str = line.split(":", 1)[1].strip().replace("%", "")
                sospecha = int(val_str)
            except:
                pass
        elif line.upper().startswith("DESCRIPCION:") or line.upper().startswith("DESCRIPCIÓN:"):
            descripcion = line.split(":", 1)[1].strip()

    # Fallback: keywords en texto libre
    if not persona:
        texto_lower = texto_limpio.lower()
        pos = ["persona detectada", "hay una persona", "persona a pie", "persona caminando"]
        neg = ["no person", "no hay persona", "no se detecta persona", "sin persona"]
        has_pos = any(k in texto_lower for k in pos)
        has_neg = any(k in texto_lower for k in neg)
        if has_pos and not has_neg:
            persona = True
            m = re.search(r'(\d+)\s*%', texto_limpio)
            sospecha = int(m.group(1)) if m else 50

    return persona, sospecha, descripcion


def get_images():
    """Obtiene las imágenes con personas detectadas de hoy."""
    images = []

    # No sospechosas
    no_sosp = BASE_DIR / "dvr_no_sospechosas"
    for f in sorted(no_sosp.glob("alerta_A1_20260317_*.jpg")):
        images.append({"path": f, "label": "NO_SOSPECHOSA"})

    # Sospechosa
    sosp = BASE_DIR / "dvr_sospechosas"
    for f in sorted(sosp.glob("alerta_A1_20260317_*.jpg")):
        images.append({"path": f, "label": "SOSPECHOSA"})

    return images


def main():
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY no configurada")
        sys.exit(1)

    images = get_images()
    if not images:
        print("No se encontraron imágenes de hoy.")
        sys.exit(1)

    print("=" * 110)
    print(f"COMPARACIÓN MODELOS - CamDetector {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 110)
    print(f"Modelos: {', '.join(MODELOS)}")
    print(f"Imágenes: {len(images)} (personas detectadas hoy)")
    print()

    # Mostrar imágenes
    for i, img in enumerate(images):
        kb = img["path"].stat().st_size / 1024
        print(f"  [{i+1}] {img['path'].name} ({kb:.0f} KB) → {img['label']}")
    print()

    all_results = {}

    for mi, modelo in enumerate(MODELOS):
        short = modelo.split("/")[-1]
        print(f"\n{'─' * 110}")
        print(f"[{mi+1}/{len(MODELOS)}] {modelo}")
        print(f"{'─' * 110}")

        model_results = []
        for ii, img in enumerate(images):
            print(f"  [{ii+1}/{len(images)}] {img['path'].name}...", end=" ", flush=True)

            raw, tokens, elapsed = analizar_imagen(img["path"], modelo)

            if raw.startswith("ERROR"):
                print(f"❌ {raw[:80]}")
                model_results.append({"error": raw})
            else:
                persona, sospecha, desc = parse_result(raw)
                p_str = "SI" if persona else "NO"
                s_str = f"{sospecha}%" if persona else "-"
                flag = "⚠️" if (persona and sospecha >= 50) else ("👤" if persona else "○")
                print(f"{flag} P:{p_str} S:{s_str} | {desc[:50]} | {elapsed}s {tokens}t")
                model_results.append({
                    "persona": persona, "sospecha": sospecha,
                    "descripcion": desc, "tokens": tokens, "time": elapsed,
                    "raw": raw[:500]
                })

            if ii < len(images) - 1:
                time.sleep(1.5)

        all_results[modelo] = model_results

    # Tabla final
    print(f"\n\n{'=' * 140}")
    print("TABLA COMPARATIVA")
    print(f"{'=' * 140}")

    hdr = f"{'#':>3} {'Imagen':<42} {'Real':<15}"
    for m in MODELOS:
        hdr += f" {m.split('/')[-1][:22]:<22}"
    print(hdr)
    print("─" * 140)

    for ii, img in enumerate(images):
        row = f"{ii+1:>3} {img['path'].name[:41]:<42} {img['label']:<15}"
        for modelo in MODELOS:
            r = all_results[modelo][ii]
            if "error" in r:
                cell = "ERROR"
            else:
                p = "SI" if r["persona"] else "NO"
                s = f"{r['sospecha']}%" if r["persona"] else "-"
                cell = f"P:{p} S:{s}"
            row += f" {cell:<22}"
        print(row)

    # Resumen
    print(f"\n{'─' * 100}")
    print("RESUMEN")
    print(f"{'─' * 100}")
    for modelo in MODELOS:
        short = modelo.split("/")[-1]
        results = all_results[modelo]
        total_t = sum(r.get("tokens", 0) for r in results)
        total_s = sum(r.get("time", 0) for r in results)
        errs = sum(1 for r in results if "error" in r)
        personas = sum(1 for r in results if r.get("persona"))
        sospechosas = sum(1 for r in results if r.get("persona") and r.get("sospecha", 0) >= 50)
        print(f"  {short:<25} Personas: {personas}/{len(images)}  Sospechosas: {sospechosas}  Tokens: {total_t:>6}  Tiempo: {total_s:.1f}s  Errores: {errs}")

    # Guardar JSON
    out = BASE_DIR.parent / "logs" / f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    export = {
        "timestamp": datetime.now().isoformat(),
        "models": MODELOS,
        "images": [str(i["path"]) for i in images],
        "results": {m: [{k: v for k, v in r.items()} for r in res] for m, res in all_results.items()}
    }
    with open(out, "w") as f:
        json.dump(export, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nJSON: {out}")
    print("Listo.")


if __name__ == "__main__":
    main()
