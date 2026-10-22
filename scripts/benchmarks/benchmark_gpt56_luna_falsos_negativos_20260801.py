#!/usr/bin/env python3
"""Prueba de falsos negativos de gpt-5.6-luna sobre casos POSITIVOS reales.

Ground truth = vision (mimo-v2.5) sobre data/dvr_sospechosas. Se testean las
imágenes donde hay persona a pie real, con el prompt exacto de producción, y
se mide cuántas veces el modelo responde NO (falso negativo) y latencia.
"""
import base64, json, statistics, time, sys
from pathlib import Path
import requests

PROJECT_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_DIR / "config" / ".env"
IMAGE_DIR = PROJECT_DIR / "data" / "dvr_sospechosas"
OUTPUT_DIR = PROJECT_DIR / "data"
MODEL = "gpt-5.6-luna"
INTER_REQUEST_DELAY = 1.5
MAX_TOKENS = 100

PROMPT = """Analiza esta imagen de una cámara de seguridad. Tu objetivo es detectar únicamente personas a pie.

IMPORTANTE: Responde exactamente en este formato, sin agregar texto adicional:

PERSONA: SI o NO

Reglas estrictas:
- Si hay una o más personas a pie visibles responde PERSONA: SI
- Para cualquier otra cosa que veas, responde PERSONA: NO
"""

def env_value(name):
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""

def parse_persona(text):
    for raw in text.upper().splitlines():
        line = raw.strip()
        if line.startswith("PERSONA:"):
            return line.split(":",1)[1].strip() in {"SI","SÍ","YES"}
    return None

def query(filename, api_key):
    path = IMAGE_DIR / filename
    payload = {"model": MODEL,
               "messages": [{"role":"user","content":[
                   {"type":"text","text":PROMPT},
                   {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+base64.b64encode(path.read_bytes()).decode("ascii")}}]}],
               "max_tokens": MAX_TOKENS}
    headers = {"Authorization":f"Bearer {api_key}","Content-Type":"application/json",
               "HTTP-Referer":"https://camdetector.local","X-Title":"CamDetector-A3-FN-gpt56"}
    started = time.time()
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=90)
        el = round(time.time()-started,3)
        try: data = r.json()
        except ValueError: data = {}
        if r.status_code != 200 or "error" in data:
            return {"model":MODEL,"image":filename,"status_code":r.status_code,"error":data.get("error",r.text[:400]),"time_seconds":el}
        raw = data["choices"][0]["message"].get("content","")
        persona = parse_persona(raw)
        usage = data.get("usage",{})
        return {"model":MODEL,"image":filename,"status_code":r.status_code,"persona":persona,
                "false_negative": persona is False, "raw":raw, "time_seconds":el,
                "tokens_total": usage.get("total_tokens",0)}
    except Exception as e:
        return {"model":MODEL,"image":filename,"status_code":None,"error":str(e),"time_seconds":round(time.time()-started,3)}

def main():
    api_key = env_value("OPENROUTER_API_KEY")
    if not api_key: sys.exit("no api key")
    pos = [l for l in Path("/tmp/opencode/gt_positivos_10.txt").read_text().splitlines() if l.strip()]
    print(f"Caso positivos (persona real): {len(pos)} | prompt produccion | delay {INTER_REQUEST_DELAY}s")
    results = []
    for i, fn in enumerate(pos, 1):
        res = query(fn, api_key)
        results.append(res)
        if "error" in res:
            print(f"[{i}/{len(pos)}] {fn} -> ERROR HTTP {res['status_code']}: {res['error']}")
        else:
            got = "SI" if res["persona"] else "NO"
            tag = "FALSO NEGATIVO" if res["false_negative"] else "correcto SI"
            print(f"[{i}/{len(pos)}] {fn} -> {got} ({tag}) | {res['time_seconds']:.3f}s")
        time.sleep(INTER_REQUEST_DELAY)
    valid = [r for r in results if "false_negative" in r]
    times = [r["time_seconds"] for r in valid]
    fn_atos = sum(r["false_negative"] for r in valid)
    summ = {"model":MODEL,"provider":"openrouter","requests":len(results),"successful":len(valid),
            "false_negatives":fn_atos,"false_negative_rate":fn_atos/len(valid) if valid else None,
            "latency_avg_seconds":statistics.mean(times) if times else None,
            "latency_median_seconds":statistics.median(times) if times else None}
    print("\n=== RESUMEN ===")
    print(json.dumps(summ, ensure_ascii=False))
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUTPUT_DIR / f"benchmark_gpt56_luna_falsos_negativos_{ts}.json"
    out.write_text(json.dumps({"timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "ground_truth":"PERSONA_VISUAL_vision","model":MODEL,"provider":"openrouter",
        "prompt":PROMPT,"summary":summ,"results":results}, indent=2, ensure_ascii=False))
    print(f"JSON: {out}")

if __name__ == "__main__":
    main()
