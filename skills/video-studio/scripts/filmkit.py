# -*- coding: utf-8 -*-
"""filmkit.py — DeepSeek 4.1 Flash + DSH 视频生产内核（v0.2.0）。

设计原则（v0.2.0 起，全部来自一次 11 分钟真实成片的复盘）：
1. 质检门禁必须真的会失败。门禁失效比没有门禁更危险。
2. 缓存必须校验输入。只判断"文件存在"会让"改一句台词不生效"静默发生。
3. 失败要报错，不要静默降级。尺寸不符、缺字形、峰值爆表都要看得见。
4. 公共函数可独立测试：不依赖当前目录、不依赖上一次调用留下的状态。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 30
SR = 48000
SAFE = (64, 58, 1856, 974)
SUB_BAND = (180, 872, 1740, 1030)   # 字幕带：两行 + 距画面底部 50px，永不被裁
SUB_MAX_CHARS = 24                  # 单行字幕中文字数上限
SUB_MAX_HOLD = None                 # None = 字幕覆盖整句（v0.2.0 默认）；设数字则钳住最大停留秒数


# ---------------------------------------------------------------- ffmpeg
def _find_ffmpeg() -> str:
    env = os.environ.get("FILMKIT_FFMPEG")
    if env and os.path.exists(env):
        return env
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Microsoft",
                     "WinGet", "Links", "ffmpeg.exe"),
        "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return shutil.which("ffmpeg") or "ffmpeg"


FFMPEG = _find_ffmpeg()
PY = sys.executable


def ffprobe_path() -> str:
    base = os.path.basename(FFMPEG)
    cand = FFMPEG[: -len(base)] + ("ffprobe.exe" if base.endswith(".exe") else "ffprobe")
    if os.path.exists(cand):
        return cand
    return shutil.which("ffprobe") or FFMPEG.replace("ffmpeg", "ffprobe")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


# ---------------------------------------------------------------- 字体与字形
_font_cache = {}
FONT_CANDIDATES = {
    "bold": [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/System/Library/Fonts/PingFang.ttc"],
    "reg": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc"],
    "light": [r"C:\Windows\Fonts\msyhl.ttc", r"C:\Windows\Fonts\msyh.ttc"],
    # mono 只保证 ASCII；中文请勿用 mono（consola 无中文字形，会画成方块）
    "mono": [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\msyh.ttc",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}


def font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    key = (int(size), weight)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_CANDIDATES.get(weight, []) + FONT_CANDIDATES["reg"]:
        if path and os.path.exists(path):
            try:
                f = ImageFont.truetype(path, int(size), index=0)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    raise RuntimeError("找不到中文字体，请在 filmkit.FONT_CANDIDATES 里补路径")


_PRIVATE = "\ue000"          # 私用区码点：任何字体都缺字形，作 .notdef 基准
_glyph_sig = {}


def _mask_sig(f, ch):
    try:
        m = f.getmask(ch)
        return (tuple(m.size), bytes(m))
    except Exception:
        return None


def glyph_missing_chars(text: str, size: int = 32, weight: str = "reg"):
    """返回字体缺字形的字符（缺字会被画成 .notdef 方块，脚本零报错）。

    实测：msyhbd 缺 ✓ × >= <= 等；v0.2.0 起 qa 会自动扫剧本里的上屏文字。
    """
    if not text:
        return []
    f = font(size, weight)
    key = (int(size), weight)
    if key not in _glyph_sig:
        _glyph_sig[key] = _mask_sig(f, _PRIVATE)
    ref = _glyph_sig[key]
    miss, seen = [], set()
    for ch in text:
        if ch in seen or ch.isspace():
            continue
        seen.add(ch)
        sig = _mask_sig(f, ch)
        if sig is None or sig == ref:
            miss.append(ch)
    return miss


def check_glyphs(lines, fields=("text", "sub", "big", "lead", "kicker", "note")):
    """扫剧本所有上屏文字，返回 [(id, 字段, 缺字符)]。"""
    errs = []
    for ln in lines:
        for fld in fields:
            v = ln.get(fld)
            if isinstance(v, str) and v:
                miss = glyph_missing_chars(v, 32, "bold")
                if miss:
                    errs.append((ln.get("id", "?"), fld, "".join(miss)))
    return errs


# ---------------------------------------------------------------- 缓动 / 确定性随机
def clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)


def lerp(a, b, p):
    return a + (b - a) * p


def smooth(p):
    p = clamp(p)
    return p * p * (3 - 2 * p)


def ease_out(p):
    p = clamp(p)
    return 1 - (1 - p) ** 3


def ease_in(p):
    p = clamp(p)
    return p ** 3


def ease_in_out(p):
    p = clamp(p)
    return 3 * p * p - 2 * p * p * p


def ease_out_back(p, s=1.7):
    p = clamp(p) - 1
    return 1 + p * p * ((s + 1) * p + s)


def rng(t: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0
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
    i = math.floor(x)
    f = x - i
    a, b = hash2(i, seed), hash2(i + 1, seed)
    return a + (b - a) * smooth(f)


# ---------------------------------------------------------------- 文本排版
_PUNCT_TAIL = "，。、；：？！）》”’"


def text_w(s: str, f) -> float:
    if not s:
        return 0.0
    bb = f.getbbox(s)
    return bb[2] - bb[0]


def wrap_cn(s: str, f, max_w: float):
    lines, cur = [], ""
    for ch in s:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if text_w(trial, f) <= max_w or not cur:
            cur = trial
        else:
            if ch in _PUNCT_TAIL:
                lines.append(cur + ch)
                cur = ""
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


_wrap_cache = {}
_tw_cache = {}


def wrap_cn_cached(s: str, size: int, max_w: float, weight: str = "reg"):
    """带缓存的换行。场景函数每帧都要调用，务必用这个版本。

    实测：每帧重新排版（12 张卡片 x 2 个 wrap_cn）会让单帧从 4.6ms 涨到 12ms。
    """
    key = (s, int(size), int(max_w), weight)
    r = _wrap_cache.get(key)
    if r is None:
        r = wrap_cn(s, font(size, weight), max_w)
        _wrap_cache[key] = r
    return r


def text_w_cached(s: str, size: int, weight: str = "reg") -> float:
    key = (s, int(size), weight)
    r = _tw_cache.get(key)
    if r is None:
        r = text_w(s, font(size, weight))
        _tw_cache[key] = r
    return r


def clear_text_cache():
    _wrap_cache.clear()
    _tw_cache.clear()


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


def check_bbox(box, safe=SAFE, tag=""):
    x0, y0, x1, y1 = box
    sx0, sy0, sx1, sy1 = safe
    if x0 < sx0 or x1 > sx1 or y0 < sy0 or y1 > sy1:
        return ["[%s] 越界: (%s) vs safe %s" % (tag, tuple(round(v) for v in box), safe)]
    return []


def fit_sub_lines(text: str, max_lines=2, max_w=1560, size=46, weight="reg"):
    """字幕折行：优先两行，超宽逐级缩字号。返回 (lines, font)。"""
    for sz in (size, int(size * 0.92), int(size * 0.84), int(size * 0.76)):
        f = font(sz, weight)
        lines = wrap_cn(text, f, max_w)
        if len(lines) <= max_lines:
            return lines, f
    f = font(int(size * 0.76), weight)
    return wrap_cn(text, f, max_w), f


def draw_subtitle(img, text: str, alpha=1.0, size=46, y=None, band_w=None):
    if not text or alpha <= 0.01:
        return img
    if band_w is None:
        band_w = SUB_BAND[2] - SUB_BAND[0]
    lines, f = fit_sub_lines(text, max_w=band_w, size=size)
    lines = lines[:2]                       # 硬约束：最多两行
    lh = int(f.size * 1.32)
    cx = (SUB_BAND[0] + SUB_BAND[2]) // 2
    total_h = lh * len(lines)
    base_y = SUB_BAND[3] - total_h if y is None else y
    base_y = max(SUB_BAND[1], min(base_y, SUB_BAND[3] - total_h))
    d = ImageDraw.Draw(img, "RGBA")
    widest = max(text_w(l, f) for l in lines)
    bx0 = cx - widest / 2 - 34
    bx1 = cx + widest / 2 + 34
    by0 = base_y - 16
    by1 = base_y + total_h + 16
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=(0, 0, 0, int(150 * alpha)))
    for i, ln in enumerate(lines):
        lw = text_w(ln, f)
        d.text((cx - lw / 2, base_y + i * lh), ln, font=f, fill=(245, 248, 252, int(255 * alpha)))
    return img


# ---------------------------------------------------------------- WAV / PCM
def wav_write(path, samples, sr=SR, channels=1):
    a = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    pcm = (a * 32767.0).astype(np.int16)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return peak


def decode_pcm(path, sr=SR):
    p = _run([FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le",
              "-ac", "1", "-ar", str(sr), "-"])
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("解码失败 %s: %s" % (path, p.stderr.decode("utf-8", "replace")[:300]))
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def audio_dur(path, sr=SR):
    return len(decode_pcm(path, sr)) / float(sr)


def wav_dur(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def trim_silence(pcm, sr=SR, thresh_db=-42.0, keep_ms=80):
    """裁掉首尾静音（edge-tts 尾部常带约 0.95s 静音；55 句累计 61.5s 空白）。"""
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size == 0:
        return pcm
    th = 10 ** (thresh_db / 20.0)
    idx = np.where(np.abs(pcm) > th)[0]
    if idx.size == 0:
        return pcm
    keep = int(sr * keep_ms / 1000.0)
    a = max(0, int(idx[0]) - keep)
    b = min(len(pcm), int(idx[-1]) + keep)
    return pcm[a:b]


def trim_silence_edges(pcm, sr=SR, thresh_db=-42.0, keep_ms=80):
    """兼容别名。"""
    return trim_silence(pcm, sr=sr, thresh_db=thresh_db, keep_ms=keep_ms)


# ---------------------------------------------------------------- 音频总线
class AudioBus:
    """采样级音频总线：确定性、1/SR 精度、可单层重放、可中途打印峰值。"""

    def __init__(self, total, sr=SR):
        self.sr = int(sr)
        self.total = float(total)
        self.bus = np.zeros(int(round(self.total * self.sr)) + self.sr, dtype=np.float32)
        self.log = []

    def place(self, src, at, gain=1.0, loop=False, fade_in=0.0, fade_out=0.0):
        if src is None or len(src) == 0:
            return self
        i0 = int(round(float(at) * self.sr))
        if i0 >= len(self.bus):
            return self
        s = np.asarray(src, dtype=np.float32).copy()
        if fade_in > 0:
            n = min(len(s), max(1, int(fade_in * self.sr)))
            s[:n] *= np.linspace(0, 1, n, dtype=np.float32)
        if fade_out > 0:
            n = min(len(s), max(1, int(fade_out * self.sr)))
            s[len(s) - n:] *= np.linspace(1, 0, n, dtype=np.float32)
        if loop and i0 + len(s) < len(self.bus):
            reps = int(np.ceil((len(self.bus) - i0) / max(1, len(s))))
            s = np.tile(s, reps)
        n = min(len(s), len(self.bus) - i0)
        self.bus[i0:i0 + n] += s[:n] * gain
        self.log.append(dict(kind="place", at=round(float(at), 3),
                             dur=round(n / self.sr, 3), gain=gain))
        return self

    def music_bed(self, chords, seg=4.0, gain=0.9, amp=0.055, xfade=1.2, start=0.0):
        """连续配乐床：和弦按 seg 秒推进 + 交叉淡化，不做硬拼接（避免接缝爆点）。"""
        if not chords:
            return self
        t = float(start)
        idx = 0
        while t < self.total:
            freqs = chords[idx % len(chords)]
            dur = min(seg + xfade, max(0.25, self.total - t + xfade))
            pcm = pad_chord(freqs, dur, amp=amp)
            self.place(pcm, t, gain=gain, fade_in=xfade * 0.6, fade_out=xfade)
            t += seg
            idx += 1
            if idx > 100000:
                break
        return self

    def mix(self, normalize=True, fade=0.6, peak_guard=0.97, verbose=True):
        out = self.bus[:int(round(self.total * self.sr))].copy()
        if out.size == 0:
            return out
        raw_peak = float(np.max(np.abs(out)))
        if fade > 0:
            n = min(len(out), max(1, int(fade * self.sr)))
            out[:n] *= np.linspace(0, 1, n, dtype=np.float32)
            out[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
        if normalize and raw_peak > 1e-6:
            out = out / raw_peak * 0.89
        out = np.tanh(out * 1.02).astype(np.float32) * 0.98
        final_peak = float(np.max(np.abs(out)))
        if verbose:
            print("   混音: 叠加峰值 %.2f -> 输出峰值 %.2f (归一化=%s, %d 层)"
                  % (raw_peak, final_peak, normalize, len(self.log)), flush=True)
        if final_peak > peak_guard and verbose:
            print("   [WARN] 混音峰值 %.3f 接近满刻度，建议降低 voice_gain / music.gain" % final_peak,
                  flush=True)
        return out


# ---------------------------------------------------------------- 程序化音源
def tone(freq, dur, sr=SR, amp=0.2, kind="sine", decay=2.2, harmonics=(1.0,)):
    """单音衰减。kind 参数保留以兼容 v0.1（目前只实现 sine）。"""
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    if kind == "sine":
        for k, a in enumerate(harmonics, start=1):
            out += (a / k) * np.sin(2 * np.pi * freq * k * t)
    else:                                   # 简单三角近似，避免旧调用直接抛错
        for k, a in enumerate(harmonics, start=1):
            out += (a / k) * (2 / np.pi) * np.arcsin(np.sin(2 * np.pi * freq * k * t))
    return (amp * out * np.exp(-decay * t).astype(np.float32)).astype(np.float32)


def pad_chord(freqs, dur, sr=SR, amp=0.05, detune=0.004):
    n = max(1, int(dur * sr))
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    for f in freqs:
        for d in (-detune, 0.0, detune):
            out += amp * np.sin(2 * np.pi * f * (1 + d) * t)
    a = min(int(0.9 * sr), n)
    r = min(int(1.2 * sr), n)
    env = np.ones(n, dtype=np.float32)
    if a > 1:
        env[:a] = np.linspace(0, 1, a)
    if r > 1:
        env[-r:] *= np.linspace(1, 0, r)
    return (out * env / max(1, len(freqs))).astype(np.float32)


def noise_burst(dur, sr=SR, amp=0.1, lo=0.02, hi=0.35, decay=6.0, seed=7):
    """噪声脉冲。lo/hi 参数保留以兼容 v0.1（当前用低通近似，不严格按频段切）。"""
    n = max(1, int(dur * sr))
    r = np.random.RandomState(seed)
    w = r.randn(n).astype(np.float32)
    k = max(1, int(sr / 2000))
    w = np.convolve(w, np.ones(k, dtype=np.float32) / k, mode="same")
    t = np.arange(n) / sr
    return (amp * w * np.exp(-decay * t)).astype(np.float32)


def whoosh(dur=0.45, sr=SR, amp=0.06, seed=3):
    n = max(1, int(dur * sr))
    r = np.random.RandomState(seed)
    w = r.randn(n).astype(np.float32)
    k = max(1, int(sr / 1800))
    w = np.convolve(w, np.ones(k, dtype=np.float32) / k, mode="same")
    t = np.linspace(0, 1, n, dtype=np.float32)
    return (amp * w * np.sin(np.pi * np.power(t, 0.7)) ** 2).astype(np.float32)


# ---------------------------------------------------------------- TTS
def tts_key(text, voice, rate, pitch) -> str:
    h = hashlib.sha1(("%s|%s|%s|%s" % (text, voice, rate, pitch)).encode("utf-8"))
    return h.hexdigest()[:16]


def tts(text, out_mp3, voice="zh-CN-YunyangNeural", rate="+0%", pitch="+0Hz",
        cache=True, retries=4, trim=True, trim_db=-42.0):
    """逐句合成旁白（带输入校验的缓存 + 网络重试 + 可选尾静音裁剪）。

    v0.1 的缓存只判断文件是否存在，改一句台词会静默复用旧音频；
    v0.2 起把 text/voice/rate/pitch 的哈希写进 meta，变了就重新合成。
    trim=True 时另外落一份裁剪后的 wav 并在 meta.path 指向它，dur 为裁剪后实测时长。
    """
    out_mp3 = str(out_mp3)
    d = os.path.dirname(os.path.abspath(out_mp3))
    if d:
        os.makedirs(d, exist_ok=True)
    meta_path = out_mp3 + ".json"
    key = tts_key(text, voice, rate, pitch)

    if cache and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 2000:
        try:
            m = json.load(open(meta_path, encoding="utf-8"))
            if m.get("key") == key and m.get("text") == text:
                if m.get("trimmed") and m.get("path") and os.path.exists(m["path"]):
                    return m
                if not m.get("trimmed") and not trim:
                    return m
        except Exception:
            pass

    import asyncio
    import time as _time

    last = None
    for attempt in range(max(1, int(retries))):
        try:
            import edge_tts

            async def go():
                c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
                await c.save(out_mp3)

            asyncio.run(go())
            if not os.path.exists(out_mp3) or os.path.getsize(out_mp3) <= 2000:
                raise RuntimeError("合成产物异常")
            break
        except Exception as e:              # noqa: BLE001
            last = e
            if attempt == max(1, int(retries)) - 1:
                raise RuntimeError("edge-tts 合成失败（重试 %d 次）：%s" % (retries, e)) from e
            _time.sleep(1.2 * (attempt + 1))

    dur_raw = audio_dur(out_mp3)
    used = out_mp3
    dur = dur_raw
    if trim:
        pcm = trim_silence(decode_pcm(out_mp3), thresh_db=trim_db)
        wav_path = os.path.splitext(out_mp3)[0] + ".trim.wav"
        wav_write(wav_path, pcm)
        used = wav_path
        dur = len(pcm) / float(SR)

    m = dict(path=used, mp3=out_mp3, text=text, voice=voice, rate=rate, pitch=pitch,
             key=key, dur=dur, dur_raw=dur_raw, trimmed=bool(trim))
    json.dump(m, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def tts_batch(lines, workdir, voice="zh-CN-YunyangNeural", rate="+0%", pitch="+0Hz",
              workers=4, trim=True):
    """并发逐句合成（并发只影响墙钟，不影响产物：每句独立缓存）。"""
    from concurrent.futures import ThreadPoolExecutor

    tts_dir = os.path.join(workdir, "tts")
    os.makedirs(tts_dir, exist_ok=True)
    results = [None] * len(lines)

    def one(i, ln):
        mp3 = os.path.join(tts_dir, str(ln["id"]) + ".mp3")
        m = tts(ln["text"], mp3, voice=voice, rate=rate, pitch=pitch, trim=trim)
        row = dict(ln)
        row.update(m)
        print("  tts %-10s %6.2fs  %s" % (ln["id"], m["dur"], ln["text"][:34]), flush=True)
        return i, row

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futs = [ex.submit(one, i, ln) for i, ln in enumerate(lines)]
        for fu in futs:
            i, row = fu.result()
            results[i] = row
    return results


# ---------------------------------------------------------------- 时间轴
def build_timeline(lines, lead=0.55, tail=0.85, min_dur=3.0, tail_map=None, max_dur=None):
    """由实测语音时长反推时间轴。第一段 start 必须严格为 0。"""
    scenes, t = [], 0.0
    for ln in lines:
        voice = float(ln.get("dur") or 0.0)
        d = max(float(min_dur), float(lead) + voice + float((tail_map or {}).get(ln["id"], tail)))
        if max_dur and d > float(max_dur):
            d = float(max_dur)
        scenes.append(dict(ln, start=round(t, 3), end=round(t + d, 3), dur=round(d, 3),
                           voice_start=round(t + float(lead), 3),
                           voice_end=round(t + float(lead) + voice, 3)))
        t += d
    if scenes:
        scenes[0]["start"] = 0.0
    return dict(total=round(t, 3), scenes=scenes, fps=FPS, frames=int(round(t * FPS)))


def sub_window(scenes, i, max_hold=None):
    """字幕窗口：默认覆盖整句语音，且绝不压下一句。

    v0.1 把 t1 钳在 voice_start + 5.2，导致 10 秒长句字幕在读到一半就消失
    （语速 +8% 的长句尤其明显）。v0.2 默认不钳；需要钳短时显式传 max_hold。
    """
    s = scenes[i]
    t0 = s["voice_start"] - 0.12
    t1 = s["voice_end"] + 0.45
    if max_hold:
        t1 = min(t1, s["voice_start"] + float(max_hold))
    t1 = min(t1, s["end"] - 0.06)
    nxt = scenes[i + 1]["voice_start"] - 0.12 if i + 1 < len(scenes) else 1e9
    return t0, min(t1, nxt)


def sub_at(scenes, t, max_hold=None):
    for i, s in enumerate(scenes):
        t0, t1 = sub_window(scenes, i, max_hold)
        if t0 <= t <= t1:
            return s.get("sub") or s.get("text", "")
    return ""


def write_srt(scenes, path, max_hold=None):
    def fmt(x):
        ms = int(round(x * 1000))
        h, ms = ms // 3600000, ms % 3600000
        m, ms = ms // 60000, ms % 60000
        s, ms = ms // 1000, ms % 1000
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)

    rows = []
    for i, s in enumerate(scenes):
        t0, t1 = sub_window(scenes, i, max_hold)
        body = s.get("sub") or s.get("text", "")
        rows.append("%d\n%s --> %s\n%s\n" % (i + 1, fmt(t0), fmt(t1), body))
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(rows))
    return len(rows)


def write_ass(scenes, path, max_hold=None, font_name="Microsoft YaHei", size=52):
    """直接写 ASS，并在头里声明 PlayResX/Y=1920/1080。

    这样交 libass 不会按 PlayResY=288 反推，避免"字号小得看不见"
    （v0.1 的已知坑：SRT 交 libass 需要 FontSize/MarginV 乘 3.75）。
    """
    def fmt(x):
        cs = int(round(x * 100))
        h, cs = cs // 360000, cs % 360000
        m, cs = cs // 6000, cs % 6000
        s, cs = cs // 100, cs % 100
        return "%d:%02d:%02d.%02d" % (h, m, s, cs)

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % W,
        "PlayResY: %d" % H,
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,%s,%d,&H00FCF8F5,&H000000FF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,"
        "1,3,1,2,60,60,56,1" % (font_name, int(size)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = []
    for i, s in enumerate(scenes):
        t0, t1 = sub_window(scenes, i, max_hold)
        txt = (s.get("sub") or s.get("text", "")).replace("\n", "\\N")
        body.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s" % (fmt(t0), fmt(t1), txt))
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(head + body) + "\n")
    return len(body)


# ---------------------------------------------------------------- 帧写入
class FrameSink:
    """PIL 帧 -> rawvideo 管道 -> libx264。

    strict=True（默认）时尺寸不符直接报错：v0.1 会静默 resize，
    上限内看着没事，下游却拿不到"场景写错了分辨率"的信号。
    """

    def __init__(self, out_path, w=W, h=H, fps=FPS, crf=18, preset="medium",
                 audio=None, bitrate=None, strict=True, extra_args=None):
        self.out = str(out_path)
        self.w, self.h, self.fps = int(w), int(h), float(fps)
        self.strict = strict
        self.frames = 0
        d = os.path.dirname(os.path.abspath(self.out))
        if d:
            os.makedirs(d, exist_ok=True)
        cmd = [FFMPEG, "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (self.w, self.h),
               "-r", "%g" % self.fps, "-i", "-"]
        if audio:
            cmd += ["-i", str(audio)]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-g", str(int(self.fps * 2)),
                "-keyint_min", str(int(self.fps * 2)), "-sc_threshold", "0"]
        if bitrate:
            cmd += ["-maxrate", bitrate, "-bufsize", bitrate]
        if audio:
            ad = media_dur(audio)
            if ad:
                cmd += ["-t", "%.3f" % ad]      # 显式 -t；不用 -shortest（会按较短一路截断）
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-movflags", "+faststart", self.out]
        if extra_args:
            cmd += list(extra_args)
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def write(self, frame):
        if frame is None:
            raise RuntimeError("场景返回了 None（帧 %d）" % self.frames)
        if frame.size != (self.w, self.h):
            if self.strict:
                raise RuntimeError("帧尺寸 %dx%d != 期望 %dx%d（场景函数必须返回目标分辨率）"
                                   % (frame.size[0], frame.size[1], self.w, self.h))
            frame = frame.resize((self.w, self.h), Image.LANCZOS)
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        self.proc.stdin.write(frame.tobytes())
        self.frames += 1

    def close(self):
        self.proc.stdin.close()
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError("ffmpeg 编码失败: "
                               + self.proc.stderr.read().decode("utf-8", "replace")[:800])
        return self.out


# ---------------------------------------------------------------- 合成与封装
def media_dur(path):
    p = _run([ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)])
    try:
        return float((p.stdout or b"0").decode().strip() or 0)
    except Exception:
        return 0.0


def probe(path):
    p = _run([ffprobe_path(), "-v", "error",
              "-show_entries", "format=duration,size,bit_rate",
              "-show_entries", "stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames,"
              "sample_rate,channels",
              "-of", "json", str(path)])
    try:
        return json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}


def _video_copy_args():
    return ["-c:v", "copy"]


def mux(video, audio, out, crf=18, preset="medium", audio_bitrate="192k",
        loudnorm=True, target_i=-16.0, target_tp=-1.5, reencode_video=False):
    """合成成片：视频流默认直接 copy（不再二次编码），音频走两遍 loudnorm。

    v0.1 对已经 CRF18 编好的 silent.mp4 又编了一遍 libx264，既有二次有损
    又是纯浪费时间。视频里没有需要再次编码的信息，copy 是正确选择；
    需要改分辨率/插字幕时才用 reencode_video=True。
    """
    d = os.path.dirname(os.path.abspath(str(out)))
    if d:
        os.makedirs(d, exist_ok=True)
    af = loudnorm_two_pass(audio, target_i=target_i, target_tp=target_tp) if loudnorm else None

    vd = media_dur(video)
    ad = media_dur(audio)
    if vd and ad and abs(vd - ad) > 0.05:
        raise RuntimeError("音视频时长不一致：video %.3fs vs audio %.3fs（差 %.3fs）。"
                           "检查混音总线长度是否等于 timeline.total。"
                           % (vd, ad, abs(vd - ad)))
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(audio)]
    if reencode_video:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    if af:
        cmd += ["-af", af]
    cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", str(SR)]
    if vd:
        cmd += ["-t", "%.3f" % vd]
    cmd += ["-movflags", "+faststart", str(out)]
    p = _run(cmd)
    if p.returncode != 0:
        raise RuntimeError("mux 失败: " + p.stderr.decode("utf-8", "replace")[:800])
    return str(out)


def loudnorm_two_pass(src, target_i=-16.0, target_tp=-1.5, target_lra=11.0):
    """两遍 loudnorm：先测量，再按测量值归一化。返回可直接给 -af 的滤镜串。"""
    p = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(src), "-af",
              "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:print_format=json"
              % (target_i, target_tp, target_lra), "-f", "null", "-"])
    txt = p.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except Exception:
        return None
    return ("loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:measured_I=%s:measured_TP=%s:"
            "measured_LRA=%s:measured_thresh=%s:offset=%s:print_format=summary"
            % (target_i, target_tp, target_lra, j.get("input_i"), j.get("input_tp"),
               j.get("input_lra"), j.get("input_thresh"), j.get("target_offset", 0.0)))


def loudness(path):
    """EBU R128 积分响度 + 真峰。v0.1 用 ebur128 默认参数拿不到 Peak，导致真峰门禁永远不触发。"""
    p = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
              "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    txt = p.stderr.decode("utf-8", "replace")
    out = {}
    m = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", txt)
    if m:
        out["I"] = float(m[-1])
    m = re.findall(r"LRA:\s*(-?[\d.]+)\s*LU", txt)
    if m:
        out["LRA"] = float(m[-1])
    m = re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", txt)
    if m:
        out["TP"] = float(m[-1])
    return out


def detect_black(path, dur=0.4, head=1.2, tail=None):
    """长黑帧检测。head/tail 秒内是刻意的淡入淡出，不算问题。"""
    p = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vf",
              "blackdetect=d=%s:pic_th=0.98" % dur, "-f", "null", "-"])
    total = media_dur(path)
    tl = tail if tail is not None else head
    bad = 0
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)",
                         p.stderr.decode("utf-8", "replace")):
        s, e = float(m.group(1)), float(m.group(2))
        if s >= head and (total <= 0 or e <= total - tl):
            bad += 1
    return bad


def detect_silence(path, th=-45, dur=1.2):
    p = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af",
              "silencedetect=n=%ddB:d=%s" % (th, dur), "-f", "null", "-"])
    return len(re.findall("silence_start", p.stderr.decode("utf-8", "replace")))


def qa_text_safe(scenes, tol=1.0, band=SUB_BAND):
    """字幕几何校验：宽度/行数/是否触及画面底部。"""
    errs = []
    for s in scenes:
        body = s.get("sub") or s.get("text") or ""
        if not body:
            continue
        lines, f = fit_sub_lines(body, max_w=band[2] - band[0])
        widest = max(text_w(l, f) for l in lines)
        total_h = int(f.size * 1.32) * len(lines)
        if len(lines) > 2:
            errs.append("字幕超过 2 行（%d 行）: %s" % (len(lines), body[:24]))
        if widest > band[2] - band[0]:
            errs.append("字幕过宽 %dpx: %s" % (widest, body[:24]))
        if band[3] - total_h < band[1]:
            errs.append("字幕过高 %dpx: %s" % (total_h, body[:24]))
        if band[3] > H - 12:
            errs.append("字幕带触及画面底部，有被裁风险")
    return errs


# ---------------------------------------------------------------- 编码质量
def sharpness(png_or_img):
    """文字/线条锐度：Laplacian 方差。"""
    import cv2
    if isinstance(png_or_img, str):
        im = cv2.imread(str(png_or_img), cv2.IMREAD_GRAYSCALE)
    else:
        im = np.array(png_or_img.convert("L"))
    if im is None:
        return 0.0
    return float(cv2.Laplacian(im, cv2.CV_64F).var())


def snap(path, seconds, out_png):
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    _run([FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % float(seconds), "-i", str(path),
          "-frames:v", "1", str(out_png)])
    return out_png


def encode_quality(film, ref_png, at_sec=None, ref_img=None):
    """同帧"原始渲染"vs"解码帧"的锐度比。

    ⚠ 关键：ref 必须是场景函数渲染出的原图，不能用成片自己抽的帧，
    否则 sharp_ref 与 sharp_dec 几乎相同、ratio 恒为 1，门禁形同虚设
    （v0.1 就是这么写的，本片 0.922 的真实值需要正确参考帧才测得出来）。
    """
    import cv2
    if ref_img is not None:
        ref = np.array(ref_img.convert("L"))
        ref_dir = os.path.dirname(os.path.abspath(str(ref_png))) if ref_png else os.getcwd()
    else:
        ref = cv2.imread(str(ref_png), cv2.IMREAD_GRAYSCALE)
        ref_dir = os.path.dirname(os.path.abspath(str(ref_png)))
    if ref is None:
        return None
    if not os.path.isdir(ref_dir):
        ref_dir = os.getcwd()
    tmp = os.path.join(ref_dir, "_dec_tmp.png")
    snap(film, at_sec if at_sec is not None else 0.0, tmp)
    dec = cv2.imread(tmp, cv2.IMREAD_GRAYSCALE)
    if dec is None:
        return None
    sa = float(cv2.Laplacian(ref, cv2.CV_64F).var())
    sb = float(cv2.Laplacian(dec, cv2.CV_64F).var())
    return dict(sharp_ref=round(sa, 1), sharp_dec=round(sb, 1),
                ratio=round(sb / sa, 4) if sa > 0 else 0.0)


def contact_sheet(frames, out_png, cols=4, thumb_w=480, labels=None):
    if not frames:
        return None
    ims = [Image.open(f) if isinstance(f, str) else f for f in frames]
    tw = int(thumb_w)
    th = int(tw * H / W)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (12, 14, 18))
    for i, im in enumerate(ims):
        sheet.paste(im.resize((tw, th), Image.LANCZOS), ((i % cols) * tw, (i // cols) * th))
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    sheet.save(out_png)
    return out_png


def concat_segments(parts, out, workdir=None):
    """分段无损拼接（各段编码参数必须一致，否则 copy 拼接会失败或花屏）。"""
    lst = os.path.join(workdir or os.path.dirname(str(out)), "concat.txt")
    d = os.path.dirname(os.path.abspath(lst))
    if d:
        os.makedirs(d, exist_ok=True)
    # 必须写绝对路径：concat 文件里的相对路径按 ffmpeg 的 cwd 解析，
    # 而不是按 concat.txt 所在目录（实测会拼成 work2/work2/... 打不开）。
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            ap = os.path.abspath(str(p))
            f.write("file '%s'\n" % ap.replace("\\", "/"))
    p = _run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
              "-c", "copy", str(out)])
    if p.returncode != 0:
        raise RuntimeError("concat 失败: " + p.stderr.decode("utf-8", "replace")[:600])
    return str(out)


# ---------------------------------------------------------------- 总质检
def qa_report(path, expect_frames=None, ref_png=None, ref_sec=None, ref_img=None,
              expected_subtitles=None, min_bitrate=0, notes=None):
    """分档质检：errors 必须修，warnings 看情况。

    v0.2 修正：
    - 真峰现在能测到（loudness 加 peak=true），<= -1 dBTP 才是真门禁；
    - 码率默认不再当"下限"（暗色简洁内容本来就只有 0.5Mbps，v0.1 会误判）；
    - 新增字幕条数校验（expected_subtitles），这是"静默丢字幕"的唯一检查点。
    """
    errors, warns = [], []
    pr = probe(path)
    fmt = pr.get("format", {})
    streams = pr.get("streams") or [{}]
    vs = [s for s in streams if s.get("codec_type") == "video"] or [{}]
    au = [s for s in streams if s.get("codec_type") == "audio"]
    v = vs[0]
    if v.get("width") != W or v.get("height") != H:
        errors.append("分辨率 %sx%s != %dx%d" % (v.get("width"), v.get("height"), W, H))
    rate = str(v.get("r_frame_rate") or "")
    if rate and rate not in ("0/0",):
        try:
            num, _, den = rate.partition("/")
            got = float(num) / float(den or 1)
            if abs(got - float(FPS)) > 0.05:
                errors.append("帧率 %.3f != %d（SKILL 要求严格一致）" % (got, FPS))
        except Exception:
            pass
    if not au:
        errors.append("无音轨")
    else:
        sr = int(au[0].get("sample_rate") or 0)
        if sr and sr != SR:
            warns.append("音轨采样率 %d != %d" % (sr, SR))
    br = int(fmt.get("bit_rate") or 0)
    if min_bitrate and br and br < min_bitrate:
        warns.append("码率 %.2f Mbps 低于参考值 %.0f Mbps（暗色简洁内容可接受，看锐度比）"
                     % (br / 1e6, min_bitrate / 1e6))
    if fmt.get("duration") and expect_frames:
        d = float(fmt["duration"])
        if abs(d - expect_frames / float(FPS)) > 0.5:
            errors.append("时长 %.2fs 与预期 %.2fs 不符" % (d, expect_frames / float(FPS)))
    lo = loudness(path)
    if "I" in lo:
        if lo["I"] > -12:
            errors.append("响度 %.1f LUFS 过大（目标 -16 ~ -14）" % lo["I"])
        elif lo["I"] < -19:
            errors.append("响度 %.1f LUFS 过小（目标 -16 ~ -14）" % lo["I"])
        elif lo["I"] < -17.2 or lo["I"] > -14.5:
            warns.append("响度 %.1f LUFS 略偏（目标 -16 ± 1）" % lo["I"])
    if "TP" in lo:
        if lo["TP"] > -1.0:
            errors.append("真峰 %.1f dBTP 超限（目标 <= -1 dBTP）" % lo["TP"])
        elif lo["TP"] > -1.4:
            warns.append("真峰 %.1f dBTP 余量不足（AAC 编码后可能更高）" % lo["TP"])
    else:
        warns.append("未能测到真峰（ebur128 解析失败），责任门禁缺一项")
    nb = detect_black(path)
    if nb > 0:
        warns.append("首尾之外检测到 %d 段长黑帧" % nb)
    if detect_silence(path) > 0:
        warns.append("检测到长静音段（确认是否有意留白）")
    if ref_img is not None or (ref_png and os.path.exists(ref_png)):
        q = encode_quality(path, ref_png, ref_sec, ref_img=ref_img)
        if q:
            if q["ratio"] < 0.85:
                errors.append("编码损失偏大：锐度比 %.3f（<0.85 文字会发虚，降 CRF 或提 preset）"
                              % q["ratio"])
            elif q["ratio"] < 0.92:
                warns.append("编码锐度比 %.3f（0.85~0.92，可接受但可更好）" % q["ratio"])
    if expected_subtitles is not None:
        got = 0
        srt = os.path.join(os.path.dirname(os.path.abspath(str(path))), "..", "sub.srt")
        for cand in (notes.get("srt") if isinstance(notes, dict) else None,
                     str(notes) if isinstance(notes, str) else None,
                     srt):
            if cand and os.path.exists(str(cand)):
                got = len([b for b in open(str(cand), encoding="utf-8").read().strip().split("\n\n")
                           if b.strip()])
                break
        if got and got != int(expected_subtitles):
            errors.append("字幕条数 %d != 剧本句数 %d（有字幕被静默丢弃）" % (got, expected_subtitles))
    return dict(errors=errors, warnings=warns, probe=pr, loudness=lo)
