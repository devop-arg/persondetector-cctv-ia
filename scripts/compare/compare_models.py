#!/usr/bin/env python3
"""
Script para comparar análisis de imágenes sospechosas con 4 modelos:
1. gemini-2.5-flash-lite (modelo actual de CamDetector)
2. openrouter/healer-alpha
3. qwen/qwen3.5-9b
4. bytedance-seed/seed-2.0-mini
"""

import os
import sys
import json
import base64
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error
import time

# Cargar configuración
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

# Prompt exacto de cam_watcher.py (cámaras A1/A2 con evaluación de sospecha)
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
- No busques objetos pequeños.
"""

MODELOS = [
    "google/gemini-2.5-flash-lite-preview-09-2025",
    "openrouter/healer-alpha",
    "qwen/qwen3.5-9b",
    "bytedance-seed/seed-2.0-mini",
]

BASE_PATH = "/home/capw/projects/camdetector/data/dvr_sospechosas"

# Detecciones sospechosas del 09/03/2026 con datos de los logs
DETECCIONES = [
    {
        "archivo": "alerta_A1_20260309_025355_593098.jpg",
        "hora": "02:54",
        "sospecha_log": 75,
        "descripcion_log": "Persona a pie visible a través de la reja en el área del jardín/entrada."
    },
    {
        "archivo": "alerta_A1_20260309_025834_087386.jpg",
        "hora": "02:59",
        "sospecha_log": 85,
        "descripcion_log": "Se observa una figura borrosa posiblemente de una persona a pie al fondo, cerca de la reja y bajo el techo."
    },
    {
        "archivo": "alerta_A1_20260309_030020_979218.jpg",
        "hora": "03:00",
        "sospecha_log": 75,
        "descripcion_log": "Figura borrosa a pie justo detrás de la reja, en el área de entrada/patio."
    },
    {
        "archivo": "alerta_A1_20260309_034434_795383.jpg",
        "hora": "03:44",
        "sospecha_log": 75,
        "descripcion_log": "Persona a pie justo detrás de la reja del jardín, cerca de la entrada."
    },
    {
        "archivo": "alerta_A1_20260309_040338_487210.jpg",
        "hora": "04:03",
        "sospecha_log": 85,
        "descripcion_log": "Persona a pie visible parcialmente detrás de la reja en el área del jardín/entrada."
    },
    {
        "archivo": "alerta_A1_20260309_051143_301774.jpg",
        "hora": "05:11",
        "sospecha_log": 75,
        "descripcion_log": "Persona a pie cerca del cerco y el área frontal de la vivienda."
    },
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


def parse_result(texto, raw_response=""):
    """
    Parsea el resultado del análisis. Maneja múltiples formatos:
    - Formato estándar: PERSONA: SI/NO, SOSPECHOSA: N, DESCRIPCION: ...
    - JSON: {"persona": "SI", ...}
    - Chain-of-thought con respuesta al final
    - Respuesta libre con indicación de persona
    """
    persona = "NO"
    sospecha = 0
    descripcion = ""

    # Intentar parsear JSON directamente
    texto_limpio = texto.strip()
    if texto_limpio.startswith("{"):
        try:
            j = json.loads(texto_limpio)
            p = str(j.get("persona", j.get("PERSONA", "NO"))).upper()
            if p in ["SI", "SÍ", "YES", "TRUE"]:
                persona = "SI"
            sospecha = int(j.get("sospechosa", j.get("sospecha", j.get("SOSPECHOSA", 0))))
            descripcion = str(j.get("descripcion", j.get("descripcion", j.get("DESCRIPCION", ""))))
            return persona, sospecha, descripcion
        except:
            pass

    # Parsear formato estándar líneas
    for line in texto_limpio.split("\n"):
        line = line.strip()
        if line.upper().startswith("PERSONA:"):
            valor = line.split(":", 1)[1].strip().upper()
            if valor in ["SI", "SÍ", "YES"]:
                persona = "SI"
        elif line.upper().startswith("SOSPECHOSA:"):
            try:
                val_str = line.split(":", 1)[1].strip().replace("%", "")
                sospecha = int(val_str)
            except:
                pass
        elif line.upper().startswith("DESCRIPCION:"):
            descripcion = line.split(":", 1)[1].strip()

    # Si no se detectó persona pero hay keywords en la respuesta libre
    if persona == "NO":
        texto_lower = texto_limpio.lower()
        persona_keywords = [
            "persona detectada", "hay una persona", "se observa una persona",
            "persona a pie", "presencia humana", "figure of a person",
            "human figure", "person visible", "walking person",
            "persona caminando", "persona parada", "persona sentada"
        ]
        no_persona_keywords = [
            "no person", "no se detecta persona", "no hay persona",
            "without person", "no human", "no visible person",
            "person: no", "persona: no"
        ]

        # Verificar negativas primero
        has_negation = any(kw in texto_lower for kw in no_persona_keywords)
        has_positive = any(kw in texto_lower for kw in persona_keywords)

        if has_positive and not has_negation:
            persona = "SI"
            # Extraer sospecha si hay
            import re
            sospecha_match = re.search(r'(\d+)\s*%', texto_limpio)
            if sospecha_match:
                sospecha = int(sospecha_match.group(1))
            else:
                sospecha = 50  # Default si detectó persona sin porcentaje

    return persona, sospecha, descripcion


def short_model(name):
    return name.split("/")[-1][:20]


def main():
    print("=" * 100)
    print("COMPARACIÓN DE MODELOS - Detecciones Sospechosas 09/03/2026")
    print("=" * 100)
    print()

    resultados = {}  # modelo -> [resultados por imagen]

    for modelo in MODELOS:
        print(f"\n{'─' * 80}")
        print(f"MODELO: {modelo}")
        print(f"{'─' * 80}")
        resultados[modelo] = []

        for i, det in enumerate(DETECCIONES, 1):
            ruta = os.path.join(BASE_PATH, det["archivo"])
            if not os.path.exists(ruta):
                print(f"  [{i}] {det['archivo']} → NO ENCONTRADA")
                resultados[modelo].append(None)
                continue

            print(f"  [{i}] {det['archivo']}...", end=" ", flush=True)
            raw = analizar_imagen(ruta, modelo)
            persona, sospecha, descripcion = parse_result(raw)
            clasif = "⚠ SOSPECHOSA" if (persona == "SI" and sospecha >= 50) else ("👤 PERSONA" if persona == "SI" else "○ NO")
            # Mostrar snippet crudo para debug
            raw_snippet = raw[:120].replace("\n", " ") if raw else "VACIO"
            print(f"{clasif} ({sospecha}%) | {descripcion[:60]}")
            print(f"           RAW: {raw_snippet}...")
            resultados[modelo].append({
                "persona": persona,
                "sospecha": sospecha,
                "descripcion": descripcion,
                "raw": raw,
            })
            time.sleep(2)  # rate limit generoso

    # Tabla comparativa
    print("\n")
    print("=" * 120)
    print("TABLA COMPARATIVA")
    print("=" * 120)
    print()

    header = f"{'Hora':>6}  {'LOG Gemini':^28}"
    for modelo in MODELOS:
        header += f"  |  {short_model(modelo):^20}"
    print(header)
    print("─" * 120)

    for i, det in enumerate(DETECCIONES):
        log_val = f"SI {det['sospecha_log']}%"
        row = f"{det['hora']:>6}  {log_val:^28}"
        for modelo in MODELOS:
            r = resultados[modelo][i]
            if r is None:
                cell = "NO ENCONTRADA"
            elif r["persona"] == "SI":
                cell = f"SI {r['sospecha']}%"
            else:
                cell = "NO"
            row += f"  |  {cell:^20}"
        print(row)

    print("─" * 120)

    # Resumen de concordancia
    print()
    print("=" * 120)
    print("RESUMEN DE CONCORDANCIA")
    print("=" * 120)
    print()

    for modelo in MODELOS:
        short = short_model(modelo)
        aciertos_persona = 0
        aciertos_sospecha = 0
        total = len(DETECCIONES)

        for i, det in enumerate(DETECCIONES):
            r = resultados[modelo][i]
            if r is None:
                continue
            log_persona = "SI"
            log_sospechosa = det["sospecha_log"] >= 50

            if r["persona"] == log_persona:
                aciertos_persona += 1

            r_sospechosa = r["persona"] == "SI" and r["sospecha"] >= 50
            if r_sospechosa == log_sospechosa:
                aciertos_sospecha += 1

        print(f"  {short:<20} → Persona: {aciertos_persona}/{total} | Sospechosa: {aciertos_sospecha}/{total}")


if __name__ == "__main__":
    main()
