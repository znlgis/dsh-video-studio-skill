
# -*- coding: utf-8 -*-
"""scenes.py — 场景绘制模板。复制这个文件改成你自己的片子。

唯一约定：提供一个 draw_frame(scene, t_local, dur, t_global, script, frame) -> PIL.Image
  scene      : timeline.json 里的一段（含 id/text/sub/start/end/dur）
  t_local    : 本段内已经过秒数
  dur        : 本段总秒数
  t_global   : 全片秒数
  frame      : 全片帧号（确定性：同一帧号必须画出同一张图）

铁律（决定画质与可复现性）：
  1. 不许用 random / time.time()；要随机用 K.hash2/hash3/vnoise(seed)
  2. 每帧从零画，不要依赖上一帧的变量
  3. 每帧只创建必要对象；静态背景预渲染一次缓存在模块级
  4. 所有文字都要落在 K.SAFE 内（y<=974），字幕由 render.py 统一画在 y>950
  5. 每段至少一个动态元素，镜头缓推 3–15%
"""
from __future__ import annotations
import math
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

import filmkit as K
from filmkit import W, H, clamp, lerp, smooth, ease_out, ease_in_out, rng

# ---------------- 主题（改这里换整片气质） ----------------
THEME = dict(
    bg_top=(8, 16, 28), bg_bot=(18, 34, 56),
    ink=(238, 244, 252), muted=(150, 174, 200), dim=(96, 118, 144),
    accent=(243, 132, 60), accent2=(86, 160, 230), good=(108, 178, 110), bad=(226, 96, 92),
    paper=(238, 232, 218),
)

_BG_CACHE = {}


def _vgrad(w, h, top, bot):
    """竖直渐变背景 + 极轻的网点，缓存复用"""
    key = (w, h, top, bot)
    if key in _BG_CACHE:
        return _BG_CACHE[key]
    a = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    top_a = np.array(top, dtype=np.float32)[None, :]
    bot_a = np.array(bot, dtype=np.float32)[None, :]
    arr = top_a * (1 - a[..., None]) + bot_a * a[..., None]
    arr = np.repeat(arr, w, axis=1)
    # 细微网点（确定性）
    yy, xx = np.mgrid[0:h, 0:w]
    dots = ((np.sin(xx * 0.35) * np.sin(yy * 0.35)) > 0.93).astype(np.float32) * 4.0
    arr = np.clip(arr + dots[..., None], 0, 255)
    im = Image.fromarray(arr.astype(np.uint8), "RGB")
    _BG_CACHE[key] = im
    return im


def _vignette(img, strength=0.35):
    key = ("vig", img.size, strength)
    if key in _BG_CACHE:
        mask = _BG_CACHE[key]
    else:
        w, h = img.size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = w / 2.0, h / 2.0
        r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / 1.4142
        m = np.clip((r - 0.45) / 0.55, 0, 1) ** 1.7 * strength
        mask = Image.fromarray((m * 255).astype(np.uint8), "L")
        _BG_CACHE[key] = mask
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(black, img, mask)


def _hud(img, chapter, progress, title):
    """顶栏章节 + 顶部细进度线 + 右上角片名"""
    d = ImageDraw.Draw(img, "RGBA")
    # 进度线
    d.rectangle([0, 0, int(W * progress), 3], fill=THEME["accent"])
    # 章节 chip
    f = K.font(28, "reg")
    tw = K.text_w(chapter, f)
    d.rounded_rectangle([64, 40, 64 + tw + 40, 92], radius=26,
                        fill=(0, 0, 0, 90), outline=(255, 255, 255, 36))
    d.ellipse([64 + 14, 58, 64 + 24, 68], fill=THEME["accent"])
    d.text((64 + 32, 52), chapter, font=f, fill=THEME["ink"])
    # 右上片名
    f2 = K.font(24, "reg")
    tw2 = K.text_w(title, f2)
    d.text((W - 64 - tw2, 54), title, font=f2, fill=(255, 255, 255, 110))
    return img


def _chapter_cover(img, t_local, dur, kicker, big, sub):
    """场景封面：kicker + 大字 + 副标题，带位移与透明度入场"""
    d = ImageDraw.Draw(img, "RGBA")
    p = ease_out(rng(t_local, 0.0, 0.9))
    p2 = ease_out(rng(t_local, 0.25, 1.3))
    out_ = 1 - ease_in_out(rng(t_local, dur - 0.55, dur))
    alpha = int(255 * p * out_)
    f_k = K.font(30, "bold")
    f_b = K.font(84, "bold")
    f_s = K.font(38, "light")
    y = 300 - 26 * (1 - p)
    d.text((150, y), kicker, font=f_k, fill=THEME["accent"] + (alpha,))
    lines = K.wrap_cn(big, f_b, 1560)
    for i, ln in enumerate(lines):
        yy = int(y + 52 + i * 104)
        d.text((150, yy), ln, font=f_b, fill=THEME["ink"] + (alpha,))
    if sub:
        yy2 = int(y + 52 + len(lines) * 104 + 26)
        for i, ln in enumerate(K.wrap_cn(sub, f_s, 1400)):
            d.text((150, yy2 + i * 56), ln, font=f_s, fill=THEME["muted"] + (int(alpha * 0.95),))
    return img


def _soft_dot(img, x, y, r, color, alpha=1.0):
    """预渲染软边圆点（比每帧 new 快）"""
    key = ("dot", int(r), color)
    if key not in _BG_CACHE:
        s = int(r * 2 + 4)
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        dd = ImageDraw.Draw(im)
        dd.ellipse([2, 2, s - 2, s - 2], fill=color + (255,))
        im = im.filter(ImageFilter.GaussianBlur(r * 0.18))
        _BG_CACHE[key] = im
    spr = _BG_CACHE[key]
    if alpha < 0.99:
        spr = spr.copy()
        a = spr.split()[3].point(lambda v: int(v * alpha))
        spr.putalpha(a)
    img.paste(spr, (int(x - spr.width / 2), int(y - spr.height / 2)), spr)
    return img


# ---------------- 场景：这里只做三个示范 ----------------
def scene_hook(scene, t, dur, tg, script, frame):
    """开场：一个点 → 展开成网络"""
    img = _vgrad(W, H, THEME["bg_top"], THEME["bg_bot"]).copy()
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = W // 2, H // 2 - 40
    p = smooth(rng(t, 0.15, 2.6))
    # 中心点 + 呼吸
    rr = 10 + 4 * math.sin(t * 2.2)
    img = _soft_dot(img, cx, cy, rr * 2.4, THEME["accent"], alpha=0.9 * rng(t, 0.1, 0.7))
    img = _soft_dot(img, cx, cy, rr, (255, 220, 180), alpha=1.0)
    # 辐射网络
    n = 18
    for i in range(n):
        a = (i / n) * 2 * math.pi + 0.2
        rr2 = 60 + 460 * p * (0.55 + 0.45 * K.hash2(i, 11))
        x2 = cx + math.cos(a) * rr2
        y2 = cy + math.sin(a) * rr2 * 0.62
        al = int(70 * p * (0.4 + 0.6 * K.hash2(i, 3)))
        d.line([cx, cy, x2, y2], fill=(255, 255, 255, al), width=1)
        if K.hash2(i, 9) > 0.45:
            img = _soft_dot(img, x2, y2, 3 + 5 * K.hash2(i, 5), THEME["accent2"], alpha=0.55 * p)
    img = _chapter_cover(img, t, dur, "01 · 开场", script["title"], "用 DeepSeek 4.1 Flash + DSH 生成")
    return _vignette(_hud(img, "01 · 开场", tg / max(0.1, scene["end"]), script["title"]))


def scene_body(scene, t, dur, tg, script, frame):
    """正文：左标题 + 右侧卡片的列表，逐条入场"""
    img = _vgrad(W, H, THEME["bg_top"], THEME["bg_bot"]).copy()
    d = ImageDraw.Draw(img, "RGBA")
    f_k = K.font(28, "bold")
    f_t = K.font(64, "bold")
    f_b = K.font(34, "reg")
    p = ease_out(rng(t, 0.0, 0.8))
    d.text((150, 250 + int(14 * (1 - p))), (scene.get("kicker") or "") ,
           font=f_k, fill=THEME["accent"] + (int(255 * p),))
    for i, ln in enumerate(K.wrap_cn(scene.get("big") or scene["text"][:18], f_t, 820)):
        d.text((150, 300 + i * 78), ln, font=f_t, fill=THEME["ink"])
    if scene.get("sub"):
        for i, ln in enumerate(K.wrap_cn(scene["sub"], f_b, 820)):
            d.text((150, 300 + 100 + i * 48), ln, font=f_b, fill=THEME["muted"])

    items = scene.get("items") or []
    bx, by, bw, bh = 1030, 240, 740, 96
    for i, it in enumerate(items):
        p_i = ease_out(rng(t, 0.45 + 0.32 * i, 1.15 + 0.32 * i))
        if p_i <= 0:
            continue
        x = bx + int(46 * (1 - p_i))
        y = by + i * (bh + 22)
        d.rounded_rectangle([x, y, x + bw, y + bh], radius=14,
                            fill=(255, 255, 255, int(16 * p_i)),
                            outline=(255, 255, 255, int(28 * p_i)))
        col = THEME["good"] if it.get("ok", True) else THEME["bad"]
        img = _soft_dot(img, x + 34, y + bh // 2, 11, col, alpha=p_i)
        d = ImageDraw.Draw(img, "RGBA")
        f_i = K.font(32, "reg")
        d.text((x + 60, y + 28), it["text"], font=f_i, fill=THEME["ink"] + (int(255 * p_i),))
    return _vignette(_hud(img, scene.get("chapter") or "02 · 正文", tg / max(0.1, scene["end"]), script["title"]))


def scene_outro(scene, t, dur, tg, script, frame):
    """收尾：大字居中 + 收缩的光点"""
    img = _vgrad(W, H, (6, 12, 22), (12, 22, 38)).copy()
    d = ImageDraw.Draw(img, "RGBA")
    p = ease_out(rng(t, 0.0, 1.0))
    cx, cy = W // 2, H // 2 - 30
    for i in range(26):
        a = (i / 26) * 2 * math.pi + t * 0.15
        rr = 500 * (1 - p) + 40 + 18 * K.hash2(i, 4)
        img = _soft_dot(img, cx + math.cos(a) * rr, cy + math.sin(a) * rr * 0.5,
                        3, THEME["accent2"], alpha=0.5 * (1 - p * 0.7))
    d = ImageDraw.Draw(img, "RGBA")
    f = K.font(96, "bold")
    lines = K.wrap_cn(scene.get("big") or script.get("end_text") or "完", f, 1500)
    alpha = int(255 * ease_out(rng(t, 0.3, 1.5)))
    for i, ln in enumerate(lines):
        lw = K.text_w(ln, f)
        d.text(((W - lw) / 2, 380 + i * 118), ln, font=f, fill=THEME["ink"] + (alpha,))
    return _vignette(_hud(img, "99 · 收尾", 1.0, script["title"]))


DRAWERS = {
    "hook": scene_hook,
    "body": scene_body,
    "outro": scene_outro,
}


def draw_frame(scene, t_local, dur, t_global, script, frame):
    kind = scene.get("kind") or "body"
    fn = DRAWERS.get(kind, scene_body)
    return fn(scene, t_local, dur, t_global, script, frame)
