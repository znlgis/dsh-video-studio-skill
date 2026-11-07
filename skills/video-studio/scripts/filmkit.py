# -*- coding: utf-8 -*-
"""filmkit.py — DeepSeek 4.1 Flash + DSH 视频生产内核（v0.3.0）。

设计原则（v0.2.0 起，全部来自一次 11 分钟真实成片的复盘）：
1. 质检门禁必须真的会失败。门禁失效比没有门禁更危险。
2. 缓存必须校验输入。只判断"文件存在"会让"改一句台词不生效"静默发生。
3. 失败要报错，不要静默降级。尺寸不符、缺字形、峰值爆表都要看得见。
4. 公共函数可独立测试：不依赖当前目录、不依赖上一次调用留下的状态。

v0.3.0 新增（声音与提示点，借鉴 ai-history-film）：
5. CueBus：画面登记提示点 -> work/cues.json -> 按提示点合成音效轨（声音跟着画面走，不靠"对齐猜"）。
6. 立体声总线：AudioBus(channels=2) / wav_write 自动识别单双声道；配乐与音效可以声像。
7. hold：每句语音结束后额外停留秒数（"让观众看清楚"的停留窗口，不用整片放慢）。
8. mix_film / duck：旁白 + 配乐 + 音效三总线混音，音效响起时配乐自动让位。
"""
from __future__ import annotations

__version__ = "0.3.0"

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
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

# v0.3.0：提示点里好写的语义别名 -> sfxkit 配方名（sfxkit 自己也有同一张表，两边保持一致）
CUE_ALIASES = {
    "blip": "ui_blip", "click": "ui_click", "relay": "relay_click", "chirp": "data_chirp",
    "sub": "sub_drop", "reverse": "reverse_swell", "rewind": "tape_rewind",
    "stone": "go_stone", "typing": "typing", "stream": "text_stream", "wind": "wind",
}


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
def wav_write(path, samples, sr=SR, channels=None):
    """写 16-bit PCM WAV。channels=None 时按数组形状自动判断（二维 = 立体声）。

    v0.3.0：支持立体声（配乐/音效要声像）。老调用 wav_write(p, mono) 行为不变。
    """
    a = np.asarray(samples, dtype=np.float32)
    if a.ndim == 2:
        n_ch = a.shape[1]
        if channels is None:
            channels = n_ch
        elif int(channels) != n_ch:
            raise ValueError("wav_write: 数组有 %d 列，但 channels=%s" % (n_ch, channels))
        a = np.clip(a, -1.0, 1.0)
        inter = a.reshape(-1)                       # 交错排列（WAV 要求）
    else:
        a = np.clip(np.ravel(a), -1.0, 1.0)
        inter = a
        if channels is None:
            channels = 1
    peak = float(np.max(np.abs(inter))) if inter.size else 0.0
    pcm = (inter * 32767.0).astype(np.int16)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(int(channels))
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return peak


def wav_read(path):
    """读 PCM WAV（16 / 24 / 32-bit），返回 float32 数组：单声道 (n,) / 立体声 (n,2)。

    v0.3.0 起支持 24-bit：scorekit 落盘默认 PCM_24（多 8 dB 动态余量），
    而 v0.2 的 wav_write 只写 16-bit。读取端必须两种都吃。
    """
    with wave.open(str(path), "rb") as w:
        n_ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)).astype(np.int32)
        v = np.where(v >= 0x800000, v - 0x1000000, v)          # 24-bit 符号扩展
        a = (v.astype(np.float32) / 8388607.0)
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483647.0
    else:
        raise ValueError("wav_read 只支持 16/24/32-bit PCM（%s 是 %d 字节）" % (path, sw))
    if n_ch > 1:
        a = a.reshape(-1, n_ch)
    return a, sr


def decode_pcm(path, sr=SR, channels=1):
    """解码成 float32 PCM：channels=1 -> (n,)，channels=2 -> (n,2)。"""
    ch = max(1, int(channels))
    p = _run([FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le",
              "-ac", str(ch), "-ar", str(sr), "-"])
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("解码失败 %s: %s" % (path, p.stderr.decode("utf-8", "replace")[:300]))
    a = np.frombuffer(p.stdout, dtype=np.float32).copy()
    if ch > 1:
        a = a[: (len(a) // ch) * ch].reshape(-1, ch)
    return a


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
    """采样级音频总线：确定性、1/SR 精度、可单层重放、可中途打印峰值。

    channels=1 为单声道（v0.2 默认行为，向后兼容）；channels=2 时每个来源自动展开成
    立体声：单声道来源用 pan 定位，立体声来源原样保留。配乐/音效要做声像时必须用 2。
    """

    def __init__(self, total, sr=SR, channels=1):
        self.sr = int(sr)
        self.total = float(total)
        self.channels = max(1, int(channels))
        n = int(round(self.total * self.sr)) + self.sr
        self.bus = np.zeros(n if self.channels == 1 else (n, self.channels), dtype=np.float32)
        self.log = []

    def _fit(self, src):
        """把任意来源变成总线的采样形状（单声道 / 立体声）。"""
        s = np.asarray(src, dtype=np.float32)
        if s.ndim == 0:
            s = s.reshape(1)
        if self.channels == 1:
            return s if s.ndim == 1 else s.mean(axis=1)
        if s.ndim == 1:
            return np.stack([s, s], axis=1)      # 单声道来源：等功率复制，保持能量
        if s.shape[1] == 1:
            return np.concatenate([s, s], axis=1)
        if s.shape[1] > 2:
            return s[:, :2]
        return s

    def place(self, src, at, gain=1.0, loop=False, fade_in=0.0, fade_out=0.0, pan_pos=None):
        if src is None or len(src) == 0:
            return self
        i0 = int(round(float(at) * self.sr))
        if i0 >= len(self.bus):
            return self
        s = np.asarray(src, dtype=np.float32)
        if pan_pos is not None and self.channels == 2 and s.ndim == 1:
            theta = (float(np.clip(pan_pos, -1.0, 1.0)) + 1.0) * np.pi / 4.0
            s = np.stack([s * np.cos(theta), s * np.sin(theta)], axis=1)
        s = np.array(self._fit(s), dtype=np.float32, copy=True)
        if fade_in > 0:
            n = min(len(s), max(1, int(fade_in * self.sr)))
            g = np.linspace(0, 1, n, dtype=np.float32)
            s[:n] *= g if s.ndim == 1 else g[:, None]
        if fade_out > 0:
            n = min(len(s), max(1, int(fade_out * self.sr)))
            g = np.linspace(1, 0, n, dtype=np.float32)
            s[len(s) - n:] *= g if s.ndim == 1 else g[:, None]
        if loop and i0 + len(s) < len(self.bus):
            reps = int(np.ceil((len(self.bus) - i0) / max(1, len(s))))
            s = np.tile(s, (reps, 1)) if s.ndim == 2 else np.tile(s, reps)
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
            g_in = np.linspace(0, 1, n, dtype=np.float32)
            g_out = np.linspace(1, 0, n, dtype=np.float32)
            if out.ndim == 1:
                out[:n] *= g_in
                out[-n:] *= g_out
            else:
                out[:n] *= g_in[:, None]
                out[-n:] *= g_out[:, None]
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


# ---------------------------------------------------------------- 提示点（v0.3.0）
# 思路来自 ai-history-film：画面在"该响一下"的地方登记一个提示点，音频阶段按提示点合成音效轨。
# 这样声音跟着画面走，不需要人肉对时间；换 BPM/改停留窗口后，提示点仍然对得上。

CUE_BASE_DB = {
    "ui_blip": -10, "ui_click": -10, "relay_click": -8, "typing": -10, "typing_at": -10,
    "text_stream": -13, "data_chirp": -12, "whoosh": -13, "impact": -5, "sub_drop": -8,
    "riser": -9, "reverse_swell": -9, "glitch": -10, "tape_rewind": -6, "worldline_shift": -9,
    "wind": -12, "rain": -12, "campfire": -12, "go_stone": -8, "roulette_tick": -10,
    "answer_chime": -10, "knock_wood": -8, "metal_hit": -8, "glass_clink": -10,
}


def cue_time(at, scenes=None, total=None):
    """把提示点的 at 解析成成片绝对秒。

    支持三种写法（CONTRACT.md §4.1）：
      "abs:12.5" / 12.5      -> 绝对秒
      "l03+0.35" / "l03-0.2" -> 场景 id 加/减偏移（相对场景 start）
      "l03"                  -> 场景起点
    """
    if isinstance(at, (int, float)):
        return round(float(at), 3)
    s = str(at).strip()
    if not s:
        raise ValueError("cue at 为空")
    if s.startswith("abs:"):
        return round(float(s[4:]), 3)
    try:
        return round(float(s), 3)
    except ValueError:
        pass
    # 形如 <id>[+-]<offset>
    cut = len(s)
    for i, ch in enumerate(s):
        if ch in "+-" and i > 0:
            cut = i
            break
    sid = s[:cut]
    off = float(s[cut:] or 0.0) if cut < len(s) else 0.0
    if scenes:
        for sc in scenes:
            if str(sc.get("id")) == sid:
                return round(float(sc["start"]) + off, 3)
    raise ValueError("cue at=%r：找不到场景 %r（先跑 layout，或改用 abs:秒）" % (at, sid))


def scenes_anchors(scenes):
    """给 scorekit 分段用的锚点：[{id, kind, start, end}]。"""
    return [dict(id=s.get("id"), kind=s.get("kind") or "body",
                 start=float(s["start"]), end=float(s["end"])) for s in scenes]


class CueBus:
    """提示点登记表：画面侧登记 -> 落盘 cues.json -> 音频侧按它合成音效。

    film.json 的 sfx.cues 与 scenes.py 的可选 cues() 钩子统一走这里；
    浏览器引擎则由页面 window.getCues() 导出同 schema 的 JSON（CONTRACT.md §4.2）。
    """

    def __init__(self, scenes=None, total=None):
        self.scenes = scenes or []
        self.total = None if total is None else float(total)
        self.items = []

    # -- 登记 --
    def add(self, at, type, **params):
        t = cue_time(at, self.scenes, self.total)
        kind = CUE_ALIASES.get(str(type), str(type))
        row = dict(params)
        row.update(t=round(t, 3), type=kind)
        if "src" not in row:
            row["src"] = "pil"
        self.items.append(row)
        return self

    def extend(self, items):
        for it in items or []:
            if not it:
                continue
            row = dict(it)
            at = row.pop("at", row.pop("t", None))
            if at is None:
                raise ValueError("提示点缺少 at/t：%r" % (it,))
            self.add(at, row.pop("type"), **row)
        return self

    # -- 解析 --
    def resolved(self):
        """按时间排序、去掉明显无效项（超过片长 2 秒的丢弃并告警）。"""
        out = []
        for row in self.items:
            t = float(row["t"])
            if t < -0.05:
                continue
            if self.total is not None and t > self.total + 2.0:
                continue
            out.append(row)
        return sorted(out, key=lambda r: float(r["t"]))

    def to_json(self, duration=None, fps=None, width=None, height=None, events=None):
        dur = self.total if duration is None else float(duration)
        return dict(version=1, duration=round(dur, 3),
                    fps=int(FPS if fps is None else fps),
                    width=int(W if width is None else width),
                    height=int(H if height is None else height),
                    cues=self.resolved(), events=list(events or []))

    def write(self, path, duration=None, events=None, **kw):
        payload = self.to_json(duration=duration, events=events, **kw)
        d = os.path.dirname(os.path.abspath(str(path)))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        return payload

    @classmethod
    def from_json(cls, path):
        with open(str(path), encoding="utf-8") as fh:
            d = json.load(fh)
        bus = cls(total=d.get("duration"))
        for c in d.get("cues") or []:
            row = dict(c)
            row.setdefault("src", "file")
            bus.items.append(row)
        return bus

    # -- 合成 --
    def build_audio(self, total=None, sr=SR, base_db=None, default_gain_db=0.0,
                    seed_base=1000, verbose=True):
        """按提示点合成音效轨 -> (n,2) float32。

        依赖 scripts/sfxkit.py（v0.3.0 新增）。缺 sfxkit 时直接报错，不静默出哑片。
        未知提示点类型：告警一次并跳过（不 crash 整片混音）。
        """
        try:
            import sfxkit as SX
        except Exception as e:                              # noqa: BLE001
            raise RuntimeError(
                "合成音效轨需要 sfxkit.py（应与 filmkit.py 同目录）：%s" % e) from e
        dur = float(self.total if total is None else total)
        base = dict(CUE_BASE_DB)
        if base_db:
            base.update(base_db)
        reg = SX.load_all() if hasattr(SX, "load_all") else {}
        n = int(round(dur * sr)) + int(sr * 0.5)
        bus = np.zeros((n, 2), dtype=np.float32)
        unknown, placed = set(), 0
        for i, c in enumerate(self.resolved()):
            kind = str(c.get("type"))
            if kind not in reg:
                unknown.add(kind)
                continue
            params = {k: v for k, v in c.items()
                      if k not in ("t", "type", "src", "gain", "pan", "base_db")}
            params.pop("dur_hint", None)
            try:
                x = SX.render(kind, seed=int(seed_base) + i, **params)
            except TypeError:
                # 兼容严格签名的配方：退化成只传 seed
                x = SX.render(kind, seed=int(seed_base) + i)
            if x is None or len(x) == 0:
                continue
            x = np.asarray(x, dtype=np.float32)
            if x.ndim == 1:
                p = c.get("pan")
                theta = ((float(np.clip(p, -1, 1)) if p is not None else 0.0) + 1.0) * np.pi / 4.0
                x = np.stack([x * np.cos(theta), x * np.sin(theta)], axis=1)
            if x.shape[1] > 2:
                x = x[:, :2]
            g = float(base.get(kind, -10.0)) + float(c.get("gain", 0.0) or 0.0) + default_gain_db
            s = int(round(float(c["t"]) * sr))
            if s < 0 or s >= n:
                continue
            e = min(n, s + len(x))
            bus[s:e] += (10.0 ** (g / 20.0)) * x[: e - s]
            placed += 1
        if unknown and verbose:
            print("   [WARN] 未知提示点类型（已跳过）: %s" % ", ".join(sorted(unknown)))
        if verbose:
            print("   音效轨：%d 个提示点落位 / %d 个登记（%.2fs）"
                  % (placed, len(self.items), dur))
        return bus[: int(round(dur * sr))]


def duck(music, sfx, depth_db=4.0, attack=0.03, release=0.35, sr=SR):
    """音效响起时配乐自动让位（最多 depth_db），避免重击被配乐吃掉。

    用分块包络实现（向量化），不做逐采样循环：块内线性插值出增益曲线。
    """
    m = np.asarray(music, dtype=np.float32)
    s = np.asarray(sfx, dtype=np.float32)
    if m.ndim == 1:
        m2 = np.stack([m, m], axis=1)
    else:
        m2 = m
    env = np.abs(s).max(axis=1) if s.ndim == 2 else np.abs(s)
    n = len(m2)
    env = env[:n]
    if env.size == 0 or float(env.max()) <= 1e-9:
        return m2
    block = max(1, int(sr * 0.01))
    nb = max(1, n // block)
    e = env[: nb * block].reshape(nb, block).max(axis=1)
    e = e / (float(np.percentile(e, 99.5)) + 1e-9)
    e = np.clip(e, 0.0, 1.0)
    # 单向平滑：起音快、释放慢（对包络做两次不同时间常数的指数平滑，仍保持向量化）
    def _smooth(x, tau_s):
        k = max(1, int(sr * tau_s / block))
        w = np.ones(k, dtype=np.float32) / k
        return np.convolve(x, w, mode="same")
    e = np.maximum(_smooth(e, attack), _smooth(e, release) * 0.7)
    gains = 10.0 ** (-float(depth_db) * np.clip(e, 0, 1) / 20.0)
    curve = np.interp(np.arange(n), np.arange(nb) * block + block / 2.0, gains).astype(np.float32)
    return (m2 * curve[:, None]).astype(np.float32)


def mix_film(voice, music=None, sfx=None, total=None, sr=SR,
             voice_gain=1.0, music_gain_db=0.0, sfx_gain_db=0.0,
             duck_db=3.0, peak_target=0.89, fade=0.4, verbose=True):
    """三总线混音：旁白 + 配乐 + 音效 -> 立体声 float32。

    顺序照 ai-history-film 的成片混音：音乐比音效低一点，音效响时音乐让位，
    最后统一做峰值保护（真正的响度标准化交给 ffmpeg 的 loudnorm）。
    """
    def _to_stereo(x):
        a = np.asarray(x, dtype=np.float32)
        if a.ndim == 1:
            return np.stack([a, a], axis=1)
        return a if a.shape[1] >= 2 else np.concatenate([a, a], axis=1)

    n = None
    v = None if voice is None else _to_stereo(voice)
    m = None if music is None else _to_stereo(music)
    s = None if sfx is None else _to_stereo(sfx)
    for x in (v, m, s):
        if x is not None:
            n = len(x) if n is None else min(n, len(x))
    if total is not None:
        want = int(round(float(total) * sr))
        n = want if n is None else min(n, want)
    if not n:
        return np.zeros((0, 2), dtype=np.float32)

    out = np.zeros((n, 2), dtype=np.float32)
    if s is not None:
        out += s[:n] * (10.0 ** (float(sfx_gain_db) / 20.0))
    if m is not None:
        mm = m[:n] * (10.0 ** (float(music_gain_db) / 20.0))
        if s is not None:
            mm = duck(mm, s[:n], depth_db=duck_db, sr=sr)
        else:
            mm = _to_stereo(mm)
        out += mm
    if v is not None:
        out += v[:n] * float(voice_gain)

    raw = float(np.max(np.abs(out))) if out.size else 0.0
    if fade > 0:
        k = min(n, max(1, int(fade * sr)))
        g_in = np.linspace(0, 1, k, dtype=np.float32)[:, None]
        g_out = np.linspace(1, 0, k, dtype=np.float32)[:, None]
        out[:k] *= g_in
        out[-k:] *= g_out
    if raw > 1e-9 and raw > peak_target:
        out *= peak_target / raw
    if verbose:
        print("   混音: 叠加峰值 %.2f -> 输出峰值 %.3f（旁白%s 配乐%s 音效%s）"
              % (raw, float(np.max(np.abs(out))) if out.size else 0.0,
                 "有" if v is not None else "无", "有" if m is not None else "无",
                 "有" if s is not None else "无"))
    return out


# ---------------------------------------------------------------- 配乐来源判定
MUSIC_PLANS = ("score", "file", "bed", "off", "none", "missing-file", "missing-bpm", "unknown-mode")


def music_plan(script, has_music_file: bool = False):
    """统一判定"这次配乐从哪来"，返回 (plan, 说明)。

    为什么要一个统一函数：v0.3.0 的 st_score 说"mode=off 不生成配乐"，但 st_audio
    又无条件读已存在的 work/music.wav —— 结果用户把 mode 改成 off / 删掉 bpm 之后，
    上一轮跑出来的旧配乐仍被混进成片（README/SKILL 写的是 off = 不要配乐）。
    现在 score 与 audio 两个 stage 都用这一份判定，口径不会再打架。

    plan 取值：
      score         scorekit 程序化配乐（work/music.wav）
      file          外部提供 / 上一轮留下的 work/music.wav
      bed           v0.2 和弦床（music.chords）
      off           明确不要配乐（mode=off/none 或 music.enabled=false）
      none          没配 bpm / chords / music.wav，本次没有配乐
      missing-file  mode=file 但没有 work/music.wav（调用方应明确报错）
      missing-bpm   mode=score 但没有 bpm（调用方应明确报错）
      unknown-mode  music.mode 写了不认识的值（调用方应明确报错）

    兼容语义（不写 music.mode，即 v0.2 老项目）：
      有 bpm -> score；没 bpm 但有外部 music.wav -> file；再退和弦床 -> bed。
    """
    mus = script.get("music") or {}
    raw = str(mus.get("mode") or "").strip().lower()
    bpm = script.get("bpm")
    if raw == "auto":
        raw = ""
    if raw in ("off", "none", "mute"):
        return "off", "music.mode=%s：本片不要配乐（不读 work/music.wav）" % raw
    if not raw and not mus.get("enabled", True):
        return "off", "music.enabled=false：本片不要配乐（不读 work/music.wav）"
    if raw in ("bed", "chord", "chords"):
        return "bed", "music.mode=bed：v0.2 和弦床（不读 work/music.wav）"
    if raw in ("file", "external", "provided"):
        if has_music_file:
            return "file", "music.mode=file：外部 work/music.wav"
        return "missing-file", ("music.mode=file 但 work/music.wav 不存在"
                                "（把外部配乐拷成 work/music.wav，或删掉 mode=file）")
    if raw in ("score", "programmatic"):
        if bpm in (None, ""):
            return "missing-bpm", "music.mode=score 但没有 bpm（scorekit 需要 bpm）"
        return "score", "music.mode=score：scorekit 程序化配乐 bpm=%s" % bpm
    if raw:
        return "unknown-mode", ("music.mode=%r 不认识（可用：score / bed / file / off，"
                                "或不写 mode 走兼容链）" % mus.get("mode"))
    # 缺省（v0.2 兼容链）
    if bpm not in (None, ""):
        return "score", "缺省配乐：有 bpm=%s，用 scorekit 程序化配乐" % bpm
    if has_music_file:
        return "file", "缺省配乐：沿用已有 work/music.wav（外部提供的配乐）"
    if mus.get("chords"):
        return "bed", "缺省配乐：没有 bpm，退回 v0.2 和弦床"
    return "none", "缺省配乐：没有 bpm / music.chords / work/music.wav，本片无配乐"


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
def build_timeline(lines, lead=0.55, tail=0.85, min_dur=3.0, tail_map=None, max_dur=None,
                   hold=0.0, hold_map=None, bar=0.0):
    """由实测语音时长反推时间轴。第一段 start 必须严格为 0。

    hold / hold_map（v0.3.0）：语音结束后额外停留的秒数——这就是 ai-history-film 的"停留窗口"：
      观众没看清的地方加停留，而不是把整片放慢。hold 为全局默认，hold_map 按 id 覆盖，
      行内 `hold` 字段优先级最高。bar > 0 时把每段时长向上取整到 bar 的整数倍
      （配乐小节对齐用，例如 bar=2.0 = 120 BPM 的一小节）。
    """
    scenes, t = [], 0.0
    for ln in lines:
        voice = float(ln.get("dur") or 0.0)
        extra = float(ln.get("hold", (hold_map or {}).get(ln["id"], hold)) or 0.0)
        d = max(float(min_dur), float(lead) + voice + float((tail_map or {}).get(ln["id"], tail)) + extra)
        if max_dur and d > float(max_dur):
            d = float(max_dur)
        if bar and float(bar) > 0:
            d = math.ceil(d / float(bar) - 1e-9) * float(bar)
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
    if loudnorm and not af:
        # 不静默降级：量不到就喊出来（qa 的响度门禁还在，但这里先说清楚）
        print("   [WARN] loudnorm 两遍测量失败（ffmpeg 输出里没解析到 JSON），本次不做响度标准化",
              flush=True)

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
# cv2 在 README / THIRD_PARTY / requirements.txt 里都写成**可选**，所以这里必须真的可选：
# 以前是函数内裸 `import cv2`，装了才算"可选"——没装时 --stage qa 直接 ImportError 崩掉，
# 而文档写"缺了跳过锐度门禁"。现在缺 cv2 时返回 None，由调用方打 WARN 并跳过该门禁。
def _cv2():
    """按需取 cv2；没装返回 None（调用方负责降级 + 报警，不静默）。"""
    try:
        import cv2
    except Exception:                                       # noqa: BLE001
        return None
    return cv2


def sharpness(png_or_img):
    """文字/线条锐度：Laplacian 方差。没装 cv2 时返回 None（不是 0.0，别把"没测"当"很差"）。"""
    cv2 = _cv2()
    if cv2 is None:
        return None
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

    没装 cv2 时返回 None（调用方据此跳过锐度门禁并 WARN）。
    """
    cv2 = _cv2()
    if cv2 is None:
        return None
    if ref_img is not None:
        ref = np.array(ref_img.convert("L"))
    else:
        ref = cv2.imread(str(ref_png), cv2.IMREAD_GRAYSCALE)
    if ref is None:
        return None
    # 解码帧写进系统临时目录：以前写在 cwd / 参考帧目录，跑一次 --stage qa
    # 就在项目根留一个 250KB 的 _dec_tmp.png（既污染目录，也可能被当成交付物）。
    with tempfile.TemporaryDirectory(prefix="filmkit_qa_") as td:
        tmp = os.path.join(td, "_dec_tmp.png")
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
    if "I" not in lo:
        warns.append("未能测到积分响度（ebur128 解析失败），响度门禁本次未执行")
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
    quality = None
    if ref_img is not None or (ref_png and os.path.exists(ref_png)):
        q = encode_quality(path, ref_png, ref_sec, ref_img=ref_img)
        quality = q
        if not q:
            if _cv2() is None:
                warns.append("没装 cv2（opencv-python-headless），锐度比门禁本次未执行——"
                             "这条门禁查的是「文字发不发虚」，装了才有；缺它不代表片子坏了，"
                             "但别把「没查」当成「通过了」")
            else:
                warns.append("锐度比计算失败（参考帧或抽帧不可读），编码锐度门禁本次未执行")
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
        if not got:
            warns.append("没有找到 sub.srt（或它是空的），字幕条数门禁本次未执行")
        elif got != int(expected_subtitles):
            errors.append("字幕条数 %d != 剧本句数 %d（有字幕被静默丢弃）" % (got, expected_subtitles))
    return dict(errors=errors, warnings=warns, probe=pr, loudness=lo, quality=quality)
