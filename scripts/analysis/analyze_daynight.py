#!/usr/bin/env python3
"""
Análisis día vs noche: Evaluar si necesitamos 2 referencias fijas o dinámica
"""
from PIL import Image
import numpy as np
from pathlib import Path


def calcular_diferencia(img1_path: str, img2_path: str, umbral: int = 30):
    """Compara dos imágenes a resolución NATIVA (sin resize)."""
    img1 = Image.open(img1_path).convert('L')
    img2 = Image.open(img2_path).convert('L')
    
    arr1 = np.array(img1, dtype=np.int16)
    arr2 = np.array(img2, dtype=np.int16)
    
    diff = np.abs(arr1 - arr2)
    pxeles_cambiados = np.sum(diff > umbral)
    total_pxeles = diff.size
    porcentaje = (pxeles_cambiados / total_pxeles) * 100
    
    return round(porcentaje, 4)


def main():
    ref_dir = Path('/home/capw/projects/camdetector/data/referencia_ia')
    capturas_dir = Path('/home/capw/projects/camdetector/data/dvr_capturas')
    
    # Obtener imágenes de referencia nocturna
    referencias_noche = sorted(ref_dir.glob('*.jpg'))
    
    # Obtener imágenes de hoy (día)
    imagenes_hoy = sorted(capturas_dir.glob('alerta_A1_20260317_*.jpg'))
    
    # Separar noche vs día
    nocturnas = []
    diurnas = []
    
    for img in imagenes_hoy:
        hora = int(img.name.split('_')[3][:2])
        if hora < 7:
            nocturnas.append(img)
        else:
            diurnas.append(img)
    
    print("=" * 80)
    print("ANÁLISIS: Día vs Noche - ¿Referencia fija o dinámica?")
    print("=" * 80)
    print(f"Resolución: NATIVA (1920x1080 = 2,073,600 píxeles)")
    print()
    
    # PRUEBA 1: Ruido dentro del mismo período (noche vs noche)
    print("-" * 80)
    print("PRUEBA 1: Ruido noche→noche (misma iluminación)")
    print("-" * 80)
    
    ref_noche = referencias_noche[0]  # 00:23
    print(f"Referencia: {ref_noche.name}\n")
    
    diffs_noche = []
    for otra in referencias_noche[1:]:
        diff = calcular_diferencia(str(ref_noche), str(otra))
        diffs_noche.append(diff)
        hora = otra.name.split('_')[2][:4]
        print(f"  vs {otra.name} (hora {hora}): {diff}%")
    
    print(f"\n  Promedio noche→noche: {np.mean(diffs_noche):.2f}%")
    print(f"  Máximo noche→noche: {max(diffs_noche):.2f}%")
    
    # PRUEBA 2: Ruido dentro del mismo período (día vs día)
    print("\n" + "-" * 80)
    print("PRUEBA 2: Ruido día→día (misma iluminación)")
    print("-" * 80)
    
    if len(diurnas) >= 5:
        ref_dia = diurnas[0]  # Primera imagen de día
        print(f"Referencia: {ref_dia.name}\n")
        
        diffs_dia = []
        for otra in diurnas[1:10]:  # Primeras 10
            diff = calcular_diferencia(str(ref_dia), str(otra))
            diffs_dia.append(diff)
            hora = otra.name.split('_')[3][:4]
            print(f"  vs {otra.name} (hora {hora}): {diff}%")
        
        print(f"\n  Promedio día→día: {np.mean(diffs_dia):.2f}%")
        print(f"  Máximo día→día: {max(diffs_dia):.2f}%")
    
    # PRUEBA 3: Diferencia noche→día (¡clave!)
    print("\n" + "-" * 80)
    print("PRUEBA 3: Noche vs Día (iluminación completamente diferente)")
    print("-" * 80)
    
    if diurnas:
        print(f"Referencia noche: {ref_noche.name}\n")
        
        diffs_noche_dia = []
        for dia in diurnas[:10]:
            diff = calcular_diferencia(str(ref_noche), str(dia))
            diffs_noche_dia.append(diff)
            hora = dia.name.split('_')[3][:4]
            print(f"  noche→día {dia.name} (hora {hora}): {diff}%")
        
        print(f"\n  Promedio noche→día: {np.mean(diffs_noche_dia):.2f}%")
        print(f"  Mínimo noche→día: {min(diffs_noche_dia):.2f}%")
    
    # PRUEBA 4: Transición amanecer (noche→día gradual)
    print("\n" + "-" * 80)
    print("PRUEBA 4: Transición gradual (noche → amanecer → día)")
    print("-" * 80)
    
    # Tomar imágenes de 07:36 a 08:00 (transición)
    transicion = [img for img in imagenes_hoy 
                  if 7 <= int(img.name.split('_')[3][:2]) <= 8]
    transicion.sort()
    
    if transicion and nocturnas:
        ultima_noche = nocturnas[-1]
        print(f"Referencia noche: {ultima_noche.name}\n")
        
        for img in transicion[:15]:
            diff = calcular_diferencia(str(ultima_noche), str(img))
            hora = img.name.split('_')[3][:4]
            print(f"  noche→{hora}: {diff}%")
    
    # PRUEBA 5: Diferencia entre dos horas de día (sol se mueve)
    print("\n" + "-" * 80)
    print("PRUEBA 5: Variación durante el día (09:35 vs 11:02)")
    print("-" * 80)
    
    if len(diurnas) >= 10:
        temprano = diurnas[0]  # 09:35
        tarde = diurnas[-1]    # 11:13
        
        diff = calcular_diferencia(str(temprano), str(tarde))
        print(f"  {temprano.name} vs {tarde.name}: {diff}%")
    
    # RESUMEN Y RECOMENDACIÓN
    print("\n" + "=" * 80)
    print("CONCLUSIÓN")
    print("=" * 80)
    
    ruido_noche = max(diffs_noche) if diffs_noche else 0
    ruido_dia = max(diffs_dia) if diffs_dia else 0
    diff_noche_dia = min(diffs_noche_dia) if diffs_noche_dia else 0
    
    print(f"""
  Ruido noche (sin cambios): {ruido_noche:.2f}%
  Ruido día (sin cambios): {ruido_dia:.2f}%
  Diferencia noche→día: {diff_noche_dia:.2f}% (mínimo)
  
  ANÁLISIS:
  - Noche→noche: {ruido_noche:.2f}% (ruido de sensor/luz)
  - Noche→día: {diff_noche_dia:.2f}% (cambio TOTAL de iluminación)
  - Ratio noche→día / noche→noche: {diff_noche_dia/ruido_noche:.0f}x
  
  RECOMENDACIÓN:""")
    
    if diff_noche_dia > 50:
        print("""
  ✅ DOS REFERENCIAS FIJAS (noche + día)
     - La noche y el día son INCOMPATIBLES como referencia única
     - Referencia noche: última imagen NO_PERSONA de horario nocturno
     - Referencia día: última imagen NO_PERSONA de horario diurno
     - Se selecciona según horario actual
     - Se actualiza solo si desviación < 5% de la referencia actual""")
    else:
        print("""
  ✅ REFERENCIA DINÁMICA (se actualiza en tiempo real)
     - La diferencia noche→día es manejable con una sola referencia
     - La referencia se actualiza gradualmente
     - La IA analiza las imágenes durante la transición (amanecer/atardecer)""")
    
    print(f"""
  UMBRALES SUGERIDOS:
  - Ruido máximo permitido: {max(ruido_noche, ruido_dia):.2f}%
  - Umbral para actualizar referencia: 5% (desviación máxima)
  - Umbral para analizar: {max(ruido_noche, ruido_dia) * 3:.2f}% (3x ruido máximo)
""")


if __name__ == '__main__':
    main()
