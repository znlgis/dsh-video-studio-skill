# -*- coding: utf-8 -*-
"""scenes.py — 场景绘制模板（v0.5.0：画风随内容变化）。

唯一约定：提供 draw_frame(scene, t_local, dur, t_global, script, frame) -> PIL.Image
  scene   : timeline.json 里的一段（含 id/text/sub/start/end/dur）
  t_local : 本段内已经过秒数
  dur     : 本段总秒数
  t_global: 全片秒数
  frame   : 全片帧号（确定性：同一帧号必须画出同一张图）

v0.5.0 画风主题化
----------------
剧本里写了 style（或 render.py 把 stylekit 解析出的计划塞进 script["_style"]）时，
本文件从 stylekit 的主题包里取配色 / 材质 / 字体族 / 版式 / 缓动 / HUD / 镜头缓推，
8 套风格各长一个样（深蓝科技 / 暖白杂志 / 手绘纸感 / 活力撞色 / 极简黑白 /
电影青橙 / 暖灰薄荷 / 深黑鎏金）。没写 style 的老项目 = legacy：
走下面原封不动的 v0.4 绘制函数，逐字节可复现（基线三帧 sha256 由 selfcheck 比对）。

铁律（决定画质与可复现性）：
  1. 不许用 random / time.time()；要随机用 K.hash2 / K.hash3 / vnoise(seed)
  2. 每帧从零画，不依赖上一帧的变量
  3. 静态背景预渲染一次并缓存（见 _bg()），不要每帧重算渐变 / 纹理 / 暗角
  4. 文字排版一律用本文件的 _wrap / _text_w（带缓存）：直接调 wrap_cn 会让单帧慢 2-3 倍
  5. 所有正文落在 K.SAFE 内（y <= 974），字幕由 render.py 统一画在 y >= 872
  6. 每段至少一个动态元素，静止不要超过 6 秒；镜头缓推 3-15%
  7. 缺字形会画成方块：用 K.glyph_missing_chars 自查，别写 ✓ × ↵ 这类符号（改用矢量线条）
  8. 性能：1080p 单帧预算 ~15ms。重活（渐变/暗角/纹理/精灵）全部预烘焙进 _CACHE；
     不许每帧全屏高斯模糊 / 全屏双线性 resize（实测 30ms/帧，直接吃掉预算）
"""
from __future__ import annotations

import math
import os
import sys
import warnings

from PIL import Image, ImageDraw, ImageFilter
import numpy as np

# stylekit 的单向依赖：stylekit 不 import 本文件（也不 import filmkit），不会成环。
# 用户会把 scenes.py 与 scripts/*.py 拷到同一目录，那种情况下直接 import 就行；
# 本仓库里是 assets/scenes.py 与 scripts/stylekit.py 两个目录，所以兜底加 ../scripts。
try:
    import stylekit as ST
except ImportError:
    _SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts")
    _SCRIPTS = os.path.normpath(_SCRIPTS)
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import stylekit as ST

import filmkit as K
# 只 import 模板**当下真的用到**的名字（多余导入会被 pyflakes 记一笔，也容易让人以为哪都能用）。
# 写自己的场景时，缓动/噪声直接走模块：K.lerp / K.ease_in / K.ease_in_out / K.vnoise / K.hash2 …
from filmkit import W, H, clamp, smooth, ease_out, rng

# ============================================================================
# 零、v0.4 兼容层（legacy 逐字节基线，别动这里的任何一行）
# ============================================================================
#
# legacy = 老项目（film.json 没有 style 字段）。这组函数是 v0.4 的原文，
# 一个字符都不许改：Lead 的基线三帧 sha256 直接对拍这里的输出。
# 新主题化代码只在 _legacy_* 之外生长，互不影响。

# ---------------- 主题（legacy 专用；新代码走 stylekit.STYLES / THEMES） ----------------
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


def _legacy_bg():
    """预烘焙背景：渐变 + 暗角一次算好，每帧只 copy（实测省 ~4.5ms/帧）。"""
    if "baked" not in _CACHE:
        base = _vgrad(W, H, THEME["bg_top"], THEME["bg_bot"])
        _CACHE["baked"] = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)),
                                          base, _vignette_mask(W, H, 0.40))
    return _CACHE["baked"]


def _legacy_hud(img, chapter, progress, title):
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


def _legacy_cover(img, t, dur, kicker, big, sub, progress, title):
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
    return _legacy_hud(img, "01 · 开场", progress, title)


def _legacy_scene_hook(scene, t, dur, tg, script, frame):
    img = _legacy_bg().copy()
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
    return _legacy_cover(img, t, dur, "00 · 开场", script.get("title", ""),
                         scene.get("lead") or "零素材，全代码生成", tg / max(0.1, script.get("_total", 1.0)),
                         script.get("title", ""))


def _legacy_scene_body(scene, t, dur, tg, script, frame):
    img = _legacy_bg().copy()
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
    return _legacy_hud(img, scene.get("chapter") or "02 · 正文",
                       tg / max(0.1, script.get("_total", 1.0)), script.get("title", ""))


def _legacy_scene_outro(scene, t, dur, tg, script, frame):
    img = _legacy_bg().copy()
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
    return _legacy_hud(img, "99 · 收尾", 1.0, script.get("title", ""))


_LEGACY_DRAWERS = {
    "hook": _legacy_scene_hook,
    "body": _legacy_scene_body,
    "outro": _legacy_scene_outro,
}


# ============================================================================
# 一、风格计划 / 主题（v0.5）
# ============================================================================

_PLAN_CACHE = {}
_THEME_WARNED = set()
_DEFAULT_PUSH = 0.04


def _warn_once(key, msg):
    if key in _THEME_WARNED:
        return
    _THEME_WARNED.add(key)
    warnings.warn(msg, RuntimeWarning, stacklevel=3)


def _plan(script):
    """取整片的风格计划：render.py 会把解析好的计划放进 script["_style"]。

    没有就自己解一次（纯函数，同参数同结果）。每帧都会调用，所以只留一条缓存；
    键里带 id(script) 之外的稳定字段，避免脚本对象被回收后 id 复用串档。
    """
    p = script.get("_style")
    if isinstance(p, dict):
        return p
    key = (id(script), script.get("title"), len(script.get("lines") or []),
           script.get("style"), bool(script.get("pacing")))
    got = _PLAN_CACHE.get(key)
    if got is None:
        got = ST.resolve_script_style(script)
        _PLAN_CACHE.clear()
        _PLAN_CACHE[key] = got
    return got


def _scene_theme(plan, scene):
    """本段用哪套主题：全片主题 + 可选的 scene["theme"] 覆盖（只许同明暗族）。"""
    base = plan.get("theme")
    if isinstance(base, dict):
        th = dict(base)
    else:
        th = dict(ST.THEMES[plan["theme_id"]])
    sid = scene.get("theme")
    if not sid or str(sid) == str(th.get("id")):
        return th
    alt = ST.THEMES.get(str(sid))
    if alt is None:
        _warn_once(("unknown", str(sid)),
                   "场景 %r 指定了未知主题 %r，已忽略（可用：%s）"
                   % (scene.get("id"), sid, " / ".join(ST.STYLE_IDS)))
        return th
    if not ST.family_compatible(th, alt):
        # 跨明暗族 = 两部片，不是两段：段界会整屏换底，QA 的闪烁门禁必炸。
        _warn_once(("family", str(th.get("id")), str(sid)),
                   "场景 %r 想从主题 %s 换到 %s，两者明暗族不同（同片换族会触发闪烁门禁），已忽略"
                   % (scene.get("id"), th.get("id"), sid))
        return th
    return dict(alt)


# ---------------- 包内小工具 ----------------

def _mix(a, b, p):
    """两色线性插值（p=0 -> a，p=1 -> b）。"""
    q = clamp(p)
    return (int(round(a[0] + (b[0] - a[0]) * q)),
            int(round(a[1] + (b[1] - a[1]) * q)),
            int(round(a[2] + (b[2] - a[2]) * q)))


def _rgba(c, alpha=255):
    """RGB + 0..255 透明度 -> 4 元组（PIL 的 RGBA 画笔要它）。"""
    return (int(c[0]), int(c[1]), int(c[2]),
            int(max(0, min(255, round(alpha)))))


def _ease(name):
    """风格包里的缓动名 -> 函数（四个都满足 f(0)=0 / f(1)=1）。"""
    got = _EASES.get(name)
    if got is not None:
        return got
    if name == "out5":
        got = lambda p: 1.0 - (1.0 - clamp(p)) ** 5
    elif name == "inOut3":
        got = K.ease_in_out
    elif name == "outBack":
        got = K.ease_out_back
    else:
        got = K.ease_out
    _EASES[name] = got
    return got


_EASES = {}


# ---------------- 字体：family 是 v0.5 新加的参数（旧 filmkit 没有，自动回退） ----------------

_FONT_CACHE = {}
_FAMILY_OK = None


def _family_ok():
    """filmkit.font 是否支持 family=...（Lead 在 v0.5 加；老版本没有就回退旧签名）。"""
    global _FAMILY_OK
    if _FAMILY_OK is None:
        try:
            K.font(16, "reg", "sans")
            _FAMILY_OK = True
        except TypeError:
            _FAMILY_OK = False
        except Exception:                    # noqa: BLE001 - 字体族实现有 bug 时退回旧签名
            _FAMILY_OK = False
    return _FAMILY_OK


def _font(th, size, weight="reg"):
    size = max(6, int(size))
    fam = str(th.get("family") or "sans")
    key = (size, weight, fam)
    f = _FONT_CACHE.get(key)
    if f is None:
        if _family_ok():
            try:
                f = K.font(size, weight, fam)
            except Exception:                # noqa: BLE001 - 某族字体缺失时退回到默认字体
                f = K.font(size, weight)
        else:
            f = K.font(size, weight)
        _FONT_CACHE[key] = f
    return f


_TW_CACHE = {}
_WRAP_CACHE = {}


def _text_w(text, size, weight, th):
    """带缓存的行宽（按主题字体族；老 filmkit 回退到 K.text_w_cached）。"""
    if not text:
        return 0.0
    size = max(6, int(size))
    fam = str(th.get("family") or "sans")
    key = (text, size, weight, fam)
    got = _TW_CACHE.get(key)
    if got is None:
        if len(_TW_CACHE) > 40000:
            _TW_CACHE.clear()
        if _family_ok():
            got = K.text_w(text, _font(th, size, weight))
        else:
            got = K.text_w_cached(text, size, weight)
        _TW_CACHE[key] = got
    return got


def _wrap(text, size, max_w, weight, th):
    """带缓存的换行（同上）。"""
    if not text:
        return []
    size = max(6, int(size))
    max_w = max(80, int(max_w))
    fam = str(th.get("family") or "sans")
    key = (text, size, max_w, weight, fam)
    got = _WRAP_CACHE.get(key)
    if got is None:
        if len(_WRAP_CACHE) > 20000:
            _WRAP_CACHE.clear()
        if _family_ok():
            got = K.wrap_cn(text, _font(th, size, weight), max_w)
        else:
            got = K.wrap_cn_cached(text, size, max_w, weight)
        _WRAP_CACHE[key] = got
    return got


def _fit_size(text, max_w, size, weight, th, floor=16):
    """按卡片可用宽度缩字号（条目文字长短不一，不能撑破卡片）。"""
    s = int(size)
    while s > floor and _text_w(text, s, weight, th) > max_w:
        s -= 2
    return s


# ---------------- 镜头缓推 + 前景视差 ----------------

class _View(object):
    """设计坐标 -> 帧坐标。

    为什么不是整帧重采样：实测 1080p 双线性整帧变换 29ms/帧（预算 15ms），
    而整帧按 push 放大又会把 150px 边距的内容推出 SAFE。所以分两层：
      背景层做**真缩放**（EXTENT + NEAREST，1.7~3.5ms，见 _bg_plate）；
      前景层做同向**视差**（0.35x，位置与卡片尺寸随镜头收放，字形不重采样保清晰）。
    """
    __slots__ = ("s", "cx", "cy")

    def __init__(self, s):
        self.s = float(s)
        self.cx = W * 0.5
        self.cy = H * 0.5

    def x(self, x):
        return int(round(self.cx + (float(x) - self.cx) * self.s))

    def y(self, y):
        return int(round(self.cy + (float(y) - self.cy) * self.s))

    def r(self, r):
        return max(0.0, float(r) * self.s)


def _push_scale(th, t_local, dur):
    """镜头缓推：t=0 -> 1.0，t=dur -> 1+push，单调平滑（端点必须收敛，不许抖）。"""
    u = clamp(t_local / max(0.05, float(dur)))
    return 1.0 + float(th.get("push") or _DEFAULT_PUSH) * smooth(u)


def _view(th, s_bg):
    return _View(1.0 + (s_bg - 1.0) * 0.35)


# ---------------- 背景：按主题烘焙一次（渐变 + 材质 + 颗粒 + 暗角） ----------------

def _grid_xy():
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    return yy, xx


def _paper_noise(th):
    """纸纹：纤维条纹（vnoise）+ 多尺度斑块（hash2 小图上采样）。一次烘焙，不进逐帧路径。"""
    freq = float(th.get("grain_freq") or 0.27)
    xs = np.arange(W, dtype=np.float32) * freq * 0.35
    fiber = np.array([K.vnoise(float(v), 3) for v in xs], dtype=np.float32) - 0.5
    fine = np.array([K.vnoise(float(v) * 2.3, 9) for v in xs], dtype=np.float32) - 0.5
    rows, cols = 27, 48
    blobs = np.array([[K.hash2(r * 31 + 7, c * 17 + 3) for c in range(cols)]
                      for r in range(rows)], dtype=np.float32) - 0.5
    blob_img = Image.fromarray(((blobs + 0.5) * 255.0).astype(np.uint8), "L")
    blobs = np.asarray(blob_img.resize((W, H), Image.BICUBIC), dtype=np.float32) / 255.0 - 0.5
    return blobs * 0.55 + fiber[None, :] * 0.30 + fine[None, :] * 0.15


def _texture_layer(th, yy, xx):
    """背景材质层（灰度偏移；深色主题提亮、浅色主题压暗）。"""
    kind = str(th.get("texture") or "none")
    alpha = float(th.get("texture_alpha") or 0.0)
    if kind == "none" or alpha <= 0.0:
        return None
    sign = 1.0 if ST.is_dark(th) else -1.0
    amp = sign * 255.0 * alpha * 0.44
    if kind == "dots":
        f = float(th.get("grain_freq") or 0.31)
        layer = ((np.sin(xx * f) * np.sin(yy * f)) > 0.955).astype(np.float32)
    elif kind == "grid":
        step = 88.0
        layer = (((xx % step) < 1.0) | ((yy % step) < 1.0)).astype(np.float32)
    elif kind == "scanlines":
        layer = ((yy % 4.0) < 1.0).astype(np.float32)
    elif kind == "paper":
        layer = _paper_noise(th)
    else:
        return None
    return layer * amp


def _grain_layer(th, yy, xx):
    """确定性颗粒（坐标 hash，等价 vnoise 的 2D 版本；不用 random）。"""
    g = float(th.get("grain") or 0.0)
    if g <= 0.05:
        return None
    xi = np.arange(W, dtype=np.int64)[None, :]
    yi = np.arange(H, dtype=np.int64)[:, None]
    v = (xi * 374761393 + yi * 668265263) & 0xFFFFFFFF
    v = (v ^ (v >> 13)) & 0xFFFFFFFF
    v = (v * 1274126177) & 0xFFFFFFFF
    v = (v ^ (v >> 16)) & 0xFFFFFFFF
    noise = (v & 0xFFFF).astype(np.float32) / 65535.0 - 0.5
    flat = np.empty((H, W), dtype=np.float32)
    np.add(noise * (g * 1.2), 0.0, out=flat)
    return flat


def _vignette_layer(th, yy, xx):
    """暗角：浅色主题压向 vignette_tint（米灰），绝不压向纯黑。"""
    strength = float(th.get("vignette") or 0.0)
    if strength <= 0.0:
        return None
    r = np.sqrt(((xx - W * 0.5) / (W * 0.5)) ** 2 + ((yy - H * 0.5) / (H * 0.5)) ** 2) / 1.4142
    m = np.clip((r - 0.40) / 0.60, 0.0, 1.0) ** 1.6 * strength
    return m


def _hashable(v):
    """把 list 递归转成 tuple：主题从 JSON 读回来时元组会变成 list，而 list 不能当 dict 键。

    为什么需要它：--jobs >1 时 render.py 的 worker 从剧本快照读 script，主题色的
    (r,g,b) 元组变成 [r,g,b]，于是下面 _bg 的缓存键抛 unhashable type: "list"。
    render.py 已按剧本重解风格，但这层防御留着——scenes.py 是用户会拷走改的模板。
    """
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _hashable(x)) for k, x in v.items()))
    return v


def _bg(th):
    """预烘焙背景：主题色渐变 + 材质 + 颗粒 + 暗角，一次算好，逐帧只 copy / 变换。"""
    key = _hashable(("baked", th.get("id"), th["bg"], th["bg2"], th["texture"], th["grain"],
                     th["vignette"], th["vignette_tint"], th["texture_alpha"],
                     th["grain_freq"]))
    got = _CACHE.get(key)
    if got is not None:
        return got
    yy, xx = _grid_xy()
    a = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    top = np.array(th["bg"], dtype=np.float32)[None, None, :]
    bot = np.array(th["bg2"], dtype=np.float32)[None, None, :]
    arr = np.repeat(top * (1.0 - a) + bot * a, W, axis=1)
    tex = _texture_layer(th, yy, xx)
    if tex is not None:
        arr = arr + tex[:, :, None]
    grain = _grain_layer(th, yy, xx)
    if grain is not None:
        arr = arr + grain[:, :, None]
    np.clip(arr, 0.0, 255.0, out=arr)
    vig = _vignette_layer(th, yy, xx)
    if vig is not None:
        tint = np.array(th["vignette_tint"], dtype=np.float32)[None, None, :]
        arr = arr * (1.0 - vig)[:, :, None] + tint * vig[:, :, None]
        np.clip(arr, 0.0, 255.0, out=arr)
    im = Image.fromarray(arr.astype(np.uint8), "RGB")
    _CACHE[key] = im
    return im


def _bg_plate(th, s):
    """逐帧背景：按镜头缩放取景（NEAREST 重采样，实测 1.7~3.5ms/帧）。"""
    base = _bg(th)
    if s <= 1.0008:
        return base.copy()
    bw, bh = W / s, H / s
    x0 = (W - bw) * 0.5
    y0 = (H - bh) * 0.5
    return base.transform((W, H), Image.EXTENT, (x0, y0, x0 + bw, y0 + bh), Image.NEAREST)


# ---------------- 版式几何（全部从 ST.LAYOUTS 取） ----------------

def _title_anchor(lay):
    """标题锚点 + 对齐方式：left-stack 左对齐 / split-right 右对齐 / center-stack 居中。"""
    if lay.get("center"):
        return W * 0.5, "center"
    if lay.get("title_x") is None:
        return float(W - 150), "right"
    return float(lay["title_x"]), "left"


def _panel_x(lay):
    if lay.get("panel_x") is None:
        return (W - float(lay["panel_w"])) * 0.5
    return float(lay["panel_x"])


def _panel_boxes(lay, th, n, y_floor, v):
    """条目卡片布局：选列数 + 压缩卡片高度，保证①不压标题②不越字幕带（856）。

    panel_gap 在 stylekit 里是**行距（pitch）**不是间隙：v0.4 用 240 + i*118 排卡片，
    所以 pitch 至少得比卡片高度多 6px，否则卡片会叠在一起。条目多时先加列，再压高度，
    最后才截断——静默重叠或画到字幕带下面比少画两条更糟。
    """
    if n <= 0:
        return []
    px = _panel_x(lay)
    pw = float(lay["panel_w"])
    base_pitch = max(float(lay["panel_gap"]), float(lay["panel_h"]) + 6.0)
    ch_base = float(lay["panel_h"])
    y0 = max(float(lay["panel_y"]), float(y_floor))
    ylim = v.cy + (856.0 - v.cy) / max(0.2, v.s)
    avail = max(80.0, ylim - y0)
    if lay.get("center"):
        cand = (1, 2) if n <= 2 else ((2, 3) if n <= 6 else (3, 2))
    else:
        cand = (1, 2) if n <= 5 else ((2, 1) if n <= 10 else (2,))
    chosen = None
    for cols in cand:
        rows = int(math.ceil(n / float(cols)))
        for ch in (ch_base, ch_base * 0.82, 54.0):
            pitch = min(base_pitch, (avail - ch) / max(1, rows - 1)) if rows > 1 else base_pitch
            if pitch < ch + 6.0:
                continue
            if (rows - 1) * pitch + ch <= avail + 0.5:
                chosen = (cols, rows, ch, pitch)
                break
        if chosen:
            break
    if chosen is None:
        cols = cand[-1]
        ch, pitch = 54.0, 60.0
        rows = int(max(1, (avail + 6.0) // pitch))
        chosen = (cols, rows, ch, pitch)
    cols, rows, ch, pitch = chosen
    cap = rows * cols
    if n > cap:
        _warn_once(("panel", lay.get("layout"), n),
                   "本段有 %d 个条目，版式只放得下 %d 个（其余已省略）；"
                   "请拆成多段或减少 items" % (n, cap))
    gx = 40.0 if cols > 1 else 0.0
    cw = (pw - gx * (cols - 1)) / cols
    boxes = []
    for i in range(min(n, cap)):
        r, c = divmod(i, cols)
        boxes.append((px + c * (cw + gx), y0 + r * pitch, cw, ch))
    return boxes


# ---------------- 文字绘制（位置过视差，字号不重采样） ----------------

def _draw_text(img, th, v, text, x, y, size, weight, color, align="left",
               alpha=1.0, tracking=0.0):
    text = "" if text is None else str(text)
    if not text or alpha <= 0.004:
        return
    size = int(size)
    px = v.x(x)
    py = v.y(y)
    font = _font(th, size, weight)
    fill = _rgba(color, 255.0 * clamp(alpha))
    d = ImageDraw.Draw(img, "RGBA")
    tr = float(tracking or 0.0)
    if tr > 0.3 and len(text) > 1:
        total = _text_w(text, size, weight, th) + tr * (len(text) - 1)
        if align == "center":
            px -= int(total * 0.5)
        elif align == "right":
            px -= int(total)
        for ch in text:
            d.text((px, py), ch, font=font, fill=fill)
            px += _text_w(ch, size, weight, th) + tr
        return
    w = _text_w(text, size, weight, th)
    if align == "center":
        px -= int(w * 0.5)
    elif align == "right":
        px -= int(w)
    d.text((px, py), text, font=font, fill=fill)


def _entrance_x(th, p, align):
    dx = float(th.get("entrance_dx") or 0)
    if align == "right":
        return -dx * (1.0 - p)
    if align == "center":
        return 0.0
    return dx * (1.0 - p)


def _entrance_y(th, p):
    return float(th.get("entrance_dy") or 0) * (1.0 - p)


# ============================================================================
# 二、HUD（三档：chip-left / corner-line / none）
# ============================================================================

def _hud(img, th, chapter, progress, title):
    style = str(th.get("hud") or "chip-left")
    if style == "none":
        return img
    accent = th["accent"]
    rule = th["rule"]
    ink = th["ink"]
    prog = clamp(progress)
    d = ImageDraw.Draw(img, "RGBA")
    if style == "chip-left":
        d.rectangle([0, 0, W, 3], fill=_rgba(rule, 14))
        d.rectangle([0, 0, int(W * prog), 3], fill=_rgba(accent, 255))
        cs = int(th["scale"]["chip"][0])
        tw = _text_w(chapter, cs, "reg", th)
        pad_l, pad_r = 16, 18
        h = int(cs * 1.95)
        x0, y0 = 64, 42
        x1 = x0 + pad_l + tw + pad_r
        fillc = _mix(th["surface"], th["bg"], 0.55)
        d.rounded_rectangle([x0, y0, x1, y0 + h], radius=h // 2,
                            fill=_rgba(fillc, 168), outline=_rgba(rule, 36))
        d.ellipse([x0 + 16, y0 + h // 2 - 5, x0 + 26, y0 + h // 2 + 5],
                  fill=_rgba(accent, 255))
        d.text((x0 + 34, y0 + (h - cs) // 2 - 3), chapter,
               font=_font(th, cs, "reg"), fill=_rgba(ink, 228))
        bs = int(th["scale"]["brand"][0])
        tw2 = _text_w(title, bs, "reg", th)
        d.text((W - 64 - tw2, 56), title, font=_font(th, bs, "reg"),
               fill=_rgba(_mix(ink, th["bg"], 0.62), 220))
        return img
    # corner-line：只有角标与细线，没有胶囊底
    d.rectangle([0, 0, W, 3], fill=_rgba(rule, 10))
    d.rectangle([0, 0, int(W * prog), 2], fill=_rgba(accent, 214))
    d.line([64, 44, 64, 96], fill=_rgba(accent, 190), width=2)
    d.line([64, 44, 116, 44], fill=_rgba(accent, 190), width=2)
    cs = int(th["scale"]["chip"][0])
    d.text((84, 54), chapter, font=_font(th, cs, "reg"), fill=_rgba(ink, 220))
    bs = int(th["scale"]["brand"][0])
    tw = _text_w(title, bs, "reg", th)
    d.text((W - 64 - tw, 56), title, font=_font(th, bs, "reg"), fill=_rgba(ink, 120))
    d.line([W - 64, 44, W - 64 - 96, 44], fill=_rgba(accent, 120), width=2)
    d.line([W - 64, 44, W - 64, 96], fill=_rgba(accent, 120), width=2)
    return img


# ============================================================================
# 三、三段场景（hook / body / outro）
# ============================================================================

def _progress(tg, script):
    return clamp(float(tg) / max(0.1, float(script.get("_total") or 1.0)))


def _deco_rings(img, th, v, t, cx, cy, strength):
    """开场放射线 + 光点（位置过视差；颜色/透明度按主题）。"""
    d = ImageDraw.Draw(img, "RGBA")
    rule = th["rule"]
    p = smooth(rng(t, 0.15, 2.6))
    opacity = clamp(0.30 if not ST.is_dark(th) else 0.27) * strength
    for i in range(18):
        a = (i / 18.0) * 2 * math.pi + 0.2
        rr = 60 + 420 * p * (0.55 + 0.45 * K.hash2(i, 11))
        x2 = cx + math.cos(a) * rr
        y2 = cy + math.sin(a) * rr * 0.62
        d.line([v.x(cx), v.y(cy), v.x(x2), v.y(y2)],
               fill=_rgba(rule, 255 * opacity * p), width=1)
        if K.hash2(i, 9) > 0.45:
            img = _soft_dot(img, v.x(x2), v.y(y2), 3 + 5 * K.hash2(i, 5),
                            th["accent2"], 0.50 * p * strength)
    img = _soft_dot(img, v.x(cx), v.y(cy), 24 + 5 * math.sin(t * 2.2),
                    th["accent"], 0.85 * rng(t, 0.1, 0.7) * strength)
    return img


def _draw_hook(scene, t, dur, tg, script, frame, plan, th):
    s_bg = _push_scale(th, t, dur)
    v = _view(th, s_bg)
    img = _bg_plate(th, s_bg)
    lay = ST.LAYOUTS[th["layout"]]
    ax, align = _title_anchor(lay)
    if lay.get("center"):
        cx, cy, strength = W * 0.5, 430.0, 0.55
    elif align == "right":
        cx, cy, strength = 620.0, 470.0, 1.0
    else:
        cx, cy, strength = 1300.0, 470.0, 1.0
    img = _deco_rings(img, th, v, t, cx, cy, strength)

    ease = _ease(th.get("ease") or "out3")
    tracking = float(th.get("tracking") or 0.0)
    big = scene.get("big") or script.get("title") or ""
    size = int(th["scale"]["display"][0])
    # 封面标题用整行宽度：版式里的 title_w 是正文栏宽（820），给 96px 大字会把标题折出孤字。
    max_w = min(1560.0, float(W - 300))
    lines = _wrap(big, size, max_w, "bold", th)
    while len(lines) > 3 and size > 42:
        size = int(size * 0.88)
        lines = _wrap(big, size, max_w, "bold", th)
    lh = int(size * float(th.get("line_gap") or 1.3))
    ksize = int(th["scale"]["kicker"][0])
    kicker = scene.get("kicker") or "00 · 开场"

    p0 = ease(rng(t, 0.05, 0.85))
    _draw_text(img, th, v, kicker, ax + _entrance_x(th, p0, align),
               lay["kicker_y"] + _entrance_y(th, p0), ksize, "bold",
               th["accent"], align, p0, tracking * 0.5)

    y = float(lay["title_y"])
    for i, ln in enumerate(lines):
        p = ease(rng(t, 0.12 + 0.13 * i, 0.88 + 0.13 * i))
        _draw_text(img, th, v, ln, ax + _entrance_x(th, p, align),
                   y + i * lh + _entrance_y(th, p), size, "bold",
                   th["ink"], align, p, tracking)

    sub = scene.get("lead") or scene.get("sub") or ""
    sub_bottom = y + len(lines) * lh
    if sub:
        ssize = int(th["scale"]["sub"][0])
        sy = sub_bottom + int(size * 0.24)
        p2 = ease(rng(t, 0.30, 1.40))
        for j, ln in enumerate(_wrap(sub, ssize, float(lay["sub_w"]), "light", th)[:3]):
            _draw_text(img, th, v, ln, ax + _entrance_x(th, p2, align),
                       sy + j * int(ssize * float(th.get("line_gap") or 1.3)),
                       ssize, "light", th["muted"], align, p2)
        sub_bottom = sy + int(ssize * float(th.get("line_gap") or 1.3))

    # 持续动态元素：标题下一条缓慢呼吸的强调线（让画面永不静止）
    amp = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(0.85 * t + 0.5))
    line_w = int(min(lay["title_w"], 760) * 0.55 * amp)
    line_y = sub_bottom + int(size * 0.42)
    if line_y < 856:
        lx = ax if align == "left" else (ax - line_w if align == "right" else ax - line_w // 2)
        ImageDraw.Draw(img, "RGBA").line(
            [v.x(lx), v.y(line_y), v.x(lx + line_w), v.y(line_y)],
            fill=_rgba(th["accent"], 190), width=3)

    return _hud(img, th, scene.get("chapter") or "01 · 开场", _progress(tg, script),
                script.get("title", ""))


def _draw_items(img, th, v, boxes, items, t, stagger):
    ease = _ease(th.get("ease") or "out3")
    card = th["card"]
    rule = th["rule"]
    ink = th["ink"]
    body = max(20, int(th["scale"]["body"][0]) - 1)
    brand = int(th["scale"]["brand"][0])
    pad = max(12, int(card["pad"] * 0.7))
    for i, it in enumerate(items):
        if i >= len(boxes):
            break
        x, y, w, h = boxes[i]
        p = ease(rng(t, 0.48 + stagger * i, 1.18 + stagger * i))
        if p <= 0.0:
            continue
        a = clamp(p)
        dy = _entrance_y(th, p)
        alpha = a
        d = ImageDraw.Draw(img, "RGBA")
        if card["fill_a"] > 0 or card["outline_a"] > 0:
            x0, y0 = v.x(x), v.y(y + dy)
            x1, y1 = v.x(x + w), v.y(y + dy + h)
            radius = int(max(0, card["radius"]) * v.s)
            d.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                                fill=_rgba(rule, card["fill_a"] * alpha),
                                outline=_rgba(rule, card["outline_a"] * alpha))
        if isinstance(it, dict):
            txt = str(it.get("text") or "")
            ok = bool(it.get("ok", True))
        else:
            txt = str(it)
            ok = True
        col = th["good"] if ok else th["bad"]
        dot_x = v.x(x + pad + 16)
        dot_y = v.y(y + dy + h * 0.5)
        dot_r = 9.0 + 1.6 * math.sin(t * 2.1 + i * 0.8)
        img = _soft_dot(img, dot_x, dot_y, dot_r * v.s, col, 0.95 * alpha)
        d = ImageDraw.Draw(img, "RGBA")
        tx = x + pad + 16 + 18 + 10
        max_w = (x + w - pad) - tx - 46
        tsize = _fit_size(txt, max_w, body, "reg", th, 16)
        d.text((v.x(tx), v.y(y + dy + (h - tsize * 1.2) * 0.5)), txt,
               font=_font(th, tsize, "reg"), fill=_rgba(ink, 236 * alpha))
        if w >= 420:
            d.text((v.x(x + w - pad), v.y(y + dy + (h - brand * 1.25) * 0.5)),
                   "%02d" % (i + 1), font=_font(th, brand, "reg"),
                   fill=_rgba(th["dim"], 120 * alpha), anchor="ra")
    return img


def _draw_body(scene, t, dur, tg, script, frame, plan, th):
    s_bg = _push_scale(th, t, dur)
    v = _view(th, s_bg)
    img = _bg_plate(th, s_bg)
    lay = ST.LAYOUTS[th["layout"]]
    ax, align = _title_anchor(lay)
    ease = _ease(th.get("ease") or "out3")
    tracking = float(th.get("tracking") or 0.0)
    gap_ratio = float(th.get("line_gap") or 1.3)

    kicker = scene.get("kicker") or ""
    ksize = int(th["scale"]["kicker"][0])
    p0 = ease(rng(t, 0.0, 0.75))
    if kicker:
        _draw_text(img, th, v, kicker, ax + _entrance_x(th, p0, align),
                   lay["kicker_y"] + _entrance_y(th, p0), ksize, "bold",
                   th["accent"], align, p0, tracking * 0.5)

    big = scene.get("big") or (scene.get("text") or "")[:18]
    bsize = int(th["scale"]["heading"][0])
    max_w = float(lay["title_w"])
    lines = _wrap(big, bsize, max_w, "bold", th)
    while len(lines) > 3 and bsize > 40:
        bsize = int(bsize * 0.9)
        lines = _wrap(big, bsize, max_w, "bold", th)
    lh = int(bsize * gap_ratio)
    p1 = ease(rng(t, 0.06, 0.80))
    y0 = float(lay["title_y"]) + _entrance_y(th, p1)
    for i, ln in enumerate(lines):
        _draw_text(img, th, v, ln, ax + _entrance_x(th, p1, align),
                   y0 + i * lh, bsize, "bold", th["ink"], align, p1, tracking)

    sub_bottom = y0 + len(lines) * lh
    sub = scene.get("sub") or ""
    if sub:
        ssize = int(th["scale"]["body"][0])
        sy = max(float(lay["sub_y"]) if not lay.get("center") else sub_bottom + int(bsize * 0.30),
                 sub_bottom + int(bsize * 0.30))
        p2 = ease(rng(t, 0.20, 0.95))
        for j, ln in enumerate(_wrap(sub, ssize, float(lay["sub_w"]), "reg", th)[:2]):
            _draw_text(img, th, v, ln, ax + _entrance_x(th, p2, align),
                       sy + j * int(ssize * gap_ratio), ssize, "reg",
                       th["muted"], align, p2)
        sub_bottom = sy + int(ssize * gap_ratio)

    # 持续动态 1：标题旁的强调线随呼吸伸缩（0.85 rad/s，永不静止）
    meter_w = int(min(max_w, 780) * 0.46 * (0.55 + 0.45 * (0.5 + 0.5 * math.sin(0.85 * t + 0.6))))
    meter_y = sub_bottom + int(bsize * 0.22)
    if meter_y < 850:
        mx = ax if align == "left" else (ax - meter_w if align == "right" else ax - meter_w // 2)
        ImageDraw.Draw(img, "RGBA").line(
            [v.x(mx), v.y(meter_y), v.x(mx + meter_w), v.y(meter_y)],
            fill=_rgba(th["accent"], 170), width=3)

    items = list(scene.get("items") or [])
    if items:
        stagger = float((plan.get("pacing") or {}).get("item_stagger") or 0.32)
        # 居中版式里标题/副标题横跨整幅，面板顶必须让开真实底边；
        # 左右版式的卡片在另一列，从 layout 的 panel_y 起步即可，不必被标题高度牵着走。
        if lay.get("center"):
            panel_top = sub_bottom + int(bsize * 0.55)
        else:
            panel_top = float(lay["panel_y"])
        boxes = _panel_boxes(lay, th, len(items), panel_top, v)
        # 持续动态 2：卡片区里一条缓慢上下扫的高光（sin 振荡，不做取模跳变）
        if boxes:
            px0 = min(b[0] for b in boxes)
            px1 = max(b[0] + b[2] for b in boxes)
            py0 = min(b[1] for b in boxes)
            py1 = max(b[1] + b[3] for b in boxes)
            k = 0.5 + 0.5 * math.sin(t * 0.55)
            sy = py0 + (py1 - py0) * k
            ImageDraw.Draw(img, "RGBA").line(
                [v.x(px0), v.y(sy), v.x(px1), v.y(sy)],
                fill=_rgba(th["accent"], 26), width=2)
        img = _draw_items(img, th, v, boxes, items, t, stagger)

    return _hud(img, th, scene.get("chapter") or "02 · 正文", _progress(tg, script),
                script.get("title", ""))


def _deco_orbit(img, th, v, t, cx, cy, p):
    """收尾旋转光点环（旋转速度恒定 -> 永远有运动）。"""
    for i in range(26):
        a = (i / 26.0) * 2.0 * math.pi + t * 0.15
        rr = 460.0 * (1.0 - p) + 60.0 + 16.0 * K.hash2(i, 4)
        img = _soft_dot(img, v.x(cx + math.cos(a) * rr),
                        v.y(cy + math.sin(a) * rr * 0.5), 3.0,
                        th["accent2"], 0.5 * (1.0 - p * 0.7))
    return img


def _draw_outro(scene, t, dur, tg, script, frame, plan, th):
    s_bg = _push_scale(th, t, dur)
    v = _view(th, s_bg)
    img = _bg_plate(th, s_bg)
    lay = ST.LAYOUTS[th["layout"]]
    ease = _ease(th.get("ease") or "out3")
    tracking = float(th.get("tracking") or 0.0)
    gap_ratio = float(th.get("line_gap") or 1.3)

    p = ease(rng(t, 0.0, 1.0))
    img = _deco_orbit(img, th, v, t, W * 0.5, 520.0, p)

    big = scene.get("big") or script.get("end_text") or "完"
    size = int(th["scale"]["display"][0])
    lines = _wrap(big, size, min(float(lay["title_w"]), 1520.0), "bold", th)
    while len(lines) > 3 and size > 42:
        size = int(size * 0.88)
        lines = _wrap(big, size, min(float(lay["title_w"]), 1520.0), "bold", th)
    lh = int(size * gap_ratio)
    pa = ease(rng(t, 0.30, 1.30))
    alpha = 0.02 + 0.98 * pa
    dy = _entrance_y(th, pa)
    top = 380.0 - (len(lines) - 1) * lh * 0.5
    for i, ln in enumerate(lines):
        _draw_text(img, th, v, ln, W * 0.5, top + i * lh + dy,
                   size, "bold", th["ink"], "center", alpha, tracking)
    bottom = top + len(lines) * lh + dy

    sub = scene.get("sub") or ""
    if sub:
        ssize = int(th["scale"]["sub"][0])
        p2 = ease(rng(t, 0.55, 1.55))
        for j, ln in enumerate(_wrap(sub, ssize, 1400, "light", th)[:2]):
            _draw_text(img, th, v, ln, W * 0.5,
                       bottom + int(size * 0.22) + j * int(ssize * gap_ratio),
                       ssize, "light", th["muted"], "center", p2)
        bottom = bottom + int(size * 0.22) + int(ssize * gap_ratio)

    # 持续动态：标题下缓慢生长的强调线 + 两端呼吸
    grow = ease(rng(t, 0.45, 1.45))
    line_w = int(340 * grow * (0.82 + 0.18 * math.sin(0.9 * t)))
    ly = min(bottom + int(size * 0.30), 840)
    ImageDraw.Draw(img, "RGBA").line(
        [v.x(W * 0.5 - line_w * 0.5), v.y(ly), v.x(W * 0.5 + line_w * 0.5), v.y(ly)],
        fill=_rgba(th["accent"], 200), width=3)

    return _hud(img, th, scene.get("chapter") or "99 · 收尾", 1.0, script.get("title", ""))


_DRAWERS = {
    "hook": _draw_hook,
    "body": _draw_body,
    "outro": _draw_outro,
}


# ============================================================================
# 四、对外契约
# ============================================================================

def draw_frame(scene, t_local, dur, t_global, script, frame):
    """按帧画一张 1920x1080 RGB 图。legacy 走 v0.4 原函数（逐字节基线）。"""
    plan = _plan(script)
    kind = scene.get("kind") or "body"
    if plan.get("legacy"):
        fn = _LEGACY_DRAWERS.get(kind) or _legacy_scene_body
        return fn(scene, t_local, dur, t_global, script, frame)
    th = _scene_theme(plan, scene)
    fn = _DRAWERS.get(kind) or _draw_body
    return fn(scene, t_local, dur, t_global, script, frame, plan, th)


# ---------------- 提示点（v0.3.0，可选） ----------------
def cues(tl, script):
    """画面提示点钩子：render.py --stage cues 会调它（没有这个函数就不调）。

    好处是"声音跟着画面的像素走"：这里算的是卡片落位、数字滚完的时刻，
    画面一改，声音自动跟着改，不用回来手工对时间。

    v0.5：条目落位步长与场景绘制共用 plan["pacing"]["item_stagger"]，
    两边读同一个数，否则音效会错位（这是 v0.4 写死 0.32 留下的坑）。

    返回 [{"at": 时刻, "type": 配方名, ...参数}]；
      at 可以写 "abs:12.5" / 12.5 / "l03+0.35"（相对该段 start）/"l03"
      type 见 references/sfx-design.md 的配方表，或 sfxkit.list_recipes()
    """
    plan = _plan(script)
    stagger = 0.32
    if not plan.get("legacy"):
        stagger = float((plan.get("pacing") or {}).get("item_stagger") or 0.32)
    out = []
    for s in tl["scenes"]:
        if s.get("kind") == "body":
            # 条目逐条落位：第一项在 0.48s 出现，之后每 stagger 秒一个（和 _draw_items 同一节拍）
            for i, _it in enumerate(s.get("items") or []):
                out.append({"at": "%s+%.2f" % (s["id"], 0.48 + stagger * i),
                            "type": "ui_blip", "pitch": 1760.0 + 110.0 * i, "gain": -6})
        if s.get("kind") == "outro":
            out.append({"at": "%s+0.35" % s["id"], "type": "answer_chime", "gain": -4})
    return out
