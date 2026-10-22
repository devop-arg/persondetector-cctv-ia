#!/usr/bin/env python3
"""
Diagnóstico pasivo del tráfico TCP del DVR hacia el puerto 8088.

Objetivo:
- Capturar conexiones reales sin tocar el servicio `dvr-alertas`
- Reconstruir el payload completo por conexión TCP
- Comparar lo que habría leído el parser corto actual contra lo que aparece
  al leer la conexión completa

Uso típico:
    sudo python3 scripts/diag_tcp_connections.py --seconds 120
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_DIR = Path("/home/capw/projects/camdetector")
DVR_IP_FILE = BASE_DIR / "data" / "dvr_ip.txt"
DEFAULT_OUT_DIR = BASE_DIR / "data" / "tcp_diag"


PACKET_RE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2}\.\d+)\s+\S+\s+\S+\s+IP\s+"
    r"(?P<src_ip>\d+\.\d+\.\d+\.\d+)\.(?P<src_port>\d+)\s+>\s+"
    r"(?P<dst_ip>\d+\.\d+\.\d+\.\d+)\.(?P<dst_port>\d+):\s+"
    r".*?length\s+(?P<length>\d+)"
)


@dataclass
class SegmentoTCP:
    seq_start: int
    payload: bytes


@dataclass
class ConexionTCP:
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    first_ts: str
    last_ts: str
    packets: int = 0
    total_payload_bytes: int = 0
    segmentos: List[SegmentoTCP] = field(default_factory=list)

    def add_segment(self, ts: str, seq_start: int, payload: bytes) -> None:
        self.last_ts = ts
        self.packets += 1
        self.total_payload_bytes += len(payload)
        if payload:
            self.segmentos.append(SegmentoTCP(seq_start=seq_start, payload=payload))

    def reassemble(self) -> bytes:
        if not self.segmentos:
            return b""
        data = bytearray()
        expected = None
        for seg in sorted(self.segmentos, key=lambda s: s.seq_start):
            if expected is None:
                expected = seg.seq_start
            if seg.seq_start > expected:
                data.extend(b"\x00" * (seg.seq_start - expected))
                expected = seg.seq_start
            if seg.seq_start < expected:
                overlap = expected - seg.seq_start
                chunk = seg.payload[overlap:]
            else:
                chunk = seg.payload
            data.extend(chunk)
            expected += len(chunk)
        return bytes(data)


def load_dvr_ip() -> str:
    if DVR_IP_FILE.exists():
        return DVR_IP_FILE.read_text().strip()
    raise FileNotFoundError(f"No existe {DVR_IP_FILE}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnóstico pasivo TCP del DVR")
    parser.add_argument("--seconds", type=int, default=120, help="Duración de captura")
    parser.add_argument("--port", type=int, default=8088, help="Puerto destino")
    parser.add_argument("--ip", default=None, help="IP del DVR; por defecto usa dvr_ip.txt")
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Directorio para binarios y resumen",
    )
    return parser.parse_args()


def seq_start_from_header(header_line: str) -> Optional[int]:
    match = re.search(r"seq\s+(\d+):(\d+)", header_line)
    if match:
        return int(match.group(1))
    match = re.search(r"seq\s+(\d+)", header_line)
    if match:
        return int(match.group(1))
    return None


def extract_payload_from_frame(frame_bytes: bytes) -> bytes:
    if len(frame_bytes) < 20:
        return b""
    version = frame_bytes[0] >> 4
    if version != 4:
        return b""
    ip_header_len = (frame_bytes[0] & 0x0F) * 4
    if len(frame_bytes) < ip_header_len + 20:
        return b""
    tcp_offset_byte = frame_bytes[ip_header_len + 12]
    tcp_header_len = ((tcp_offset_byte >> 4) & 0x0F) * 4
    payload_offset = ip_header_len + tcp_header_len
    if payload_offset > len(frame_bytes):
        return b""
    return frame_bytes[payload_offset:]


def parse_hex_line(line: str) -> bytes:
    if "0x" not in line:
        return b""
    _, hex_part = line.split(":", 1)
    hex_words = []
    for token in hex_part.strip().split():
        if len(token) == 4 and all(ch in "0123456789abcdefABCDEF" for ch in token):
            hex_words.append(token)
        else:
            break
    return bytes.fromhex("".join(hex_words))


def parse_tcpdump_stream(stream, dvr_ip: str, dst_port: int) -> Dict[Tuple[str, int], ConexionTCP]:
    conexiones: Dict[Tuple[str, int], ConexionTCP] = {}
    current_header = None
    current_frame = bytearray()

    def flush_packet() -> None:
        nonlocal current_header, current_frame
        if not current_header:
            return
        match = PACKET_RE.match(current_header)
        if not match:
            current_header = None
            current_frame = bytearray()
            return

        src_ip = match.group("src_ip")
        src_port = int(match.group("src_port"))
        dst = int(match.group("dst_port"))
        length = int(match.group("length"))
        if src_ip != dvr_ip or dst != dst_port or length <= 0:
            current_header = None
            current_frame = bytearray()
            return

        payload = extract_payload_from_frame(bytes(current_frame))
        seq_start = seq_start_from_header(current_header)
        if seq_start is None:
            current_header = None
            current_frame = bytearray()
            return

        key = (src_ip, src_port)
        conn = conexiones.get(key)
        if conn is None:
            conn = ConexionTCP(
                src_ip=src_ip,
                src_port=src_port,
                dst_ip=match.group("dst_ip"),
                dst_port=dst,
                first_ts=match.group("ts"),
                last_ts=match.group("ts"),
            )
            conexiones[key] = conn
        conn.add_segment(match.group("ts"), seq_start, payload)
        current_header = None
        current_frame = bytearray()

    for raw_line in stream:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        if line.startswith("\t0x"):
            current_frame.extend(parse_hex_line(line))
            continue
        flush_packet()
        current_header = line
        current_frame = bytearray()

    flush_packet()
    return conexiones


def extract_current_short_parser_channel(data: bytes) -> Optional[int]:
    if len(data) >= 9 and len(data) < 28 and data[4:8] == b"\xff\x00\x02\x59":
        canal = data[8]
        if 1 <= canal <= 8:
            return canal
    return None


def extract_small_u32_fields(data: bytes, start_offset: int = 0) -> List[Dict[str, int]]:
    fields: List[Dict[str, int]] = []
    aligned_start = start_offset + ((4 - (start_offset % 4)) % 4)
    for offset in range(aligned_start, len(data) - 3, 4):
        value = int.from_bytes(data[offset:offset + 4], "big")
        if 1 <= value <= 16:
            fields.append({"offset": offset, "value": value})
    return fields


def detect_signature_offsets(data: bytes, signature: bytes) -> List[int]:
    offsets = []
    start = 0
    while True:
        idx = data.find(signature, start)
        if idx == -1:
            return offsets
        offsets.append(idx)
        start = idx + 1


def summarize_connection(conn: ConexionTCP) -> Dict:
    full_data = conn.reassemble()
    short_channel = extract_current_short_parser_channel(full_data[:11])
    return {
        "src_ip": conn.src_ip,
        "src_port": conn.src_port,
        "first_ts": conn.first_ts,
        "last_ts": conn.last_ts,
        "packets": conn.packets,
        "payload_bytes": len(full_data),
        "short_parser_channel": short_channel,
        "short_prefix_hex": full_data[:11].hex(),
        "sig_ff000259_offsets": detect_signature_offsets(full_data, b"\xff\x00\x02\x59"),
        "sig_ff001200_offsets": detect_signature_offsets(full_data, b"\xff\x00\x12\x00"),
        "tail_hex": full_data[-64:].hex(),
        "tail_small_u32_fields": extract_small_u32_fields(full_data, max(0, len(full_data) - 64)),
        "all_small_u32_fields": extract_small_u32_fields(full_data),
    }


def run_capture(args: argparse.Namespace) -> List[Dict]:
    dvr_ip = args.ip or load_dvr_ip()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tcpdump_cmd = [
        "tcpdump", "-nn", "-l", "-i", "any", "-s", "0", "-X",
        "host", dvr_ip, "and", "port", str(args.port),
    ]
    proc = subprocess.Popen(
        tcpdump_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        deadline = time.time() + args.seconds
        lines = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            lines.append(line)
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    conexiones = parse_tcpdump_stream(lines, dvr_ip, args.port)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resumen_path = out_dir / f"resumen_{timestamp}.json"
    resumen = []
    for idx, conn in enumerate(sorted(conexiones.values(), key=lambda c: (c.first_ts, c.src_port)), start=1):
        full_data = conn.reassemble()
        bin_path = out_dir / f"conn_{timestamp}_{idx:03d}_{conn.src_port}.bin"
        bin_path.write_bytes(full_data)
        item = summarize_connection(conn)
        item["bin_path"] = str(bin_path)
        resumen.append(item)

    resumen_path.write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(f"IP DVR: {dvr_ip}")
    print(f"Conexiones analizadas: {len(resumen)}")
    print(f"Resumen JSON: {resumen_path}")
    print()
    for item in resumen:
        short_ch = f"A{item['short_parser_channel']}" if item["short_parser_channel"] else "-"
        tail_fields = ",".join(
            f"{field['offset']}:{field['value']}"
            for field in item["tail_small_u32_fields"]
        ) or "-"
        print(
            f"{item['first_ts']} | sport={item['src_port']} | bytes={item['payload_bytes']} | "
            f"parser_corto={short_ch} | tail_u32={tail_fields} | ff000259={item['sig_ff000259_offsets']} | "
            f"ff001200={item['sig_ff001200_offsets']}"
        )
    return resumen


def main() -> int:
    args = parse_args()
    try:
        run_capture(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
