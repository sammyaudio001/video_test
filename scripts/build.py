"""Arma el Short 9:16 de punta a punta a partir de la voz, sus tiempos y las imágenes.

Entradas (por defecto, relativas a la raíz del repo):
  audio/voz.mp3 + audio/voz_alignment.json   (scripts/tts.py)
  audio/musica.wav + audio/riser.wav         (scripts/music.py)
  guion/escenas.json, guion/tts_subs.json    (sustituciones texto-pantalla -> texto-voz)
  imagenes/escena_XX.png                     (una por escena; la 18 reutiliza la 1)
  clips/escena_XX_anim.mp4                   (opcional: si existe, reemplaza el Ken Burns)

Salida: final/short_es.mp4 (+ clips/escena_XX.mp4, final/subs_es.ass, final/build_report.json)

Orden de render (reglas de video-use): clip por escena -> concat sin recodificar ->
subtítulos al final en una sola codificación.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FPS = 30
W, H = 1080, 1920
LEAD = 0.08          # la imagen entra un pelo antes que la palabra
HEAD_PAD = 0.05      # silencio que se deja antes de la primera palabra
TAIL_PAD = 0.25      # y después de "una palabra:" (bucle)
MAX_DUR, MIN_DUR = 50.0, 45.0
DARK = -0.22         # brillo en la unión del bucle (fin de la 18 = inicio de la 1)
KEYWORDS = {"croatoan", "cruz", "vacío", "1590", "tormenta", "jamás", "nadie",
            "ciento", "quince", "palabra", "palabra:"}
HILITE = "&H004AC9FF&"   # ámbar (#FFC94A) en BGR de ASS


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def probe_dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(p)]).stdout.strip())


def lufs(p: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af",
                          "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    summ = err[err.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))


def ebur(p: Path) -> dict:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af",
                          "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    summ = err[err.rfind("Summary:"):]
    return {"I_LUFS": float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1)),
            "LRA_LU": float(re.search(r"LRA:\s+(-?[\d.]+) LU", summ).group(1)),
            "TruePeak_dBTP": float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ).group(1))}


# ---------------------------------------------------------------- tiempos

def words_from_alignment(al: dict) -> list[dict]:
    chars, st, en = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    words, cur = [], None
    for c, s, e in zip(chars, st, en):
        if c.isspace():
            if cur:
                words.append(cur)
            cur = None
            continue
        if cur is None:
            cur = {"text": "", "start": s, "end": e}
        cur["text"] += c
        cur["end"] = e
    if cur:
        words.append(cur)
    return words


def tts_text(text: str, subs: dict) -> str:
    for a, b in subs.items():
        text = text.replace(a, b)
    return text


def scene_word_spans(scenes, words, subs):
    spans, i = [], 0
    for s in scenes:
        n = len(tts_text(s["text_es"], subs).split())
        spans.append((i, i + n))
        i += n
    if i != len(words):
        raise SystemExit(f"El audio tiene {len(words)} palabras y el guion {i}: revisa tts_subs.json")
    return spans


def display_word_times(scenes, words, spans):
    """Palabras de pantalla (con 'Croatoan', '1590') con tiempos tomados del audio."""
    out = []
    for s, (a, b) in zip(scenes, spans):
        disp = s["text_es"].split()
        tw = words[a:b]
        if len(disp) == len(tw):
            out += [{"text": d, "start": w["start"], "end": w["end"]} for d, w in zip(disp, tw)]
            continue
        n, m = len(tw), len(disp)
        for j, d in enumerate(disp):
            lo, hi = int(j * n / m), max(int((j + 1) * n / m), int(j * n / m) + 1)
            out.append({"text": d, "start": tw[lo]["start"], "end": tw[hi - 1]["end"]})
    return out


# ---------------------------------------------------------------- audio

def prepare_voice(voice: Path, words, work: Path, max_tempo=1.06):
    t0 = max(0.0, words[0]["start"] - HEAD_PAD)
    t1 = words[-1]["end"] + TAIL_PAD
    dur = t1 - t0
    tempo = 1.0
    if dur > MAX_DUR:
        tempo = dur / (MAX_DUR - 0.2)
        if tempo > max_tempo:
            raise SystemExit(f"La voz dura {dur:.2f}s: haría falta acelerar x{tempo:.3f}. Regenerar con más speed.")
    af = f"atrim={t0:.3f}:{t1:.3f},asetpts=PTS-STARTPTS"
    if tempo != 1.0:
        af += f",atempo={tempo:.4f}"
    out = work / "voz_proc.wav"
    sh(["ffmpeg", "-y", "-i", str(voice), "-af", af, "-ar", "48000", "-ac", "2", str(out)])
    remap = lambda t: (t - t0) / tempo
    return out, remap, probe_dur(out), tempo, dur


def mix(voice_wav: Path, music: Path, riser: Path, dur: float, out: Path, work: Path):
    iv, im, ir = lufs(voice_wav), lufs(music), lufs(riser)
    g_music = (iv - 18.0) - im       # cama a ~-18 LU bajo la voz
    g_riser = (iv - 12.0) - ir
    rdur = probe_dur(riser)
    rstart = max(0.0, dur - rdur)
    fc = (
        f"[1:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={g_music:.2f}dB[m];"
        f"[0:a]asplit=2[v][sc];"
        # ducking: la voz comprime la música ~4-6 dB mientras habla
        f"[m][sc]sidechaincompress=threshold=0.03:ratio=5:attack=30:release=450:makeup=1[md];"
        f"[2:a]volume={g_riser:.2f}dB,adelay={int(rstart * 1000)}|{int(rstart * 1000)},"
        f"atrim=0:{dur:.3f}[r];"
        f"[v][md][r]amix=inputs=3:normalize=0:duration=first[mix]"
    )
    pre = work / "mix_pre.wav"
    sh(["ffmpeg", "-y", "-i", str(voice_wav), "-i", str(music), "-i", str(riser),
        "-filter_complex", fc, "-map", "[mix]", "-ar", "48000", str(pre)])
    # Limitador previo para que loudnorm pueda trabajar en modo lineal (sin bombeo),
    # luego loudnorm en dos pasadas: -14 LUFS. TP objetivo -1.5 para que tras el AAC quede <= -1 dBTP.
    gain = -14.0 - lufs(pre)
    lim_db = min(-1.0, -2.5 - gain)
    pre_l = work / "mix_lim.wav"
    sh(["ffmpeg", "-y", "-i", str(pre), "-af",
        f"aresample=192000,alimiter=limit={10 ** (lim_db / 20):.4f}:attack=3:release=80:level=false,aresample=48000",
        str(pre_l)])
    pre = pre_l
    ln = "loudnorm=I=-14:TP=-1.5:LRA=11"
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(pre), "-af",
                          ln + ":print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = json.loads(err[err.rfind("{"):err.rfind("}") + 1])
    ln2 = (f"{ln}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
           f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    sh(["ffmpeg", "-y", "-i", str(pre), "-af", ln2 + ",aresample=48000", "-ar", "48000", str(out)])
    return {"voice_LUFS": iv, "music_gain_dB": round(g_music, 2), "riser_gain_dB": round(g_riser, 2),
            "riser_start_s": round(rstart, 2), "limiter_dB": round(lim_db, 2), "loudnorm_pass1": m}


# ---------------------------------------------------------------- video

def kenburns(img: Path, nframes: int, move: str, out: Path, dark_in=False, dark_out=False):
    n1 = max(nframes - 1, 1)
    p = f"(on/{n1})"
    zmax = 1.12
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if move == "zoom_in":
        z, x, y = f"1+{zmax - 1}*{p}", cx, cy
    elif move in ("zoom_out", "zoom_out_dark"):
        zm = 1.18 if move == "zoom_out_dark" else zmax
        z, x, y = f"{zm}-{zm - 1}*{p}", cx, cy
    elif move == "pan_right":
        z, x, y = str(zmax), f"(iw-iw/zoom)*{p}", cy
    elif move == "pan_left":
        z, x, y = str(zmax), f"(iw-iw/zoom)*(1-{p})", cy
    elif move == "pan_up":
        z, x, y = str(zmax), cx, f"(ih-ih/zoom)*(1-{p})"
    elif move == "pan_down":
        z, x, y = str(zmax), cx, f"(ih-ih/zoom)*{p}"
    else:
        raise ValueError(move)
    dur = nframes / FPS
    bright = "0"
    if dark_in:
        bright = f"{DARK}*max(0\\,1-t/1.0)"
    if dark_out:
        bright = f"{DARK}*min(1\\,max(0\\,(t-{dur - 1.4:.3f})/1.4))"
    vf = (f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,crop={2 * W}:{2 * H},setsar=1,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},"
          f"eq=brightness='{bright}':eval=frame,vignette=PI/5,noise=alls=5:allf=t,format=yuv420p")
    sh(["ffmpeg", "-y", "-framerate", str(FPS), "-loop", "1", "-i", str(img), "-vf", vf,
        "-frames:v", str(nframes), "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-r", str(FPS), "-an", str(out)])


def anim_clip(src: Path, nframes: int, out: Path):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
          f"vignette=PI/5,noise=alls=5:allf=t,format=yuv420p")
    sh(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-vf", vf, "-frames:v", str(nframes),
        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-r", str(FPS), "-an", str(out)])


# ---------------------------------------------------------------- subtítulos

def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def chunk(words):
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_punct = w["text"][-1] in ".,:;?!"
        gap = nxt["start"] - w["end"] if nxt else 0
        nxt_kw = nxt and nxt["text"].strip(".,:;?!¿¡").lower() == "croatoan"
        if nxt is None or end_punct or gap > 0.3 or len(cur) >= 3 or nxt_kw or \
                (len(cur) >= 2 and w["end"] - cur[0]["start"] > 0.45):
            chunks.append(cur)
            cur = []
    return chunks


def build_ass(words, total, out: Path):
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Montserrat Black,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,2,80,80,640,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    chunks = chunk(words)
    lines = []
    for k, c in enumerate(chunks):
        a = c[0]["start"]
        b = chunks[k + 1][0]["start"] if k + 1 < len(chunks) else total
        if b - c[-1]["end"] > 0.6:
            b = c[-1]["end"] + 0.25
        a = 0.0 if k == 0 else a
        parts = []
        for w in c:
            txt = w["text"].upper()
            key = w["text"].strip(".,:;?!¿¡").lower()
            if key == "croatoan":
                parts.append(r"{\c" + HILITE + r"\fscx115\fscy115\t(0,180,\fscx100\fscy100)}" + txt + r"{\r}")
            elif key in KEYWORDS:
                parts.append(r"{\c" + HILITE + "}" + txt + r"{\r}")
            else:
                parts.append(txt)
        # pequeño "pop" de entrada en cada bloque
        pop = r"{\fad(40,0)\fscx92\fscy92\t(0,90,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Main,,0,0,0,,{pop}{' '.join(parts)}")
    out.write_text(hdr + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", type=Path, default=ROOT / "audio")
    ap.add_argument("--images-dir", type=Path, default=ROOT / "imagenes")
    ap.add_argument("--clips-dir", type=Path, default=ROOT / "clips")
    ap.add_argument("--out", type=Path, default=ROOT / "final/short_es.mp4")
    ap.add_argument("--no-write-scenes", action="store_true")
    a = ap.parse_args()
    T = {}
    t_all = time.time()

    data = json.loads((ROOT / "guion/escenas.json").read_text(encoding="utf-8"))
    scenes = data["scenes"]
    subs_path = ROOT / "guion/tts_subs.json"
    subs = json.loads(subs_path.read_text(encoding="utf-8")) if subs_path.exists() else {}
    al = json.loads((a.audio_dir / "voz_alignment.json").read_text(encoding="utf-8"))
    words = words_from_alignment(al)
    spans = scene_word_spans(scenes, words, subs)
    work = a.clips_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)

    t = time.time()
    voice_wav, remap, total, tempo, raw_dur = prepare_voice(a.audio_dir / "voz.mp3", words, work)
    for w in words:
        w["start"], w["end"] = remap(w["start"]), remap(w["end"])
    T["voz_recorte_s"] = round(time.time() - t, 1)

    # cortes de escena en frames (acumulados: sin deriva)
    starts = [0.0] + [max(0.0, words[s0]["start"] - LEAD) for s0, _ in spans[1:]]
    bounds = [round(x * FPS) for x in starts] + [round(total * FPS)]
    t = time.time()
    clip_paths = []
    for i, s in enumerate(scenes):
        nf = bounds[i + 1] - bounds[i]
        s["start_s"], s["end_s"] = round(bounds[i] / FPS, 3), round(bounds[i + 1] / FPS, 3)
        out = a.clips_dir / f"escena_{s['id']:02d}.mp4"
        anim = a.clips_dir / f"escena_{s['id']:02d}_anim.mp4"
        if anim.exists():
            anim_clip(anim, nf, out)
        else:
            img = a.images_dir / Path(s["image"]).name
            kenburns(img, nf, s["ffmpeg_move"], out, dark_in=(i == 0), dark_out=(i == len(scenes) - 1))
        clip_paths.append(out)
        print(f"  escena {s['id']:02d}  {s['start_s']:6.2f}-{s['end_s']:6.2f}  ({nf / FPS:.2f}s)  {s['ffmpeg_move']}"
              f"{'  [anim]' if anim.exists() else ''}")
    T["clips_s"] = round(time.time() - t, 1)

    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in clip_paths))
    base = work / "base.mp4"
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base)])

    t = time.time()
    mixed = work / "mix_final.wav"
    mixinfo = mix(voice_wav, a.audio_dir / "musica.wav", a.audio_dir / "riser.wav", total, mixed, work)
    T["mezcla_master_s"] = round(time.time() - t, 1)

    disp = display_word_times(scenes, words, spans)
    ass = a.out.with_name(a.out.stem.replace("short", "subs") + ".ass")
    ncues = build_ass(disp, total, ass)

    t = time.time()
    fontsdir = "/usr/share/fonts/opentype/montserrat"
    sh(["ffmpeg", "-y", "-i", str(base), "-i", str(mixed),
        "-vf", f"subtitles='{ass}':fontsdir='{fontsdir}'",  # subtítulos AL FINAL
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(a.out)])
    T["render_final_s"] = round(time.time() - t, 1)

    meas = ebur(a.out)
    rep = {"duracion_s": round(probe_dur(a.out), 3), "voz_original_s": round(raw_dur, 3),
           "atempo": round(tempo, 4), "subtitulos": ncues, **meas, "mezcla": mixinfo,
           "tiempos_s": {**T, "total": round(time.time() - t_all, 1)},
           "escenas": [{k: s[k] for k in ("id", "start_s", "end_s")} for s in scenes]}
    a.out.with_name("build_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    if not a.no_write_scenes:
        (ROOT / "guion/escenas.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("duracion_s", "I_LUFS", "TruePeak_dBTP", "LRA_LU", "atempo")}))
    if not (MIN_DUR <= rep["duracion_s"] <= MAX_DUR):
        print(f"AVISO: duración {rep['duracion_s']}s fuera de {MIN_DUR}-{MAX_DUR}s")


if __name__ == "__main__":
    main()
