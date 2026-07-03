#!/usr/bin/env python3
"""
Script de prueba 3: Comparación temporal nocturna específica
Foco: Ráfagas nocturnas consecutivas vs imágenes con personas
"""

import sys
from pathlib import Path

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
    
    return round(porcentaje, 4)


def main():
    capturas_dir = Path('/home/capw/projects/camdetector/data/dvr_capturas')
    sospechosas_dir = Path('/home/capw/projects/camdetector/data/dvr_sospechosas')
    
    # Obtener imágenes nocturnas (antes de 07:37)
    capturas = sorted(capturas_dir.glob('alerta_A1_20260317_0[0-7]*.jpg'))
    nocturnas = [c for c in capturas if int(c.name.split('_')[3][:2]) < 7 or 
                 (int(c.name.split('_')[3][:2]) == 7 and int(c.name.split('_')[3][2:4]) < 37)]
    
    print("=" * 80)
    print("PRUEBA 3: Ráfagas nocturnas específicas")
    print("=" * 80)
    
    # PRUEBA 1: Ráfagas nocturnas consecutivas (diferencia < 1 segundo)
    print("\n" + "-" * 80)
    print("PRUEBA 1: Diferencia entre imágenes consecutivas (1-2s de separación)")
    print("-" * 80)
    
    # Agrupar por ráfaga (gap > 10s = nueva ráfaga)
    rafagas = []
    rafaga_actual = []
    for img in nocturnas:
        if not rafaga_actual:
            rafaga_actual.append(img)
        else:
            # Extraer timestamp
            ts_actual = int(img.name.split('_')[3][:6])
            ts_anterior = int(rafaga_actual[-1].name.split('_')[3][:6])
            if ts_actual - ts_anterior <= 10:  # Mismo segundo o máximo 10s
                rafaga_actual.append(img)
            else:
                rafagas.append(rafaga_actual)
                rafaga_actual = [img]
    if rafaga_actual:
        rafagas.append(rafaga_actual)
    
    print(f"  Encontradas {len(rafagas)} ráfagas nocturnas")
    
    diffs_noche = []
    for i, rafaga in enumerate(rafagas[:10]):  # Primeras 10 ráfagas
        print(f"\n  📸 Ráfaga {i+1}: {len(rafaga)} imágenes")
        
        # Comparar cada imagen consecutiva en la ráfaga
        for j in range(1, len(rafaga)):
            diff = calcular_diferencia(str(rafaga[j-1]), str(rafaga[j]))
            diffs_noche.append(diff)
            
            ts1 = rafaga[j-1].name.split('_')[3][:6]
            ts2 = rafaga[j].name.split('_')[3][:6]
            separacion = int(ts2) - int(ts1)
            estado = "✅ ANALIZAR" if diff > 5.0 else "⏭️ SALTAR"
            print(f"    {ts1}→{ts2} (Δ{separacion}s): {diff}% {estado}")
    
    promedio_noche = np.mean(diffs_noche) if diffs_noche else 0
    max_noche = max(diffs_noche) if diffs_noche else 0
    p90_noche = np.percentile(diffs_noche, 90) if diffs_noche else 0
    
    print(f"\n  📊 Estadísticas ruido nocturno:")
    print(f"    Promedio: {round(promedio_noche, 4)}%")
    print(f"    Máximo: {round(max_noche, 4)}%")
    print(f"    Percentil 90: {round(p90_noche, 4)}%")
    
    # PRUEBA 2: Comparar nocturnas vs sospechosas de noche
    print("\n" + "-" * 80)
    print("PRUEBA 2: Sospechosas nocturnas vs referencia nocturna")
    print("-" * 80)
    
    # Referencia nocturna (00:23 - imagen de referencia_ia)
    ref_noche = Path('/home/capw/projects/camdetector/data/referencia_ia/alerta_A1_20260317_002355_960317.jpg')
    
    # Sospechosas de noche (00:00 - 07:45)
    sospechosas = sorted(sospechosas_dir.glob('alerta_A1_*.jpg'))
    sospechosas_noche = []
    for s in sospechosas:
        partes = s.name.split('_')
        if len(partes) >= 4:
            hora = partes[3][:2]
            fecha = partes[2]
            # Noche = 00:01 a 07:45
            if hora.isdigit() and int(hora) < 8:
                sospechosas_noche.append(s)
    
    if sospechosas_noche:
        print(f"  Referencia: {ref_noche.name}")
        print(f"  Sospechosas nocturnas: {len(sospechosas_noche)}")
        
        for s in sospechosas_noche:
            diff = calcular_diferencia(str(ref_noche), str(s))
            hora = s.name.split('_')[3][:4]
            print(f"    {s.name} (hora {hora}): {diff}%")
    
    # PRUEBA 3: Simulación real
    print("\n" + "-" * 80)
    print("PRUEBA 3: Simulación real con intercalado + fondo subtraction")
    print("-" * 80)
    
    # Simular: cada imagen se compara con la última analizada
    # Si diferencia < umbral → saltar
    # Si diferencia > umbral → analizar y actualizar referencia
    
    UMBRAL = max_noche * 2.5  # 2.5x el máximo ruido nocturno
    
    print(f"  Umbral: {round(UMBRAL, 4)}%")
    print(f"  Imágenes nocturnas: {len(nocturnas)}\n")
    
    ultima_analizada = str(nocturnas[0])
    analizados = 0
    saltados = 0
    
    for img in nocturnas[1:]:
        diff = calcular_diferencia(ultima_analizada, str(img))
        
        if diff > UMBRAL:
            analizados += 1
            ultima_analizada = str(img)
            print(f"  ✅ ANALIZAR: {img.name[-25:]} ({diff}%)")
        else:
            saltados += 1
            print(f"  ⏭️ SALTAR: {img.name[-25:]} ({diff}%)")
    
    print(f"\n  📊 Resultado simulación:")
    print(f"    Total: {analizados + saltados}")
    print(f"    Analizados: {analizados} ({round(analizados/(analizados+saltados)*100, 1)}%)")
    print(f"    Saltados: {saltados} ({round(saltados/(analizados+saltados)*100, 1)}%)")
    print(f"    Ahorro: {saltados} llamadas a API evitadas")
    
    # RESUMEN FINAL
    print("\n" + "=" * 80)
    print("CONCLUSIONES")
    print("=" * 80)
    print(f"""
  1. Ruido nocturno (imagen vs imagen consecutiva):
     - Promedio: {round(promedio_noche, 4)}%
     - Máximo: {round(max_noche, 4)}%
     - Percentil 90: {round(p90_noche, 4)}%
  
  2. Umbral recomendado: {round(UMBRAL, 4)}%
     - 2.5x el máximo ruido nocturno
     - Detecta cambios > {round(UMBRAL, 4)}%
  
  3. Implementación:
     - Guardar última imagen analizada por cámara
     - Comparar con nueva imagen usando resta de fondo
     - Si diferencia < {round(UMBRAL, 4)}% → SKIP (sin IA)
     - Si diferencia > {round(UMBRAL, 4)}% → ANALIZAR + actualizar referencia
  
  4. Riesgo:
     - Objetos muy pequeños (< {round(UMBRAL, 4)}% de píxeles) pueden pasar desapercibidos
     - Pero el intercalado ya reduce 50% de análisis
     - Y el techo de 20 análisis/evento limita el coste máximo
""")


if __name__ == '__main__':
    main()
