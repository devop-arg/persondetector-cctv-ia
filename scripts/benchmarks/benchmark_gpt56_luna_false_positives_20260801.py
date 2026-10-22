#!/usr/bin/env python3
"""Prueba de falsos positivos de Cam3 para gpt-5.6-luna (OpenRouter).

Replica exactamente la metodología de benchmark_a3_false_positives_20260726.py:
mismo prompt de producción para A3/A4, mismas 11 imágenes (ground_truth NO_PERSONA),
separación de 13 s entre requests (máx. 4.62 RPM). Mide falsos positivos y latencia.
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
MODEL = "gpt-5.6-luna"
INTER_REQUEST_DELAY = 13.0
MAX_TOKENS = 100

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


def query(filename: str, api_key: str) -> dict:
    path = IMAGE_DIR / filename
    payload = {
        "model": MODEL,
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
        "X-Title": "CamDetector-A3-FP-Benchmark-gpt56",
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
                "model": MODEL,
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
            "model": MODEL,
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
            "model": MODEL,
            "image": filename,
            "expected_persona": False,
            "started_at": started_at,
            "status_code": None,
            "error": str(exc),
            "time_seconds": round(time.time() - started, 3),
        }


def summary(results: list[dict]) -> dict:
    valid = [r for r in results if "false_positive" in r]
    times = [r["time_seconds"] for r in valid]
    return {
        "model": MODEL,
        "provider": "openrouter",
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
    api_key = env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY no configurada")
    missing = [str(IMAGE_DIR / name) for name in IMAGES if not (IMAGE_DIR / name).is_file()]
    if missing:
        raise SystemExit("Imágenes faltantes:\n" + "\n".join(missing))

    print(f"Modelo: {MODEL} (provider openrouter) | Casos Cam3 sin persona: {len(IMAGES)}")
    print(f"Prompt Cam3 exacto | max_tokens {MAX_TOKENS}")
    print(f"Separación: {INTER_REQUEST_DELAY}s | máximo teórico: {60 / INTER_REQUEST_DELAY:.2f} RPM")
    results = []
    for i, filename in enumerate(IMAGES, 1):
        result = query(filename, api_key)
        results.append(result)
        if "error" in result:
            print(f"[{i}/{len(IMAGES)}] {filename} -> ERROR HTTP {result['status_code']}: {result['error']}")
        else:
            got = "SI" if result["persona"] else "NO"
            tag = "FALSO POSITIVO" if result["false_positive"] else "correcto NO"
            print(f"[{i}/{len(IMAGES)}] {filename} -> {got} ({tag}) | {result['time_seconds']:.3f}s | {result['tokens_total']} tokens")
        if i < len(IMAGES):
            time.sleep(INTER_REQUEST_DELAY)

    summar = summary(results)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUTPUT_DIR / f"benchmark_gpt56_luna_false_positives_{timestamp}.json"
    output.write_text(json.dumps({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "camera": "DVR-A3",
        "ground_truth": "NO_PERSONA_VISUAL",
        "model": MODEL,
        "provider": "openrouter",
        "prompt": PROMPT,
        "parameters": {
            "max_tokens": MAX_TOKENS,
            "inter_request_delay_seconds": INTER_REQUEST_DELAY,
            "max_theoretical_rpm": 60 / INTER_REQUEST_DELAY,
        },
        "images": IMAGES,
        "summary": summar,
        "results": results,
    }, indent=2, ensure_ascii=False))
    print("\n=== RESUMEN ===")
    print(json.dumps(summar, ensure_ascii=False))
    print(f"JSON: {output}")


if __name__ == "__main__":
    main()