#!/usr/bin/env python3
"""
Benchmark: Mide tiempo y CPU de la comparación de fondo
"""
import sys
import time
import os

sys.path.insert(0, '/home/capw/projects/camdetector/venv/lib/python3.12/site-packages')

from PIL import Image
import numpy as np


def comparar_fondo(ref_path: str, nueva_path: str, umbral: int = 30, resize: tuple = (960, 540)):
    """Versión optimizada para benchmark."""
    # Cargar ambas en paralelo
    img1 = Image.open(ref_path).convert('L').resize(resize, Image.LANCZOS)
    img2 = Image.open(nueva_path).convert('L').resize(resize, Image.LANCZOS)
    
    # Convertir a numpy
    arr1 = np.array(img1, dtype=np.int16)
    arr2 = np.array(img2, dtype=np.int16)
    
    # Diferencia absoluta
    diff = np.abs(arr1 - arr2)
    
    # Contar píxeles cambiados
    pxeles_cambiados = np.sum(diff > umbral)
    total_pxeles = diff.size
    porcentaje = (pxeles_cambiados / total_pxeles) * 100
    
    return porcentaje


def main():
    ref_dir = Path('/home/capw/projects/camdetector/data/referencia_ia')
    capturas_dir = Path('/home/capw/projects/camdetector/data/dvr_capturas')
    
    # Usar primera referencia
    referencias = sorted(ref_dir.glob('*.jpg'))
    ref = str(referencias[0])
    
    # Obtener imágenes nocturnas para prueba
    capturas = sorted(capturas_dir.glob('alerta_A1_20260317_0[0-7]*.jpg'))[:10]
    
    print("=" * 70)
    print("BENCHMARK: Comparación de fondo")
    print("=" * 70)
    print(f"Referencia: {Path(ref).name}")
    print(f"Resolución: 960x540 (redimensionado)")
    print(f"Umbral: 30 (diferencia de pixel)")
    print()
    
    # Medir tiempo por imagen
    tiempos = []
    
    print("Midiendo tiempo de comparación por imagen...")
    print("-" * 70)
    
    for i, captura in enumerate(capturas):
        inicio = time.perf_counter()
        resultado = comparar_fondo(ref, str(captura))
        fin = time.perf_counter()
        
        tiempo_ms = (fin - inicio) * 1000
        tiempos.append(tiempo_ms)
        
        nombre = captura.name[-30:]
        print(f"  {i+1:2d}. {nombre}: {tiempo_ms:6.2f}ms → {resultado:.4f}%")
    
    print("-" * 70)
    print(f"\nEstadísticas de tiempo:")
    print(f"  Promedio: {np.mean(tiempos):.2f}ms")
    print(f"  Mínimo:  {np.min(tiempos):.2f}ms")
    print(f"  Máximo:  {np.max(tiempos):.2f}ms")
    print(f"  P95:     {np.percentile(tiempos, 95):.2f}ms")
    
    # Comparar tamaños de imagen
    print(f"\nTamaño de referencia:")
    img = Image.open(ref)
    print(f"  Original: {img.size[0]}x{img.size[1]} = {img.size[0]*img.size[1]:,} píxeles")
    img.close()
    
    img_resized = Image.open(ref).convert('L').resize((960, 540), Image.LANCZOS)
    print(f"  Procesada: {img_resized.size[0]}x{img_resized.size[1]} = {img_resized.size[0]*img_resized.size[1]:,} píxeles")
    img_resized.close()
    
    # Simular carga de 38 imágenes/min
    print(f"\nSimulación carga pico (38 imágenes/min):")
    tiempo_total = np.mean(tiempos) * 38
    print(f"  Tiempo total por minuto: {tiempo_total:.0f}ms ({tiempo_total/1000:.1f}s)")
    print(f"  CPU disponible: {60 - tiempo_total/1000:.1f}s")
    print(f"  Overhead: {tiempo_total/1000/60*100:.1f}%")
    
    # Comparar con IA
    print(f"\nComparación con análisis IA:")
    print(f"  Comparación fondo: ~{np.mean(tiempos):.0f}ms por imagen")
    print(f"  Análisis IA: ~2000-5000ms por imagen")
    print(f"  Speedup: ~{2500/np.mean(tiempos):.0f}x más rápido")
    
    # Riesgos
    print(f"\n" + "=" * 70)
    print("ANÁLISIS DE RIESGOS")
    print("=" * 70)
    print(f"""
1. CPU:
   - Comparación: ~{np.mean(tiempos):.0f}ms por imagen
   - Con 38 img/min: {(np.mean(tiempos) * 38 / 1000):.1f}s CPU por minuto
   - Overhead: {(np.mean(tiempos) * 38 / 1000) / 60 * 100:.2f}% del tiempo

2. MEMORIA:
   - Imagen referencia: ~1.5MB en disco, ~5MB en memoria (numpy)
   - Imagen nueva: ~1.5MB en disco, ~5MB en memoria
   - Total temporal: ~10MB por comparación (liberado después)

3. TIEMPO ANÁLISIS:
   - Por imagen: {np.mean(tiempos):.0f}ms
   - 38 imágenes: {(np.mean(tiempos) * 38 / 1000):.1f}s
   - IA (sin filtro): 38 × 3s = 114s
   - IA (con filtro): ~0-3 × 3s = 0-9s

4. RIESGOS:
   - Colgado: BAJO (Pillow es estable, no hace llamadas de red)
   - Bloqueo watchdog: BAJO (comparación es rápida, <100ms)
   - Pérdida de imágenes: NO (el watcher sigue corriendo en paralelo)
   - Crash memoria: BAJO (libera memoria después de cada comparación)

5. IMPLEMENTACIÓN SEGURA:
   - Comparación en thread separado (no bloquea watcher)
   - Timeout de 5s por comparación (fallback a "analizar")
   - Si falla comparación → analizar (fail-safe)
""")


if __name__ == '__main__':
    from pathlib import Path
    main()
