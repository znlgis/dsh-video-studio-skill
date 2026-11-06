# -*- coding: utf-8 -*-
"""filmkit.py — DeepSeek 4.1 Flash + DSH 视频生产内核。"""
from __future__ import annotations
import hashlib, json, math, os, re, subprocess, sys, wave
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 30
SR = 48000
SAFE = (64, 58, 1856, 974)
SUB_BAND = (180, 872, 1740, 1030)   # 字幕带：留足两行 + 距画面底部 50px，永不被裁

def _find_ffmpeg() -> str:
    for c in (r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
              "/usr/bin/ffmpeg"):
        if os.path.exists(c):
            return c
    return "ffmpeg"

FFMPEG = os.environ.get("FILMKIT_FFMPEG") or _find_ffmpeg()
PY = sys.executable

_font_cache: dict = {}
FONT_CANDIDATES = {
    "bold": [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"],
    "reg": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc"],
    "light": [r"C:\Windows\Fonts\msyhl.ttc"],
    "mono": [r"C:\Windows\Fonts\consola.ttf"],
}

def font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    key = (size, weight)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_CANDIDATES.get(weight, []) + FONT_CANDIDATES["reg"]:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size, index=0)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    raise RuntimeError("找不到中文字体，请在 FONT_CANDIDATES 里补路径")

def clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)

def lerp(a, b, p):
    return a + (b - a) * p

def smooth(p):
    p = clamp(p); return p * p * (3 - 2 * p)

def ease_out(p):
    p = clamp(p); return 1 - (1 - p) ** 3

def ease_in(p):
    p = clamp(p); return p ** 3

def ease_in_out(p):
    p = clamp(p); return 3 * p * p - 2 * p * p * p

def ease_out_back(p, s=1.7):
    p = clamp(p) - 1; return 1 + p * p * ((s + 1) * p + s)

def rng(t: float, lo: float, hi: float) -> float:
    if hi <= lo: return 1.0
    return clamp((t - lo) / (hi - lo))

def _h(n) -> float:
    x = (int(n) * 374761393 + 668265263) & 0xFFFFFFFF
    x = (x ^ (x >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFFFF) / float(0xFFFFFF)

def hash2(n, m) -> float:
    return _h(int(n) * 73856093 ^ int(m) * 19349663)

def hash3(n, m, k) -> float:
    return _h(int(n) * 73856093 ^ int(m) * 19349663 ^ int(k) * 83492791)

def vnoise(x: float, seed: int = 0) -> float:
    i = math.floor(x); f = x - i
    a = hash2(i, seed); b = hash2(i + 1, seed)
    return a + (b - a) * smooth(f)

_NARROW = re.compile(r"[A-Za-z0-9\-_./:%@#&+=()\[\]{}<>'\"`~!?*,;]")
_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

def text_w(s: str, f) -> float:
    if not s: return 0.0
    bb = f.getbbox(s)
    return bb[2] - bb[0]

PUNCT_TAIL = "，。、；：？！）》”’"

def wrap_cn(s: str, f, max_w: float) -> list:
    lines, cur = [], ""
    for ch in s:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        trial = cur + ch
        if text_w(trial, f) <= max_w or not cur:
            cur = trial
        else:
            if ch in PUNCT_TAIL:
                lines.append(cur + ch); cur = ""
            else:
                lines.append(cur); cur = ch
    if cur: lines.append(cur)
    if lines and any(ch in PUNCT_TAIL for ch in lines[0][:1]):
        pass
    return lines

def draw_multiline(img, xy, lines, f, fill, align="left", line_gap=1.35):
    d = ImageDraw.Draw(img, "RGBA")
    lh = int(f.size * line_gap)
    x, y = xy
    boxes = []
    for i, ln in enumerate(lines):
        lw = text_w(ln, f)
        px = x - lw / 2 if align == "center" else (x - lw if align == "right" else x)
        d.text((px, y + i * lh), ln, font=f, fill=fill)
        boxes.append((px, y + i * lh, px + lw, y + i * lh + f.size))
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))

def check_bbox(box, safe=SAFE, tag="") -> list:
    x0, y0, x1, y1 = box
    sx0, sy0, sx1, sy1 = safe
    errs = []
    if x0 < sx0 or x1 > sx1 or y0 < sy0 or y1 > sy1:
        errs.append("[%s] 越界: (%s) vs safe %s" % (tag, tuple(round(v) for v in box), safe))
    return errs

def fit_sub_lines(text: str, max_lines=2, max_w=1560, size=46, weight="reg"):
    for sz in (size, int(size*0.92), int(size*0.84), int(size*0.76)):
        f = font(sz, weight)
        lines = wrap_cn(text, f, max_w)
        if len(lines) <= max_lines:
            return lines, f
    f = font(int(size*0.76), weight)
    return wrap_cn(text, f, max_w), f

def draw_subtitle(img, text: str, alpha=1.0, size=46, y=None,
                  band_w=None) -> Image:
    if not text or alpha <= 0.01:
        return img
    if band_w is None:
        band_w = SUB_BAND[2] - SUB_BAND[0]
    lines, f = fit_sub_lines(text, max_w=band_w, size=size)
    lh = int(f.size * 1.32)
    cx = (SUB_BAND[0] + SUB_BAND[2]) // 2
    total_h = lh * len(lines)
    base_y = SUB_BAND[3] - total_h if y is None else y
    base_y = max(SUB_BAND[1], min(base_y, SUB_BAND[3] - total_h))
    d = ImageDraw.Draw(img, "RGBA")
    widest = max(text_w(l, f) for l in lines)
    bx0 = cx - widest/2 - 34; bx1 = cx + widest/2 + 34
    by0 = base_y - 16; by1 = base_y + total_h + 16
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=(0, 0, 0, int(150*alpha)))
    for i, ln in enumerate(lines):
        lw = text_w(ln, f)
        d.text((cx - lw/2, base_y + i*lh), ln, font=f, fill=(245, 248, 252, int(255*alpha)))
    return img

def wav_write(path, samples, sr=SR, channels=1):
    a = np.clip(samples, -1.0, 1.0)
    pcm = (a * 32767.0).astype(np.int16)
    d = os.path.dirname(os.path.abspath(path))
    if d: os.makedirs(d, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())

def decode_pcm(path, sr=SR) -> np.ndarray:
    p = subprocess.run([FFMPEG, "-v", "error", "-i", str(path),
                        "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"], capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("解码失败 %s: %s" % (path, p.stderr.decode("utf-8", "replace")[:300]))
    return np.frombuffer(p.stdout, dtype=np.float32).copy()

def audio_dur(path, sr=SR) -> float:
    return len(decode_pcm(path, sr)) / float(sr)

def wav_dur(path) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())

class AudioBus:
    def __init__(self, total, sr=SR):
        self.sr = sr; self.total = total
        self.bus = np.zeros(int(round(total * sr)) + sr, dtype=np.float32)
        self.log = []

    def place(self, src, at, gain=1.0, loop=False, fade_in=0.0, fade_out=0.0):
        if src is None or len(src) == 0: return
        i0 = int(round(at * self.sr))
        if i0 >= len(self.bus): return
        s = src.copy()
        if fade_in > 0:
            n = min(len(s), int(fade_in * self.sr))
            s[:n] = s[:n] * np.linspace(0, 1, n, dtype=np.float32)
        if fade_out > 0:
            n = min(len(s), int(fade_out * self.sr))
            s[len(s)-n:] = s[len(s)-n:] * np.linspace(1, 0, n, dtype=np.float32)
        if loop and i0 + len(s) < len(self.bus):
            reps = int(np.ceil((len(self.bus) - i0) / len(s)))
            s = np.tile(s, reps)
        n = min(len(s), len(self.bus) - i0)
        self.bus[i0:i0+n] += s[:n] * gain
        self.log.append(dict(at=round(at, 3), dur=round(n/self.sr, 3), gain=gain))

    def mix(self, normalize=True, fade=0.6):
        out = self.bus[:int(round(self.total * self.sr))].copy()
        if fade > 0:
            n = min(len(out), int(fade * self.sr))
            out[:n] = out[:n] * np.linspace(0, 1, n, dtype=np.float32)
            out[-n:] = out[-n:] * np.linspace(1, 0, n, dtype=np.float32)
        if normalize and np.max(np.abs(out)) > 1e-6:
            out = out / np.max(np.abs(out)) * 0.89
        return np.tanh(out * 1.02).astype(np.float32) * 0.98

def tone(freq, dur, sr=SR, amp=0.2, kind="sine", decay=2.2, harmonics=(1.0,)):
    n = int(dur * sr); t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    for k, a in enumerate(harmonics, start=1):
        if kind == "sine":
            out += (a / k) * np.sin(2 * np.pi * freq * k * t)
    env = np.exp(-decay * t).astype(np.float32)
    return (amp * out * env).astype(np.float32)

def pad_chord(freqs, dur, sr=SR, amp=0.05, detune=0.004):
    n = int(dur * sr); t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    for f in freqs:
        for d in (-detune, 0.0, detune):
            out += amp * np.sin(2 * np.pi * f * (1 + d) * t)
    a = int(0.9 * sr); r = int(1.2 * sr)
    env = np.ones(n, dtype=np.float32)
    a = min(a, n); env[:a] = np.linspace(0, 1, a)
    r = min(r, n); env[-r:] *= np.linspace(1, 0, r)
    return (out * env / max(1, len(freqs))).astype(np.float32)

def noise_burst(dur, sr=SR, amp=0.1, lo=0.02, hi=0.35, decay=6.0, seed=7):
    n = int(dur * sr); r = np.random.RandomState(seed)
    w = r.randn(n).astype(np.float32)
    k = max(1, int(sr / 2000))
    w = np.convolve(w, np.ones(k, dtype=np.float32)/k, mode="same")
    t = np.arange(n)/sr
    env = np.exp(-decay*t)
    return (amp * w * env).astype(np.float32)

def tts(text, out_mp3, voice="zh-CN-YunyangNeural", rate="+0%", pitch="+0Hz", cache=True):
    d = os.path.dirname(os.path.abspath(out_mp3))
    if d: os.makedirs(d, exist_ok=True)
    meta_path = out_mp3 + ".json"
    if cache and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 2000 and os.path.exists(meta_path):
        try:
            m = json.load(open(meta_path, encoding="utf-8"))
            m["dur"] = audio_dur(out_mp3)
            return m
        except Exception:
            pass
    import asyncio, edge_tts
    async def go():
        c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await c.save(out_mp3)
    asyncio.run(go())
    m = dict(path=out_mp3, text=text, voice=voice, rate=rate, dur=audio_dur(out_mp3))
    json.dump(m, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    return m

def tts_batch(lines, workdir, voice="zh-CN-YunyangNeural", rate="+0%", pitch="+0Hz"):
    out = []
    for ln in lines:
        mp3 = os.path.join(workdir, "tts", ln["id"] + ".mp3")
        m = tts(ln["text"], mp3, voice=voice, rate=rate, pitch=pitch)
        row = dict(ln); row.update(m); out.append(row)
        print("  tts %-10s %6.2fs  %s" % (ln["id"], m["dur"], ln["text"][:36]), flush=True)
    return out

def build_timeline(lines, lead=0.55, tail=0.85, min_dur=3.0, tail_map=None):
    scenes, t = [], 0.0
    for ln in lines:
        d = max(min_dur, lead + float(ln["dur"]) + (tail_map or {}).get(ln["id"], tail))
        scenes.append(dict(ln, start=round(t, 3), end=round(t + d, 3), dur=round(d, 3),
                           voice_start=round(t + lead, 3),
                           voice_end=round(t + lead + float(ln["dur"]), 3)))
        t += d
    return dict(total=round(t, 3), scenes=scenes, fps=FPS, frames=int(round(t * FPS)))

def sub_window(scenes, i):
    s = scenes[i]
    t0 = s["voice_start"] - 0.12
    t1 = min(s["voice_end"] + 0.45, s["voice_start"] + 5.2)
    nxt = scenes[i + 1]["voice_start"] - 0.12 if i + 1 < len(scenes) else 1e9
    return t0, min(t1, nxt)

def sub_at(scenes, t):
    for i, s in enumerate(scenes):
        t0, t1 = sub_window(scenes, i)
        if t0 <= t <= t1:
            return s.get("sub") or s.get("text", "")
    return ""

def write_srt(scenes, path):
    def fmt(x):
        ms = int(round(x * 1000)); h = ms // 3600000; ms %= 3600000
        m = ms // 60000; ms %= 60000; s = ms // 1000; ms %= 1000
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)
    rows = []
    for i, s in enumerate(scenes):
        t0, t1 = sub_window(scenes, i)
        body = s.get("sub") or s.get("text", "")
        rows.append("%d\n%s --> %s\n%s\n" % (i + 1, fmt(t0), fmt(t1), body))
    d = os.path.dirname(os.path.abspath(path))
    if d: os.makedirs(d, exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(rows))

class FrameSink:
    def __init__(self, out_path, w=W, h=H, fps=FPS, crf=18, preset="medium",
                 audio=None, bitrate=None):
        self.out = str(out_path)
        d = os.path.dirname(os.path.abspath(self.out))
        if d: os.makedirs(d, exist_ok=True)
        cmd = [FFMPEG, "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
               "-r", str(fps), "-i", "-"]
        if audio:
            cmd += ["-i", str(audio)]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-g", str(fps * 2)]
        if bitrate:
            cmd += ["-maxrate", bitrate, "-bufsize", bitrate]
        if audio:
            # 不用 -shortest：它会按较短一路截断（实测请求 524 帧只出 488 帧）
            d = media_dur(audio)
            if d:
                cmd += ["-t", "%.3f" % d]
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-movflags", "+faststart", self.out]
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.n = 0

    def write(self, frame):
        if frame.size != (W, H):
            frame = frame.resize((W, H), Image.LANCZOS)
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        self.proc.stdin.write(frame.tobytes())
        self.n += 1

    def close(self):
        self.proc.stdin.close()
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError("ffmpeg 编码失败: " + self.proc.stderr.read().decode("utf-8", "replace")[:800])
        return self.out

def mux(video, audio, out, crf=18, preset="medium", audio_bitrate="192k",
        loudnorm=True, target_i=-16.0, target_tp=-1.5):
    """合成成片。默认做两遍 EBU R128 响度标准化（视频直通不重编码）。"""
    d = os.path.dirname(os.path.abspath(str(out)))
    if d: os.makedirs(d, exist_ok=True)
    af = None
    if loudnorm:
        af = loudnorm_two_pass(audio, target_i=target_i, target_tp=target_tp)
    # ⚠ 不用 -shortest：它会按较短的一路截断。实测踩过"请求 524 帧只出 488 帧"
    #    （音频比视频短 1.2 s，-shortest 直接把视频砍了）。改成显式对齐 + 校验。
    vd = media_dur(video)
    ad = media_dur(audio)
    if vd and ad and abs(vd - ad) > 0.05:
        raise RuntimeError("音视频时长不一致：video %.3fs vs audio %.3fs（差 %.3fs）。"
                           "请检查混音总线长度是否等于 total。" % (vd, ad, abs(vd - ad)))
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(audio),
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if af:
        cmd += ["-af", af]
    cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
    if vd:
        cmd += ["-t", "%.3f" % vd]
    cmd += ["-movflags", "+faststart", str(out)]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("mux 失败: " + p.stderr.decode("utf-8", "replace")[:800])
    return str(out)

def ffprobe_path():
    base = os.path.basename(FFMPEG)
    cand = FFMPEG[: -len(base)] + ("ffprobe.exe" if base.endswith(".exe") else "ffprobe")
    return cand if os.path.exists(cand) else FFMPEG.replace("ffmpeg", "ffprobe")

def probe(path):
    p = subprocess.run([ffprobe_path(), "-v", "error",
                        "-show_entries", "format=duration,size,bit_rate",
                        "-show_entries", "stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return {}

def loudness(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = p.stderr
    out = {}
    for key, pat in (("I", r"I:\s*(-?[\d.]+)\s*LUFS"), ("LRA", r"LRA:\s*(-?[\d.]+)\s*LU"),
                     ("TP", r"Peak:\s*(-?[\d.]+)\s*dBFS")):
        m = re.findall(pat, txt)
        if m: out[key] = float(m[-1])
    return out

def detect_black(path, dur=0.4, head=1.2, tail=None):
    """长黑帧检测。head/tail 秒内是刻意的淡入淡出，不算问题。"""
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-vf",
                        "blackdetect=d=%s:pic_th=0.98" % dur, "-f", "null", "-"],
                       capture_output=True, text=True)
    pr = probe(path)
    total = float((pr.get("format") or {}).get("duration") or 0)
    tl = tail if tail is not None else head
    bad = 0
    for m in re.finditer(r"black_start:([d.]+)\s+black_end:([\d.]+)", p.stderr):
        s, e = float(m.group(1)), float(m.group(2))
        if s >= head and (total <= 0 or e <= total - tl):
            bad += 1
    return bad

def detect_silence(path, th=-45, dur=1.2):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af",
                        "silencedetect=n=%ddB:d=%s" % (th, dur), "-f", "null", "-"],
                       capture_output=True, text=True)
    return len(re.findall("silence_start", p.stderr))

def qa_film(path, expect_frames=None, min_bitrate=3000000):
    errs = []
    pr = probe(path)
    fmt = pr.get("format", {})
    streams = pr.get("streams") or [{}]
    vs = [s for s in streams if s.get("codec_type") == "video"] or [{}]
    au = [s for s in streams if s.get("codec_type") == "audio"]
    v = vs[0]
    if v.get("width") != W or v.get("height") != H:
        errs.append("分辨率 %sx%s != %dx%d" % (v.get("width"), v.get("height"), W, H))
    if not au: errs.append("无音轨")
    br = int(fmt.get("bit_rate") or 0)
    if br and br < min_bitrate:
        errs.append("码率 %.2f Mbps 偏低（文字密集建议 >= %.0f Mbps）" % (br / 1e6, min_bitrate / 1e6))
    if fmt.get("duration") and expect_frames:
        d = float(fmt["duration"])
        if abs(d - expect_frames / FPS) > 0.5:
            errs.append("时长 %.2fs 与预期 %.2fs 不符" % (d, expect_frames / FPS))
    lo = loudness(path)
    if "I" in lo:
        if lo["I"] > -12: errs.append("响度 %.1f LUFS 过大（目标 -16 ~ -14）" % lo["I"])
        elif lo["I"] < -20: errs.append("响度 %.1f LUFS 过小（目标 -16 ~ -14）" % lo["I"])
    if "TP" in lo and lo["TP"] > -0.2:
        errs.append("真峰 %.1f dBTP 有削波风险（目标 <= -0.5）" % lo["TP"])
    if detect_black(path) > 0: errs.append("检测到长黑帧")
    return errs

def concat_segments(parts, out, workdir=None):
    lst = os.path.join(workdir or os.path.dirname(str(out)), "concat.txt")
    d = os.path.dirname(os.path.abspath(lst))
    if d: os.makedirs(d, exist_ok=True)
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % str(p).replace("\\", "/"))
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", str(out)]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("concat 失败: " + p.stderr.decode("utf-8", "replace")[:600])
    return str(out)

def snap(path, seconds, out_png):
    d = os.path.dirname(os.path.abspath(out_png))
    if d: os.makedirs(d, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(seconds), "-i", str(path),
                    "-frames:v", "1", str(out_png)], capture_output=True)
    return out_png

def contact_sheet(frames, out_png, cols=4, thumb_w=480):
    if not frames: return None
    ims = [Image.open(f) if isinstance(f, str) else f for f in frames]
    tw = thumb_w; th = int(tw * H / W)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (12, 14, 18))
    for i, im in enumerate(ims):
        sheet.paste(im.resize((tw, th), Image.LANCZOS), ((i % cols) * tw, (i // cols) * th))
    d = os.path.dirname(os.path.abspath(out_png))
    if d: os.makedirs(d, exist_ok=True)
    sheet.save(out_png)
    return out_png

def loudnorm_two_pass(src, target_i=-16.0, target_tp=-1.5, target_lra=11.0):
    """两遍 loudnorm：先测量，再按测量值归一化。返回可直接给 -af 的滤镜串。"""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(src), "-af",
                        "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:print_format=json" % (target_i, target_tp, target_lra),
                        "-f", "null", "-"], capture_output=True, text=True)
    txt = p.stderr
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except Exception:
        return None
    return ("loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:measured_I=%s:measured_TP=%s:"
            "measured_LRA=%s:measured_thresh=%s:offset=%s:print_format=summary" % (
                target_i, target_tp, target_lra, j.get("input_i"), j.get("input_tp"),
                j.get("input_lra"), j.get("input_thresh"), j.get("target_offset", 0.0)))


def sharpness(png_or_img):
    """文字/线条锐度：Laplacian 方差。同一画面 CRF 越高越糊，用来横比编码档位。"""
    import cv2
    if isinstance(png_or_img, str):
        im = cv2.imread(png_or_img, cv2.IMREAD_GRAYSCALE)
    else:
        im = np.array(png_or_img.convert("L"))
    if im is None:
        return 0.0
    return float(cv2.Laplacian(im, cv2.CV_64F).var())


def qa_text_safe(scenes, tol=1.0):
    """字幕几何校验：宽度/高度是否落在字幕带内，是否触及画面底部。"""
    errs = []
    for s in scenes:
        body = s.get("sub") or s.get("text") or ""
        if not body:
            continue
        lines, f = fit_sub_lines(body)
        widest = max(text_w(l, f) for l in lines)
        total_h = int(f.size * 1.32) * len(lines)
        if widest > SUB_BAND[2] - SUB_BAND[0]:
            errs.append("字幕过宽 %dpx: %s" % (widest, body[:24]))
        if SUB_BAND[3] - total_h < SUB_BAND[1]:
            errs.append("字幕过高 %dpx: %s" % (total_h, body[:24]))
        if SUB_BAND[3] > H - 12:
            errs.append("字幕带触及画面底部，有被裁风险")
    return errs


def encode_quality(film, ref_png, at_sec=None):
    """用同帧的"原始 PNG"与"解码后帧"比锐度，客观衡量编码损失。

    返回 dict(sharp_ref, sharp_dec, ratio)。ratio >= 0.85 视为文字仍锐利；
    比"码率下限"可靠得多——暗色渐变内容本来就码率低，但文字可以不糊。
    """
    import cv2
    a = cv2.imread(str(ref_png), cv2.IMREAD_GRAYSCALE)
    if a is None:
        return None
    tmp = os.path.join(os.path.dirname(os.path.abspath(str(ref_png))), "_dec_tmp.png")
    snap(film, at_sec if at_sec is not None else 0.0, tmp)
    b = cv2.imread(tmp, cv2.IMREAD_GRAYSCALE)
    if b is None:
        return None
    sa = float(cv2.Laplacian(a, cv2.CV_64F).var())
    sb = float(cv2.Laplacian(b, cv2.CV_64F).var())
    return dict(sharp_ref=round(sa, 1), sharp_dec=round(sb, 1),
                ratio=round(sb / sa, 4) if sa > 0 else 0.0)


def qa_report(path, expect_frames=None, min_bitrate=3000000, ref_png=None, ref_sec=None):
    """分档质检：errors 必须修，warnings 看情况。"""
    errors, warns = [], []
    pr = probe(path)
    fmt = pr.get("format", {})
    streams = pr.get("streams") or [{}]
    vs = [s for s in streams if s.get("codec_type") == "video"] or [{}]
    au = [s for s in streams if s.get("codec_type") == "audio"]
    v = vs[0]
    if v.get("width") != W or v.get("height") != H:
        errors.append("分辨率 %sx%s != %dx%d" % (v.get("width"), v.get("height"), W, H))
    if not au:
        errors.append("无音轨")
    br = int(fmt.get("bit_rate") or 0)
    if br and br < min_bitrate:
        warns.append("码率 %.2f Mbps 低于参考值 %.0f Mbps（暗色简洁内容可接受，看锐度比）"
                     % (br / 1e6, min_bitrate / 1e6))
    if fmt.get("duration") and expect_frames:
        d = float(fmt["duration"])
        if abs(d - expect_frames / FPS) > 0.5:
            errors.append("时长 %.2fs 与预期 %.2fs 不符" % (d, expect_frames / FPS))
    lo = loudness(path)
    if "I" in lo:
        if lo["I"] > -12:
            errors.append("响度 %.1f LUFS 过大（目标 -16 ~ -14）" % lo["I"])
        elif lo["I"] < -19:
            errors.append("响度 %.1f LUFS 过小（目标 -16 ~ -14）" % lo["I"])
        elif lo["I"] < -17.2 or lo["I"] > -14.5:
            warns.append("响度 %.1f LUFS 略偏（目标 -16 ± 1）" % lo["I"])
    if "TP" in lo and lo["TP"] > -0.2:
        errors.append("真峰 %.1f dBTP 有削波风险（目标 <= -1）" % lo["TP"])
    nb = detect_black(path)
    if nb > 0:
        warns.append("首尾之外检测到 %d 段长黑帧" % nb)
    if detect_silence(path) > 0:
        warns.append("检测到长静音段（确认是否有意留白）")
    if ref_png and os.path.exists(ref_png):
        q = encode_quality(path, ref_png, ref_sec)
        if q:
            if q["ratio"] < 0.85:
                errors.append("编码损失偏大：锐度比 %.3f（<0.85 文字会发虚，降低 CRF 或提高 preset）" % q["ratio"])
            elif q["ratio"] < 0.92:
                warns.append("编码锐度比 %.3f（0.85~0.92，可接受但可更好）" % q["ratio"])
    return dict(errors=errors, warnings=warns, probe=pr, loudness=lo)


def media_dur(path):
    """任意媒体时长（秒），失败返回 0.0。比 wave.open 通用。"""
    p = subprocess.run([ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float((p.stdout or "0").strip())
    except Exception:
        return 0.0

