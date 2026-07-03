#!/usr/bin/env python3
"""
Script para probar el nuevo prompt con las 39 imágenes de personas no sospechosas.
Usa el mismo prompt y modelo que cam_watcher.py
"""

import os
import sys
import json
import base64
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

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
AI_MODEL = CONFIG.get("AI_MODEL", "google/gemini-2.5-flash-lite-preview-09-2025")

# Nuevo prompt exacto
DETECTION_PROMPT_SOSPECHOSA = """Analiza esta imagen de una cámara de seguridad residencial. Tu objetivo es detectar ÚNICAMENTE personas a pie.

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

# Imágenes a probar (las 39 de hoy)
IMAGENES = [
    ("00:03:02", "alerta_A1_20260306_000256_727093.jpg", 10, "objeto pequeño (¿animal?) junto a valla"),
    ("00:03:29", "alerta_A1_20260306_000323_195052.jpg", 0, "persona camina por vereda"),
    ("00:03:31", "alerta_A1_20260306_000325_121892.jpg", 0, "persona camina por vereda junto a reja"),
    ("00:03:34", "alerta_A1_20260306_000329_216161.jpg", 0, "persona camina por vereda al lado de reja"),
    ("00:03:39", "alerta_A1_20260306_000334_411337.jpg", 0, "persona camina por vereda junto a valla"),
    ("00:03:41", "alerta_A1_20260306_000335_345515.jpg", 25, "persona de pie junto a reja (acera/calle)"),
    ("00:03:45", "alerta_A1_20260306_000340_585965.jpg", 40, "persona camina por vereda cerca de reja"),
    ("00:03:47", "alerta_A1_20260306_000341_531285.jpg", 0, "persona camina por calle/vereda"),
    ("00:03:52", "alerta_A1_20260306_000346_660278.jpg", 0, "persona camina por calle junto a perro"),
    ("00:03:54", "alerta_A1_20260306_000347_609734.jpg", 0, "persona camina por vereda fuera del predio"),
    ("03:27:05", "alerta_A1_20260306_032659_780732.jpg", 20, "persona camina por vereda a la izquierda"),
    ("03:27:09", "alerta_A1_20260306_032703_772219.jpg", 25, "persona camina en acera exterior"),
    ("03:27:12", "alerta_A1_20260306_032706_760894.jpg", 10, "persona camina por calle fuera de verja"),
    ("06:23:48", "alerta_A1_20260306_062341_936760.jpg", 10, "persona en bicicleta pasa por calle"),
    ("06:30:30", "alerta_A1_20260306_063024_405259.jpg", 10, "objeto pequeño (¿animal?) cerca de valla"),
    ("06:36:20", "alerta_A1_20260306_063613_527146.jpg", 25, "persona camina por calle frente a propiedad"),
    ("07:04:39", "alerta_A1_20260306_070433_892842.jpg", 0, "ciclista pasa por calle"),
    ("07:13:27", "alerta_A1_20260306_071321_283467.jpg", 25, "persona camina por acera junto a reja"),
    ("07:13:29", "alerta_A1_20260306_071323_311354.jpg", 25, "persona camina por vereda fuera de reja"),
    ("07:15:25", "alerta_A1_20260306_071519_783885.jpg", 10, "pequeño pájaro en el piso"),
    ("07:16:51", "alerta_A1_20260306_071645_501721.jpg", 0, "persona camina por vereda/calle"),
    ("07:16:53", "alerta_A1_20260306_071647_516609.jpg", 0, "persona camina por vereda junto a reja"),
    ("07:16:57", "alerta_A1_20260306_071651_682029.jpg", 20, "persona camina por vereda fuera de propiedad"),
    ("07:17:00", "alerta_A1_20260306_071654_770194.jpg", 10, "persona camina por calle fuera de verja"),
    ("07:17:03", "alerta_A1_20260306_071657_858929.jpg", 0, "persona camina en calle fuera de propiedad"),
    ("07:17:09", "alerta_A1_20260306_071703_933041.jpg", 10, "persona sentada/parada en acera (fondo)"),
    ("07:25:47", "alerta_A1_20260306_072539_919043.jpg", 20, "animal pequeño bebiendo agua"),
    ("07:28:41", "alerta_A1_20260306_072836_104220.jpg", 15, "persona en calle al otro lado de verja"),
    ("07:30:30", "alerta_A1_20260306_073024_747934.jpg", 0, "persona en bicicleta"),
    ("07:30:35", "alerta_A1_20260306_073029_905271.jpg", 10, "pájaro volando cerca de pared"),
    ("07:31:03", "alerta_A1_20260306_073057_697940.jpg", 25, "persona en calle/caminando fuera de propiedad"),
    ("07:31:15", "alerta_A1_20260306_073110_310172.jpg", 20, "persona en moto pasa por calle"),
    ("07:34:44", "alerta_A1_20260306_073438_678590.jpg", 20, "dos personas en bicicleta"),
    ("07:36:34", "alerta_A1_20260306_073629_040939.jpg", 20, "persona en moto pasa por calle"),
    ("07:40:46", "alerta_A1_20260306_074040_703659.jpg", 30, "ciclista pasa por calle"),
    ("07:44:01", "alerta_A1_20260306_074355_023675.jpg", 15, "persona en moto transita por calle"),
    ("07:44:07", "alerta_A1_20260306_074401_096618.jpg", 15, "animal pequeño interactuando con toma de agua"),
    ("07:44:56", "alerta_A1_20260306_074451_072508.jpg", 0, "persona anda en bicicleta"),
    ("07:44:59", "alerta_A1_20260306_074454_062989.jpg", 25, "dos personas en bicicleta/moto"),
]

BASE_PATH = "/home/capw/projects/camdetector/data/dvr_no_sospechosas"


def encode_image(image_path):
    """Encode image to base64."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analizar_imagen(image_path):
    """Analiza una imagen con OpenRouter."""
    try:
        base64_image = encode_image(image_path)
        
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "model": AI_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DETECTION_PROMPT_SOSPECHOSA},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        }
        
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
            
    except Exception as e:
        return f"ERROR: {e}"


def parse_result(texto):
    """Parsea el resultado del análisis."""
    persona = "NO"
    sospecha = 0
    descripcion = ""
    
    for line in texto.strip().split("\n"):
        line = line.strip()
        if line.startswith("PERSONA:"):
            valor = line.split(":", 1)[1].strip().upper()
            if valor in ["SI", "SÍ"]:
                persona = "SI"
        elif line.startswith("SOSPECHOSA:"):
            try:
                sospecha = int(line.split(":", 1)[1].strip())
            except:
                pass
        elif line.startswith("DESCRIPCION:"):
            descripcion = line.split(":", 1)[1].strip()
    
    return persona, sospecha, descripcion


def main():
    print("=" * 80)
    print("TEST DEL NUEVO PROMPT CON 39 IMÁGENES")
    print("=" * 80)
    print()
    
    si_original = 0
    no_original = 0
    
    si_nuevo = 0
    no_nuevo = 0
    
    cambios = []
    
    for i, (hora, archivo, sospecha_original, desc_original) in enumerate(IMAGENES, 1):
        ruta = os.path.join(BASE_PATH, archivo)
        
        if not os.path.exists(ruta):
            print(f"[{i:2d}] {hora} {archivo} → NO ENCONTRADA")
            continue
        
        print(f"[{i:2d}] {hora} {archivo}...", end=" ", flush=True)
        
        resultado = analizar_imagen(ruta)
        
        if resultado.startswith("ERROR:"):
            print(f"ERROR: {resultado}")
            continue
        
        persona, sospecha, descripcion = parse_result(resultado)
        
        # Clasificación original
        if sospecha_original >= 50:
            orig_clasif = "PERSONA"
            si_original += 1
        else:
            orig_clasif = "NO_PERSONA"
            no_original += 1
        
        # Clasificación nueva
        if persona == "SI":
            nuevo_clasif = "PERSONA"
            si_nuevo += 1
        else:
            nuevo_clasif = "NO_PERSONA"
            no_nuevo += 1
        
        # Verificar cambio
        if orig_clasif != nuevo_clasif:
            cambios.append({
                "i": i,
                "hora": hora,
                "archivo": archivo,
                "desc_original": desc_original,
                "nuevo_resultado": resultado.strip(),
                "clasif_original": orig_clasif,
                "clasif_nuevo": nuevo_clasif
            })
        
        print(f"{nuevo_clasif} (sospecha={sospecha}%) | {descripcion[:50]}...")
    
    print()
    print("=" * 80)
    print("RESUMEN")
    print("=" * 80)
    print()
    print(f"Original: {si_original} personas, {no_original} no-persona")
    print(f"Nuevo:    {si_nuevo} personas, {no_nuevo} no-persona")
    print()
    
    if cambios:
        print(f"CAMBIOS DETECTADOS: {len(cambios)}")
        print("-" * 80)
        for c in cambios:
            print(f"\n[{c['i']:2d}] {c['hora']} {c['archivo']}")
            print(f"   Original: {c['clasif_original']} - {c['desc_original']}")
            print(f"   Nuevo:    {c['clasif_nuevo']}")
            print(f"   Respuesta nueva:")
            for line in c['nuevo_resultado'].strip().split("\n"):
                print(f"      {line}")
    else:
        print("SIN CAMBIOS - El nuevo prompt produce los mismos resultados")


if __name__ == "__main__":
    main()
