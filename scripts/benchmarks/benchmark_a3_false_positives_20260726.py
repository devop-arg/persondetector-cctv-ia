#!/usr/bin/env python3
"""Prueba de falsos positivos de Cam3 del 26/07/2026.

Compara el modelo activo con Gemini 3.5 usando el prompt exacto de producción
para A3/A4. Las 11 imágenes fueron revisadas visualmente: no se distingue
ninguna persona a pie. Se mantiene una separación de 13 s entre requests del
mismo modelo: máximo teórico 4.62 RPM.
"""

import base64
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_DIR / "config" / ".env"
IMAGE_DIR = PROJECT_DIR / "data" / "dvr_sospechosas"
OUTPUT_DIR = PROJECT_DIR / "data"
CURRENT_MODEL = "google/gemini-3.1-flash-lite-preview"
NEW_MODEL = "google/gemini-3.5-flash-lite"
INTER_REQUEST_DELAY = 13.0
MAX_TOKENS = 100
IMAGE_SCALE = 100

# Prompt exacto de DETECTION_PROMPT de cam_watcher.py para Cam3/Cam4.
PROMPT = """Analiza esta imagen de una cámara de seguridad. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO

Reglas estrictas:
- Si hay una o más personas a pie visibles responde PERSONA: SI
- Para cualquier otra cosa que veas, responde PERSONA: NO
"""

# Todas revisadas visualmente como SIN PERSONA. Son los frames que el modelo
# actual marcó repetidamente como positivos durante la madrugada.
IMAGES = [
    "alerta_A3_20260726_000211_226568.jpg",
    "alerta_A3_20260726_000329_927793.jpg",
    "alerta_A3_20260726_000627_624252.jpg",
    "alerta_A3_20260726_000659_083872.jpg",
    "alerta_A3_20260726_000836_146953.jpg",
    "alerta_A3_20260726_000953_488929.jpg",
    "alerta_A3_20260726_001105_339924.jpg",
    "alerta_A3_20260726_001254_148241.jpg",
    "alerta_A3_20260726_001350_587350.jpg",
    "alerta_A3_20260726_001623_941169.jpg",
    "alerta_A3_20260726_001747_641970.jpg",
]


def env_value(name: str) -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def parse_persona(text: str) -> bool | None:
    for raw_line in text.upper().splitlines():
        line = raw_line.strip()
        if line.startswith("PERSONA:"):
            value = line.split(":", 1)[1].strip()
            return value in {"SI", "SÍ", "YES"}
    return None


def query(model: str, filename: str, api_key: str) -> dict:
    path = IMAGE_DIR / filename
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                        },
                    },
                ],
            }
        ],
        "max_tokens": MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://camdetector.local",
        "X-Title": "CamDetector-A3-FP-Benchmark",
    }
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=90,
        )
        elapsed = round(time.time() - started, 3)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200 or "error" in data:
            return {
                "model": model,
                "image": filename,
                "expected_persona": False,
                "started_at": started_at,
                "status_code": response.status_code,
                "error": data.get("error", response.text[:500]),
                "time_seconds": elapsed,
            }
        raw = data["choices"][0]["message"].get("content", "")
        persona = parse_persona(raw)
        usage = data.get("usage", {})
        return {
            "model": model,
            "image": filename,
            "expected_persona": False,
            "started_at": started_at,
            "status_code": response.status_code,
            "persona": persona,
            "false_positive": persona is True,
            "raw": raw,
            "time_seconds": elapsed,
            "tokens_prompt": usage.get("prompt_tokens", 0),
            "tokens_completion": usage.get("completion_tokens", 0),
            "tokens_total": usage.get("total_tokens", 0),
        }
    except Exception as exc:
        return {
            "model": model,
            "image": filename,
            "expected_persona": False,
            "started_at": started_at,
            "status_code": None,
            "error": str(exc),
            "time_seconds": round(time.time() - started, 3),
        }


def summary(model: str, results: list[dict]) -> dict:
    valid = [r for r in results if "false_positive" in r]
    times = [r["time_seconds"] for r in valid]
    return {
        "model": model,
        "requests": len(results),
        "successful_responses": len(valid),
        "errors": len(results) - len(valid),
        "false_positives": sum(r["false_positive"] for r in valid),
        "false_positive_rate": sum(r["false_positive"] for r in valid) / len(valid) if valid else None,
        "latency_avg_seconds": statistics.mean(times) if times else None,
        "latency_median_seconds": statistics.median(times) if times else None,
        "tokens_total": sum(r.get("tokens_total", 0) for r in valid),
    }


def main() -> None:
    configured = env_value("AI_MODEL")
    if configured != CURRENT_MODEL:
        raise SystemExit(f"AI_MODEL inesperado: {configured!r}")
    api_key = env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY no configurada")
    missing = [str(IMAGE_DIR / name) for name in IMAGES if not (IMAGE_DIR / name).is_file()]
    if missing:
        raise SystemExit("Imágenes faltantes:\n" + "\n".join(missing))

    models = [CURRENT_MODEL, NEW_MODEL]
    all_results = {}
    print(f"Casos Cam3 sin persona: {len(IMAGES)}")
    print(f"Prompt Cam3 exacto | resolución {IMAGE_SCALE}% | max_tokens {MAX_TOKENS}")
    print(f"Separación: {INTER_REQUEST_DELAY}s | máximo teórico: {60 / INTER_REQUEST_DELAY:.2f} RPM/modelo")
    for model in models:
        print(f"\n=== {model} ===")
        results = []
        for i, filename in enumerate(IMAGES, 1):
            result = query(model, filename, api_key)
            results.append(result)
            if "error" in result:
                print(f"[{i}/{len(IMAGES)}] {filename} -> ERROR HTTP {result['status_code']}: {result['error']}")
            else:
                got = "SI" if result["persona"] else "NO"
                tag = "FALSO POSITIVO" if result["false_positive"] else "correcto NO"
                print(f"[{i}/{len(IMAGES)}] {filename} -> {got} ({tag}) | {result['time_seconds']:.3f}s | {result['tokens_total']} tokens")
            if i < len(IMAGES):
                time.sleep(INTER_REQUEST_DELAY)
        all_results[model] = results

    summaries = [summary(model, all_results[model]) for model in models]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUTPUT_DIR / f"benchmark_a3_false_positives_20260726_{timestamp}.json"
    output.write_text(json.dumps({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "camera": "DVR-A3",
        "ground_truth": "NO_PERSONA_VISUAL",
        "configured_model": configured,
        "new_model": NEW_MODEL,
        "prompt": PROMPT,
        "parameters": {
            "image_scale": IMAGE_SCALE,
            "max_tokens": MAX_TOKENS,
            "inter_request_delay_seconds": INTER_REQUEST_DELAY,
            "max_theoretical_rpm_per_model": 60 / INTER_REQUEST_DELAY,
        },
        "images": IMAGES,
        "summaries": summaries,
        "results": all_results,
    }, indent=2, ensure_ascii=False))
    print("\n=== RESUMEN ===")
    for item in summaries:
        print(json.dumps(item, ensure_ascii=False))
    print(f"JSON: {output}")


if __name__ == "__main__":
    main()
