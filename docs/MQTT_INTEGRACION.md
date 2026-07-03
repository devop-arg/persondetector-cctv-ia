# Integración MQTT - CamDetector + Domótica

## Documento de Diseño

**Versión**: 1.0  
**Fecha**: Enero 2026  
**Estado**: Planificación (no implementado)

---

## ÍNDICE

1. [Objetivo](#1-objetivo)
2. [Arquitectura](#2-arquitectura)
3. [Componentes](#3-componentes)
4. [Instalación Mosquitto](#4-instalación-mosquitto)
5. [Configuración de Seguridad](#5-configuración-de-seguridad)
6. [Modificaciones a CamDetector](#6-modificaciones-a-camdetector)
7. [Configuración de Actuadores](#7-configuración-de-actuadores)
8. [Topics MQTT](#8-topics-mqtt)
9. [Ejemplos de Código](#9-ejemplos-de-código)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. OBJETIVO

Extender CamDetector para que, además de enviar notificaciones a Telegram cuando detecta una persona, también publique mensajes MQTT que permitan:

- Encender un reflector por 60 segundos
- Activar una sirena por 60 segundos
- Notificar a otros sistemas domóticos (Home Assistant, Node-RED, etc.)

### 1.1 Flujo Actual

```
Cámara → FTP → cam_watcher → Gemini AI → ¿Persona? → Telegram
```

### 1.2 Flujo Propuesto

```
Cámara → FTP → cam_watcher → Gemini AI → ¿Persona? ─┬→ Telegram
                                                     │
                                                     └→ MQTT → ESP32-C3 → Reflector/Sirena
```

---

## 2. ARQUITECTURA

### 2.1 Diagrama General

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VPS (142.171.248.233)                              │
│                                                                              │
│  ┌──────────────────┐                      ┌──────────────────┐              │
│  │   cam_watcher    │                      │    Mosquitto     │              │
│  │    (Python)      │──── MQTT Publish ───►│     (Docker)     │              │
│  │                  │                      │                  │              │
│  │ Topic:           │                      │  Puerto: 1883    │◄─── TCP ────┐│
│  │ camdetector/     │                      │  Puerto: 8883    │◄─── TLS ────┤│
│  │   persona        │                      │                  │              ││
│  └──────────────────┘                      └──────────────────┘              ││
│                                                                              ││
└──────────────────────────────────────────────────────────────────────────────┘│
                                                                                │
                                         Internet                               │
                                            │                                   │
              ┌─────────────────────────────┼───────────────────────────────────┘
              │                             │
              ▼                             ▼
     ┌─────────────────────┐       ┌─────────────────────┐
     │      ESP32-C3       │       │      ESP32-C3       │
     │      (ESPHome)      │       │      (ESPHome)      │
     │                     │       │                     │
     │  ┌───────────────┐  │       │  ┌───────────────┐  │
     │  │  Relay 5V     │  │       │  │  Relay 5V     │  │
     │  │  (Reflector)  │  │       │  │  (Sirena)     │  │
     │  └───────────────┘  │       │  └───────────────┘  │
     │                     │       │                     │
     │  Suscrito a:        │       │  Suscrito a:        │
     │  camdetector/       │       │  camdetector/       │
     │    reflector        │       │    sirena           │
     └─────────────────────┘       └─────────────────────┘
              │                             │
              ▼                             ▼
         ┌────────┐                    ┌────────┐
         │   💡   │                    │   🔊   │
         │Reflect.│                    │ Sirena │
         └────────┘                    └────────┘
```

### 2.2 Flujo de Mensajes

```
1. cam_watcher detecta persona
        │
        ▼
2. Publica a MQTT:
   - Topic: camdetector/persona
   - Payload: {"camera": "192.168.1.10", "timestamp": "2026-01-24T03:15:00", "confidence": "alta"}
        │
        ├──────────────────────────────────────────────────┐
        ▼                                                  ▼
3a. Publica a:                                    3b. Publica a:
    Topic: camdetector/reflector                      Topic: camdetector/sirena
    Payload: ON                                       Payload: ON
        │                                                  │
        ▼                                                  ▼
4a. ESP32-C3 #1 recibe                            4b. ESP32-C3 #2 recibe
    Enciende GPIO → Relay → Reflector                 Enciende GPIO → Relay → Sirena
        │                                                  │
        ▼                                                  ▼
5a. Después de 60s, cam_watcher publica:          5b. Después de 60s, cam_watcher publica:
    Topic: camdetector/reflector                      Topic: camdetector/sirena
    Payload: OFF                                      Payload: OFF
```

---

## 3. COMPONENTES

### 3.1 Software

| Componente | Versión | Función |
|------------|---------|---------|
| Mosquitto | 2.x | Broker MQTT (Docker) |
| paho-mqtt | 2.x | Cliente MQTT Python |
| ESPHome | latest | Firmware ESP32 |

### 3.2 Hardware (en casa del usuario)

| Componente | Cantidad | Precio ARG | Descripción |
|------------|----------|------------|-------------|
| ESP32-C3 SuperMini | 2 | $5.000 c/u | Ya disponibles |
| Módulo Relay 5V | 2 | $1.500 c/u | Opto-aislado |
| Fuente 5V 1A | 2 | $2.000 c/u | Para ESP32 + Relay |
| Cables dupont | 1 pack | $500 | Conexiones |
| **TOTAL** | | **~$17.000** | |

### 3.3 Conexión ESP32-C3 + Relay

```
ESP32-C3 SuperMini          Relay 5V
┌─────────────────┐        ┌─────────────┐
│                 │        │             │
│  3.3V ──────────┼────────┤ VCC         │
│  GND  ──────────┼────────┤ GND         │
│  GPIO4 ─────────┼────────┤ IN          │
│                 │        │             │
│                 │        │  NO ────────┼──── A carga (reflector/sirena)
│                 │        │  COM ───────┼──── Fase 220V
│                 │        │  NC         │
└─────────────────┘        └─────────────┘
                                 │
                            ┌────┴────┐
                            │  Carga  │
                            │  220V   │
                            └─────────┘
```

**IMPORTANTE**: El relay aísla la parte de control (5V/3.3V) de la carga (220V). Trabajar con 220V requiere precaución.

---

## 4. INSTALACIÓN MOSQUITTO

### 4.1 Opción Docker (Recomendada)

Crear directorio y archivos de configuración:

```bash
# Crear estructura
mkdir -p /opt/mosquitto/{config,data,log}

# Crear docker-compose.yml
cat > /opt/mosquitto/docker-compose.yml << 'EOF'
version: '3.8'

services:
  mosquitto:
    container_name: mosquitto
    image: eclipse-mosquitto:2
    restart: unless-stopped
    ports:
      - "1883:1883"   # MQTT sin TLS
      - "8883:8883"   # MQTT con TLS (opcional)
    volumes:
      - ./config:/mosquitto/config
      - ./data:/mosquitto/data
      - ./log:/mosquitto/log
    environment:
      - TZ=America/Argentina/Buenos_Aires
EOF
```

### 4.2 Configuración Básica (Sin Autenticación)

Para pruebas iniciales:

```bash
cat > /opt/mosquitto/config/mosquitto.conf << 'EOF'
# Mosquitto Configuration - Básico (Sin Auth)
# WARNING: Solo para pruebas, no usar en producción

listener 1883
allow_anonymous true

persistence true
persistence_location /mosquitto/data/

log_dest file /mosquitto/log/mosquitto.log
log_type all
EOF
```

### 4.3 Iniciar Servicio

```bash
cd /opt/mosquitto
docker compose up -d

# Verificar
docker compose logs -f
```

### 4.4 Probar Conexión

```bash
# Instalar cliente mosquitto (en cualquier máquina)
apt install mosquitto-clients

# Suscribirse a topic de prueba
mosquitto_sub -h 142.171.248.233 -t "test/#" -v &

# Publicar mensaje
mosquitto_pub -h 142.171.248.233 -t "test/hola" -m "Funciona!"

# Deberías ver: test/hola Funciona!
```

---

## 5. CONFIGURACIÓN DE SEGURIDAD

### 5.1 Opción A: Usuario + Contraseña (Recomendado para empezar)

```bash
# Crear archivo de contraseñas
docker exec -it mosquitto mosquitto_passwd -c /mosquitto/config/passwd camdetector
# Ingresás la contraseña cuando lo pida

# Actualizar configuración
cat > /opt/mosquitto/config/mosquitto.conf << 'EOF'
# Mosquitto Configuration - Con Autenticación

listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd

persistence true
persistence_location /mosquitto/data/

log_dest file /mosquitto/log/mosquitto.log
log_type warning
log_type error
log_type notice
EOF

# Reiniciar
docker compose restart
```

**Credenciales a usar:**
- Usuario: `camdetector`
- Contraseña: (la que elegiste)

### 5.2 Opción B: TLS + Certificados (Más seguro)

Para conexiones encriptadas sobre internet:

```bash
# Generar certificados (self-signed)
mkdir -p /opt/mosquitto/config/certs
cd /opt/mosquitto/config/certs

# CA (Certificate Authority)
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=Mosquitto CA"

# Servidor
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/CN=142.171.248.233"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt -days 3650

# Permisos
chmod 644 *.crt
chmod 600 *.key
```

Configuración con TLS:

```bash
cat > /opt/mosquitto/config/mosquitto.conf << 'EOF'
# Mosquitto Configuration - TLS + Auth

# Puerto sin TLS (solo localhost para cam_watcher)
listener 1883 127.0.0.1
allow_anonymous false
password_file /mosquitto/config/passwd

# Puerto con TLS (para clientes externos)
listener 8883
allow_anonymous false
password_file /mosquitto/config/passwd
cafile /mosquitto/config/certs/ca.crt
certfile /mosquitto/config/certs/server.crt
keyfile /mosquitto/config/certs/server.key
require_certificate false

persistence true
persistence_location /mosquitto/data/
EOF
```

### 5.3 Firewall

```bash
# Abrir puerto MQTT (elegir uno según configuración)
sudo ufw allow 1883/tcp comment "MQTT sin TLS"
# O
sudo ufw allow 8883/tcp comment "MQTT con TLS"
```

---

## 6. MODIFICACIONES A CAMDETECTOR

### 6.1 Nueva Variable de Entorno (.env)

Agregar a `/home/capw/projects/camdetector/config/.env`:

```bash
# ============================================
# MQTT Configuration
# ============================================
MQTT_ENABLED=true
MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USERNAME=camdetector
MQTT_PASSWORD=tu_contraseña_segura
MQTT_USE_TLS=false

# Duración de activación (segundos)
MQTT_ACTUATOR_DURATION=60

# Topics
MQTT_TOPIC_PERSONA=camdetector/persona
MQTT_TOPIC_REFLECTOR=camdetector/reflector
MQTT_TOPIC_SIRENA=camdetector/sirena
```

### 6.2 Instalar Dependencia

```bash
pip install paho-mqtt
```

### 6.3 Código Python para MQTT

Agregar a `cam_watcher.py`:

```python
# ============================================
# MQTT CLIENT
# ============================================
import paho.mqtt.client as mqtt
import json
import threading

# Configuración MQTT
MQTT_ENABLED = os.getenv('MQTT_ENABLED', 'false').lower() == 'true'
MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
MQTT_USE_TLS = os.getenv('MQTT_USE_TLS', 'false').lower() == 'true'
MQTT_ACTUATOR_DURATION = int(os.getenv('MQTT_ACTUATOR_DURATION', '60'))

MQTT_TOPIC_PERSONA = os.getenv('MQTT_TOPIC_PERSONA', 'camdetector/persona')
MQTT_TOPIC_REFLECTOR = os.getenv('MQTT_TOPIC_REFLECTOR', 'camdetector/reflector')
MQTT_TOPIC_SIRENA = os.getenv('MQTT_TOPIC_SIRENA', 'camdetector/sirena')

# Cliente MQTT global
mqtt_client = None

def mqtt_connect():
    """Conecta al broker MQTT."""
    global mqtt_client
    
    if not MQTT_ENABLED:
        logger.info("MQTT deshabilitado")
        return False
    
    try:
        mqtt_client = mqtt.Client(client_id="camdetector", protocol=mqtt.MQTTv5)
        
        if MQTT_USERNAME:
            mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
        if MQTT_USE_TLS:
            mqtt_client.tls_set()
        
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"MQTT conectado a {MQTT_BROKER}:{MQTT_PORT}")
        return True
        
    except Exception as e:
        logger.error(f"Error conectando MQTT: {e}")
        return False

def mqtt_publish(topic: str, payload: str, retain: bool = False):
    """Publica mensaje MQTT."""
    global mqtt_client
    
    if not MQTT_ENABLED or mqtt_client is None:
        return False
    
    try:
        result = mqtt_client.publish(topic, payload, qos=1, retain=retain)
        result.wait_for_publish()
        logger.debug(f"MQTT publicado: {topic} = {payload}")
        return True
    except Exception as e:
        logger.error(f"Error publicando MQTT: {e}")
        return False

def mqtt_notificar_persona(camera_ip: str, imagen: str, confianza: str):
    """Notifica detección de persona via MQTT."""
    if not MQTT_ENABLED:
        return
    
    from datetime import datetime
    
    # Publicar evento de persona detectada
    payload = json.dumps({
        "camera": camera_ip,
        "image": imagen,
        "confidence": confianza,
        "timestamp": datetime.now().isoformat()
    })
    mqtt_publish(MQTT_TOPIC_PERSONA, payload)
    
    # Activar reflector
    mqtt_publish(MQTT_TOPIC_REFLECTOR, "ON")
    logger.info(f"MQTT: Reflector ON")
    
    # Activar sirena (opcional, descomentar si querés)
    # mqtt_publish(MQTT_TOPIC_SIRENA, "ON")
    # logger.info(f"MQTT: Sirena ON")
    
    # Programar apagado después de MQTT_ACTUATOR_DURATION segundos
    def apagar_actuadores():
        time.sleep(MQTT_ACTUATOR_DURATION)
        mqtt_publish(MQTT_TOPIC_REFLECTOR, "OFF")
        logger.info(f"MQTT: Reflector OFF (después de {MQTT_ACTUATOR_DURATION}s)")
        # mqtt_publish(MQTT_TOPIC_SIRENA, "OFF")
        # logger.info(f"MQTT: Sirena OFF")
    
    threading.Thread(target=apagar_actuadores, daemon=True).start()

def mqtt_disconnect():
    """Desconecta del broker MQTT."""
    global mqtt_client
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logger.info("MQTT desconectado")
```

### 6.4 Integración en el Flujo Existente

Modificar la función que procesa detecciones para incluir MQTT:

```python
# Después de enviar a Telegram, agregar:
if persona_detectada:
    # Notificar por Telegram (existente)
    enviar_telegram_foto(imagen_path, mensaje)
    
    # Notificar por MQTT (nuevo)
    mqtt_notificar_persona(camera_ip, imagen_nombre, confianza)
```

### 6.5 Inicialización

Agregar al inicio del script:

```python
# En main() o al inicio del script:
if MQTT_ENABLED:
    mqtt_connect()
```

---

## 7. CONFIGURACIÓN DE ACTUADORES

### 7.1 Opción A: ESP32-C3 con ESPHome

ESPHome es la forma más fácil de programar los ESP32.

#### Instalación de ESPHome

```bash
# En tu PC (no en el VPS)
pip install esphome
```

#### Configuración YAML para Reflector

Crear `reflector.yaml`:

```yaml
esphome:
  name: reflector-jardin
  friendly_name: Reflector Jardin

esp32:
  board: esp32-c3-devkitm-1
  framework:
    type: arduino

# WiFi de tu casa
wifi:
  ssid: "TU_WIFI_SSID"
  password: "TU_WIFI_PASSWORD"

# Logs por puerto serial
logger:

# API para Home Assistant (opcional)
api:
  encryption:
    key: "genera_una_key_aleatoria_de_32_chars"

# OTA para actualizar por WiFi
ota:
  password: "ota_password"

# Conexión MQTT
mqtt:
  broker: 142.171.248.233
  port: 1883
  username: camdetector
  password: !secret mqtt_password
  
  # Last Will Testament
  birth_message:
    topic: camdetector/reflector/status
    payload: online
  will_message:
    topic: camdetector/reflector/status
    payload: offline
  
  # Suscripción al topic de control
  on_message:
    - topic: camdetector/reflector
      then:
        - lambda: |-
            if (x == "ON") {
              id(relay_reflector).turn_on();
            } else if (x == "OFF") {
              id(relay_reflector).turn_off();
            }

# Relay conectado a GPIO4
switch:
  - platform: gpio
    pin: GPIO4
    name: "Reflector"
    id: relay_reflector
    
    on_turn_on:
      - logger.log: "Reflector encendido"
    on_turn_off:
      - logger.log: "Reflector apagado"
```

#### Configuración YAML para Sirena

Crear `sirena.yaml`:

```yaml
esphome:
  name: sirena-alarma
  friendly_name: Sirena Alarma

esp32:
  board: esp32-c3-devkitm-1
  framework:
    type: arduino

wifi:
  ssid: "TU_WIFI_SSID"
  password: "TU_WIFI_PASSWORD"

logger:

api:
  encryption:
    key: "otra_key_aleatoria_diferente"

ota:
  password: "ota_password"

mqtt:
  broker: 142.171.248.233
  port: 1883
  username: camdetector
  password: !secret mqtt_password
  
  birth_message:
    topic: camdetector/sirena/status
    payload: online
  will_message:
    topic: camdetector/sirena/status
    payload: offline
  
  on_message:
    - topic: camdetector/sirena
      then:
        - lambda: |-
            if (x == "ON") {
              id(relay_sirena).turn_on();
            } else if (x == "OFF") {
              id(relay_sirena).turn_off();
            }

switch:
  - platform: gpio
    pin: GPIO4
    name: "Sirena"
    id: relay_sirena
```

#### Archivo de Secrets

Crear `secrets.yaml` en el mismo directorio:

```yaml
mqtt_password: "tu_contraseña_mqtt"
wifi_ssid: "TU_WIFI_SSID"
wifi_password: "TU_WIFI_PASSWORD"
```

#### Flashear ESP32

```bash
# Compilar y subir (primera vez por USB)
esphome run reflector.yaml

# Después podés actualizar por WiFi (OTA)
esphome run reflector.yaml --device reflector-jardin.local
```

### 7.2 Opción B: Flashear Switch eWeLink con Tasmota

Si preferís usar los switches existentes en lugar de ESP32+Relay.

#### Requisitos

- Switch eWeLink basado en ESP8266/ESP8285 (la mayoría lo son)
- Adaptador USB-TTL (CH340, FT232, CP2102)
- Soldador (para conectar pines TX/RX/GND/3.3V)

#### Proceso

1. **Abrir el switch** (cuidado con 220V, desconectar primero)
2. **Identificar chip**: Si dice ESP8266/ESP8285, se puede flashear
3. **Soldar cables** a los pines de programación
4. **Flashear Tasmota**:
   ```bash
   # Descargar Tasmota
   wget https://github.com/arendst/Tasmota/releases/download/v13.x.x/tasmota.bin
   
   # Flashear (con esptool)
   esptool.py --port /dev/ttyUSB0 write_flash -fs 1MB 0x0 tasmota.bin
   ```
5. **Configurar WiFi y MQTT** desde la interfaz web de Tasmota

#### Configuración MQTT en Tasmota

En la interfaz web del switch:
- Configuration → Configure MQTT
  - Host: `142.171.248.233`
  - Port: `1883`
  - User: `camdetector`
  - Password: `tu_contraseña`
  - Topic: `reflector` (o `sirena`)
  - Full Topic: `camdetector/%topic%/`

#### Comandos Tasmota

Con la configuración anterior, el switch responde a:
- `cmnd/camdetector/reflector/POWER` → `ON` / `OFF`

---

## 8. TOPICS MQTT

### 8.1 Estructura de Topics

```
camdetector/
├── persona              # Evento de detección (JSON)
├── reflector            # Control reflector (ON/OFF)
├── reflector/status     # Estado reflector (online/offline)
├── sirena               # Control sirena (ON/OFF)
├── sirena/status        # Estado sirena (online/offline)
└── status               # Estado general del sistema
```

### 8.2 Payloads

| Topic | Payload | Descripción |
|-------|---------|-------------|
| `camdetector/persona` | `{"camera": "192.168.1.10", "timestamp": "...", "confidence": "alta"}` | Evento de detección |
| `camdetector/reflector` | `ON` / `OFF` | Comando para reflector |
| `camdetector/sirena` | `ON` / `OFF` | Comando para sirena |
| `camdetector/*/status` | `online` / `offline` | Estado de dispositivo |

### 8.3 QoS (Quality of Service)

| Nivel | Garantía | Uso |
|-------|----------|-----|
| QoS 0 | Ninguna (fire and forget) | Datos no críticos |
| **QoS 1** | Al menos una vez | **Recomendado para comandos** |
| QoS 2 | Exactamente una vez | Transacciones críticas |

---

## 9. EJEMPLOS DE CÓDIGO

### 9.1 Publicar desde Python (completo)

```python
import paho.mqtt.client as mqtt
import json
import time
import ssl

class MQTTManager:
    def __init__(self, broker, port, username=None, password=None, use_tls=False):
        self.broker = broker
        self.port = port
        self.client = mqtt.Client(client_id="camdetector", protocol=mqtt.MQTTv5)
        
        if username:
            self.client.username_pw_set(username, password)
        
        if use_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_NONE)
            self.client.tls_insecure_set(True)
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.connected = False
    
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"MQTT conectado a {self.broker}")
            self.connected = True
        else:
            print(f"Error de conexión MQTT: {reason_code}")
    
    def _on_disconnect(self, client, userdata, reason_code, properties):
        print(f"MQTT desconectado: {reason_code}")
        self.connected = False
    
    def connect(self):
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            time.sleep(1)  # Esperar conexión
            return self.connected
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def publish(self, topic, payload, retain=False):
        if not self.connected:
            return False
        result = self.client.publish(topic, payload, qos=1, retain=retain)
        result.wait_for_publish()
        return result.rc == 0
    
    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()


# Uso
if __name__ == "__main__":
    mqtt_mgr = MQTTManager(
        broker="localhost",
        port=1883,
        username="camdetector",
        password="tu_password"
    )
    
    if mqtt_mgr.connect():
        # Publicar detección
        mqtt_mgr.publish("camdetector/persona", json.dumps({
            "camera": "192.168.1.10",
            "timestamp": "2026-01-24T03:15:00"
        }))
        
        # Encender reflector
        mqtt_mgr.publish("camdetector/reflector", "ON")
        
        time.sleep(60)
        
        # Apagar reflector
        mqtt_mgr.publish("camdetector/reflector", "OFF")
        
        mqtt_mgr.disconnect()
```

### 9.2 Suscribirse desde Python

```python
import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"Recibido: {msg.topic} = {msg.payload.decode()}")

client = mqtt.Client()
client.username_pw_set("camdetector", "password")
client.on_message = on_message

client.connect("142.171.248.233", 1883)
client.subscribe("camdetector/#")

client.loop_forever()
```

### 9.3 Probar desde Terminal

```bash
# Terminal 1: Suscribirse
mosquitto_sub -h 142.171.248.233 -u camdetector -P "password" -t "camdetector/#" -v

# Terminal 2: Publicar
mosquitto_pub -h 142.171.248.233 -u camdetector -P "password" \
  -t "camdetector/reflector" -m "ON"

# Deberías ver en Terminal 1:
# camdetector/reflector ON
```

---

## 10. TROUBLESHOOTING

### 10.1 Problemas de Conexión

**Error: Connection refused**
```bash
# Verificar que Mosquitto está corriendo
docker ps | grep mosquitto

# Ver logs
docker logs mosquitto

# Verificar puerto abierto
netstat -tlnp | grep 1883
```

**Error: Not authorized**
```bash
# Verificar credenciales
mosquitto_pub -h localhost -u camdetector -P "password" -t test -m "test"

# Si falla, recrear usuario
docker exec -it mosquitto mosquitto_passwd -b /mosquitto/config/passwd camdetector nueva_password
docker restart mosquitto
```

### 10.2 Problemas con ESP32

**No se conecta al WiFi**
- Verificar SSID y password en el YAML
- Verificar que el ESP32 está cerca del router
- Los ESP32-C3 solo soportan 2.4GHz

**No recibe mensajes MQTT**
- Verificar que el topic es exactamente igual
- Verificar que el broker está accesible desde la red local
- Probar con `mosquitto_sub` desde otra PC en la misma red

### 10.3 Logs Útiles

```bash
# Logs de Mosquitto
docker logs -f mosquitto

# Logs de cam_watcher (buscar MQTT)
sudo journalctl -u cam_watcher -f | grep -i mqtt

# Ver todos los mensajes MQTT
mosquitto_sub -h localhost -u camdetector -P "password" -t "#" -v
```

---

## 11. PRÓXIMOS PASOS

### Orden de implementación recomendado:

1. [ ] **Instalar Mosquitto** en Docker
2. [ ] **Configurar autenticación** básica (usuario/contraseña)
3. [ ] **Probar conexión** con mosquitto_pub/sub
4. [ ] **Instalar paho-mqtt** en el entorno de cam_watcher
5. [ ] **Agregar código MQTT** a cam_watcher.py
6. [ ] **Probar publicación** desde cam_watcher
7. [ ] **Configurar ESP32-C3** con ESPHome
8. [ ] **Conectar relay** al ESP32
9. [ ] **Probar flujo completo**: Detección → MQTT → Relay → Reflector

---

## 12. REFERENCIAS

- [Mosquitto Docker](https://hub.docker.com/_/eclipse-mosquitto)
- [paho-mqtt Python](https://pypi.org/project/paho-mqtt/)
- [ESPHome](https://esphome.io/)
- [Tasmota](https://tasmota.github.io/docs/)
- [MQTT Specification](https://mqtt.org/mqtt-specification/)

---

**Documento creado**: Enero 2026  
**Proyecto**: CamDetector - Sistema de Detección de Personas  
**Estado**: Planificación (no implementado)
