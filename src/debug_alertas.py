#!/usr/bin/env python3
"""
Script de diagnóstico para ver qué datos envía el DVR en las alertas.
Ejecutar: python3 debug_alertas.py
"""
import socket

LISTEN_PORT = 8089  # Puerto diferente para no interferir con el servicio

print(f"Escuchando en puerto {LISTEN_PORT}...")
print("Esperando conexión del DVR (configurá el DVR para enviar a este puerto)...")
print("O simulá con: echo 'test' | nc localhost 8089")
print("-" * 60)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', LISTEN_PORT))
    s.listen(5)
    
    for i in range(5):  # Capturar 5 alertas
        conn, addr = s.accept()
        with conn:
            data = conn.recv(4096)
            print(f"\n[Alerta {i+1}] Desde: {addr[0]}:{addr[1]}")
            print(f"Longitud: {len(data)} bytes")
            print(f"Raw (hex): {data.hex()[:200]}...")
            print(f"Raw (ascii): {data[:200]}")
            
            # Intentar decodificar como texto
            try:
                texto = data.decode('utf-8', errors='replace')
                print(f"UTF-8: {texto[:500]}")
            except:
                pass
            
            # Buscar patrones conocidos de Hikvision
            if b'channel' in data.lower() or b'Channel' in data:
                print(">>> Contiene 'channel'!")
            if b'<' in data and b'>' in data:
                print(">>> Parece ser XML!")
            
            print("-" * 60)

print("\nDiagnóstico completado.")
