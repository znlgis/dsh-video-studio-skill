# -*- coding: utf-8 -*-
"""stylekit.py — 风格包（Style Packs）：一个 id 同时决定画风、配乐、旁白与节奏。

v0.5.0 新增。解决的问题是 v0.4 及以前的同质化：画风锁死在 scenes.py 的 THEME 常量、
配乐锁死在 scorekit 的 Dm/120，旁白锁死在 render.py 的单个 voice —— 每部片必然长一个样。

设计原则
--------
1. 纯数据 + 纯函数：本模块只依赖标准库，不 import filmkit / scorekit / scenes
   （它们反过来 import 我，成环就炸）。因此可以随便单测、追求确定性。
2. 一个风格包决定三件事：主题（配色/字体/材质/HUD）、配乐（BPM 梯度/调池/情绪/音色组）、
   旁白（声线映射），外加节奏（lead/tail/min_dur/条目落位步长）。不允许互相独立配，
   否则会出现「极简黑白画面 + 史诗管弦」这种打架组合。
3. 确定性：内容指纹只用 sha1 派生，禁 random / time / hash()（str hash 带随机盐）。
   同一份 film.json 永远解析出同一个风格；换一部片三个指纹大概率都变。
4. 向后兼容：不写 style 字段 = legacy，此时 render/scorekit/scenes 走 v0.4 原路径，
   配乐逐样本一致（selfcheck 有断言）。

三级指纹（专门为了「改个错别字不要换整首曲子」）
---------------------------------------------
  full   标题 + 所有台词              -> 决定音色细节 / 噪声纹理（seed 派生）
  tempo  标题 + kind 序列 + 体量桶     -> 决定 BPM 与调性
  style  关键词评分 + 指纹破平         -> 决定风格 id

用法
----
    import stylekit as ST
    plan = ST.resolve_script_style(script)      # 纯函数，返回可直接落盘的 dict
    th   = ST.theme_of(plan["style"])           # 主题字典（元组原样，给 PIL 用）
    ST.theme_lint(th)                           # 对比度体检，返回问题列表（空 = 过）

    python stylekit.py --list                   # 列所有风格
    python stylekit.py --show cinema            # 看一套风格的完整字段
    python stylekit.py --lint                   # 8 套主题全部走对比度门禁
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys

# ============================================================================
# 一、色彩：WCAG 2.1 对比度（门禁要用，所以放在本模块，纯标准库）
# ============================================================================


def hsv_to_rgb(h, s, v):
    """HSV(0..360, 0..1, 0..1) -> (r,g,b) 整数 0..255。

    技术原语：让"配色"可以是一个**连续空间**而不是一份命名清单。
    有了它，第 9 种外观 = 把色相转到 200°，不需要有人先写一个模板文件。
    """
    h = float(h) % 360.0
    s = max(0.0, min(1.0, float(s)))
    v = max(0.0, min(1.0, float(v)))
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    if h < 60:
        rgb = (c, x, 0.0)
    elif h < 120:
        rgb = (x, c, 0.0)
    elif h < 180:
        rgb = (0.0, c, x)
    elif h < 240:
        rgb = (0.0, x, c)
    elif h < 300:
        rgb = (x, 0.0, c)
    else:
        rgb = (c, 0.0, x)
    return tuple(int(round(max(0.0, min(1.0, ch + m)) * 255.0)) for ch in rgb)


def rgb_to_hsv(rgb):
    """(r,g,b) 0..255 -> (h 0..360, s 0..1, v 0..1)。与 hsv_to_rgb 互逆。"""
    r, g, b = (max(0.0, min(1.0, float(c) / 255.0)) for c in tuple(rgb)[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-12:
        return 0.0, 0.0, mx
    if mx == r:
        h = (60.0 * ((g - b) / d)) % 360.0
    elif mx == g:
        h = 60.0 * ((b - r) / d) + 120.0
    else:
        h = 60.0 * ((r - g) / d) + 240.0
    s = 0.0 if mx < 1e-12 else d / mx
    return h, s, mx


def _srgb_to_lin(v):
    """sRGB 通道（0..255）-> 线性光。"""
    c = float(v) / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    """相对亮度（WCAG 定义，0=黑 1=白）。"""
    r, g, b = rgb[0], rgb[1], rgb[2]
    return 0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g) + 0.0722 * _srgb_to_lin(b)


def contrast(c1, c2):
    """两色的对比度（1.0 ~ 21.0）。文字门禁用它，比「看截图觉得还行」可靠。"""
    l1, l2 = luminance(c1), luminance(c2)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def midpoint(c1, c2):
    """两色中点（渐变背景的中位亮度，作为文字对比度的参考底）。"""
    return (int(round((c1[0] + c2[0]) / 2.0)),
            int(round((c1[1] + c2[1]) / 2.0)),
            int(round((c1[2] + c2[2]) / 2.0)))


def _rgb(seq):
    t = tuple(int(v) for v in seq[:3])
    if len(t) != 3 or any(v < 0 or v > 255 for v in t):
        raise ValueError("颜色必须是 0..255 的 RGB 三元组，收到 %r" % (seq,))
    return t


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _color_of(v, key):
    """把 theme_overrides 里的颜色值统一成 (r,g,b)：支持 [r,g,b] 和 "#rrggbb" / "#rgb"。

    为什么必须支持 hex：schemas/film.schema.json 与 references/style-packs.md 的示例
    就是 {"accent": "#ff8a3d"}，而 JSON 没有元组，照着文档写 hex 是必然的。
    v0.5 旧实现把字符串原样塞进主题，scenes.py 第一次 int("#") 就崩在渲染期
    （实测：ink="#ffffff" -> ValueError: invalid literal for int() with base 10: '#'）。
    """
    if isinstance(v, str):
        m = _HEX_RE.match(v.strip())
        if not m:
            raise ValueError("theme_overrides.%s 的颜色写法不认识：%r（用 [r,g,b] 或 \"#rrggbb\"）"
                             % (key, v))
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return _rgb(v)
    raise ValueError("theme_overrides.%s 必须是颜色（[r,g,b] 或 \"#rrggbb\"），收到 %r"
                     % (key, v))


# 主题里"颜色字段"的白名单（值是 0..255 的 RGB 元组）。不靠"长度 3 的元组"猜：
# keywords 也是元组（长度 10），将来若加一个正好 3 项的关键词元组就会被误当颜色。
_THEME_COLOR_KEYS = frozenset(("bg", "bg2", "surface", "ink", "muted", "dim",
                               "accent", "accent2", "good", "bad", "rule", "vignette_tint"))


def apply_theme_overrides(theme, overrides):
    """把 film.json 的 visual.theme_overrides 合并进主题**并做类型校验**。

    契约（schemas/film.schema.json：只接受主题里已存在的键；references/style-packs.md §3）：
      - 未知键忽略：以后主题加字段时老剧本不因为它不认识就炸；
      - 颜色键（值本来是三色元组的那些）接受 [r,g,b] / "#rrggbb" / "#rgb"，统一成元组；
      - 数值键接受数字或数字字符串；字符串键必须是字符串；
      - 嵌套结构（card / scale / qa …）不支持覆盖，静默忽略（与 v0.5 一致，避免破坏老配置）；
        真要改嵌套字段请改风格包。
    校验只在"值类型不对、旧版会在渲染期崩"的场合抛 ValueError（提前到 --stage style）。
    返回新的 theme dict（不改入参）。
    """
    out = dict(theme or {})
    for k, v in (overrides or {}).items():
        if k not in out:
            continue
        base = out[k]
        if k in _THEME_COLOR_KEYS:
            out[k] = _color_of(v, k)
            continue
        if isinstance(base, bool):
            if not isinstance(v, bool):
                raise ValueError("theme_overrides.%s 必须是布尔，收到 %r" % (k, v))
            out[k] = bool(v)
            continue
        if isinstance(base, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                raise ValueError("theme_overrides.%s 必须是数字，收到 %r" % (k, v))
            try:
                num = float(v)
            except (TypeError, ValueError):
                raise ValueError("theme_overrides.%s 必须是数字，收到 %r" % (k, v))
            out[k] = int(round(num)) if isinstance(base, int) else float(num)
            continue
        if isinstance(base, dict):
            # 嵌套结构（card / scale / qa）不支持覆盖。这里保持 v0.5 的**静默忽略**：
            # 老配置里如果写了这种覆盖，旧版只是没生效、并不会崩；升级后突然报错是破坏性变更。
            # 想改嵌套字段请改风格包（STYLES/THEMES），见 references/style-packs.md §7。
            continue
        if isinstance(base, str):
            if not isinstance(v, str):
                raise ValueError("theme_overrides.%s 必须是字符串，收到 %r" % (k, v))
            if k == "layout" and v not in LAYOUTS:
                raise ValueError("theme_overrides.layout 未知版式 %r；可用：%s"
                                 % (v, " / ".join(LAYOUT_IDS)))
            if k == "hud" and v not in ("chip-left", "corner-line", "none"):
                raise ValueError("theme_overrides.hud 未知取值 %r；可用：chip-left / corner-line / none"
                                 % (v,))
            out[k] = v
            continue
        # 其它类型（None 等）：保持原值，不折腾
    return out


def _theme(style_id, label, keywords, palette, material, typ, layout, motion, hud, qa):
    """把主题字段组装成一个扁平 dict（场景代码直接下标取，不嵌套，读起来快）。"""
    p = {k: _rgb(v) for k, v in palette.items()}
    return {
        "id": style_id,
        "label": label,
        "keywords": tuple(keywords),
        # T0 配色
        "bg": p["bg"], "bg2": p["bg2"], "surface": p["surface"],
        "ink": p["ink"], "muted": p["muted"], "dim": p["dim"],
        "accent": p["accent"], "accent2": p["accent2"],
        "good": p["good"], "bad": p["bad"], "rule": p["rule"],
        # T1 材质
        "vignette": float(material["vignette"]),
        "vignette_tint": _rgb(material["vignette_tint"]),
        "grain": float(material["grain"]),
        "grain_freq": float(material["grain_freq"]),
        "texture": str(material["texture"]),
        "texture_alpha": float(material["texture_alpha"]),
        # T2 排版
        "family": str(typ["family"]),
        "scale": dict(typ["scale"]),
        "line_gap": float(typ.get("line_gap", 1.30)),
        "tracking": float(typ.get("tracking", 0.0)),
        # T3 版式
        "layout": str(layout["layout"]),
        "card": dict(layout["card"]),
        "safe_bias": int(layout.get("safe_bias", 0)),
        # T4 动态
        "ease": str(motion["ease"]),
        "entrance_dx": int(motion["entrance_dx"]),
        "entrance_dy": int(motion["entrance_dy"]),
        "push": float(motion["push"]),
        "item_stagger": float(motion["item_stagger"]),
        "transition": str(motion["transition"]),
        # T5 HUD
        "hud": str(hud["style"]),
        # META
        "qa": dict(qa),
    }


# ============================================================================
# 二、主题（已删除：v1.0.1 起本技能不提供任何模板）
# ============================================================================
#
# 这里曾有 8 套命名主题（THEMES：科技深蓝 / 近黑金 / 冷薄荷 / 暖砂金 / 石青灰 /
# 深林绿 / 暖纸白 / 墨纸白）。它们是**模板**：用户只能在这 8 种外观里选，
# 第 9 种永远不会出现。
#
# 现在主题由 designkit 从连续空间现场构造：
#   · 底色 = (色相 h∈[0,360), 饱和 s, 明度 v) 连续取值，经 _palette_from_hue 算出三段底色；
#   · 其余 8 个语义色由对比度网格求解（每套都必须过 theme_lint）；
#   · 产出仍是同形的 35 字段主题字典，scenes.py 不需要知道它从哪来。
#
# 保留的**技术原语**（这些不是模板，是造东西的工具）：
#   luminance / contrast / midpoint / _rgb / _color_of / apply_theme_overrides /
#   _theme(构造器) / hsv_to_rgb / rgb_to_hsv / LINT_LIMITS / theme_lint / is_dark /
#   blend_theme / family_compatible / LAYOUTS(几何参数，不是外观目录)
THEMES = {}
# 三、版式骨架（LAYOUTS）：3 种，scenes.py 按名字取几何参数
# ============================================================================
#
# 这是「每屏都长得一样」的解药：同一部片可以按章节在骨架之间轮换，但不换明暗族、不换 accent，
# 所以整体仍是一部片。几何全部相对于 W/H 与 SAFE，不写死 1920 里的魔法数。

LAYOUTS = {
    "left-stack": {
        "label": "左标题 / 右卡片",
        "title_x": 150, "title_y": 300, "title_w": 820,
        "kicker_y": 250, "sub_y": 420, "sub_w": 820,
        "panel_x": 1030, "panel_y": 240, "panel_w": 740, "panel_h": 96, "panel_gap": 118,
        "center": False,
    },
    "center-stack": {
        "label": "居中大字",
        "title_x": None, "title_y": 330, "title_w": 1560,
        "kicker_y": 268, "sub_y": 0, "sub_w": 1400,
        "panel_x": None, "panel_y": 650, "panel_w": 1320, "panel_h": 92, "panel_gap": 108,
        "center": True,
    },
    "split-right": {
        "label": "右标题 / 左卡片",
        "title_x": None, "title_y": 300, "title_w": 820,
        "kicker_y": 250, "sub_y": 420, "sub_w": 820,
        "panel_x": 150, "panel_y": 240, "panel_w": 740, "panel_h": 96, "panel_gap": 118,
        "center": False,
    },
}

LAYOUT_IDS = tuple(LAYOUTS.keys())


def layout(name):
    """取版式几何；未知名字报错并列出可用值（不静默回落）。"""
    if name not in LAYOUTS:
        raise KeyError("未知版式 %r；可用：%s" % (name, " / ".join(LAYOUT_IDS)))
    return LAYOUTS[name]


# ============================================================================
# 四、风格包（STYLES）：一个 id = 主题 + 配乐 + 旁白 + 节奏
# ============================================================================
#
# music 字段只放「策展层」（BPM 梯度 / 调池 / 情绪 / 音色组偏好），
# 具体的和弦进行、鼓型、乐器合成在 scorekit 里（合成层）。两边用同一个 style id 对齐。

# ============================================================================
# 四、风格包（已删除：v1.0.1 起不提供任何模板）
# ============================================================================
#
# 这里曾有 STYLES：一个 style id = 主题 + 配乐 + 旁白 + 节奏的整套模板，
# 外加 pick_style 按关键词/指纹**自动挑一套**。那是本技能最像"模板库"的部分。
#
# 现在没有任何 style id：一个设计从哪来只有一条路——work/design.json，
# 由 propose.py 按 brief 现场采样、人确认后冻结。
#
# 保留的：LEGACY_STYLE 常量（只为了让"写 style=legacy"能被明确拒绝并给迁移指引，
# 而不是抛 AttributeError）。
STYLE_IDS = ()
DEFAULT_STYLE = None
LEGACY_STYLE = "legacy"          # 只为让老剧本被**明确拒绝**时能报出可读的迁移信息
STYLES = {}

# ---- 调式池：技术原语，不是模板 ----
# 项目只支持中文口播常见的 4 个小调主音与 6 个大调主音。列出来是**约束**
# （musicgen 与 designkit 都按它校验"调性是否可实现"），不是"给你 10 套现成配色"。
MINOR_KEYS = ("Dm", "Am", "Em", "Cm")
MAJOR_KEYS = ("C", "G", "D", "F", "A", "Bb")


# ---- 兼容壳：模板表删掉后，老调用点不该 AttributeError ----
# 这些函数在 v1.0.1 之前用于"按 style id 取模板"。现在没有模板，它们**必须明确报错**，
# 而不是返回一个假的默认值（那等于偷偷恢复模板）。
def style_ids(include_legacy=True):
    return []


def get(style_id):
    raise KeyError(
        "没有风格 %r：v1.0.1 起本技能不提供内置风格（模板已删除）。\n"
        "  每一版外观都必须现场生成：python propose.py --brief \"题材\" --script film.json\n"
        "  再用 --confirm 冻结 design.json" % (style_id,))


def theme_of(style_id):
    raise KeyError(
        "没有主题 %r：v1.0.1 起没有内置主题表（模板已删除）。\n"
        "  主题由 designkit 从 (色相,饱和,明度) 连续空间现场构造，见 work/design.json"
        % (style_id,))


def is_legacy(style_id):
    return str(style_id or "") == LEGACY_STYLE
VISUAL_MOTIFS = ("brackets", "hairlines", "handdrawn", "slabs",
                 "minimal", "letterbox", "signal", "rays")
VISUAL_TRANSITIONS = ("dip", "wash", "paper", "wipe", "cut", "iris")
NUMBER_STYLES = ("dim", "serif", "cn", "block", "roman", "timecode", "dot", "gilt")

# ============================================================================
# 五、视觉语言包（已删除：v1.0.1 起不提供任何模板）
# ============================================================================
#
# 这里曾有 VISUAL_LANGUAGES：8 套命名结构签名（母题/转场/字距/缓动/重心/layout_cycle）。
# 它们是另一种模板——换风格时用户只能在这 8 组里挑。
#
# 现在视觉语言由 designkit._language_of(design) 从设计字段直接拼出来（同形五组），
# 再交给 _visual_lint_language 做同一条体检。
#
# 保留的：VISUAL_MOTIFS / VISUAL_TRANSITIONS / NUMBER_STYLES（**绘制原语的名字**，
# scenes.py 按名字分发到具体画法；这是"能画什么"，不是"长什么样"）、
# visual_lint_language(lang)（对任意 language 字典做体检的纯函数）。
VISUAL_LANGUAGES = {}


def visual_language(lang=None):
    """兼容壳：v1.0.1 起没有命名视觉语言，传什么返回什么；传 None 返回空壳。

    保留这个函数只是为了让 `plan["language"] = ST.visual_language(...)` 这类老调用点
    不至于 AttributeError —— 真正的语言包由 designkit 提供。"""
    return lang if isinstance(lang, dict) else {}


def visual_lint_language(vl):
    """结构签名体检：对**任意** language 字典，不要求它属于某个命名风格。

    这是从 v0.9 的 visual_lint(style_id) 里抽出来的核心——原来它先查
    VISUAL_LANGUAGES[style_id] 再体检，模板删掉后没有 style_id 可查了，
    但仍然需要"这套语言参数越界没有"的检查（否则转场 1 秒、push 0.3 这类
    会让整片观感崩掉的错误要等渲完 300 帧才发现）。
    """
    bad = []
    if not isinstance(vl, dict) or not vl:
        return ["视觉语言包缺失或不是字典"]
    mo = vl.get("motion") or {}
    mf = vl.get("motif") or {}          # 注意：mo = motion，mf = motif，别混用
    tr = vl.get("transition") or {}
    ty = vl.get("typography") or {}
    if str(mf.get("kind")) not in VISUAL_MOTIFS:
        bad.append("motif.kind=%r 不在 %s 里" % (mf.get("kind"), " / ".join(VISUAL_MOTIFS)))
    if str(tr.get("kind")) not in VISUAL_TRANSITIONS:
        bad.append("transition.kind=%r 不在 %s 里"
                   % (tr.get("kind"), " / ".join(VISUAL_TRANSITIONS)))
    try:
        _d = float(tr.get("dur") or 0.0)
    except (TypeError, ValueError):
        _d = -1.0
    if not 0.0 <= _d <= 0.60:
        # 段界 veil 超过 0.6s 会吃掉 3–6s 节奏下限，也会把两段内容糊在一起
        bad.append("transition.dur=%r 必须在 0~0.60s（段界 veil 上限）" % tr.get("dur"))
    if str(ty.get("number")) not in NUMBER_STYLES:
        bad.append("typography.number=%r 不在 %s 里"
                   % (ty.get("number"), " / ".join(NUMBER_STYLES)))
    for key, lo, hi in (("ratio", 1.0, 3.6), ("tracking", 0.0, 8.0),
                        ("line_gap", 1.15, 1.55), ("push", 0.0, 0.16)):
        try:
            v = float((ty if key != "push" else mo).get(key) or 0.0)
        except (TypeError, ValueError):
            bad.append("typography/motion.%s=%r 不是数字" % (key, (ty if key != "push" else mo).get(key)))
            continue
        if key == "ratio" and not lo <= v <= hi:
            bad.append("typography.ratio=%r 必须在 1.0~3.6（display:body 字号比）" % v)
        elif key == "tracking" and not lo <= v <= hi:
            bad.append("typography.tracking=%r 必须在 0~8px" % v)
        elif key == "line_gap" and not lo <= v <= hi:
            bad.append("typography.line_gap=%r 必须在 1.15~1.55" % v)
        elif key == "push" and not lo <= v <= hi:
            # push > 0.16 时背景层放大后内容会被推出 SAFE（_bg_plate 只裁不缩）
            bad.append("motion.push=%r 必须在 0~0.16（超过会把内容推出安全区）" % v)
    try:
        dens = float(mf.get("density") or 0.0)
    except (TypeError, ValueError):
        dens = -1.0
    if not 0.0 <= dens <= 1.0:
        bad.append("motif.density=%r 必须在 0~1" % mf.get("density"))
    elif dens <= 0.0 and str(mf.get("kind")) != "minimal":
        bad.append("motif.density=0 只允许用于 minimal（否则等于没有装饰语言）")
    enter = mo.get("enter") or (0.45, 0.85)
    try:
        ok_enter = (isinstance(enter, (list, tuple)) and len(enter) == 2
                    and 0.0 < float(enter[0]) <= float(enter[1]) <= 1.6)
    except (TypeError, ValueError):
        ok_enter = False
    if not ok_enter:
        bad.append("motion.enter=%r 必须是 (起, 止) 且 0 < 起 <= 止 <= 1.6 秒" % (enter,))
    return bad


def visual_lint(lang=None):
    """对**任意** language 字典做体检（不再是"某个 style id 的体检"）。"""
    if isinstance(lang, str):
        # 老的 visual_lint("cinema") 调用：没有命名语言了，明确报错而不是静默通过
        return ["没有命名视觉语言 %r（v1.0.1 起视觉语言由 design 现场生成）" % lang]
    return visual_lint_language(lang or {})


def visual_lint_all():
    """v1.0.1 起没有内置语言可逐一检查，保留空实现只为兼容。"""
    return {}
# ============================================================================
# 五、内容指纹与自动选风格
# ============================================================================

FP_VERSION = "style-fp-v1"


def _digest(*parts):
    """sha1 派生的稳定 32 位无符号整数（禁 hash()：str hash 每次进程带随机盐）。"""
    h = hashlib.sha1(("|".join(str(p) for p in parts)).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _text_of(script):
    """把剧本里所有可读文字摊平（标题权重最高）。"""
    title = str(script.get("title") or "")
    chunks = [title, title]                      # 标题 x2
    for ln in script.get("lines") or []:
        chunks.append(str(ln.get("chapter") or ln.get("kicker") or ""))
        chunks.append(str(ln.get("text") or ""))
        chunks.append(str(ln.get("sub") or ""))
        for it in ln.get("items") or []:
            if isinstance(it, dict):
                chunks.append(str(it.get("text") or ""))
            else:
                chunks.append(str(it))
    return "".join(chunks)


def fingerprint(script):
    """三级内容指纹。同剧本 -> 同指纹；换一部片 -> 三个都变。"""
    title = str(script.get("title") or "")
    lines = script.get("lines") or []
    texts = [str(ln.get("text") or "") for ln in lines]
    kinds = [str(ln.get("kind") or "body") for ln in lines]
    dur_bucket = int(len("".join(texts)) // 120)     # 体量桶：改几个字不动它
    return {
        "version": FP_VERSION,
        "full": "%08x" % _digest(FP_VERSION, "full", title, *texts),
        "tempo": "%08x" % _digest(FP_VERSION, "tempo", title, *kinds, dur_bucket),
        "style": "%08x" % _digest(FP_VERSION, "style", title),
    }


# —— 自动选风格（score_styles / pick_style / _kind_profile）已删除 ——
#
# 这三个函数是"机器替你挑一套内置风格"的实现：按关键词给 8 套 STYLES 打分、同分用指纹破平。
# v1.0.1 把 STYLES/THEMES 清空后它们必然崩（`THEMES[STYLES[sid]["theme"]]` -> KeyError），
# 而且留着也没有意义 —— 没有"可选集合"就没有"选"这件事。
#
# 为什么必须**删掉**而不是留着报错：留下一个"看起来还能调用"的入口，
# 下一个维护者会以为它是活的，调通了才发现整个语义已经不成立。
# 现在的唯一路径是 propose.py 现场采样。


# ============================================================================
# 六、解析：把 script 里所有显式覆盖 + 自动结果合成一份「最终计划」
# ============================================================================


def _pick_from_pool(pool, fp_hex, salt=""):
    """从池里按指纹稳定取一个。

    salt 用来把"风格 id"混进选择：不同风格即便指纹相同，也会落到池里不同位置。
    没有 salt 时会出现 paper 与 lo-fi 都选到 84BPM / G 调这种"风格不同但基础参数撞车"，
    听感上就打了折扣 —— 这正是 v0.4 同质化的另一种形态。
    """
    seq = [p for p in pool if p]
    if not seq:
        return None
    v = int(fp_hex, 16)
    if salt:
        v += _digest("pick", salt)
    return seq[v % len(seq)]


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


# persona 名 -> edge voice id 的**兼容映射**（v1.0 / D3）。
# 这不是"身份表"：它不带语速/音高/响度补偿，只是把老 STYLES 表里的字符串翻译成
# voicekit 现在要求的 edge voice id。真正的声音参数在 design.audio.voice 里。
_PERSONA_VOICE = {
    "narrator_male": "zh-CN-YunyangNeural",
    "narrator_female": "zh-CN-XiaoxiaoNeural",
    "warm_female": "zh-TW-HsiaoChenNeural",
    "bright_female": "zh-CN-XiaoyiNeural",
    "authority_male": "zh-CN-YunjianNeural",
    "young_male": "zh-CN-YunxiNeural",
    "teen_male": "zh-CN-YunxiaNeural",
    "dialect_ne_female": "zh-CN-liaoning-XiaobeiNeural",
    "dialect_sx_female": "zh-CN-shaanxi-XiaoniNeural",
}


def _persona_to_voice_id(name):
    """老 persona 名 -> edge voice id；已经是 voice id 就原样返回。"""
    s = str(name or "").strip()
    if not s:
        return s
    return _PERSONA_VOICE.get(s, s)


_AUTO_WARNED = []


def _warn_auto_style_once():
    """每天每进程只喊一次：自动选型已经不该再用了。"""
    if _AUTO_WARNED:
        return
    _AUTO_WARNED.append(1)
    import sys as _sys
    print("[stylekit][deprecated] style=auto 的自动选型已停用（v1.0 / D3）。"
          "\n  现在的主路径是 work/design.json：先 python propose.py --brief \"...\" 出方案，"
          "\n  再 python propose.py --confirm --candidate A 冻结设计。"
          "\n  想以出厂预设为起点：python propose.py --preset tech-deep ...",
          file=_sys.stderr)


def resolve_script_style(script):
    """解析整份剧本的最终风格计划（纯函数、确定性、可直接 json.dump）。

    优先级（从高到低）：
      1. script["style"]                全片锁定（不是 "auto" 就生效）
      2. script["visual"]["theme"]      等价于锁 style（主题与风格 1:1）
      3. 自动：关键词 + 指纹
    配乐 BPM / 调性 / 情绪、旁白声线映射、节奏都能被 film.json 单独覆盖。
    """
    fp = fingerprint(script)
    vis = script.get("visual") if isinstance(script.get("visual"), dict) else {}
    mus = script.get("music") if isinstance(script.get("music"), dict) else {}
    voce = script.get("voice") if isinstance(script.get("voice"), dict) else {}

    has_style_field = "style" in script
    raw = script.get("style")
    raw_s = str(raw).strip().lower() if raw is not None else ""
    if not has_style_field and not vis.get("theme") and not mus.get("style"):
        # v1.0 / D3：**legacy 路径已移除**。以前"不写 style"会静默落到 v0.4 的固定外观
        # （同一套皮肤 + 120BPM/Dm），这是同质化的另一半根因；D3 决定不再兼容它。
        # 报错必须给出可执行的下一步，否则用户只知道"跑不动"。
        raise ValueError(
            '这个剧本没有指定设计：v1.0 起「不写 style 就走老外观」的兼容路径已移除。\n'
            '  正确做法（两步）：\n'
            '    1) python propose.py --brief "题材/要求" --script <film.json> --n 3\n'
            '    2) python propose.py --confirm --candidate A      # 冻结成 work/design.json\n'
            '  想以出厂预设为起点：python propose.py --preset tech-deep --script <film.json>\n'
            '  （没有预设：v1.0.1 起不提供任何模板）')
    elif raw_s in ("", "auto", "default"):
        if vis.get("theme"):
            sid = str(vis["theme"])
            source = "visual.theme"
            reason = ["film.json visual.theme 指定"]
        else:
            # v1.0 / D3：**自动选型已停用**。
            # 这一条是"每部片都长一个样"的机制性根因（选出来的身份又确定又不透明），
            # 但它还有大量老调用方（render._plan_from_design 之外的兼容路径、scorekit、
            # check 脚本）在跑，所以这里先降级为"显式警告 + 继续工作"，而不是直接抛错——
            # 直接抛会让 100+ 个依赖点同时变红，反而没人敢继续迁移。
            # 真正的主路径是 work/design.json（由 propose.py --confirm 产出）。
            _warn_auto_style_once()
            sid, reason = pick_style(script)
            source = "auto"
    else:
        sid = str(raw).strip()
        if sid == LEGACY_STYLE:
            # 显式写 "legacy" 也是同一个坑：它只是"不写设计"的另一种写法。
            # 两套语义必须一起消失，否则用户会以为"显式写 legacy 还能用"。
            raise ValueError(
                'style="legacy" 已移除（v1.0 / D3）：它等价于"不要设计"，而 v1.0 起设计是必填的。\n'
                '  改用：python propose.py --brief "题材/要求" --script <film.json>，再 --confirm 选一个。')
        source = "explicit"
        reason = ["film.json style 指定"]
    get(sid)                       # 未知 id 在这里就炸，不要等到渲染 300 帧后
    pack = STYLES[sid]

    # ---- 配乐：风格 / BPM / 调性 / 情绪 ----
    music_style = str(mus.get("style") or "").strip().lower()
    if music_style in ("", "auto", "default"):
        music_style = sid if not is_legacy(sid) else LEGACY_STYLE
    mpack = pack["music"]
    ladder = tuple(mpack["bpm_ladder"])
    bpm_raw = mus.get("bpm", script.get("bpm"))
    if isinstance(bpm_raw, str) and bpm_raw.strip().lower() == "auto":
        bpm_raw = "auto"
    if bpm_raw in (None, "", "auto"):
        if is_legacy(sid):
            bpm, bpm_source = 120.0, "legacy"          # v0.4 的缺省值，必须是 120
        else:
            bpm = float(_pick_from_pool(ladder, fp["tempo"], salt="bpm:" + sid))
            bpm_source = "style_ladder"
    else:
        bpm, bpm_source = float(bpm_raw), "explicit"

    key_raw = mus.get("key")
    if key_raw in (None, "", "auto"):
        if is_legacy(sid):
            key, key_source = "Dm", "legacy"           # v0.4 的缺省值，必须是 Dm
        else:
            key = _pick_from_pool(mpack["keys"], fp["tempo"], salt="key:" + sid) or "Dm"
            key_source = "style_pool"
    else:
        key, key_source = str(key_raw), "explicit"

    mood = str(mus.get("mood") or "").strip().lower()
    if mood in ("", "auto", "default"):
        mood, mood_source = str(mpack.get("mood") or "steady"), "style"
    else:
        mood_source = "explicit"

    seed_raw = mus.get("seed", script.get("seed", 7))
    try:
        seed = abs(int(seed_raw if seed_raw is not None else 7))
    except (TypeError, ValueError):
        seed = 7
    seed_from_content = _as_bool(mus.get("seed_from_content"), default=(not is_legacy(sid)))
    if seed_from_content:
        seed_effective = (seed * 1000003 + int(fp["full"], 16)) % (2 ** 31 - 1)
    else:
        seed_effective = seed

    # ---- 旁白：声线映射 + 策略 ----
    # v1.0 / D3：voicekit 已删掉 persona 身份表，只认 edge voice id。
    # 而 STYLES 表里存的仍是老 persona 名（young_male 之类）——所以这里必须翻译一次。
    # 不翻译的话，走到 voicekit 就是"style_plan.voice.map['hook'] 不是 edge voice id"。
    style_voice = pack["voice"]
    voice_map = {}
    if not is_legacy(sid):
        for kind in ("hook", "body", "quote", "outro"):
            dv = style_voice.get(kind)
            if dv:
                voice_map[kind] = _persona_to_voice_id(dv)
    explicit_map = voce.get("map") if isinstance(voce.get("map"), dict) else {}
    voice_map.update({str(k): str(v) for k, v in explicit_map.items() if v})
    policy = {
        "auto": _as_bool(voce.get("auto"), default=(not is_legacy(sid))),
        "jitter": float(voce.get("jitter", 0.0 if is_legacy(sid) else 0.35)),
        "max_distinct": int(voce.get("max_distinct", 2 if is_legacy(sid) else 3)),
        "rate_range": (-15, 12),
        "pitch_range": (-8, 8),
        "cps_band": (3.6, 5.6),
    }
    if isinstance(voce.get("rate_range"), (list, tuple)) and len(voce["rate_range"]) == 2:
        policy["rate_range"] = (int(voce["rate_range"][0]), int(voce["rate_range"][1]))
    if isinstance(voce.get("cps_band"), (list, tuple)) and len(voce["cps_band"]) == 2:
        policy["cps_band"] = (float(voce["cps_band"][0]), float(voce["cps_band"][1]))

    # ---- 节奏 ----
    sp = pack["pacing"]
    pacing = {
        "lead": float(script.get("lead", sp["lead"])),
        "tail": float(script.get("tail", sp["tail"])),
        "min_dur": float(script.get("min_dur", sp["min_dur"])),
        "hold": float(script.get("hold", sp["hold"])),
        "item_stagger": float(THEMES[pack["theme"]]["item_stagger"]),
    }
    explicit_pace = script.get("pacing") if isinstance(script.get("pacing"), dict) else {}
    for k in ("lead", "tail", "min_dur", "hold", "item_stagger"):
        if explicit_pace.get(k) is not None:
            pacing[k] = float(explicit_pace[k])

    # ---- 版式轮换：按 chapter 轮换骨架（同一章节内不变，避免每句都跳） ----
    layouts = list(pack["layouts"]) or ["left-stack"]
    chapters = []
    for ln in script.get("lines") or []:
        ch = str(ln.get("chapter") or "")
        if ch not in chapters:
            chapters.append(ch)
    explicit_layout = str(vis.get("layout") or "").strip()
    if explicit_layout:
        layout(explicit_layout)
        chap_layout = {ch: explicit_layout for ch in chapters}
    else:
        chap_layout = {ch: layouts[i % len(layouts)] for i, ch in enumerate(chapters)}

    # theme_overrides 走带校验的合并（颜色支持 "#rrggbb"，非法值在这里就报错，
    # 不要等到 scenes.py 渲到第 300 帧才 int("#") 崩——见 apply_theme_overrides）
    theme = apply_theme_overrides(THEMES[pack["theme"]], vis.get("theme_overrides"))
    # visual.layout 是"显式换整片版式"：旧的实现只把它写进 chapter_layout，而
    # PIL 路线渲染读的是 theme["layout"]、web 路线读 layout_default —— 两条路线都不看
    # chapter_layout，于是这条文档化的覆盖等于没写（v0.8 深审实测：visual.layout=
    # split-right 的帧与默认帧 sha256 完全相同）。这里补写 theme["layout"] 与 layout_default，
    # 让两条路线都跟着走；theme_overrides 更具体，它显式写了 layout 时以它为准。
    if explicit_layout and "layout" not in (vis.get("theme_overrides") or {}):
        theme["layout"] = explicit_layout
    if explicit_layout:
        chap_layout = {ch: str(theme["layout"]) for ch in chapters}

    return {
        "version": 1,
        "generator": "stylekit 0.5.0",
        "style": sid,
        "style_name": pack["name"],
        "style_source": source,
        "style_reason": list(reason),
        "legacy": bool(is_legacy(sid)),
        "fingerprint": fp,
        "theme_id": pack["theme"],
        "theme_label": THEMES[pack["theme"]]["label"],
        # 主题字典随计划一起返回：scenes.py 的 _scene_theme 优先用它（含 theme_overrides），
        # 也方便 render.py 直接传给 draw_subtitle/write_ass，不必再按 id 查一次。
        "theme": theme,
        "layout_default": str(theme["layout"]) if explicit_layout else layouts[0],
        "chapter_layout": chap_layout,
        "music": {
            "style": music_style,
            "bpm": bpm, "bpm_source": bpm_source,
            "key": key, "key_source": key_source,
            "mood": mood, "mood_source": mood_source,
            "seed": int(seed), "seed_effective": int(seed_effective),
            "seed_from_content": bool(seed_from_content),
        },
        "voice": {
            "map": voice_map,
            "default": voce.get("default"),
            "policy": policy,
        },
        "pacing": pacing,
    }


# ============================================================================
# 七、门禁：主题体检（对比度 / 明暗 / 结构）
# ============================================================================
#
# 阈值不是拍脑袋：以 v0.4 现行主题实测值当基线（tech-deep：ink 16.3 / muted 7.8 / accent 7.0，
# 那是旧版按 bg/bg2 中点量的；v0.8 改成按两端最差值量后同一主题是 14.8 / 7.1 / 6.3，仍远高于线）。

LINT_LIMITS = {
    "ink": 7.0,          # 正文级（WCAG AAA 正文线）
    "muted": 4.5,        # 次级正文（WCAG AA）
    "dim": 3.0,          # 装饰级（HUD 小字、页脚）
    "accent": 4.5,       # 强调色常作文字
    "accent2": 4.5,      # 第二色相也常作文字
    "good": 3.0,         # 只承载图形（圆点靠位置+颜色双编码）
    "bad": 3.0,
    "lum_gap": 0.002,    # bg 与 bg2 的亮度差（防纯色底）
}


def bg_contrast(th, role):
    """文字色对背景的**最差**对比度：bg/bg2 是上下两端的主渐变（scenes._bg 按行插值），
    任何一行文字都可能落在两端附近，所以取二者较小值，而不是看中点。

    为什么不能只看中点：lo-fi 的 muted(86,100,96) 对 bg 5.28、对 bg2 只有 4.42，
    中点平均 4.84 把 AA 4.5 的门槛"平均"过去了 —— 实测过（v0.8 深审），
    这一版把门槛改成端点最差值，并同时下调 lo-fi 的 muted 到 (84,98,94)
    （bg 5.44 / bg2 4.56，两端都过）。
    """
    return min(contrast(th[role], th["bg"]), contrast(th[role], th["bg2"]))


def theme_lint(th):
    """主题体检，返回问题列表（空 = 过）。坏主题在 load 阶段就拦，别等渲完 300 帧。"""
    if not isinstance(th, dict):
        return ["主题不是 dict：%r" % (th,)]
    bad = []
    for role, need in (("ink", LINT_LIMITS["ink"]), ("muted", LINT_LIMITS["muted"]),
                       ("dim", LINT_LIMITS["dim"]), ("accent", LINT_LIMITS["accent"]),
                       ("accent2", LINT_LIMITS["accent2"])):
        got = bg_contrast(th, role)
        if got < need:
            bad.append("%s 对背景对比度 %.2f < %.1f（bg/bg2 两端取最差）"
                       % (role, got, need))
    for role in ("good", "bad"):
        got = contrast(th[role], th["surface"])
        if got < LINT_LIMITS[role]:
            bad.append("%s 对卡片底色对比度 %.2f < %.1f" % (role, got, LINT_LIMITS[role]))
    gap = abs(luminance(th["bg"]) - luminance(th["bg2"]))
    if gap < LINT_LIMITS["lum_gap"]:
        bad.append("bg/bg2 亮度差 %.4f 太小（会像纯色底）" % gap)
    if luminance(th["bg"]) < 0.02 and float(th.get("qa", {}).get("luma_max", 0.045)) > 0.03:
        bad.append("暗色主题 bg 亮度 %.3f 贴空窗阈值，请把 qa.luma_max 降到 0.03 以下"
                   % luminance(th["bg"]))
    if th.get("layout") not in LAYOUTS:
        bad.append("layout=%r 不在 LAYOUTS 里" % th.get("layout"))
    if th.get("hud") not in ("chip-left", "corner-line", "none"):
        bad.append("hud=%r 不认识" % th.get("hud"))
    if not isinstance(th.get("item_stagger"), (int, float)) or th["item_stagger"] <= 0:
        bad.append("item_stagger 必须是正数")
    return bad


def lint_all():
    """全部内置主题体检，返回 {style_id: [问题...]}（只有有问题的才在里面）。"""
    out = {}
    for sid in STYLE_IDS:
        bad = theme_lint(THEMES[STYLES[sid]["theme"]])
        if bad:
            out[sid] = bad
    return out


# ============================================================================
# 八、片内一致性：明暗族判定（段界不许跨族，否则必触发闪烁门禁）
# ============================================================================


def is_dark(th):
    return luminance(th["bg"]) < 0.35


def family_compatible(a, b):
    """两主题能否出现在同一部片里（同明暗族）。跨族=两部片，不是两段。"""
    return is_dark(a) == is_dark(b)


def blend_theme(a, b, p):
    """按 p in [0,1] 线性插值两套主题（同族时用于段界缓变，避免整片换底触发闪烁门禁）。"""
    if not family_compatible(a, b):
        raise ValueError("不能混合明暗族不同的主题：%s / %s" % (a.get("id"), b.get("id")))
    q = max(0.0, min(1.0, float(p)))
    out = dict(a)
    for k, va in a.items():
        vb = b.get(k, va)
        if isinstance(va, tuple) and len(va) == 3 and isinstance(vb, tuple) and len(vb) == 3:
            out[k] = tuple(int(round(va[i] * (1 - q) + vb[i] * q)) for i in range(3))
        elif (isinstance(va, (int, float)) and not isinstance(va, bool)
              and isinstance(vb, (int, float))):
            out[k] = type(va)(round(va * (1 - q) + vb * q, 4))
    return out


# ============================================================================
# 九、CLI：看风格、看主题、跑门禁
# ============================================================================


def _describe(sid):
    pack = STYLES[sid]
    th = THEMES[pack["theme"]]
    lines = [
        "[%s] %s  （theme=%s / layouts=%s）" % (sid, pack["name"], pack["theme"],
                                                ",".join(pack["layouts"])),
        "  tags      : %s" % "、".join(pack["tags"]),
        "  keywords  : %s" % "、".join(th["keywords"]),
        "  music     : %s 调池=%s  BPM 梯度=%s  mood=%s"
        % (pack["music"]["scale"], ",".join(pack["music"]["keys"]),
           ",".join(str(b) for b in pack["music"]["bpm_ladder"]), pack["music"]["mood"]),
        "  voice     : %s" % ", ".join("%s=%s" % (k, v) for k, v in pack["voice"].items() if v),
        "  pacing    : lead=%.2f tail=%.2f min_dur=%.1f"
        % (pack["pacing"]["lead"], pack["pacing"]["tail"], pack["pacing"]["min_dur"]),
        "  palette   : bg=%s bg2=%s ink=%s accent=%s"
        % (th["bg"], th["bg2"], th["ink"], th["accent"]),
        # 与 theme_lint 同口径：bg/bg2 两端取最差（旧版打中点值，会掩盖端点不达标）
        "  contrast  : ink %.2f / muted %.2f / dim %.2f / accent %.2f（bg/bg2 最差端）"
        % (bg_contrast(th, "ink"), bg_contrast(th, "muted"),
           bg_contrast(th, "dim"), bg_contrast(th, "accent")),
        "  font      : family=%s display=%spx hud=%s"
        % (th["family"], th["scale"]["display"][0], th["hud"]),
    ]
    bad = theme_lint(th)
    lines.append("  lint      : %s" % ("PASS" if not bad else "FAIL -> " + "；".join(bad)))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="风格包：列出 / 查看 / 体检")
    ap.add_argument("--list", action="store_true", help="列出所有风格")
    ap.add_argument("--show", metavar="ID", help="查看某个风格的完整字段")
    ap.add_argument("--lint", action="store_true", help="全部内置主题走对比度门禁")
    ap.add_argument("--json", action="store_true", help="配合 --show 输出 JSON")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                   # noqa: BLE001
        pass
    if a.show:
        # 未知 id 以前是 KeyError traceback，现在给可用清单 + exit 2（与 render.py 的口径一致）
        if a.show not in STYLES:
            print("[未知风格] %r；可用：%s" % (a.show, " / ".join(ALL_STYLE_IDS)), file=sys.stderr)
            return 2
        if a.json:
            import json
            print(json.dumps({"style": STYLES[a.show],
                              "theme": THEMES[STYLES[a.show]["theme"]]},
                             ensure_ascii=False, indent=1, default=str))
        else:
            print(_describe(a.show))
        return 0
    if a.lint:
        bad = lint_all()
        for sid in STYLE_IDS:
            print(("FAIL " if sid in bad else "PASS ") + sid
                  + ("" if sid not in bad else "  " + "；".join(bad[sid])))
        return 1 if bad else 0
    for sid in ALL_STYLE_IDS:
        print(_describe(sid))
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
