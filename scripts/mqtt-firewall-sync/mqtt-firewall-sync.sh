#!/bin/bash
# =============================================================================
# mqtt-firewall-sync.sh
# =============================================================================
# Restringe el acceso al broker MQTT mosquitto (puertos 1883, 9001) en docker
# para que solo acepte conexiones desde:
#   - loopback (127.0.0.1)
#   - red docker (172.16.0.0/12)
#   - IP pública de la casa (leída de CamDetector dvr_ip.txt)
#
# Aplica las reglas en la chain DOCKER-USER (que docker respeta antes de su NAT).
# Idempotente: elimina reglas previas etiquetadas y reinserta.
#
# Disparado por:
#   - mqtt-firewall-sync.path  (on-change de dvr_ip.txt)
#   - mqtt-firewall-sync.timer (cada 5 min como red de seguridad)
#
# Logs: journalctl -u mqtt-firewall-sync.service
# =============================================================================

set -euo pipefail

DVR_IP_FILE="/home/capw/projects/camdetector/data/dvr_ip.txt"
MQTT_PORTS=(1883 9001)
TAG="mqtt-fw-sync"
DOCKER_NET="172.16.0.0/12"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- 1. Leer IP de casa ------------------------------------------------------
if [[ ! -f "$DVR_IP_FILE" ]]; then
    log "ERROR: $DVR_IP_FILE no existe. Abortando."
    exit 1
fi

CASA_IP=$(tr -d '[:space:]' < "$DVR_IP_FILE")

if ! [[ "$CASA_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    log "ERROR: IP de casa inválida en $DVR_IP_FILE: '$CASA_IP'. Abortando."
    exit 1
fi

log "IP de casa: $CASA_IP"

# --- 2. Limpiar reglas previas ----------------------------------------------
# Lista todas las reglas en DOCKER-USER que contengan nuestro TAG y bórralas.
# Iteramos en reverso por número de línea para no romper los índices.
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    rule_num=$(echo "$line" | awk '{print $1}')
    [[ "$rule_num" =~ ^[0-9]+$ ]] && iptables -D DOCKER-USER "$rule_num"
done < <(iptables -L DOCKER-USER --line-numbers -n | grep -F "$TAG" | tac)

# --- 3. Insertar reglas nuevas (orden: ACCEPT primero, DROP al final) -------
# Insertamos al inicio de la chain para que se evalúen antes que el RETURN
# implícito de Docker.
#
# Orden de inserción (insertamos siempre en pos 1, por lo que la última
# insertada queda primera):
#   1. DROP final  (cualquier IP no permitida)   ← evaluado último
#   2. ACCEPT casa (IP dinámica de CamDetector)
#   3. ACCEPT red docker
#   4. ACCEPT loopback                            ← evaluado primero

for PORT in "${MQTT_PORTS[@]}"; do
    # DROP catch-all
    iptables -I DOCKER-USER 1 -p tcp --dport "$PORT" \
        -m comment --comment "${TAG}:drop:${PORT}" -j DROP

    # ACCEPT desde IP de casa
    iptables -I DOCKER-USER 1 -p tcp --dport "$PORT" -s "$CASA_IP/32" \
        -m comment --comment "${TAG}:casa:${PORT}" -j ACCEPT

    # ACCEPT desde red docker (HA → mosquitto)
    iptables -I DOCKER-USER 1 -p tcp --dport "$PORT" -s "$DOCKER_NET" \
        -m comment --comment "${TAG}:docker:${PORT}" -j ACCEPT

    # ACCEPT desde loopback
    iptables -I DOCKER-USER 1 -p tcp --dport "$PORT" -s 127.0.0.0/8 \
        -m comment --comment "${TAG}:loopback:${PORT}" -j ACCEPT
done

log "Firewall MQTT sincronizado. IP de casa: $CASA_IP"
log "Reglas activas:"
iptables -L DOCKER-USER -n --line-numbers | grep -F "$TAG" | sed 's/^/  /'

exit 0
