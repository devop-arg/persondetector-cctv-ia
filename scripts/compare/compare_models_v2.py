#!/usr/bin/env python3
"""
Script para comparar análisis de imágenes sospechosas con 4 modelos.
Captura respuestas completas para debug.
"""

import os
import sys
import json
import base64
import re
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error
import time

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

DETECTION_PROMPT = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar ÚNICAMENTE personas a pie.

IMPORTANTE: Responde EXACTAMENTE en este formato, sin agregar texto adicional:

PERSONA: SI o NO
SOSPECHOSA: [0-100] (porcentaje de actividad sospechosa)
DESCRIPCION: [descripción breve solo si hay PERSONA, sino dejar vacío]

Criterios de SOSPECHOSA (solo aplica si PERSONA es SI):
- 50-100: Persona a pie DENTRO del predio o patio.
- 0-49: Persona a pie pasando por la vereda o calle exterior.

Ejemplos:

PERSONA: SI
SOSPECHOSA: 80
DESCRIPCION: Persona a pie ingresando al patio.

PERSONA: NO
SOSPECHOSA: 0
DESCRIPCION:

Reglas estrictas:
- Responde PERSONA: SI **solo** si ves personas a pie presentes.
- Si no logras distinguir claramente a una persona a pie, responde PERSONA: NO y SOSPECHOSA: 0.
- No busques objetos pequeños."""

MODELOS = [
    "google/gemini-2.5-flash-lite-preview-09-2025",
    "openrouter/healer-alpha",
    "qwen/qwen3.5-9b",
    "bytedance-seed/seed-2.0-mini",
]

BASE_PATH = "/home/capw/projects/camdetector/data/dvr_sospechosas"

DETECCIONES = [
    {"archivo": "alerta_A1_20260309_025355_593098.jpg", "hora": "02:54", "sospecha_log": 75},
    {"archivo": "alerta_A1_20260309_025834_087386.jpg", "hora": "02:59", "sospecha_log": 85},
    {"archivo": "alerta_A1_20260309_030020_979218.jpg", "hora": "03:00", "sospecha_log": 75},
    {"archivo": "alerta_A1_20260309_034434_795383.jpg", "hora": "03:44", "sospecha_log": 75},
    {"archivo": "alerta_A1_20260309_040338_487210.jpg", "hora": "04:03", "sospecha_log": 85},
    {"archivo": "alerta_A1_20260309_051143_301774.jpg", "hora": "05:11", "sospecha_log": 75},
]


def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analizar_imagen(image_path, modelo):
    try:
        base64_image = encode_image(image_path)
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "model": modelo,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DETECTION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ]
        }
        req = urllib.request.Request(
            url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ERROR: {e}"


def parse_robust(texto):
    """
    Parser robusto que busca PERSONA/SOSPECHOSA/DESCRIPCION
    incluso si hay chain-of-thought o ruido antes.
    """
    persona = "NO"
    sospecha = 0
    descripcion = ""

    # Limpiar y buscar en todo el texto
    lines = texto.strip().split("\n")

    for line in lines:
        line_clean = line.strip()
        line_upper = line_clean.upper()

        # Buscar PERSONA en cualquier línea
        if "PERSONA:" in line_upper:
            match = re.search(r'PERSONA:\s*(\S+)', line_upper)
            if match:
                val = match.group(1).strip().rstrip('.')
                if val in ["SI", "SÍ", "YES"]:
                    persona = "SI"

        # Buscar SOSPECHOSA en cualquier línea
        if "SOSPECHOSA:" in line_upper:
            match = re.search(r'SOSPECHOSA:\s*(\d+)', line_upper)
            if match:
                try:
                    sospecha = int(match.group(1))
                except:
                    pass

        # Buscar DESCRIPCION en cualquier línea
        if "DESCRIPCION:" in line_upper:
            match = re.search(r'DESCRIPCION:\s*(.*)', line_clean)
            if match:
                descripcion = match.group(1).strip()

    # Si PERSONA está en SI pero sospecha = 0, podría ser modelo puso "SI" 
    # con sospecha externo al formato
    if persona == "SI" and sospecha == 0:
        # Buscar cualquier porcentaje en el texto
        pcts = re.findall(r'(\d+)\s*%', texto)
        if pcts:
            # Tomar el más alto como sospecha
            sospecha = max(int(p) for p in pcts if int(p) <= 100)

    return persona, sospecha, descripcion


def short_model(name):
    parts = name.split("/")
    if len(parts) > 1:
        return parts[-1][:22]
    return name[:22]


def main():
    print("=" * 120)
    print("COMPARACIÓN DE MODELOS - Detecciones Sospechosas 09/03/2026")
    print("=" * 120)
    print()

    resultados = {}

    for modelo in MODELOS:
        print(f"\n{'─' * 100}")
        print(f"MODELO: {modelo}")
        print(f"{'─' * 100}")
        resultados[modelo] = []

        for i, det in enumerate(DETECCIONES, 1):
            ruta = os.path.join(BASE_PATH, det["archivo"])
            if not os.path.exists(ruta):
                print(f"  [{i}] {det['archivo']} → NO ENCONTRADA")
                resultados[modelo].append(None)
                continue

            print(f"  [{i}] {det['archivo']}...", end=" ", flush=True)
            raw = analizar_imagen(ruta, modelo)
            persona, sospecha, descripcion = parse_robust(raw)

            if persona == "SI" and sospecha >= 50:
                clasif = "⚠ SOSPECHOSA"
            elif persona == "SI":
                clasif = "👤 PERSONA"
            else:
                clasif = "○ NO"

            print(f"{clasif} ({sospecha}%)")
            # Mostrar respuesta cruda completa (primera vez por modelo para debug)
            if i <= 2:
                raw_display = raw[:500].replace("\n", "\\n")
                print(f"     RAW: {raw_display}")

            resultados[modelo].append({
                "persona": persona,
                "sospecha": sospecha,
                "descripcion": descripcion,
            })
            time.sleep(2)

    # Tabla comparativa
    print("\n")
    print("=" * 130)
    print("TABLA COMPARATIVA")
    print("=" * 130)
    print()

    header = f"{'Hora':>6}  {'LOG Gemini':^22}"
    for modelo in MODELOS:
        header += f"  │  {short_model(modelo):^22}"
    print(header)
    print("─" * 130)

    for i, det in enumerate(DETECCIONES):
        log_val = f"SI {det['sospecha_log']}%"
        row = f"{det['hora']:>6}  {log_val:^22}"
        for modelo in MODELOS:
            r = resultados[modelo][i]
            if r is None:
                cell = "N/A"
            elif r["persona"] == "SI" and r["sospecha"] >= 50:
                cell = f"SI {r['sospecha']}%"
            elif r["persona"] == "SI":
                cell = f"SI {r['sospecha']}%"
            else:
                cell = "NO"
            row += f"  │  {cell:^22}"
        print(row)

    print("─" * 130)

    # Resumen de concordancia
    print()
    print("=" * 130)
    print("RESUMEN DE CONCORDANCIA (vs LOG Gemini)")
    print("=" * 130)
    print()

    for modelo in MODELOS:
        short = short_model(modelo)
        aciertos_persona = 0
        aciertos_sospechosa = 0
        falsos_neg = 0
        total = len(DETECCIONES)

        for i, det in enumerate(DETECCIONES):
            r = resultados[modelo][i]
            if r is None:
                continue

            log_sospechosa = det["sospecha_log"] >= 50
            model_sospechosa = r["persona"] == "SI" and r["sospecha"] >= 50

            if r["persona"] == "SI":
                aciertos_persona += 1

            if model_sospechosa == log_sospechosa:
                aciertos_sospechosa += 1
            elif log_sospechosa and not model_sospechosa:
                falsos_neg += 1

        print(f"  {short:<22} → Persona: {aciertos_persona}/{total} | Sospechosa: {aciertos_sospechosa}/{total} | Falsos neg: {falsos_neg}")

    # Guardar resultados a JSON
    output_path = "/home/capw/projects/camdetector/scripts/comparacion_resultados.json"
    export = {}
    for modelo in MODELOS:
        export[modelo] = resultados[modelo]
    with open(output_path, "w") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)
    print(f"\nResultados guardados en: {output_path}")


if __name__ == "__main__":
    main()
