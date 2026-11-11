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


THEMES = {}

THEMES["tech-deep"] = _theme(
    "tech-deep", "科技深蓝",
    ("AI", "算法", "代码", "模型", "系统", "数据", "架构", "开源", "编程", "大模型"),
    dict(bg=(7, 14, 26), bg2=(17, 33, 54), surface=(26, 42, 64),
         ink=(240, 245, 252), muted=(150, 174, 200), dim=(120, 142, 168),
         accent=(243, 132, 60), accent2=(86, 170, 232),
         good=(108, 178, 110), bad=(226, 96, 92), rule=(255, 255, 255)),
    dict(vignette=0.40, vignette_tint=(0, 0, 0), grain=5.0, grain_freq=0.31,
         texture="dots", texture_alpha=0.045),
    dict(family="sans", scale={"display": (96, "bold"), "heading": (64, "bold"),
                               "sub": (38, "light"), "body": (34, "reg"),
                               "kicker": (28, "bold"), "chip": (27, "reg"),
                               "brand": (23, "reg")}),
    dict(layout="left-stack",
         card={"radius": 14, "fill_a": 16, "outline_a": 28, "pad": 34, "h": 96, "gap": 118}),
    dict(ease="out3", entrance_dx=0, entrance_dy=26, push=0.04, item_stagger=0.32, transition="black"),
    dict(style="chip-left"),
    dict(luma_max=0.030, flicker_thresh=6.0, dark=True),
)

THEMES["magazine"] = _theme(
    "magazine", "杂志暖白",
    ("报告", "复盘", "年度", "总结", "研究", "分析", "调研", "白皮书", "趋势", "发布"),
    dict(bg=(250, 246, 238), bg2=(236, 228, 214), surface=(255, 252, 246),
         ink=(28, 26, 24), muted=(88, 82, 74), dim=(112, 104, 94),
         accent=(176, 48, 40), accent2=(38, 86, 128),
         good=(30, 110, 72), bad=(160, 36, 32), rule=(28, 26, 24)),
    dict(vignette=0.10, vignette_tint=(214, 204, 186), grain=3.0, grain_freq=0.27,
         texture="paper", texture_alpha=0.030),
    dict(family="serif", scale={"display": (92, "bold"), "heading": (60, "bold"),
                                "sub": (36, "light"), "body": (33, "reg"),
                                "kicker": (27, "bold"), "chip": (26, "reg"),
                                "brand": (22, "reg")}, line_gap=1.34, tracking=2.0),
    dict(layout="split-right",
         card={"radius": 8, "fill_a": 26, "outline_a": 40, "pad": 30, "h": 92, "gap": 112},
         safe_bias=-8),
    dict(ease="out5", entrance_dx=18, entrance_dy=20, push=0.06, item_stagger=0.38, transition="wash"),
    dict(style="corner-line"),
    dict(luma_max=0.045, flicker_thresh=6.0, dark=False),
)

THEMES["paper"] = _theme(
    "paper", "手绘纸感",
    ("手工", "自然", "历史", "传统", "木", "田野", "匠", "古", "茶", "手艺", "山河"),
    dict(bg=(240, 232, 212), bg2=(222, 210, 186), surface=(249, 244, 232),
         ink=(46, 38, 30), muted=(92, 80, 64), dim=(112, 98, 80),
         accent=(42, 72, 116), accent2=(124, 72, 32),
         good=(48, 98, 60), bad=(150, 48, 40), rule=(46, 38, 30)),
    dict(vignette=0.14, vignette_tint=(198, 184, 158), grain=9.0, grain_freq=0.22,
         texture="paper", texture_alpha=0.055),
    dict(family="kai", scale={"display": (92, "bold"), "heading": (60, "bold"),
                              "sub": (36, "light"), "body": (33, "reg"),
                              "kicker": (27, "bold"), "chip": (26, "reg"),
                              "brand": (22, "reg")}, line_gap=1.36, tracking=1.0),
    dict(layout="left-stack",
         card={"radius": 4, "fill_a": 30, "outline_a": 52, "pad": 32, "h": 94, "gap": 116}),
    dict(ease="inOut3", entrance_dx=0, entrance_dy=18, push=0.03, item_stagger=0.42, transition="wash"),
    dict(style="corner-line"),
    dict(luma_max=0.045, flicker_thresh=6.0, dark=False),
)

THEMES["vivid"] = _theme(
    "vivid", "活力撞色",
    ("活动", "上新", "招募", "挑战", "年轻", "潮流", "种草", "福利", "开箱", "限量"),
    dict(bg=(20, 18, 34), bg2=(38, 26, 58), surface=(46, 34, 70),
         ink=(255, 252, 246), muted=(206, 196, 224), dim=(168, 158, 190),
         accent=(255, 214, 64), accent2=(120, 224, 255),
         good=(120, 230, 150), bad=(255, 120, 150), rule=(255, 255, 255)),
    dict(vignette=0.24, vignette_tint=(12, 10, 22), grain=4.0, grain_freq=0.35,
         texture="grid", texture_alpha=0.060),
    dict(family="sans", scale={"display": (104, "bold"), "heading": (68, "bold"),
                               "sub": (39, "reg"), "body": (35, "reg"),
                               "kicker": (30, "bold"), "chip": (27, "reg"),
                               "brand": (23, "reg")}, line_gap=1.26, tracking=1.0),
    dict(layout="center-stack",
         card={"radius": 22, "fill_a": 30, "outline_a": 56, "pad": 32, "h": 100, "gap": 122}),
    dict(ease="outBack", entrance_dx=0, entrance_dy=34, push=0.10, item_stagger=0.26, transition="black"),
    dict(style="chip-left"),
    dict(luma_max=0.030, flicker_thresh=8.0, dark=True),
)

THEMES["mono"] = _theme(
    "mono", "极简黑白",
    ("观点", "为什么", "本质", "真相", "极简", "反思", "边界", "定义", "取舍"),
    dict(bg=(246, 246, 244), bg2=(222, 222, 220), surface=(255, 255, 255),
         ink=(16, 16, 16), muted=(68, 68, 68), dim=(104, 104, 104),
         accent=(16, 16, 16), accent2=(80, 80, 80),
         good=(38, 92, 52), bad=(150, 36, 36), rule=(16, 16, 16)),
    dict(vignette=0.06, vignette_tint=(214, 214, 212), grain=0.0, grain_freq=0.30,
         texture="none", texture_alpha=0.0),
    dict(family="sans", scale={"display": (104, "bold"), "heading": (66, "bold"),
                               "sub": (36, "light"), "body": (33, "reg"),
                               "kicker": (26, "bold"), "chip": (25, "reg"),
                               "brand": (21, "reg")}, line_gap=1.40, tracking=6.0),
    dict(layout="center-stack",
         card={"radius": 0, "fill_a": 0, "outline_a": 70, "pad": 28, "h": 90, "gap": 108}),
    dict(ease="inOut3", entrance_dx=0, entrance_dy=12, push=0.02, item_stagger=0.55, transition="none"),
    dict(style="corner-line"),
    dict(luma_max=0.045, flicker_thresh=6.0, dark=False),
)

THEMES["cinema"] = _theme(
    "cinema", "电影青橙",
    ("故事", "记忆", "城市", "深夜", "旅行", "纪实", "情绪", "时代", "变迁", "命运"),
    dict(bg=(8, 14, 16), bg2=(20, 32, 34), surface=(26, 40, 42),
         ink=(238, 234, 226), muted=(164, 176, 170), dim=(128, 140, 136),
         accent=(232, 126, 58), accent2=(104, 196, 190),
         good=(116, 196, 140), bad=(214, 92, 84), rule=(255, 255, 255)),
    dict(vignette=0.55, vignette_tint=(0, 0, 0), grain=6.0, grain_freq=0.29,
         texture="scanlines", texture_alpha=0.035),
    dict(family="sans", scale={"display": (94, "bold"), "heading": (58, "bold"),
                               "sub": (36, "light"), "body": (33, "reg"),
                               "kicker": (27, "bold"), "chip": (26, "reg"),
                               "brand": (22, "reg")}, line_gap=1.38, tracking=3.0),
    dict(layout="center-stack",
         card={"radius": 2, "fill_a": 14, "outline_a": 30, "pad": 30, "h": 92, "gap": 114}),
    dict(ease="inOut3", entrance_dx=0, entrance_dy=16, push=0.12, item_stagger=0.46, transition="black"),
    dict(style="corner-line"),
    dict(luma_max=0.020, flicker_thresh=6.0, dark=True),
)

THEMES["lo-fi"] = _theme(
    "lo-fi", "暖灰薄荷",
    ("深夜", "学习", "效率", "专注", "写作", "整理", "笔记", "书", "日常", "房间"),
    # 冷调灰绿：和 paper（暖牛皮纸）在色相上分开，不靠"亮度差不多"硬撑
    dict(bg=(232, 238, 236), bg2=(210, 220, 216), surface=(244, 248, 246),
         ink=(44, 52, 50), muted=(84, 98, 94), dim=(112, 126, 122),
         accent=(20, 86, 84), accent2=(104, 72, 96),
         good=(40, 100, 72), bad=(146, 52, 50), rule=(44, 52, 50)),
    dict(vignette=0.12, vignette_tint=(206, 200, 190), grain=7.0, grain_freq=0.24,
         texture="paper", texture_alpha=0.048),
    dict(family="sans", scale={"display": (86, "bold"), "heading": (58, "bold"),
                               "sub": (35, "light"), "body": (32, "reg"),
                               "kicker": (26, "bold"), "chip": (25, "reg"),
                               "brand": (21, "reg")}, line_gap=1.36, tracking=1.0),
    dict(layout="left-stack",
         card={"radius": 18, "fill_a": 40, "outline_a": 26, "pad": 34, "h": 98, "gap": 120},
         safe_bias=6),
    dict(ease="out3", entrance_dx=0, entrance_dy=22, push=0.05, item_stagger=0.44, transition="wash"),
    dict(style="chip-left"),
    dict(luma_max=0.045, flicker_thresh=6.0, dark=False),
)

THEMES["epic"] = _theme(
    "epic", "深黑鎏金",
    ("史诗", "爆发", "冲击", "巅峰", "决战", "荣耀", "震撼", "传奇", "极限", "突破"),
    dict(bg=(9, 9, 12), bg2=(24, 20, 16), surface=(32, 28, 22),
         ink=(248, 242, 228), muted=(186, 176, 158), dim=(142, 134, 120),
         accent=(222, 178, 84), accent2=(206, 116, 92),
         good=(140, 200, 140), bad=(222, 96, 84), rule=(255, 255, 255)),
    dict(vignette=0.62, vignette_tint=(0, 0, 0), grain=5.0, grain_freq=0.26,
         texture="scanlines", texture_alpha=0.028),
    dict(family="serif", scale={"display": (108, "bold"), "heading": (70, "bold"),
                                "sub": (38, "light"), "body": (34, "reg"),
                                "kicker": (29, "bold"), "chip": (27, "reg"),
                                "brand": (23, "reg")}, line_gap=1.32, tracking=4.0),
    dict(layout="center-stack",
         card={"radius": 6, "fill_a": 20, "outline_a": 44, "pad": 32, "h": 98, "gap": 120}),
    dict(ease="out5", entrance_dx=0, entrance_dy=24, push=0.14, item_stagger=0.34, transition="black"),
    dict(style="corner-line"),
    dict(luma_max=0.018, flicker_thresh=6.0, dark=True),
)

# ============================================================================
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

STYLE_IDS = ("tech-deep", "magazine", "paper", "vivid", "mono", "cinema", "lo-fi", "epic")
DEFAULT_STYLE = "tech-deep"
LEGACY_STYLE = "legacy"          # 显式老行为：v0.4 的画风 + 配乐 + 单一旁白

MINOR_KEYS = ("Dm", "Am", "Em", "Cm")
MAJOR_KEYS = ("C", "G", "D", "F", "A", "Bb")

STYLES = {
    "tech-deep": {
        "name": "科技深蓝",
        "tags": ("AI", "工程", "数据", "科普"),
        "theme": "tech-deep",
        "layouts": ("left-stack", "split-right"),
        "music": {"keys": ("Dm", "Am", "Em", "Cm"), "scale": "minor",
                  "bpm_ladder": (100, 108, 116, 124), "mood": "steady"},
        "voice": {"body": "narrator_male", "hook": "young_male",
                  "quote": "narrator_female", "outro": "narrator_male"},
        "pacing": {"lead": 0.58, "tail": 0.88, "min_dur": 3.0, "hold": 0.0},
    },
    "magazine": {
        "name": "杂志暖白",
        "tags": ("报告", "复盘", "研究", "出版物"),
        "theme": "magazine",
        "layouts": ("left-stack", "split-right"),
        "music": {"keys": ("C", "G", "D", "F"), "scale": "major",
                  "bpm_ladder": (88, 92, 96, 100), "mood": "reflective"},
        "voice": {"body": "narrator_female", "hook": "narrator_female",
                  "quote": "authority_male", "outro": "narrator_female"},
        "pacing": {"lead": 0.64, "tail": 0.94, "min_dur": 3.2, "hold": 0.2},
    },
    "paper": {
        "name": "手绘纸感",
        "tags": ("手工", "自然", "历史", "人文"),
        "theme": "paper",
        "layouts": ("left-stack", "center-stack"),
        "music": {"keys": ("G", "D", "C"), "scale": "major",
                  "bpm_ladder": (84, 88, 92), "mood": "hopeful"},
        "voice": {"body": "warm_female", "hook": "narrator_female",
                  "quote": "narrator_male", "outro": "warm_female"},
        "pacing": {"lead": 0.68, "tail": 1.00, "min_dur": 3.4, "hold": 0.3},
    },
    "vivid": {
        "name": "活力撞色",
        "tags": ("活动", "营销", "年轻", "上新"),
        "theme": "vivid",
        "layouts": ("center-stack", "left-stack"),
        "music": {"keys": ("D", "G", "A"), "scale": "major",
                  "bpm_ladder": (120, 126, 132), "mood": "uplifting"},
        "voice": {"body": "narrator_female", "hook": "bright_female",
                  "quote": "young_male", "outro": "bright_female"},
        "pacing": {"lead": 0.46, "tail": 0.76, "min_dur": 2.8, "hold": 0.0},
    },
    "mono": {
        "name": "极简黑白",
        "tags": ("观点", "演讲", "思辨", "极简"),
        "theme": "mono",
        "layouts": ("center-stack",),
        "music": {"keys": ("C", "F", "Am"), "scale": "major",
                  "bpm_ladder": (60, 64, 68), "mood": "calm"},
        "voice": {"body": "narrator_male", "hook": "authority_male",
                  "quote": "narrator_female", "outro": "authority_male"},
        "pacing": {"lead": 0.86, "tail": 1.24, "min_dur": 4.0, "hold": 0.6},
    },
    "cinema": {
        "name": "电影青橙",
        "tags": ("纪录", "叙事", "散文", "城市"),
        "theme": "cinema",
        "layouts": ("center-stack", "left-stack"),
        "music": {"keys": ("Dm", "Am"), "scale": "minor",
                  "bpm_ladder": (76, 80, 84), "mood": "reflective"},
        "voice": {"body": "narrator_male", "hook": "authority_male",
                  "quote": "narrator_female", "outro": "narrator_male"},
        "pacing": {"lead": 0.76, "tail": 1.10, "min_dur": 3.6, "hold": 0.4},
    },
    "lo-fi": {
        "name": "暖灰薄荷",
        "tags": ("日常", "学习", "专注", "深夜"),
        "theme": "lo-fi",
        "layouts": ("left-stack", "split-right"),
        "music": {"keys": ("C", "F", "G"), "scale": "major",
                  "bpm_ladder": (72, 78, 84), "mood": "calm"},
        "voice": {"body": "narrator_female", "hook": "warm_female",
                  "quote": "narrator_male", "outro": "narrator_female"},
        "pacing": {"lead": 0.72, "tail": 1.06, "min_dur": 3.4, "hold": 0.4},
    },
    "epic": {
        "name": "深黑鎏金",
        "tags": ("史诗", "宣传", "高潮", "荣耀"),
        "theme": "epic",
        "layouts": ("center-stack",),
        "music": {"keys": ("Cm", "Dm"), "scale": "minor",
                  "bpm_ladder": (88, 92, 96), "mood": "driving"},
        "voice": {"body": "authority_male", "hook": "narrator_male",
                  "quote": "narrator_female", "outro": "authority_male"},
        "pacing": {"lead": 0.62, "tail": 0.96, "min_dur": 3.2, "hold": 0.2},
    },
}

# 老行为（不写 style 时）：画风 = v0.4 的深蓝橙，配乐 = v0.4 的 Dm/120/seed7，旁白 = script.voice
STYLES[LEGACY_STYLE] = {
    "name": "v0.4 兼容",
    "tags": ("兼容", "老项目"),
    "theme": "tech-deep",
    "layouts": ("left-stack",),
    "music": {"keys": ("Dm", "Am", "Cm", "Em"), "scale": "minor",
              "bpm_ladder": (120,), "mood": "steady", "legacy": True},
    "voice": {"body": None, "hook": None, "quote": None, "outro": None},
    # 必须是 render.py v0.4 的函数默认值（lead=0.55 / tail=0.85 / min_dur=3.0），
    # 否则"没写 lead 字段的老剧本"会悄悄换掉节奏 —— legacy 的含义就是一个字节都不变。
    "pacing": {"lead": 0.55, "tail": 0.85, "min_dur": 3.0, "hold": 0.0},
}

# ============================================================================
# 四、视觉语言包（VISUAL_LANGUAGES）：v0.9
# ============================================================================
#
# 为什么还要一层
# -------------
# v0.5 的 THEMES 只管"皮肤"：配色、字体族、纹理、颗粒、字距、缓动、HUD、3 种版式几何。
# 8 套风格共用同一批版式与同一套装饰（开场放射线 + 收尾光点环），所以换风格只是换色，
# 构图骨架（剪影）没变 —— 这正是"每个视频都差不多"的根因。
#
# 这一层补的是**结构签名**，四组：
#   motion     入场怎么进、镜头怎么推、呼吸元素多快
#   motif      这套风格独有的装饰图形语言（直角括角 / 手绘内框 / letterbox / 信号条 …）
#   transition 段与段之间怎么过（dip / wash / 纸卷 / 斜擦 / 硬切 / 放射环）
#   typography 字距、行高、对齐、编号写法、display:body 字号比
#
# 设计原则（与 THEMES 一致）
#   1. 纯数据：不 import PIL / filmkit，scenes.py 反过来读我，成环就炸。
#   2. 只追加不改顺序：新风格往 STYLE_IDS 末尾追加，老风格的指纹映射不漂。
#   3. 每条签名都必须"能被剪影看出差别"：只改颜色/字重的项不算签名。
#   4. 全部受 PIL 预算约束：装饰要么预烘焙成静态层，要么是 O(几) 次 ImageDraw 调用；
#      禁每帧全屏 GaussianBlur（实测 30–60ms）与全屏双线性 resize（29ms），预算 25ms。
#
# 剪影判据（tools/silhouette_qa.py 用的口径，也是 P1 的验收标准）
#   同一段内容在两种风格下渲 3 帧 → 灰度 → 以四角亮度中位数为基准三值化 →
#   比较 前景占比 F / 前景质心 (Cx,Cy) / 四边覆盖率 B / 左右对称度 S。
#   五项里至少一项达标才算"这套风格真的不一样"：
#     ΔF ≥ 6pp、|ΔCx| ≥ 8pp、|ΔCy| ≥ 8pp、ΔB ≥ 14pp、ΔS ≥ 0.20

VISUAL_MOTIFS = ("brackets", "hairlines", "handdrawn", "slabs",
                 "minimal", "letterbox", "signal", "rays")
VISUAL_TRANSITIONS = ("dip", "wash", "paper", "wipe", "cut", "iris")
NUMBER_STYLES = ("dim", "serif", "cn", "block", "roman", "timecode", "dot", "gilt")

VISUAL_LANGUAGES = {}

VISUAL_LANGUAGES["tech-deep"] = {
    "motion": {"ease": "out3", "enter": (0.45, 0.85), "push": 0.04, "breathe_hz": 0.85},
    "motif": {"kind": "brackets", "density": 1.0,
              "detail": {"corner": 24, "rule_step": 120, "scan": True}},
    "transition": {"kind": "dip", "dur": 0.28},
    "typography": {"tracking": 0.0, "line_gap": 1.30, "align": "left",
                   "number": "dim", "ratio": 1.50},
    "bias": {"x": 0, "y": 0, "inset": 0},
    "layout_cycle": ("left-stack", "split-right", "center-stack"),
}

VISUAL_LANGUAGES["magazine"] = {
    "motion": {"ease": "out5", "enter": (0.60, 1.10), "push": 0.001, "breathe_hz": 0.50},
    "motif": {"kind": "hairlines", "density": 0.85,
              "detail": {"header_rule": True, "column_rule": True, "pageno": True}},
    "transition": {"kind": "wash", "dur": 0.35},
    "typography": {"tracking": 2.0, "line_gap": 1.34, "align": "right",
                   "number": "serif", "ratio": 1.53},
    "bias": {"x": 60, "y": -20, "inset": 0},
    "layout_cycle": ("split-right", "left-stack", "center-stack"),
}

VISUAL_LANGUAGES["paper"] = {
    "motion": {"ease": "inOut3", "enter": (0.55, 1.00), "push": 0.03, "breathe_hz": 0.42},
    "motif": {"kind": "handdrawn", "density": 1.0,
              "detail": {"wobble": 3.0, "seal": True, "tape": True}},
    "transition": {"kind": "paper", "dur": 0.35},
    "typography": {"tracking": 1.0, "line_gap": 1.36, "align": "left",
                   "number": "cn", "ratio": 1.53},
    # 重心偏移（px）：paper 是"手账"气质——内容整体压在左下、右侧留出大片空白给框与胶带；
    # 这不是配色能表达的差异，是构图差异（剪影 Cx/Cy 直接反映它）。
    "bias": {"x": -150, "y": 80, "inset": 40},
    "layout_cycle": ("left-stack", "center-stack", "split-right"),
}

VISUAL_LANGUAGES["vivid"] = {
    "motion": {"ease": "outBack", "enter": (0.35, 0.70), "push": 0.10, "breathe_hz": 1.10},
    "motif": {"kind": "slabs", "density": 1.0,
              "detail": {"bar_h": 24, "slant": 18, "sticker": True}},
    "transition": {"kind": "wipe", "dur": 0.20},
    "typography": {"tracking": 1.0, "line_gap": 1.26, "align": "center",
                   "number": "block", "ratio": 2.97},
    "bias": {"x": 0, "y": 40, "inset": 0},
    "layout_cycle": ("center-stack", "left-stack", "split-right"),
}

VISUAL_LANGUAGES["mono"] = {
    "motion": {"ease": "inOut3", "enter": (0.50, 0.90), "push": 0.02, "breathe_hz": 0.35},
    "motif": {"kind": "minimal", "density": 0.55,
              "detail": {"rule_h": 2, "box_radius": 0}},
    "transition": {"kind": "cut", "dur": 0.0},
    "typography": {"tracking": 6.0, "line_gap": 1.40, "align": "center",
                   "number": "roman", "ratio": 3.15},
    "bias": {"x": 0, "y": 0, "inset": 0},
    "layout_cycle": ("center-stack", "center-stack", "center-stack"),
}

VISUAL_LANGUAGES["cinema"] = {
    "motion": {"ease": "inOut3", "enter": (0.70, 1.20), "push": 0.12, "breathe_hz": 0.30},
    "motif": {"kind": "letterbox", "density": 1.0,
              "detail": {"top": 40, "bottom": 44, "flares": True}},
    "transition": {"kind": "dip", "dur": 0.45},
    "typography": {"tracking": 3.0, "line_gap": 1.38, "align": "center",
                   "number": "timecode", "ratio": 1.62},
    "bias": {"x": 0, "y": 30, "inset": 0},
    "layout_cycle": ("center-stack", "left-stack", "split-right"),
}

VISUAL_LANGUAGES["lo-fi"] = {
    "motion": {"ease": "out3", "enter": (0.45, 0.85), "push": 0.05, "breathe_hz": 0.60},
    "motif": {"kind": "signal", "density": 0.90,
              "detail": {"bars": 12, "radius": 18, "note": True}},
    "transition": {"kind": "wash", "dur": 0.30},
    "typography": {"tracking": 1.0, "line_gap": 1.36, "align": "left",
                   "number": "dot", "ratio": 1.48},
    # lo-fi 是"桌面便签"气质：整体右移、内容偏上，底部留给信号条与时间戳。
    "bias": {"x": 60, "y": -70, "inset": 0},
    "layout_cycle": ("left-stack", "split-right", "center-stack"),
}

VISUAL_LANGUAGES["epic"] = {
    "motion": {"ease": "out5", "enter": (0.70, 1.20), "push": 0.14, "breathe_hz": 0.28},
    "motif": {"kind": "rays", "density": 1.0,
              "detail": {"rays": 18, "rule_gap": 26, "frame": True}},
    "transition": {"kind": "iris", "dur": 0.40},
    "typography": {"tracking": 4.0, "line_gap": 1.32, "align": "center",
                   "number": "gilt", "ratio": 3.18},
    "bias": {"x": 0, "y": -30, "inset": 0},
    "layout_cycle": ("center-stack", "center-stack", "center-stack"),
}

# legacy：v0.4 行为，没有视觉语言（scenes.py 走 _LEGACY_DRAWERS，不会读到这张表）
VISUAL_LANGUAGES[LEGACY_STYLE] = {
    "motion": {"ease": "out3", "enter": (0.45, 0.85), "push": 0.04, "breathe_hz": 0.85},
    "motif": {"kind": "brackets", "density": 1.0, "detail": {}},
    "transition": {"kind": "cut", "dur": 0.0},
    "typography": {"tracking": 0.0, "line_gap": 1.30, "align": "left",
                   "number": "dim", "ratio": 1.50},
    "layout_cycle": ("left-stack",),
}


def visual_language(style_id):
    """取风格的结构签名；未知 id 退到默认风格（不让拼写错误炸掉渲染）。"""
    sid = str(style_id or "").strip()
    got = VISUAL_LANGUAGES.get(sid)
    if got is not None:
        return got
    return VISUAL_LANGUAGES[DEFAULT_STYLE]


def visual_lint(style_id):
    """结构签名体检（--stage style 就拦，不等到渲完 300 帧）。返回问题列表，空 = 过。"""
    bad = []
    sid = str(style_id or "")
    if sid not in VISUAL_LANGUAGES:
        return ["风格 %r 没有视觉语言包" % sid]
    vl = VISUAL_LANGUAGES[sid]
    mo = vl.get("motion") or {}
    mf = vl.get("motif") or {}          # 注意：mo = motion，mf = motif，别混用
    tr = vl.get("transition") or {}
    ty = vl.get("typography") or {}
    if str(mf.get("kind")) not in VISUAL_MOTIFS:
        bad.append("motif.kind=%r 不在 %s 里" % (mf.get("kind"), " / ".join(VISUAL_MOTIFS)))
    if str(tr.get("kind")) not in VISUAL_TRANSITIONS:
        bad.append("transition.kind=%r 不在 %s 里"
                   % (tr.get("kind"), " / ".join(VISUAL_TRANSITIONS)))
    if not 0.0 <= float(tr.get("dur") or 0.0) <= 0.60:
        # 段界 veil 超过 0.6s 会吃掉 3–6s 节奏下限，也会把两段内容糊在一起
        bad.append("transition.dur=%r 必须在 0~0.60s（段界 veil 上限）" % tr.get("dur"))
    if str(ty.get("number")) not in NUMBER_STYLES:
        bad.append("typography.number=%r 不在 %s 里"
                   % (ty.get("number"), " / ".join(NUMBER_STYLES)))
    if not 1.0 <= float(ty.get("ratio") or 0.0) <= 3.6:
        bad.append("typography.ratio=%r 必须在 1.0~3.6（display:body 字号比）" % ty.get("ratio"))
    if not 0.0 <= float(ty.get("tracking") or 0.0) <= 8.0:
        bad.append("typography.tracking=%r 必须在 0~8px" % ty.get("tracking"))
    if not 1.15 <= float(ty.get("line_gap") or 0.0) <= 1.55:
        bad.append("typography.line_gap=%r 必须在 1.15~1.55" % ty.get("line_gap"))
    if not 0.0 <= float(mf.get("density") or 0.0) <= 1.0:
        bad.append("motif.density=%r 必须在 0~1" % mf.get("density"))
    if float(mf.get("density") or 0.0) <= 0.0 and str(mf.get("kind")) != "minimal":
        bad.append("motif.density=0 只允许用于 minimal（否则等于没有装饰语言）")
    enter = mo.get("enter") or (0.45, 0.85)
    if (not isinstance(enter, (list, tuple)) or len(enter) != 2
            or not 0.0 < float(enter[0]) <= float(enter[1]) <= 1.6):
        bad.append("motion.enter=%r 必须是 (起, 止) 且 0 < 起 <= 止 <= 1.6 秒" % (enter,))
    if not 0.0 <= float(mo.get("push") or 0.0) <= 0.16:
        # push > 0.16 时背景层放大后内容会被推出 SAFE（_bg_plate 只裁不缩）
        bad.append("motion.push=%r 必须在 0~0.16（超过会把内容推出安全区）" % mo.get("push"))
    return bad


def visual_lint_all():
    """全部内置风格的视觉语言体检，返回 {style_id: [问题]}（只有有问题的才在里面）。"""
    out = {}
    for sid in STYLE_IDS:
        bad = visual_lint(sid)
        if bad:
            out[sid] = bad
    return out


ALL_STYLE_IDS = (LEGACY_STYLE,) + STYLE_IDS


def style_ids(include_legacy=True):
    return list(ALL_STYLE_IDS) if include_legacy else list(STYLE_IDS)


def get(style_id):
    """取风格包；未知 id 报错并列出可用值（不静默回落，v0.4 的教训）。"""
    sid = str(style_id or "").strip()
    if sid not in STYLES:
        raise KeyError("未知风格 %r；可用：%s" % (style_id, " / ".join(ALL_STYLE_IDS)))
    return STYLES[sid]


def theme_of(style_id):
    """取风格对应的主题字典（元组颜色原样返回，给 PIL 直接下标）。"""
    return THEMES[get(style_id)["theme"]]


def is_legacy(style_id):
    return str(style_id or LEGACY_STYLE) == LEGACY_STYLE


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


def _kind_profile(script):
    """kind 分布画像：用于给风格打分（题材相近的片子 kind 结构也像）。"""
    kinds = [str(ln.get("kind") or "body") for ln in script.get("lines") or []]
    n = max(1, len(kinds))
    return {
        "hook": kinds.count("hook") / n,
        "body": kinds.count("body") / n,
        "outro": kinds.count("outro") / n,
        "items": sum(1 for ln in script.get("lines") or [] if ln.get("items")) / n,
        "avg_len": sum(len(str(ln.get("text") or "")) for ln in script.get("lines") or []) / n,
    }


def score_styles(script):
    """给所有风格打分，返回 [(style_id, score, hits)]，按分数降序（同分按 STYLE_IDS 顺序）。"""
    text = _text_of(script)
    prof = _kind_profile(script)
    fp = fingerprint(script)
    rows = []
    for sid in STYLE_IDS:
        th = THEMES[STYLES[sid]["theme"]]
        hits = []
        score = 0.0
        for kw in th["keywords"]:
            c = text.count(str(kw))
            if c:
                score += 3.0 * c
                hits.append(str(kw))
        if prof["items"] >= 0.5:
            score += 1.5 if sid in ("tech-deep", "magazine", "vivid") else 0.0
        if prof["avg_len"] >= 28:
            score += 1.0 if sid in ("cinema", "paper", "mono") else 0.0
        if prof["hook"] > 0 and prof["outro"] > 0:
            score += 0.5 if sid in ("tech-deep", "vivid", "epic") else 0.0
        # 指纹破平：必须把**风格 id** 混进哈希，否则这一项对所有行是同一个常数，
        # 等于没破平（v0.8 深审实测：旧写法在"AI 报告"上 tech-deep / magazine 同分 12.0，
        # 永远按 STYLE_IDS 顺序选 tech-deep）。幅度上限 0.0006 << 最小真实分差 0.5，
        # 只在精确同分时起作用，且不改变"改错别字不换风格"（指纹只吃标题）。
        score += (_digest("tiebreak", fp["style"], sid) % 7) * 0.0001
        rows.append((sid, round(score, 4), hits))
    rows.sort(key=lambda r: (-r[1], STYLE_IDS.index(r[0])))
    return rows


def pick_style(script):
    """内容驱动选风格。返回 (style_id, reason[list[str], 给用户看的理由])。"""
    rows = score_styles(script)
    best_id, best_score, best_hits = rows[0]
    fp = fingerprint(script)
    if best_score >= 1.0:
        if best_hits:
            reason = ["关键词命中：%s" % "、".join(dict.fromkeys(best_hits))[:80]]
        else:
            # 分数可以只由结构加分（items / 长句 / hook+outro）凑到 1.0 以上，
            # 这时旧文案会写"关键词命中："后面空一截 —— 等于向用户报告没发生的事。
            reason = ["无关键词命中，按题材结构加分选中（items / 长句 / hook+outro）"]
    else:
        pool = list(STYLE_IDS)
        best_id = pool[int(fp["tempo"], 16) % len(pool)]
        reason = ["无关键词命中，按内容指纹落桶（%s）" % fp["tempo"][:8]]
    return best_id, reason


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
            '  （预设见：python presetkit.py）')
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
