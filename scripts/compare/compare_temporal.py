#!/usr/bin/env python3
"""
Script de prueba 2: Comparación temporal (imagen N vs imagen N-1)
Objetivo: Detectar si una imagen consecutiva tiene cambio respecto a la anterior.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/home/capw/projects/camdetector/venv/lib/python3.12/site-packages')

from PIL import Image
import numpy as np


def calcular_diferencia(img1_path: str, img2_path: str, umbral: int = 30, resize: tuple = (960, 540)):
    """Compara dos imágenes y calcula la diferencia."""
    img1 = Image.open(img1_path).convert('L').resize(resize, Image.LANCZOS)
    img2 = Image.open(img2_path).convert('L').resize(resize, Image.LANCZOS)
    
    arr1 = np.array(img1, dtype=np.int16)
    arr2 = np.array(img2, dtype=np.int16)
    
    diff = np.abs(arr1 - arr2)
    
    pxeles_cambiados = np.sum(diff > umbral)
    total_pxeles = diff.size
    porcentaje = (pxeles_cambiados / total_pxeles) * 100
    
    return {
        'diferencia_media': round(np.mean(diff), 2),
        'diferencia_max': int(np.max(diff)),
        'pxeles_cambiados': int(pxeles_cambiados),
        'porcentaje_cambiado': round(porcentaje, 4),
        'pxeles_diff': diff
    }


def main():
    capturas_dir = Path('/home/capw/projects/camdetector/data/dvr_capturas')
    sospechosas_dir = Path('/home/capw/projects/camdetector/data/dvr_sospechosas')
    
    # Obtener capturas de A1 ordenadas por fecha
    capturas = sorted(capturas_dir.glob('alerta_A1_*.jpg'))
    
    # Agrupar por día
    por_dia = {}
    for c in capturas:
        # Extraer fecha del nombre: alerta_A1_YYYYMMDD_HHMMSS...
        partes = c.name.split('_')
        if len(partes) >= 3:
            fecha = partes[2]
            if fecha not in por_dia:
                por_dia[fecha] = []
            por_dia[fecha].append(c)
    
    print("=" * 80)
    print("PRUEBA 2: Comparación temporal (imagen consecutiva N vs N-1)")
    print("=" * 80)
    
    # PRUEBA 1: Imágenes consecutivas misma noche
    print("\n" + "-" * 80)
    print("PRUEBA 1: Consecutivas misma noche (ruido base)")
    print("-" * 80)
    
    diffs_noche = []
    for fecha, imagenes in sorted(por_dia.items()):
        if len(imagenes) < 2:
            continue
        imagenes.sort()
        
        print(f"\n  📅 {fecha} ({len(imagenes)} imágenes):")
        
        for i in range(1, min(len(imagenes), 6)):  # Primeras 5 comparaciones
            resultado = calcular_diferencia(str(imagenes[i-1]), str(imagenes[i]))
            diffs_noche.append(resultado['porcentaje_cambiado'])
            
            estado = "✅ ANALIZAR" if resultado['porcentaje_cambiado'] > 1.0 else "⏭️ SALTAR"
            print(f"    {imagenes[i-1].name[-20:]} → {imagenes[i].name[-20:]}: {resultado['porcentaje_cambiado']}% {estado}")
    
    promedio_noche = np.mean(diffs_noche) if diffs_noche else 0
    max_noche = max(diffs_noche) if diffs_noche else 0
    
    print(f"\n  📊 Ruido base (consecutivas):")
    print(f"    Promedio: {round(promedio_noche, 4)}%")
    print(f"    Máximo: {round(max_noche, 4)}%")
    
    # PRUEBA 2: Consecutivas dentro de sospechosas (persona misma ráfaga)
    print("\n" + "-" * 80)
    print("PRUEBA 2: Sospechosas consecutivas (misma ráfaga)")
    print("-" * 80)
    
    sospechosas = sorted(sospechosas_dir.glob('alerta_A1_*.jpg'))
    
    # Buscar pares con timestamps similares (misma ráfaga)
    for i in range(1, len(sospechosas)):
        nombre1 = sospechosas[i-1].name
        nombre2 = sospechosas[i].name
        
        # Extraer timestamp
        try:
            ts1 = nombre1.split('_')[2] + nombre1.split('_')[3].split('.')[0]
            ts2 = nombre2.split('_')[2] + nombre2.split('_')[3].split('.')[0]
            
            if ts1[:8] == ts2[:8]:  # Mismo día
                resultado = calcular_diferencia(str(sospechosas[i-1]), str(sospechosas[i]))
                print(f"  {nombre1[-25:]} → {nombre2[-25:]}: {resultado['porcentaje_cambiado']}%")
        except:
            pass
    
    # PRUEBA 3: Diferencia entre primer captura noche y sospechosa
    print("\n" + "-" * 80)
    print("PRUEBA 3: Captura vs Sospechosa (detección de objeto)")
    print("-" * 80)
    
    # Tomar una captura de noche (fondo vacío)
    nocturnas = [c for c in capturas if '20260317' in c.name and int(c.name.split('_')[3][:2]) < 6]
    if nocturnas:
        nocturnas.sort()
        fondo = nocturnas[-1]
        print(f"  Fondo: {fondo.name}")
        
        for sospechosa in sospechosas:
            resultado = calcular_diferencia(str(fondo), str(sospechosa))
            umbral = max_noche * 3  # 3x el ruido base
            estado = "✅ ANALIZAR" if resultado['porcentaje_cambiado'] > umbral else "⏭️ SALTAR"
            print(f"  → {sospechosa.name}: {resultado['porcentaje_cambiado']}% {estado}")
    
    # PRUEBA 4: Simulación de intercalado + fondo subtraction
    print("\n" + "-" * 80)
    print("PRUEBA 4: Simulación completa (fondo subtraction + intercalado)")
    print("-" * 80)
    
    # Simular ráfaga de 20 imágenes
    # Referencia: última imagen nocturna
    # Decision: cambio > umbral = analizar, sino saltar
    ultima_noche = sorted([c for c in capturas if '20260317' in c.name])[-1] if capturas else None
    
    if ultima_noche:
        print(f"  Fondo referencia: {ultima_noche.name}")
        print(f"  Umbral: {round(max_noche * 3, 4)}% (3x ruido base)\n")
        
        analizados = 0
        saltados = 0
        
        for captura in capturas[-20:]:
            resultado = calcular_diferencia(str(ultima_noche), str(captura))
            umbral = max_noche * 3
            
            if resultado['porcentaje_cambiado'] > umbral:
                analizados += 1
                print(f"  ✅ ANALIZAR: {captura.name[-25:]} ({resultado['porcentaje_cambiado']}%)")
            else:
                saltados += 1
                print(f"  ⏭️ SALTAR: {captura.name[-25:]} ({resultado['porcentaje_cambiado']}%)")
        
        print(f"\n  📊 Resultado:")
        print(f"    Total: {analizados + saltados}")
        print(f"    Analizados: {analizados} ({round(analizados/(analizados+saltados)*100, 1)}%)")
        print(f"    Saltados: {saltados} ({round(saltados/(analizados+saltados)*100, 1)}%)")
        print(f"    Ahorro estimado: {saltados} llamadas a API evitadas")
    
    # RESUMEN
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    print(f"""
  Estrategia: Comparar imagen N vs imagen N-1 (misma cámara, misma noche)
  
  Ruido base (sin cambios):
    - Promedio: {round(promedio_noche, 4)}%
    - Máximo: {round(max_noche, 4)}%
  
  Umbral recomendado: {round(max_noche * 3, 4)}% (3x máximo ruido)
  
  Regla:
    Si diferencia > {round(max_noche * 3, 4)}%:
      → Hay cambio real → Analizar
    Si diferencia <= {round(max_noche * 3, 4)}%:
      → Solo ruido → Saltar
  
  Implementación en cam_watcher.py:
    - Guardar última imagen analizada por cámara
    - Al recibir nueva imagen, comparar con última guardada
    - Si diferencia < umbral → SKIP (sin llamar a IA)
""")


if __name__ == '__main__':
    main()
