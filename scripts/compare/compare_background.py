#!/usr/bin/env python3
"""
Script de prueba: Comparación de fondo vs nuevas imágenes.
Objetivo: Detectar si una imagen tiene "algo diferente" respecto al fondo nocturno.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Agregar venv al path
sys.path.insert(0, '/home/capw/projects/camdetector/venv/lib/python3.12/site-packages')

from PIL import Image
import numpy as np


def calcular_diferencia(img1_path: str, img2_path: str, umbral: int = 30, resize: tuple = (960, 540)):
    """
    Compara dos imágenes y calcula la diferencia.
    
    Args:
        img1_path: Ruta a la imagen de referencia (fondo)
        img2_path: Ruta a la imagen nueva
        umbral: Diferencia mínima por pixel para considerar "cambiado" (0-255)
        resize: Tamaño para redimensionar (más rápido)
    
    Returns:
        dict con métricas de diferencia
    """
    # Cargar y convertir a escala de grises
    img1 = Image.open(img1_path).convert('L').resize(resize, Image.LANCZOS)
    img2 = Image.open(img2_path).convert('L').resize(resize, Image.LANCZOS)
    
    # Convertir a arrays numpy
    arr1 = np.array(img1, dtype=np.int16)
    arr2 = np.array(img2, dtype=np.int16)
    
    # Diferencia absoluta por pixel
    diff = np.abs(arr1 - arr2)
    
    # Estadísticas
    diff_media = np.mean(diff)
    diff_max = np.max(diff)
    pxeles_cambiados = np.sum(diff > umbral)
    total_pxeles = diff.size
    porcentaje = (pxeles_cambiados / total_pxeles) * 100
    
    return {
        'diferencia_media': round(diff_media, 2),
        'diferencia_max': int(diff_max),
        'pxeles_cambiados': int(pxeles_cambiados),
        'total_pxeles': int(total_pxeles),
        'porcentaje_cambiado': round(porcentaje, 4),
        'pxeles_diff': diff
    }


def main():
    ref_dir = Path('/home/capw/projects/camdetector/data/referencia_ia')
    sospechosas_dir = Path('/home/capw/projects/camdetector/data/dvr_sospechosas')
    capturas_dir = Path('/home/capw/projects/camdetector/data/dvr_capturas')
    
    # Obtener imágenes de referencia
    referencias = sorted(ref_dir.glob('*.jpg'))
    
    if len(referencias) < 2:
        print("ERROR: Se necesitan al menos 2 imágenes de referencia")
        return
    
    print("=" * 80)
    print("PRUEBA: Comparación de fondo nocturno")
    print("=" * 80)
    
    # Usar primera imagen como referencia base
    ref_img = referencias[0]
    print(f"\n📸 Imagen de referencia: {ref_img.name}")
    
    # PRUEBA 1: Comparar referencias entre sí (línea base)
    print("\n" + "-" * 80)
    print("PRUEBA 1: Referencia vs otras imágenes nocturnas (sin actividad)")
    print("-" * 80)
    
    resultados_vacias = []
    for ref2 in referencias[1:]:
        resultado = calcular_diferencia(str(ref_img), str(ref2))
        nombre = ref2.name
        print(f"  {nombre}:")
        print(f"    Diferencia media: {resultado['diferencia_media']}")
        print(f"    Diferencia máxima: {resultado['diferencia_max']}")
        print(f"    Píxeles cambiados (>{30}): {resultado['pxeles_cambiados']:,}")
        print(f"    Porcentaje cambiado: {resultado['porcentaje_cambiado']}%")
        resultados_vacias.append(resultado['porcentaje_cambiado'])
    
    promedio_vacias = np.mean(resultados_vacias) if resultados_vacias else 0
    max_vacias = max(resultados_vacias) if resultados_vacias else 0
    
    print(f"\n  📊 Línea base:")
    print(f"    Promedio cambio: {round(promedio_vacias, 4)}%")
    print(f"    Máximo cambio: {round(max_vacias, 4)}%")
    
    # PRUEBA 2: Comparar con imágenes sospechosas (con objetos)
    print("\n" + "-" * 80)
    print("PRUEBA 2: Referencia vs imágenes sospechosas (con objetos)")
    print("-" * 80)
    
    sospechosas = sorted(sospechosas_dir.glob('*.jpg'))
    resultados_sospechosas = []
    
    for sospechosa in sospechosas:
        resultado = calcular_diferencia(str(ref_img), str(sospechosa))
        nombre = sospechosa.name
        print(f"  {nombre}:")
        print(f"    Diferencia media: {resultado['diferencia_media']}")
        print(f"    Diferencia máxima: {resultado['diferencia_max']}")
        print(f"    Píxeles cambiados (>{30}): {resultado['pxeles_cambiados']:,}")
        print(f"    Porcentaje cambiado: {resultado['porcentaje_cambiado']}%")
        resultados_sospechosas.append(resultado['porcentaje_cambiado'])
    
    promedio_sospechosas = np.mean(resultados_sospechosas) if resultados_sospechosas else 0
    min_sospechosas = min(resultados_sospechosas) if resultados_sospechosas else 0
    
    print(f"\n  📊 Imágenes con objetos:")
    print(f"    Promedio cambio: {round(promedio_sospechosas, 4)}%")
    print(f"    Mínimo cambio: {round(min_sospechosas, 4)}%")
    
    # PRUEBA 3: Comparar con capturas recientes (mixto)
    print("\n" + "-" * 80)
    print("PRUEBA 3: Referencia vs capturas recientes (últimas 10)")
    print("-" * 80)
    
    capturas = sorted(capturas_dir.glob('alerta_A1_*.jpg'))[-10:]
    for captura in capturas:
        resultado = calcular_diferencia(str(ref_img), str(captura))
        nombre = captura.name
        # Determinar si sería analizada o saltada
        umbral_recomendado = promedio_vacias * 3  # 3x el promedio vacío
        estado = "✅ ANALIZAR" if resultado['porcentaje_cambiado'] > umbral_recomendado else "⏭️ SALTAR"
        
        print(f"  {nombre}:")
        print(f"    Porcentaje cambiado: {resultado['porcentaje_cambiado']}%")
        print(f"    Decisión: {estado}")
    
    # PRUEBA 4: Análisis de zona de interés (máscara)
    print("\n" + "-" * 80)
    print("PRUEBA 4: Análisis por zonas (arriba vs abajo)")
    print("-" * 80)
    
    # La parte superior suele ser cielo (más ruido), la inferior patio (más importante)
    ref = Image.open(str(ref_img)).convert('L').resize((960, 540), Image.LANCZOS)
    ref_arr = np.array(ref, dtype=np.int16)
    
    alto = ref_arr.shape[0]
    zona_superior = ref_arr[:alto//2, :]
    zona_inferior = ref_arr[alto//2:, :]
    
    print(f"  Zona superior (cielo): {zona_superior.shape}")
    print(f"  Zona inferior (patio): {zona_inferior.shape}")
    
    # Comparar con imagen sospechosa por zonas
    if sospechosas:
        img = Image.open(str(sospechosas[0])).convert('L').resize((960, 540), Image.LANCZOS)
        img_arr = np.array(img, dtype=np.int16)
        
        diff_superior = np.abs(zona_superior - img_arr[:alto//2, :])
        diff_inferior = np.abs(zona_inferior - img_arr[alto//2:, :])
        
        pct_superior = (np.sum(diff_superior > 30) / diff_superior.size) * 100
        pct_inferior = (np.sum(diff_inferior > 30) / diff_inferior.size) * 100
        
        print(f"\n  Comparando con: {sospechosas[0].name}")
        print(f"    Cambio zona superior: {round(pct_superior, 4)}%")
        print(f"    Cambio zona inferior: {round(pct_inferior, 4)}%")
    
    # RESUMEN FINAL
    print("\n" + "=" * 80)
    print("RESUMEN Y RECOMENDACIONES")
    print("=" * 80)
    print(f"""
  Referencia: {ref_img.name}
  Imágenes vacías (base): promedio {round(promedio_vacias, 4)}%, máximo {round(max_vacias, 4)}%
  Imágenes con objetos: promedio {round(promedio_sospechosas, 4)}%, mínimo {round(min_sospechosas, 4)}%
  
  Separación: {round(min_sospechosas / max_vacias, 1)}x si mínimo sospechosa > máximo vacía
  
  Umbral recomendado: {round(max_vacias * 2, 4)}% (2x máximo de imágenes vacías)
  
  Si PORCENTAGE_CAMBIADO > {round(max_vacias * 2, 4)}%:
    → ANALIZAR con IA (algo se movió en la escena)
  Si PORCENTAGE_CAMBIADO <= {round(max_vacias * 2, 4)}%:
    → SALTAR (solo ruido de noche)
""")


if __name__ == '__main__':
    main()
