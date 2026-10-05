"""Música de fondo oscura + riser, sintetizadas desde cero (sin samples => libre de derechos).

Uso: python scripts/music.py --dur 50 --out audio/musica.wav --riser audio/riser.wav
"""
import argparse
import wave

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000


def lowpass(x, fc, order=4):
    return sosfilt(butter(order, fc, "low", fs=SR, output="sos"), x)


def bandpass(x, lo, hi, order=2):
    return sosfilt(butter(order, [lo, hi], "band", fs=SR, output="sos"), x)


def saw(f, t, phase=0.0):
    return 2 * ((f * t + phase) % 1.0) - 1


def write_wav(path, stereo):
    stereo = stereo / (np.max(np.abs(stereo)) + 1e-9) * 0.7  # pico -3 dBFS
    pcm = (stereo * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def music(dur, seed=7):
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    L = np.zeros_like(t)
    R = np.zeros_like(t)

    # 1) Drone grave en Re (D1 + quinta), sierras desafinadas filtradas que "respiran"
    for f, amp in [(36.71, 1.0), (55.0, 0.55), (73.42, 0.35)]:
        for det, pan in [(-0.12, 0.3), (0.0, 0.5), (0.13, 0.7)]:
            s = saw(f + det, t, rng.random())
            L += s * amp * (1 - pan)
            R += s * amp * pan
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * t / 9.0)
    cutoff_mod = lowpass(L, 180) * (0.6 + 0.4 * lfo), lowpass(R, 180) * (0.6 + 0.4 * lfo)
    L, R = cutoff_mod

    # 2) Pad menor (Re menor -> Si bemol) que entra a los 4 s
    chords = [[146.83, 174.61, 220.0], [116.54, 146.83, 174.61]]
    pad = np.zeros_like(t)
    seg = 8.0
    for i, c in enumerate(chords * int(dur // (2 * seg) + 1)):
        a, b = i * seg, (i + 1) * seg
        m = (t >= a) & (t < b + 2)
        env = np.clip((t[m] - a) / 2.5, 0, 1) * np.clip((b + 2 - t[m]) / 2.5, 0, 1)
        for f in c:
            pad[m] += env * (np.sin(2 * np.pi * f * t[m]) + 0.3 * np.sin(2 * np.pi * 2.003 * f * t[m]))
    pad = lowpass(pad, 900) * np.clip((t - 4) / 6, 0, 1)
    L += 0.18 * pad
    R += 0.18 * np.roll(pad, int(0.013 * SR))

    # 3) Latido sub cada 1.15 s (tensión)
    beat = np.zeros_like(t)
    for k, start in enumerate(np.arange(2.0, dur, 1.15)):
        for off, g in [(0.0, 1.0), (0.28, 0.6)]:
            st = start + off
            m = (t >= st) & (t < st + 0.35)
            tt = t[m] - st
            beat[m] += g * np.sin(2 * np.pi * (48 + 30 * np.exp(-tt * 25)) * tt) * np.exp(-tt * 9)
    beat *= np.clip((t - 2) / 8, 0.3, 1)
    L += 0.9 * beat
    R += 0.9 * beat

    # 4) Viento: ruido filtrado con barrido lento
    noise = rng.standard_normal(len(t))
    wind = bandpass(noise, 250, 1400) * (0.4 + 0.6 * (0.5 + 0.5 * np.sin(2 * np.pi * t / 13 + 1)))
    L += 0.05 * wind
    R += 0.05 * np.roll(wind, 2400)

    # 5) Agudo inquietante con trémolo (entra a mitad)
    eerie = np.sin(2 * np.pi * 1174.66 * t + 3 * np.sin(2 * np.pi * 0.3 * t)) * (0.5 + 0.5 * np.sin(2 * np.pi * 5.5 * t))
    eerie *= np.clip((t - dur * 0.45) / 6, 0, 1) * 0.025
    L += eerie
    R += eerie * 0.7

    fade = np.clip(t / 1.5, 0, 1)  # entra suave; sin fade out: el bucle corta en seco
    return np.stack([L * fade, R * fade], axis=1)


def riser(dur=4.0, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    x = t / dur
    noise = rng.standard_normal(len(t))
    # ruido con paso-banda que sube de 300 Hz a 6 kHz, por bloques
    out = np.zeros_like(t)
    blk = 2400
    for i in range(0, len(t), blk):
        fc = 300 * (20 ** x[i])
        out[i:i + blk] = bandpass(noise[i:i + blk + 0], fc * 0.7, min(fc * 1.4, 20000))[: len(out[i:i + blk])]
    tone = np.sin(2 * np.pi * np.cumsum(110 * (4 ** x)) / SR) + 0.5 * np.sin(2 * np.pi * np.cumsum(165 * (4 ** x)) / SR)
    env = x ** 2.2
    sig = (0.6 * out + 0.4 * tone) * env
    return np.stack([sig, np.roll(sig, 300)], axis=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dur", type=float, default=50)
    ap.add_argument("--out", default="audio/musica.wav")
    ap.add_argument("--riser", default="audio/riser.wav")
    a = ap.parse_args()
    write_wav(a.out, music(a.dur))
    write_wav(a.riser, riser())
    print("ok", a.out, a.riser)
