#!/usr/bin/env python3
"""Compara Gemini 3.1 Flash Lite contra Gemini 3.5 Flash Lite en CamDetector.

La prueba usa el prompt y los parámetros reales de producción y limita cada
modelo a 4.62 RPM como máximo: hay 13 segundos entre requests del mismo
modelo. Las etiquetas fueron revisadas visualmente antes de ejecutar.
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
DATA_DIR = PROJECT_DIR / "data"
IMAGE_DIRS = {
    "no_persona": DATA_DIR / "dvr_no_sospechosas",
    "sospechosas": DATA_DIR / "dvr_sospechosas",
}

CURRENT_MODEL = "google/gemini-3.1-flash-lite-preview"
NEW_MODEL = "google/gemini-3.5-flash-lite"
INTER_REQUEST_DELAY = 13.0
MAX_TOKENS = 100
IMAGE_SCALE = 100

# Prompt exacto de producción para DVR-A1/DVR-A2.
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
- No agregues descripción, explicación ni texto extra
"""

# Cinco negativos visualmente confirmados. El último es un falso positivo
# histórico conservado en dvr_sospechosas, útil para medir falsos positivos.
CASES = [
    {
        "image": "alerta_A1_20260722_031745_150897.jpg",
        "directory": "no_persona",
        "expected_persona": False,
        "note": "escena vacía con vehículo al fondo",
    },
    {
        "image": "alerta_A1_20260725_000242_866693.jpg",
        "directory": "no_persona",
        "expected_persona": False,
        "note": "entrada vacía",
    },
    {
        "image": "alerta_A2_20260723_030027_715060.jpg",
        "directory": "no_persona",
        "expected_persona": False,
        "note": "patio y calle vacíos",
    },
    {
        "image": "alerta_A2_20260726_010713_748034.jpg",
        "directory": "no_persona",
        "expected_persona": False,
        "note": "patio vacío con vehículo",
    },
    {
        "image": "alerta_A2_20260529_065517_854767.jpg",
        "directory": "sospechosas",
        "expected_persona": False,
        "note": "falso positivo histórico; no se ve persona a pie",
    },
    {
        "image": "alerta_A1_20260517_001914_536349.jpg",
        "directory": "sospechosas",
        "expected_persona": True,
        "note": "persona visible junto a un perro",
    },
    {
        "image": "alerta_A1_20260714_071252_399714.jpg",
        "directory": "sospechosas",
        "expected_persona": True,
        "note": "persona visible en la entrada",
    },
    {
        "image": "alerta_A2_20260517_004914_129900.jpg",
        "directory": "sospechosas",
        "expected_persona": True,
        "note": "dos personas visibles",
    },
    {
        "image": "alerta_A2_20260530_012555_559182.jpg",
        "directory": "sospechosas",
        "expected_persona": True,
        "note": "persona visible en primer plano",
    },
    {
        "image": "alerta_A2_20260709_005027_014216.jpg",
        "directory": "sospechosas",
        "expected_persona": True,
        "note": "persona visible junto al portón",
    },
]


def load_env_value(name: str) -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def load_api_key() -> str:
    return load_env_value("OPENROUTER_API_KEY")


def image_path(case: dict) -> Path:
    return IMAGE_DIRS[case["directory"]] / case["image"]


def image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def parse_response(text: str) -> tuple[bool | None, int]:
    persona = None
    sospechosa = 0
    for raw_line in text.upper().splitlines():
        line = raw_line.strip()
        if line.startswith("PERSONA:"):
            value = line.split(":", 1)[1].strip()
            persona = value in {"SI", "SÍ", "YES"}
        elif line.startswith("SOSPECHOSA:"):
            value = line.split(":", 1)[1].strip().replace("%", "")
            try:
                sospechosa = int(value)
            except ValueError:
                sospechosa = 0
    return persona, sospechosa


def query_model(model: str, case: dict, api_key: str) -> dict:
    path = image_path(case)
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
                            "url": f"data:image/jpeg;base64,{image_base64(path)}"
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
        "X-Title": "CamDetector-Gemini-35-Benchmark",
    }

    started_at = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=90,
        )
        elapsed = round(time.time() - started_at, 3)
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code != 200 or "error" in data:
            error = data.get("error", response.text[:500])
            return {
                "model": model,
                "image": case["image"],
                "expected_persona": case["expected_persona"],
                "started_at": started_iso,
                "status_code": response.status_code,
                "error": error,
                "time_seconds": elapsed,
            }

        content = data["choices"][0]["message"].get("content", "")
        persona, sospechosa = parse_response(content)
        usage = data.get("usage", {})
        return {
            "model": model,
            "image": case["image"],
            "expected_persona": case["expected_persona"],
            "started_at": started_iso,
            "status_code": response.status_code,
            "persona": persona,
            "sospechosa": sospechosa,
            "correct_persona": persona == case["expected_persona"],
            "raw": content,
            "time_seconds": elapsed,
            "tokens_prompt": usage.get("prompt_tokens", 0),
            "tokens_completion": usage.get("completion_tokens", 0),
            "tokens_total": usage.get("total_tokens", 0),
            "cost": usage.get("cost"),
        }
    except Exception as exc:
        return {
            "model": model,
            "image": case["image"],
            "expected_persona": case["expected_persona"],
            "started_at": started_iso,
            "status_code": None,
            "error": str(exc),
            "time_seconds": round(time.time() - started_at, 3),
        }


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(model: str, results: list[dict]) -> dict:
    valid = [r for r in results if "correct_persona" in r]
    times = [r["time_seconds"] for r in valid]
    tokens = [r["tokens_total"] for r in valid if r.get("tokens_total")]
    false_positives = sum(
        1 for r in valid if not r["expected_persona"] and r["persona"] is True
    )
    false_negatives = sum(
        1 for r in valid if r["expected_persona"] and r["persona"] is not True
    )
    return {
        "model": model,
        "requests": len(results),
        "successful_responses": len(valid),
        "errors": len(results) - len(valid),
        "accuracy_persona": (sum(r["correct_persona"] for r in valid) / len(valid))
        if valid
        else None,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "latency_avg_seconds": statistics.mean(times) if times else None,
        "latency_median_seconds": statistics.median(times) if times else None,
        "latency_p95_seconds": percentile(times, 95),
        "tokens_avg": statistics.mean(tokens) if tokens else None,
        "tokens_total": sum(tokens) if tokens else 0,
        "cost_total": sum(r.get("cost") or 0 for r in valid),
    }


def main() -> None:
    configured_model = load_env_value("AI_MODEL")
    if configured_model != CURRENT_MODEL:
        raise SystemExit(
            f"AI_MODEL inesperado: {configured_model!r}; se esperaba {CURRENT_MODEL!r}"
        )

    api_key = load_api_key()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY no configurada")

    missing = [str(image_path(case)) for case in CASES if not image_path(case).is_file()]
    if missing:
        raise SystemExit("Imágenes faltantes:\n" + "\n".join(missing))

    models = [CURRENT_MODEL, NEW_MODEL]
    print(f"Modelos: {', '.join(models)}")
    print(f"Casos: {len(CASES)} (5 negativos visuales + 5 positivos visuales)")
    print(f"Resolución: {IMAGE_SCALE}% | max_tokens: {MAX_TOKENS}")
    print(f"Separación mínima entre requests del mismo modelo: {INTER_REQUEST_DELAY}s")
    print("Máximo teórico: 60 / 13 = 4.62 RPM por modelo")

    all_results: dict[str, list[dict]] = {}
    for model in models:
        print(f"\n=== {model} ===")
        model_results = []
        for index, case in enumerate(CASES, 1):
            result = query_model(model, case, api_key)
            model_results.append(result)
            if "error" in result:
                print(
                    f"[{index}/{len(CASES)}] {case['image']} -> ERROR "
                    f"HTTP {result['status_code']}: {result['error']}"
                )
            else:
                expected = "SI" if result["expected_persona"] else "NO"
                got = "SI" if result["persona"] else "NO"
                print(
                    f"[{index}/{len(CASES)}] {case['image']} -> "
                    f"{got} (esp {expected}) | sosp={result['sospechosa']}% | "
                    f"{result['time_seconds']:.3f}s | "
                    f"{result['tokens_total']} tokens"
                )
            if index < len(CASES):
                print(f"  esperando {INTER_REQUEST_DELAY:.1f}s para respetar RPM...")
                time.sleep(INTER_REQUEST_DELAY)
        all_results[model] = model_results

    summaries = [summarize(model, all_results[model]) for model in models]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = DATA_DIR / f"benchmark_gemini_35_vs_31_{timestamp}.json"
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "configured_model": configured_model,
        "new_model": NEW_MODEL,
        "prompt": PROMPT,
        "parameters": {
            "image_scale": IMAGE_SCALE,
            "max_tokens": MAX_TOKENS,
            "inter_request_delay_seconds": INTER_REQUEST_DELAY,
            "max_theoretical_rpm_per_model": 60 / INTER_REQUEST_DELAY,
        },
        "cases": CASES,
        "summaries": summaries,
        "results": all_results,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print("\n=== RESUMEN ===")
    for summary in summaries:
        print(json.dumps(summary, ensure_ascii=False))
    print(f"JSON: {output}")


if __name__ == "__main__":
    main()
