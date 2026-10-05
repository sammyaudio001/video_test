"""Plan B de imágenes: Pollinations (gratis, sin key). Genera 768x1344 (9:16) desde guion/escenas.json.

Uso:
  python scripts/images.py --only 1        # prueba: una sola imagen
  python scripts/images.py                 # todas las que falten
"""
import argparse
import json
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def fetch(prompt, neg, out, seed, tries=2):
    q = urllib.parse.quote(prompt.replace(" --ar 9:16", ""))
    url = (f"https://image.pollinations.ai/prompt/{q}?width=768&height=1344&seed={seed}"
           f"&model=flux&nologo=true&negative_prompt={urllib.parse.quote(neg)}")
    last = None
    for _ in range(tries):  # regla: dos fallos seguidos => parar
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                out.write_bytes(r.content)
                return
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5)
    raise SystemExit(f"Pollinations falló dos veces en {out.name}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    scenes = json.loads((ROOT / "guion/escenas.json").read_text())["scenes"]
    for s in scenes:
        if s["prompt_en"] is None or (a.only and s["id"] not in a.only):
            continue
        out = ROOT / s["image"]
        if out.exists() and not a.force:
            continue
        t0 = time.time()
        fetch(s["prompt_en"], s["negative_prompt"], out, seed=1000 + s["id"])
        print(f"escena {s['id']:02d} -> {out.name} ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
