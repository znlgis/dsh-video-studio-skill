# -*- coding: utf-8 -*-
"""scenes.py — 场景绘制模板（v0.2.0）。

唯一约定：提供 draw_frame(scene, t_local, dur, t_global, script, frame) -> PIL.Image
  scene   : timeline.json 里的一段（含 id/text/sub/start/end/dur）
  t_local : 本段内已经过秒数
  dur     : 本段总秒数
  t_global: 全片秒数
  frame   : 全片帧号（确定性：同一帧号必须画出同一张图）

铁律（决定画质与可复现性）：
  1. 不许用 random / time.time()；要随机用 K.hash2 / K.hash3 / vnoise(seed)
  2. 每帧从零画，不依赖上一帧的变量
  3. 静态背景预渲染一次并缓存（见 _bg()），不要每帧重算渐变
  4. 文字排版一律用 K.wrap_cn_cached / K.text_w_cached：直接调 wrap_cn 会让单帧慢 2-3 倍
  5. 所有正文落在 K.SAFE 内（y <= 974），字幕由 render.py 统一画在 y >= 872
  6. 每段至少一个动态元素，静止不要超过 6 秒；镜头缓推 3-15%
  7. 缺字形会画成方块：用 K.glyph_missing_chars 自查，别写 ✓ × ↵ 这类符号（改用矢量线条）
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter
import numpy as np

import filmkit as K
from filmkit import W, H, clamp, lerp, smooth, ease_out, ease_in_out, rng

# ---------------- 主题（改这里换整片气质） ----------------
THEME = dict(
    bg_top=(7, 14, 26), bg_bot=(17, 33, 54),
    ink=(240, 245, 252), muted=(150, 174, 200), dim=(96, 118, 144),
    accent=(243, 132, 60), accent2=(86, 170, 232),
    good=(108, 178, 110), bad=(226, 96, 92),
)

_CACHE = {}


def _vgrad(w, h, top, bot):
    """竖直渐变背景 + 极轻网点（确定性），缓存复用。"""
    key = ("bg", w, h, top, bot)
    if key in _CACHE:
        return _CACHE[key]
    a = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    arr = np.array(top, dtype=np.float32)[None, :] * (1 - a[..., None]) \
        + np.array(bot, dtype=np.float32)[None, :] * a[..., None]
    arr = np.repeat(arr, w, axis=1)
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.clip(arr + (((np.sin(xx * 0.31) * np.sin(yy * 0.31)) > 0.955)
                         .astype(np.float32) * 5.0)[..., None], 0, 255)
    im = Image.fromarray(arr.astype(np.uint8), "RGB")
    _CACHE[key] = im
    return im


def _vignette_mask(w, h, strength=0.40):
    key = ("vig", w, h, strength)
    if key in _CACHE:
        return _CACHE[key]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2.0) / (w / 2.0)) ** 2 + ((yy - h / 2.0) / (h / 2.0)) ** 2) / 1.4142
    m = np.clip((r - 0.40) / 0.60, 0, 1) ** 1.6 * strength
    mask = Image.fromarray((m * 255).astype(np.uint8), "L")
    _CACHE[key] = mask
    return mask


def _bg():
    """预烘焙背景：渐变 + 暗角一次算好，每帧只 copy（实测省 ~4.5ms/帧）。"""
    if "baked" not in _CACHE:
        base = _vgrad(W, H, THEME["bg_top"], THEME["bg_bot"])
        _CACHE["baked"] = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)),
                                          base, _vignette_mask(W, H, 0.40))
    return _CACHE["baked"]


def _soft_dot(img, x, y, r, color, alpha=1.0):
    """柔边圆点精灵（预渲染一次，之后只 paste）。"""
    if r < 0.6 or alpha <= 0.004:
        return img
    key = ("dot", int(round(r * 2)), color)
    if key not in _CACHE:
        s = int(r * 2 + 6)
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([3, 3, s - 3, s - 3], fill=color + (255,))
        _CACHE[key] = im.filter(ImageFilter.GaussianBlur(max(1.0, r * 0.22)))
    spr = _CACHE[key]
    if alpha < 0.985:
        spr = spr.copy()
        spr.putalpha(spr.split()[3].point(lambda v: int(v * alpha)))
    img.paste(spr, (int(x - spr.width / 2), int(y - spr.height / 2)), spr)
    return img


def _hud(img, chapter, progress, title):
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, 0, int(W * clamp(progress)), 3], fill=THEME["accent"])
    d.rectangle([0, 0, W, 3], fill=(255, 255, 255, 11))
    f = K.font(27, "reg")
    tw = K.text_w_cached(chapter, 27, "reg")
    d.rounded_rectangle([64, 42, 64 + tw + 46, 96], radius=27,
                        fill=(4, 10, 20, 150), outline=(255, 255, 255, 40))
    d.ellipse([64 + 16, 62, 64 + 26, 72], fill=THEME["accent"])
    d.text((64 + 34, 56), chapter, font=f, fill=(228, 236, 246))
    f2 = K.font(23, "reg")
    tw2 = K.text_w_cached(title, 23, "reg")
    d.text((W - 64 - tw2, 58), title, font=f2, fill=(255, 255, 255, 96))
    return img


def _cover(img, t, dur, kicker, big, sub, progress, title):
    d = ImageDraw.Draw(img, "RGBA")
    p = ease_out(rng(t, 0.0, 0.9))
    p2 = ease_out(rng(t, 0.25, 1.3))
    d.text((150, 300 - 26 * (1 - p)), kicker, font=K.font(30, "bold"),
           fill=THEME["accent"] + (255,))
    y = 352
    for ln in K.wrap_cn_cached(big, 84, 1560, "bold"):
        d.text((150, y), ln, font=K.font(84, "bold"), fill=THEME["ink"] + (255,))
        y += 104
    if sub:
        for j, ln in enumerate(K.wrap_cn_cached(sub, 38, 1400, "light")):
            d.text((150, y + 26 + j * 56), ln, font=K.font(38, "light"),
                   fill=THEME["muted"] + (int(242 * p2),))
    return _hud(img, "01 · 开场", progress, title)


# ---------------- 场景 ----------------
def scene_hook(scene, t, dur, tg, script, frame):
    img = _bg().copy()
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = W // 2, H // 2 - 40
    p = smooth(rng(t, 0.15, 2.6))
    for i in range(18):
        a = (i / 18.0) * 2 * math.pi + 0.2
        rr = 60 + 460 * p * (0.55 + 0.45 * K.hash2(i, 11))
        x2, y2 = cx + math.cos(a) * rr, cy + math.sin(a) * rr * 0.62
        d.line([cx, cy, x2, y2], fill=(255, 255, 255, int(70 * p)), width=1)
        if K.hash2(i, 9) > 0.45:
            img = _soft_dot(img, x2, y2, 3 + 5 * K.hash2(i, 5), THEME["accent2"], 0.55 * p)
    img = _soft_dot(img, cx, cy, 26 + 6 * math.sin(t * 2.2), THEME["accent"], 0.9 * rng(t, 0.1, 0.7))
    return _cover(img, t, dur, "00 · 开场", script.get("title", ""),
                  scene.get("lead") or "零素材，全代码生成", tg / max(0.1, script.get("_total", 1.0)),
                  script.get("title", ""))


def scene_body(scene, t, dur, tg, script, frame):
    img = _bg().copy()
    d = ImageDraw.Draw(img, "RGBA")
    p = ease_out(rng(t, 0.0, 0.8))
    d.text((150, 250 + int(14 * (1 - p))), scene.get("kicker", ""),
           font=K.font(28, "bold"), fill=THEME["accent"] + (int(255 * p),))
    for i, ln in enumerate(K.wrap_cn_cached(scene.get("big") or scene["text"][:18], 64, 820, "bold")):
        d.text((150, 300 + i * 78), ln, font=K.font(64, "bold"), fill=THEME["ink"])
    if scene.get("sub"):
        for i, ln in enumerate(K.wrap_cn_cached(scene["sub"], 34, 820)):
            d.text((150, 420 + i * 48), ln, font=K.font(34, "reg"), fill=THEME["muted"])

    for i, it in enumerate(scene.get("items") or []):
        p_i = ease_out(rng(t, 0.45 + 0.32 * i, 1.15 + 0.32 * i))
        if p_i <= 0:
            continue
        x, y = 1030 + int(46 * (1 - p_i)), 240 + i * 118
        d.rounded_rectangle([x, y, x + 740, y + 96], radius=14,
                            fill=(255, 255, 255, int(16 * p_i)),
                            outline=(255, 255, 255, int(28 * p_i)))
        col = THEME["good"] if it.get("ok", True) else THEME["bad"]
        img = _soft_dot(img, x + 34, y + 48, 11, col, p_i)
        d = ImageDraw.Draw(img, "RGBA")
        d.text((x + 60, y + 28), it["text"], font=K.font(32, "reg"),
               fill=THEME["ink"] + (int(255 * p_i),))
    return _hud(img, scene.get("chapter") or "02 · 正文",
                tg / max(0.1, script.get("_total", 1.0)), script.get("title", ""))


def scene_outro(scene, t, dur, tg, script, frame):
    img = _bg().copy()
    d = ImageDraw.Draw(img, "RGBA")
    p = ease_out(rng(t, 0.0, 1.0))
    cx, cy = W // 2, H // 2 - 30
    for i in range(26):
        a = (i / 26.0) * 2 * math.pi + t * 0.15
        rr = 500 * (1 - p) + 40 + 18 * K.hash2(i, 4)
        img = _soft_dot(img, cx + math.cos(a) * rr, cy + math.sin(a) * rr * 0.5,
                        3, THEME["accent2"], 0.5 * (1 - p * 0.7))
    d = ImageDraw.Draw(img, "RGBA")
    lines = K.wrap_cn_cached(scene.get("big") or script.get("end_text") or "完", 96, 1500, "bold")
    alpha = int(255 * ease_out(rng(t, 0.3, 1.5)))
    for i, ln in enumerate(lines):
        d.text(((W - K.text_w_cached(ln, 96, "bold")) / 2, 380 + i * 118), ln,
               font=K.font(96, "bold"), fill=THEME["ink"] + (alpha,))
    return _hud(img, "99 · 收尾", 1.0, script.get("title", ""))


DRAWERS = {"hook": scene_hook, "body": scene_body, "outro": scene_outro}


def draw_frame(scene, t_local, dur, t_global, script, frame):
    fn = DRAWERS.get(scene.get("kind") or "body", scene_body)
    return fn(scene, t_local, dur, t_global, script, frame)
