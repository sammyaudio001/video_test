"""Voz con ElevenLabs (una sola pasada) + tiempos por carácter.

Lee la key de ELEVENLABS_API_KEY (variable de entorno) y la voz de ELEVENLABS_VOICE_ID.
Nunca imprime ni guarda la key.

Salida: audio/voz.mp3 y audio/voz_alignment.json

Uso: python scripts/tts.py [--text guion/es_tts.txt] [--speed 1.07] [--stability 0.35]
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=str(ROOT / "guion/es_tts.txt"))
    ap.add_argument("--voice", default=os.environ.get("ELEVENLABS_VOICE_ID", ""))
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--speed", type=float, default=1.07)
    ap.add_argument("--stability", type=float, default=0.35)
    ap.add_argument("--similarity", type=float, default=0.8)
    ap.add_argument("--style", type=float, default=0.15)
    ap.add_argument("--out", default=str(ROOT / "audio/voz.mp3"))
    a = ap.parse_args()

    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        sys.exit("Falta ELEVENLABS_API_KEY en el entorno.")
    if not a.voice:
        sys.exit("Falta ELEVENLABS_VOICE_ID (o --voice).")

    # Saltos de línea -> espacio: una sola pasada continua; el texto termina en ':' (final suspendido).
    text = " ".join(Path(a.text).read_text(encoding="utf-8").split())
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{a.voice}/with-timestamps",
        params={"output_format": "mp3_44100_192"},
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": a.model,
            "language_code": "es",
            "voice_settings": {
                "stability": a.stability,
                "similarity_boost": a.similarity,
                "style": a.style,
                "use_speaker_boost": True,
                "speed": a.speed,
            },
        },
        timeout=300,
    )
    if r.status_code != 200:
        sys.exit(f"ElevenLabs devolvió {r.status_code}: {r.text[:300]}")
    d = r.json()
    Path(a.out).write_bytes(base64.b64decode(d["audio_base64"]))
    al = d.get("normalized_alignment") or d["alignment"]
    Path(a.out).with_name("voz_alignment.json").write_text(
        json.dumps({"text": text, **al}, ensure_ascii=False, indent=1), encoding="utf-8")
    cost = r.headers.get("character-cost") or r.headers.get("x-character-count")
    print(f"ok {a.out}  caracteres facturados: {cost or len(text)}")


if __name__ == "__main__":
    main()
