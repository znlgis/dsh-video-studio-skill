# -*- coding: utf-8 -*-
"""designkit.py — 设计 DSL（v1.0）：把"这一版长什么样"写成可校验、可采样的 JSON。

设计原则（与 stylekit 一致的纪律）
----------------------------------
1. 纯标准库 + stylekit（stylekit 也不 import PIL / filmkit），不碰渲染。
   contrast / theme_lint / visual_lint 的阈值只有一份，全部从 stylekit 复用，本文件不抄第二份。
2. 确定性：只用 sha1 派生，禁 random / time / hash()（str hash 每进程带随机盐）。
   同一份 brief 永远采样出同一组候选。
3. fail-closed：设计缺字段、枚举越界、对比度不达标 -> 立刻报错，**绝不静默回落默认值**
   （静默回落 = 所有片子又长得一样，这正是本次改造要根除的毛病）。

design.json 的形状（schema=1）
-----------------------------
    {
      "schema": 1,
      "id": "design-1a2b3c4d",          # ^[a-z0-9][a-z0-9._-]{1,63}$
      "label": "科技深蓝·四角括线",       # <= 40 字
      "pitch": "……",                    # 一句话立意（中文），<= 120 字
      "structure": {"segments": 6, "beats": [...], "hold_policy": "readable",
                    "target_sec": 45.0 | null},
      "visual": {
        "palette": {bg,bg2,surface,ink,muted,dim,accent,accent2,good,bad,rule},  # 11 色，[r,g,b]
        "family": "sans|serif|kai|round",
        "typography": {"tracking","line_gap","number","ratio","align"},
        "motif": {"kind","density","detail"},
        "transition": {"kind","dur"},
        "motion": {"ease","enter","push","breathe_hz"},
        "bias": {"x","y","inset"},
        "layout": "left-stack|center-stack|split-right",
        "hud": "chip-left|corner-line|none",
        "size": "1920x1080", "fps": 30            # 可选（schema 里可选）
      },
      "audio": {
        "voice": "zh-CN-YunxiNeural" 或
                 {"mode":"edge_id","id":"zh-CN-YunxiNeural",
                  "map":{kind: id},"jitter":0.2,"max_distinct":2,
                  "rate":"+0%","pitch":"+0Hz"},
        "music": {"mode":"params","style":"tech-deep","scale":"minor",
                  "bpm":104,"key":"Dm","mood":"steady","seed":7},
        "sfx": {"enabled":true,"density":"normal","gain_db":-6}   # 可选
      },
      "pacing": {"lead","tail","min_dur","hold","item_stagger"},
      "_unmatched_constraints": ["没认出来的要求"]   # 采样期附加信息（不参与指纹）
    }

配色不是"随便取色"：语义色由对比度求解得到
-----------------------------------------
实测（本轮）：纯 HSV 随机取色的 theme_lint 通过率 21/60 = 35%，不可用。
改成"色相族 + (s,v) 网格求解，取最接近设计意图且满足对比度的色"后：60/60 = 100%。
网格是 s,v ∈ {0,0.1,...,1.0} 的 11×11；可行解按 |Δs|+|Δv| 取最小（并列取先遇到者，确定性）。
7 个语义色的对比度门槛直接取 stylekit.LINT_LIMITS（与 theme_lint 同一份）。

注意：**设计里存的是求解后的 11 个颜色**，不是配色族名——这样 design.json 是自足的，
换机器/换版本也不会因为"rs 里没有那个族"而漂移；配色族只用于采样期的求解过程。

对外接口
--------
    validate_design(design) -> list[str]        # 空 = 过；fail-closed 用
    design_to_plan(design)  -> dict             # 直接喂给 scenes.draw_frame 的 plan
    sample_designs(brief, n=3, seed=None) -> list[dict]
    design_fingerprint(design) -> str
    design_distance(a, b)   -> dict             # 用于"伪多方案"门禁

brief.must_have / avoid 是真过滤（v1.1）
----------------------------------------
词表见 _CONSTRAINT_WORDS：配色 / 母题 / 转场 / 情绪 / 声线，中文按子串认、英文按整词认。
must_have 是 AND（候选不许违反任何一条），avoid 是否定（违反一条就出局）。
认不出的那一条**不静默丢**，而是写进每个候选的 ``_unmatched_constraints``；只有"整个
must_have 一条都认不出"才报错——那等于用户的要求被整体忽略，继续"成功"是最坏的结果。
约束把某一类的选择域清空（两条互斥、或 must_have 与 avoid 撞车）时立刻报错，并在
报错里点名是哪一条，绝不放宽后照常返回。

CLI
---
    python designkit.py --list-palettes
    python designkit.py --sample "AI 大模型的三年" --n 3
    python designkit.py --lint design.json
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import stylekit as ST

SCHEMA = 1

# ============================================================================
# 一、采样域：直接复用 stylekit（不抄第二份）
# ============================================================================

MOTIFS = ST.VISUAL_MOTIFS
TRANSITIONS = ST.VISUAL_TRANSITIONS
EASES = ("out3", "out5", "inOut3", "outBack")
SCALES = ("minor", "major")
MOODS = ("calm", "reflective", "hopeful", "steady", "driving", "tense", "uplifting")
NUMBERS = ST.NUMBER_STYLES
LAYOUTS = tuple(ST.LAYOUT_IDS)
HUDS = ("chip-left", "corner-line", "none")
FONT_FAMILIES = ("sans", "serif", "kai", "round")
ALIGNS = ("left", "center", "right")
SIZES = ("1920x1080", "1280x720", "960x540")
FPSES = (24, 25, 30, 50, 60)
HOLD_POLICIES = ("none", "readable", "long")
SFX_DENSITIES = ("none", "sparse", "normal", "rich")

# 配色族：只定"底色 + 主色相锚点"，其余语义色由 (s,v) 网格求解（见 _solve_palette）。
# 契约要求每个族至少有 bg / bg2 / surface / accent_hue / dark 五个键。
PALETTE_FAMILIES = {
    "deep-blue": dict(label="科技深蓝", bg=(7, 14, 26), bg2=(17, 33, 54),
                      surface=(26, 42, 64), accent_hue=24, dark=True),
    "near-black": dict(label="近黑金", bg=(8, 8, 10), bg2=(22, 22, 26),
                       surface=(32, 32, 38), accent_hue=48, dark=True),
    "cool-mint": dict(label="冷薄荷", bg=(12, 26, 24), bg2=(20, 38, 35),
                      surface=(30, 50, 46), accent_hue=168, dark=True),
    "sand": dict(label="暖砂金", bg=(28, 23, 17), bg2=(40, 33, 24),
                 surface=(52, 43, 32), accent_hue=32, dark=True),
    "slate": dict(label="石青灰", bg=(28, 32, 40), bg2=(42, 48, 60),
                  surface=(54, 62, 76), accent_hue=18, dark=True),
    "forest": dict(label="深林绿", bg=(10, 22, 16), bg2=(18, 32, 24),
                   surface=(26, 44, 34), accent_hue=96, dark=True),
    "warm-paper": dict(label="暖纸白", bg=(243, 236, 224), bg2=(227, 216, 196),
                       surface=(255, 252, 246), accent_hue=8, dark=False),
    "ink-paper": dict(label="墨纸白", bg=(246, 246, 244), bg2=(222, 222, 220),
                      surface=(255, 255, 255), accent_hue=0, dark=False),
}

# 11 个颜色字段（theme_lint 检的就是这些；顺序固定，便于审计）
PALETTE_KEYS = ("bg", "bg2", "surface", "ink", "muted", "dim",
                "accent", "accent2", "good", "bad", "rule")

# 设计意图 (s0, v0)：求解器在可行域里找离它最近的网格点。
# 暗色族要"亮而不刺眼"的文字、明色族要"深而不死黑"的文字；accent 两族都要求高饱和。
_ROLE_INTENT = {
    True: {"ink": (0.08, 0.94), "muted": (0.22, 0.72), "dim": (0.24, 0.56),
           "accent": (0.86, 0.94), "accent2": (0.58, 0.90)},
    False: {"ink": (0.18, 0.16), "muted": (0.16, 0.36), "dim": (0.12, 0.52),
            "accent": (0.86, 0.56), "accent2": (0.58, 0.50)},
}
_ROLE_HUE = {"ink": 0, "muted": 0, "dim": 0, "accent": 0, "accent2": 150}
_GOOD_HUE = 135.0
_BAD_HUE = 5.0

# 情绪 -> 曲风（P0 桥：scorekit 仍是 9 套编曲参数；P2 会换成参数化编排器）
_MUSIC_BY_MOOD = {
    "calm": "lo-fi", "reflective": "magazine", "hopeful": "paper",
    "steady": "tech-deep", "driving": "vivid", "tense": "cinema",
    "uplifting": "epic",
}
_MOOD_BPM = {
    "calm": 78, "reflective": 86, "hopeful": 92, "steady": 104,
    "driving": 124, "tense": 98, "uplifting": 118,
}
_MOOD_WORD = {
    "calm": "克制留白", "reflective": "回望与沉淀", "hopeful": "向上的暖意",
    "steady": "稳稳推进", "driving": "强驱动节奏", "tense": "绷紧的悬念",
    "uplifting": "昂扬收束",
}
_MOTIF_WORD = {
    "brackets": "四角括线", "hairlines": "细线页眉", "handdrawn": "手绘内框",
    "slabs": "粗色块", "minimal": "极简留白", "letterbox": "电影遮幅",
    "signal": "信号条", "rays": "放射线",
}
_TRANS_WORD = {
    "dip": "叠化", "wash": "淡入", "paper": "纸卷",
    "wipe": "擦入", "cut": "硬切", "iris": "光圈",
}

# 声线池：全部是可被 static 校验的 zh-CN-XxxNeural（本模块不 import voicekit——
# 那会拉起 edge_tts。格式只做正则，不做"这个 id 是否真的存在"的联网校验）。
#
# v1.0 / P4：**只从"有响度补偿数据的音色"里采样**。
# 为什么：voicekit 的响度补偿是一张按 voice id 的数据表（VOICE_LOUDNESS / assets/voice_loudness.json）；
# 采样器如果产出表里没有的音色，那行的 gain_db 会回落 0 —— 等于"这个候选的旁白电平没校准"。
# 实测（改造前）：5 个题材 × 3 候选共产出 7 个音色，其中 **4 个没有补偿数据**。
# 两处各存一份清单必然漂移，所以这里直接读共享 JSON（不 import voicekit）。
_VOICE_LOUDNESS_PATH = os.path.normpath(
    os.path.join(HERE, os.pardir, "assets", "voice_loudness.json"))


def _loudness_voices():
    """读共享响度表里的音色（读不到就返回空，由调用方兜底到内置池）。"""
    try:
        with open(_VOICE_LOUDNESS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        voices = tuple(sorted(str(k) for k in (data.get("voices") or {})))
        return tuple(v for v in voices if _VOICE_RE.match(v))
    except (OSError, json.JSONDecodeError):
        return ()


_FALLBACK_VOICES = (
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural", "zh-CN-YunyangNeural",
)
# 只认三地中文（CN/TW/HK）：实测 edge-tts 免费端点的 zh 音色就这三类，
# 而响度补偿表里确实有 zh-TW-HsiaoChenNeural（软糯女声）——只写 zh-CN 会把它判非法。
_VOICE_RE = re.compile(r"^zh-(?:CN|TW|HK)-[A-Z][A-Za-z]*Neural$")
VOICES = _loudness_voices() or _FALLBACK_VOICES
VOICE_KINDS = ("hook", "body", "quote", "outro")

# 骨架几何按母题给（母题决定"框长什么样"，卡片只是它的容器）
_CARD_BY_MOTIF = {
    "brackets": dict(radius=14, fill_a=16, outline_a=28, pad=34, h=96, gap=118),
    "hairlines": dict(radius=8, fill_a=26, outline_a=40, pad=30, h=92, gap=112),
    "handdrawn": dict(radius=4, fill_a=30, outline_a=52, pad=32, h=94, gap=116),
    "slabs": dict(radius=22, fill_a=30, outline_a=56, pad=32, h=100, gap=122),
    "minimal": dict(radius=0, fill_a=0, outline_a=70, pad=28, h=90, gap=108),
    "letterbox": dict(radius=10, fill_a=20, outline_a=32, pad=36, h=88, gap=124),
    "signal": dict(radius=12, fill_a=22, outline_a=36, pad=30, h=92, gap=114),
    "rays": dict(radius=18, fill_a=18, outline_a=46, pad=34, h=96, gap=126),
}
_TEXTURE_BY_MOTIF = {
    "brackets": "dots", "hairlines": "paper", "handdrawn": "paper",
    "slabs": "grid", "minimal": "none", "letterbox": "none",
    "signal": "scanlines", "rays": "none",
}

# 采样用的离散档位（取整避免浮点尾差，JSON 才好逐字节比对）
_DENSITIES = (0.55, 0.70, 0.85, 1.00)
_PUSHES = (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.14)
_BREATHES = (0.30, 0.40, 0.50, 0.60, 0.75, 0.90, 1.05)
_ENTERS = ((0.35, 0.75), (0.40, 0.90), (0.45, 0.85), (0.50, 1.00),
           (0.55, 1.00), (0.60, 1.10), (0.70, 1.20))
_XS = (-150, -90, -60, -30, 0, 30, 60, 90, 150)
_YS = (-90, -60, -30, 0, 30, 60, 90)
_INSETS = (0, 20, 40, 60)

# 校验用的键白名单（schema 的 additionalProperties=false 的同义实现）
# _unmatched_constraints 是采样期附带的"这条要求没生效"的记录（见 _resolve_constraints）：
# 不参与指纹、不参与渲染，但必须被契约接纳，否则采样器吐出的设计自己就过不了校验。
_TOP_KEYS = ("schema", "id", "label", "pitch", "structure", "visual", "audio", "pacing",
             "_unmatched_constraints")
_STRUCT_KEYS = ("segments", "beats", "hold_policy", "target_sec")
_VISUAL_KEYS = ("palette", "family", "typography", "motif", "transition", "motion",
                "bias", "layout", "hud", "size", "fps")
_TYPO_KEYS = ("tracking", "line_gap", "number", "ratio", "align")
_MOTIF_KEYS = ("kind", "density", "detail")
_TRANS_KEYS = ("kind", "dur")
_MOTION_KEYS = ("ease", "enter", "push", "breathe_hz")
_BIAS_KEYS = ("x", "y", "inset")
_AUDIO_KEYS = ("voice", "music", "sfx")
_VOICE_KEYS = ("mode", "id", "rate", "pitch", "map", "jitter", "max_distinct")
_MUSIC_KEYS = ("mode", "arranger", "style", "scale", "bpm", "key", "mood", "seed",
               "gain_db", "duck_db", "sections", "file")
_SFX_KEYS = ("enabled", "density", "gain_db", "cues")
_PACING_KEYS = ("lead", "tail", "min_dur", "hold", "item_stagger")
_BRIEF_KEYS = ("topic", "duration_target", "must_have", "avoid")

_MAX_RESAMPLE = 50


# ============================================================================
# 二、小工具：确定性哈希 / 颜色 / 类型
# ============================================================================


def _digest(*parts):
    """sha1 派生的稳定 48 位整数（禁 hash()：str hash 每次进程带随机盐）。"""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.sha1(raw).hexdigest()[:12], 16)


def _canonical(obj):
    """规范 JSON：排序键 + 紧凑分隔符；同一对象永远同一串（指纹与采样共用）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _hsv(hue_deg, s, v):
    """HSV -> RGB 元组；色相用角度（0~360），s/v 用 0~1。"""
    r, g, b = colorsys.hsv_to_rgb((float(hue_deg) % 360.0) / 360.0,
                                  _clamp(float(s), 0.0, 1.0),
                                  _clamp(float(v), 0.0, 1.0))
    return (int(round(r * 255.0)), int(round(g * 255.0)), int(round(b * 255.0)))


def _hex(rgb):
    """(r,g,b) -> "#rrggbb"（只用于展示/距离描述）。"""
    try:
        t = ST._color_of(rgb, "display")
    except Exception:                       # noqa: BLE001 - 展示用，坏值返回空串
        return ""
    return "#%02x%02x%02x" % t


def _slug(text):
    s = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-")
    return s or "design"


# ============================================================================
# 三、配色求解：色相族 + (s,v) 网格（本轮实测唯一 100% 通过的做法）
# ============================================================================


def _solve_role(hue, s0, v0, need, refs):
    """在 s,v ∈ {0,0.1,...,1.0} 里找"对 refs 都 >= need"且最接近 (s0,v0) 的颜色。

    为什么是网格而不是二分：对比度对 s、v 并非单维单调（v 到 0/1 两端都能拉高对比），
    单维二分只能找到一个可行点，找不到**最贴近设计意图**的那个。11×11 网格足够密，
    且并列时取先遇到者（s 升序、v 升序），结果确定。
    """
    best, best_d = None, None
    for si in range(11):
        s = si / 10.0
        for vi in range(11):
            v = vi / 10.0
            c = _hsv(hue, s, v)
            if min(ST.contrast(c, r) for r in refs) < need:
                continue
            d = abs(s - s0) + abs(v - v0)
            if best is None or d < best_d - 1e-12:
                best, best_d = c, d
    return best


def _solve_palette(family, hue_shift=0.0, target_shift=0.0):
    """一个配色族 -> 11 个颜色（语义色全部求解；无解直接报错，不回落）。

    hue_shift / target_shift 是采样器给同族不同候选留的小扰动（确定性，幅度很小），
    让"同族不同设计"也有色相差异；扰动不会改变可行性（网格里始终有 v=0/v=1 的端点）。
    """
    if family not in PALETTE_FAMILIES:
        raise ValueError("未知配色族 %r；可用：%s"
                         % (family, " / ".join(PALETTE_FAMILIES)))
    fam = PALETTE_FAMILIES[family]
    bg, bg2, surface = fam["bg"], fam["bg2"], fam["surface"]
    dark = bool(fam["dark"])
    refs = (bg, bg2)
    colors = {"bg": bg, "bg2": bg2, "surface": surface}
    lost = []
    for role in ("ink", "muted", "dim", "accent", "accent2"):
        s0, v0 = _ROLE_INTENT[dark][role]
        c = _solve_role(fam["accent_hue"] + _ROLE_HUE[role] + hue_shift,
                        _clamp(s0 + target_shift, 0.0, 1.0),
                        _clamp(v0 - target_shift, 0.0, 1.0),
                        ST.LINT_LIMITS[role], refs)
        if c is None:
            lost.append(role)
        else:
            colors[role] = c
    for role, hue in (("good", _GOOD_HUE), ("bad", _BAD_HUE)):
        c = _solve_role(hue, 0.62, 0.66, ST.LINT_LIMITS[role], (surface,))
        if c is None:
            lost.append(role)
        else:
            colors[role] = c
    if lost:
        raise ValueError("配色族 %s 下这些语义色无解（对比度不可达）：%s"
                         % (family, "、".join(lost)))
    colors["rule"] = (255, 255, 255) if dark else (16, 16, 16)
    return {k: colors[k] for k in PALETTE_KEYS}


# ============================================================================
# 四、design -> theme / language（35 字段主题；五组视觉签名）
# ============================================================================


def _font_scale(ratio):
    """字号盘：display:body = ratio（visual_lint 查的就是这个比值），其余槽位固定。

    基准 body=34 是本模块的画布假设；ratio 是设计变量，真正决定大标题有多大。
    """
    body = 34
    return {
        "display": (max(20, int(round(body * float(ratio)))), "bold"),
        "heading": (64, "bold"),
        "sub": (38, "light"),
        "body": (body, "reg"),
        "kicker": (28, "bold"),
        "chip": (27, "reg"),
        "brand": (23, "reg"),
    }


def _card_for(motif_kind):
    got = _CARD_BY_MOTIF.get(str(motif_kind))
    if got is None:
        raise ValueError("母题 %r 没有卡片几何（designkit 的 _CARD_BY_MOTIF 要跟着 "
                         "stylekit.VISUAL_MOTIFS 更新）" % (motif_kind,))
    return dict(got)


def _safe_bias(bias):
    # 主题的 safe_bias 是题目安全区的整体偏移；把它挂到设计重心上，避免两处各写一份。
    return int(_clamp(round(float(bias["x"]) * 0.12), -28, 28))


def _theme_id(design_id):
    # 设计 id 已经是 "design-xxxxxxxx"；这里只做字符规整，不再叠前缀。
    return _slug(design_id)


def _theme_of_design(design):
    """把 design.visual 展开成 35 字段主题（颜色是 (r,g,b) 元组）。

    直接调 stylekit._theme：字段集合/默认口径只有一份，本文件不手搓 35 个键。
    随后校验"字段集合与 stylekit 完全一致"，防止 stylekit 加字段后这里悄悄少一个。
    """
    vis = design["visual"]
    pal = vis["palette"]
    colors = {k: ST._color_of(pal[k], "visual.palette." + k) for k in PALETTE_KEYS}
    dark = ST.is_dark({"bg": colors["bg"]})
    if dark:
        material = dict(vignette=0.42, vignette_tint=(0, 0, 0),
                        grain=5.0, grain_freq=0.31,
                        texture=_TEXTURE_BY_MOTIF[vis["motif"]["kind"]],
                        texture_alpha=(0.0 if _TEXTURE_BY_MOTIF[vis["motif"]["kind"]] == "none"
                                       else 0.045))
    else:
        material = dict(vignette=0.16,
                        vignette_tint=tuple(int(round(c * 0.86)) for c in colors["bg2"]),
                        grain=3.0, grain_freq=0.27,
                        texture=_TEXTURE_BY_MOTIF[vis["motif"]["kind"]],
                        texture_alpha=(0.0 if _TEXTURE_BY_MOTIF[vis["motif"]["kind"]] == "none"
                                       else 0.030))
    qa = dict(luma_max=(0.030 if ST.luminance(colors["bg"]) < 0.02 else 0.045),
              flicker_thresh=6.0, dark=bool(dark))
    mo = vis["motion"]
    th = ST._theme(
        _theme_id(design["id"]),
        str(design["label"]),
        (),
        colors,
        material,
        dict(family=vis["family"], scale=_font_scale(vis["typography"]["ratio"]),
             line_gap=float(vis["typography"]["line_gap"]), tracking=0.0),
        dict(layout=vis["layout"], card=_card_for(vis["motif"]["kind"]),
             safe_bias=_safe_bias(vis["bias"])),
        dict(ease=mo["ease"],
             entrance_dx=0,
             entrance_dy=int(round(14.0 + float(mo["push"]) * 100.0)),
             push=float(mo["push"]),
             item_stagger=float(design["pacing"]["item_stagger"]),
             transition=vis["transition"]["kind"]),
        dict(style=vis["hud"]),
        qa,
    )
    ref = set(ST.THEMES[ST.DEFAULT_STYLE].keys())
    if set(th.keys()) != ref:
        raise ValueError("主题字段集合与 stylekit 不一致：缺 %s / 多 %s"
                         % (sorted(ref - set(th.keys())), sorted(set(th.keys()) - ref)))
    return th


def _language_of(design):
    """design -> 五组视觉签名（motion / motif / transition / typography / bias）。

    与 stylekit.VISUAL_LANGUAGES 同形；scenes.py 直接读这五组（layout_cycle 没有消费者）。
    所有字段都直接下标：缺字段会抛 KeyError，由 validate_design 转成一条问题，
    绝不在这里塞默认值。
    """
    vis = design["visual"]
    ty = vis["typography"]
    mo = vis["motion"]
    return {
        "motion": {"ease": mo["ease"],
                   "enter": (float(mo["enter"][0]), float(mo["enter"][1])),
                   "push": float(mo["push"]),
                   "breathe_hz": float(mo["breathe_hz"])},
        "motif": {"kind": vis["motif"]["kind"],
                  "density": float(vis["motif"]["density"]),
                  "detail": dict(vis["motif"]["detail"])},
        "transition": {"kind": vis["transition"]["kind"],
                       "dur": float(vis["transition"]["dur"])},
        "typography": {"tracking": float(ty["tracking"]),
                       "line_gap": float(ty["line_gap"]),
                       "align": ty["align"],
                       "number": ty["number"],
                       "ratio": float(ty["ratio"])},
        "bias": {"x": int(vis["bias"]["x"]),
                 "y": int(vis["bias"]["y"]),
                 "inset": int(vis["bias"]["inset"])},
    }


_VISUAL_PROBE_ID = "__designkit_probe__"


def _visual_lint_language(lang):
    """用 stylekit.visual_lint 体检一份临时语言包（阈值同源，本文件不重写边界）。

    做法：把语言包临时注册进 stylekit.VISUAL_LANGUAGES（同一个 dict 对象），跑完恢复。
    这样 transition.dur / motif.density / push / ratio / tracking / line_gap / enter
    的门槛全部来自 stylekit，不存在"两边各写一份、改一边忘另一边"。
    """
    old = ST.VISUAL_LANGUAGES.get(_VISUAL_PROBE_ID)
    ST.VISUAL_LANGUAGES[_VISUAL_PROBE_ID] = lang
    try:
        return list(ST.visual_lint(_VISUAL_PROBE_ID))
    finally:
        if old is None:
            ST.VISUAL_LANGUAGES.pop(_VISUAL_PROBE_ID, None)
        else:
            ST.VISUAL_LANGUAGES[_VISUAL_PROBE_ID] = old


# ============================================================================
# 五、校验（fail-closed：缺字段/越界/对比度不达标全部变成问题）
# ============================================================================


def _need(obj, keys, path, bad):
    for k in keys:
        if k not in obj:
            bad.append("%s 缺必填字段 %r（fail-closed：不会取默认值）" % (path, k))


def _only(obj, allowed, path, bad):
    for k in sorted(obj, key=str):
        if k not in allowed:
            bad.append("%s 有未知字段 %r（设计契约不认这个键）" % (path, k))


def _num(v, path, bad, lo=None, hi=None):
    if not _is_num(v):
        bad.append("%s 必须是数字，收到 %r" % (path, v))
        return False
    if lo is not None and float(v) < lo:
        bad.append("%s=%r 不能小于 %s" % (path, v, lo))
        return False
    if hi is not None and float(v) > hi:
        bad.append("%s=%r 不能大于 %s" % (path, v, hi))
        return False
    return True


def _int_of(v, path, bad, lo=None, hi=None):
    if not _is_int(v):
        bad.append("%s 必须是整数，收到 %r" % (path, v))
        return False
    if lo is not None and v < lo:
        bad.append("%s=%r 不能小于 %s" % (path, v, lo))
        return False
    if hi is not None and v > hi:
        bad.append("%s=%r 不能大于 %s" % (path, v, hi))
        return False
    return True


def _enum_of(v, allowed, path, bad):
    if v not in allowed:
        bad.append("%s=%r 不在 %s 里" % (path, v, " / ".join(allowed)))
        return False
    return True


def _validate_voice(vo, bad):
    """voice 允许两种形态：裸 edge id 字符串，或 {mode,id,map,jitter,max_distinct}。"""
    if isinstance(vo, str):
        if not _VOICE_RE.match(vo):
            bad.append("audio.voice=%r 不是 edge voice id（必须匹配 zh-CN-XxxNeural）" % (vo,))
        return
    if not isinstance(vo, dict):
        bad.append("audio.voice 必须是字符串或 dict，收到 %r" % (type(vo).__name__,))
        return
    _only(vo, _VOICE_KEYS, "audio.voice", bad)
    if "mode" not in vo:
        bad.append("audio.voice 缺必填字段 'mode'（fail-closed：不会猜 edge_id/wav）")
        return
    mode = vo.get("mode")
    if mode not in ("edge_id", "wav"):
        bad.append("audio.voice.mode=%r 必须是 edge_id / wav" % (mode,))
        return
    vid = vo.get("id")
    if not isinstance(vid, str) or not vid.strip():
        bad.append("audio.voice.id 必须是非空字符串")
    elif mode == "edge_id" and not _VOICE_RE.match(vid):
        bad.append("audio.voice.id=%r 不是 edge voice id（必须匹配 zh-CN-XxxNeural）" % (vid,))
    if mode == "edge_id":
        _need(vo, ("map", "jitter", "max_distinct"), "audio.voice", bad)
    if "map" in vo:
        mp = vo["map"]
        if not isinstance(mp, dict):
            bad.append("audio.voice.map 必须是 dict")
        else:
            for k in sorted(mp, key=str):
                v = mp[k]
                if isinstance(v, str):
                    if not _VOICE_RE.match(v):
                        bad.append("audio.voice.map[%r]=%r 不是 edge voice id" % (k, v))
                elif isinstance(v, dict):
                    _only(v, ("mode", "id"), "audio.voice.map.%s" % k, bad)
                    if v.get("mode") not in ("edge_id", "wav"):
                        bad.append("audio.voice.map[%r].mode 必须是 edge_id / wav" % (k,))
                    if not isinstance(v.get("id"), str) or not v.get("id"):
                        bad.append("audio.voice.map[%r].id 必须是非空字符串" % (k,))
                    elif v.get("mode") == "edge_id" and not _VOICE_RE.match(str(v["id"])):
                        bad.append("audio.voice.map[%r].id=%r 不是 edge voice id"
                                   % (k, v.get("id")))
                else:
                    bad.append("audio.voice.map[%r] 必须是 voice id 或 {mode,id}" % (k,))
    if "jitter" in vo:
        _num(vo["jitter"], "audio.voice.jitter", bad, 0.0, 1.0)
    if "max_distinct" in vo:
        _int_of(vo["max_distinct"], "audio.voice.max_distinct", bad, 1, 5)
    for k in ("rate", "pitch"):
        if k in vo and not isinstance(vo[k], str):
            bad.append("audio.voice.%s 必须是字符串（如 \"+0%%\" / \"+0Hz\"）" % k)


def _validate_music(mu, bad):
    if not isinstance(mu, dict):
        bad.append("audio.music 必须是 dict")
        return
    _only(mu, _MUSIC_KEYS, "audio.music", bad)
    if "mode" not in mu:
        bad.append("audio.music 缺必填字段 'mode'（fail-closed：不会猜 params/file/off）")
        return
    mode = mu.get("mode")
    if mode not in ("params", "file", "off"):
        bad.append("audio.music.mode=%r 必须是 params / file / off" % (mode,))
    _need(mu, ("style", "scale", "bpm", "key", "mood", "seed"), "audio.music", bad)
    if "style" in mu:
        _enum_of(mu["style"], tuple(ST.STYLE_IDS), "audio.music.style", bad)
    if "scale" in mu:
        _enum_of(mu["scale"], SCALES, "audio.music.scale", bad)
    if "mood" in mu:
        _enum_of(mu["mood"], MOODS, "audio.music.mood", bad)
    if "bpm" in mu:
        _num(mu["bpm"], "audio.music.bpm", bad, 40.0, 220.0)
    if "key" in mu and isinstance(mu["key"], str):
        scale = mu.get("scale")
        if scale in SCALES:
            pool = ST.MINOR_KEYS if scale == "minor" else ST.MAJOR_KEYS
            if mu["key"] not in pool:
                bad.append("audio.music.key=%r 与 scale=%r 不匹配（%s 调池：%s）"
                           % (mu["key"], scale, scale, " / ".join(pool)))
    elif "key" in mu:
        bad.append("audio.music.key 必须是字符串")
    if "seed" in mu:
        _int_of(mu["seed"], "audio.music.seed", bad)
    if mode == "file" and not (isinstance(mu.get("file"), str) and mu.get("file")):
        bad.append("audio.music.mode=file 时必须给非空 audio.music.file")
    if "gain_db" in mu:
        _num(mu["gain_db"], "audio.music.gain_db", bad, -60.0, 12.0)
    if "duck_db" in mu:
        _num(mu["duck_db"], "audio.music.duck_db", bad, 0.0, 24.0)
    if "arranger" in mu and not isinstance(mu["arranger"], str):
        bad.append("audio.music.arranger 必须是字符串")
    if "sections" in mu:
        sec = mu["sections"]
        if not isinstance(sec, (list, tuple)) or any(not isinstance(x, dict) for x in sec):
            bad.append("audio.music.sections 必须是对象数组")


def _validate_sfx(sfx, bad):
    if not isinstance(sfx, dict):
        bad.append("audio.sfx 必须是 dict")
        return
    _only(sfx, _SFX_KEYS, "audio.sfx", bad)
    if "enabled" in sfx and not isinstance(sfx["enabled"], bool):
        bad.append("audio.sfx.enabled 必须是布尔")
    if "density" in sfx:
        _enum_of(sfx["density"], SFX_DENSITIES, "audio.sfx.density", bad)
    if "gain_db" in sfx:
        _num(sfx["gain_db"], "audio.sfx.gain_db", bad, -40.0, 12.0)
    if "cues" in sfx:
        cues = sfx["cues"]
        if not isinstance(cues, (list, tuple)) or any(not isinstance(x, dict) for x in cues):
            bad.append("audio.sfx.cues 必须是对象数组")


def validate_design(design):
    """fail-closed 校验。返回问题列表（空 = 通过）。

    覆盖：
      1. schema 版本 / id / label / pitch / 四个必填块；
      2. 枚举：motif.kind / transition.kind / typography.number / ease / scale / mood
         （以及 family / align / layout / hud / size / fps / 声线格式）；
      3. 数值边界：**直接调 stylekit.visual_lint**（临时语言包），与阈值同源；
      4. 配色：把 design 展开成 35 字段主题后调 stylekit.theme_lint（不自己写对比度）；
      5. 任何"缺了就取默认值"的路径都在这里变成一条问题。
    """
    bad = []
    if not isinstance(design, dict):
        return ["design 不是 dict：%r" % (design,)]

    _need(design, ("schema", "id", "label", "pitch", "structure", "visual", "audio", "pacing"),
          "design", bad)
    _only(design, _TOP_KEYS, "design", bad)

    um = design.get("_unmatched_constraints")
    if um is not None and (not isinstance(um, (list, tuple))
                           or any(not isinstance(x, str) for x in um)):
        bad.append("_unmatched_constraints 必须是字符串数组（认不出的 brief 要求）")

    sch = design.get("schema")
    if not _is_int(sch):
        bad.append("schema 必须是整数 %d，收到 %r" % (SCHEMA, sch))
    elif sch != SCHEMA:
        bad.append("schema 版本不支持：%r（本模块只认 %d）" % (sch, SCHEMA))

    did = design.get("id")
    if not isinstance(did, str) or not re.match(r"^[a-z0-9][a-z0-9._-]{1,63}$", did):
        bad.append("id=%r 必须匹配 ^[a-z0-9][a-z0-9._-]{1,63}$" % (did,))
    label = design.get("label")
    if not isinstance(label, str) or not label.strip():
        bad.append("label 必须是非空字符串")
    elif len(label) > 40:
        bad.append("label 超过 40 字（当前 %d）" % len(label))
    pitch = design.get("pitch")
    if not isinstance(pitch, str) or not pitch.strip():
        bad.append("pitch 必须是非空字符串（一句话立意）")
    elif len(pitch) > 120:
        bad.append("pitch 超过 120 字（当前 %d）" % len(pitch))

    # ---- structure ----
    st = design.get("structure")
    if not isinstance(st, dict):
        bad.append("structure 必须是 dict")
    else:
        _only(st, _STRUCT_KEYS, "structure", bad)
        _need(st, ("segments",), "structure", bad)
        if "segments" in st:
            _int_of(st["segments"], "structure.segments", bad, 1, 40)
        if "beats" in st:
            beats = st["beats"]
            if not isinstance(beats, (list, tuple)) or any(not isinstance(x, str) for x in beats):
                bad.append("structure.beats 必须是字符串数组")
        if "hold_policy" in st:
            _enum_of(st["hold_policy"], HOLD_POLICIES, "structure.hold_policy", bad)
        if "target_sec" in st and st["target_sec"] is not None:
            _num(st["target_sec"], "structure.target_sec", bad, 3.0, 1800.0)

    # ---- visual ----
    vis = design.get("visual")
    if not isinstance(vis, dict):
        bad.append("visual 必须是 dict")
        vis = {}
    else:
        _only(vis, _VISUAL_KEYS, "visual", bad)
        _need(vis, ("palette", "family", "typography", "motif", "transition", "motion",
                    "bias", "layout", "hud"), "visual", bad)

    pal = vis.get("palette")
    if not isinstance(pal, dict):
        bad.append("visual.palette 必须是 dict（11 色）")
    else:
        _only(pal, PALETTE_KEYS, "visual.palette", bad)
        _need(pal, PALETTE_KEYS, "visual.palette", bad)
        for k in PALETTE_KEYS:
            if k not in pal:
                continue
            try:
                ST._color_of(pal[k], "visual.palette." + k)
            except Exception as e:                       # noqa: BLE001
                bad.append("visual.palette.%s 不是颜色（[r,g,b] 或 \"#rrggbb\"）：%s"
                           % (k, e))

    if "family" in vis:
        _enum_of(vis["family"], FONT_FAMILIES, "visual.family", bad)

    ty = vis.get("typography")
    if not isinstance(ty, dict):
        bad.append("visual.typography 必须是 dict")
    else:
        _only(ty, _TYPO_KEYS, "visual.typography", bad)
        _need(ty, ("tracking", "line_gap", "number", "ratio", "align"),
              "visual.typography", bad)
        for k in ("tracking", "line_gap", "ratio"):
            if k in ty:
                _num(ty[k], "visual.typography." + k, bad)
        if "number" in ty:
            _enum_of(ty["number"], NUMBERS, "visual.typography.number", bad)
        if "align" in ty:
            _enum_of(ty["align"], ALIGNS, "visual.typography.align", bad)

    mo = vis.get("motif")
    if not isinstance(mo, dict):
        bad.append("visual.motif 必须是 dict")
    else:
        _only(mo, _MOTIF_KEYS, "visual.motif", bad)
        _need(mo, _MOTIF_KEYS, "visual.motif", bad)
        if "kind" in mo:
            _enum_of(mo["kind"], MOTIFS, "visual.motif.kind", bad)
        if "density" in mo:
            _num(mo["density"], "visual.motif.density", bad, 0.0, 1.0)
        if "detail" in mo and not isinstance(mo["detail"], dict):
            bad.append("visual.motif.detail 必须是 dict（可以是空 {}）")

    tr = vis.get("transition")
    if not isinstance(tr, dict):
        bad.append("visual.transition 必须是 dict")
    else:
        _only(tr, _TRANS_KEYS, "visual.transition", bad)
        _need(tr, _TRANS_KEYS, "visual.transition", bad)
        if "kind" in tr:
            _enum_of(tr["kind"], TRANSITIONS, "visual.transition.kind", bad)
        if "dur" in tr:
            _num(tr["dur"], "visual.transition.dur", bad, 0.0, 0.60)

    mo2 = vis.get("motion")
    if not isinstance(mo2, dict):
        bad.append("visual.motion 必须是 dict")
    else:
        _only(mo2, _MOTION_KEYS, "visual.motion", bad)
        _need(mo2, _MOTION_KEYS, "visual.motion", bad)
        if "ease" in mo2:
            _enum_of(mo2["ease"], EASES, "visual.motion.ease", bad)
        for k in ("push", "breathe_hz"):
            if k in mo2:
                _num(mo2[k], "visual.motion." + k, bad)
        if "enter" in mo2:
            ent = mo2["enter"]
            if (not isinstance(ent, (list, tuple)) or len(ent) != 2
                    or any(not _is_num(x) for x in ent)):
                bad.append("visual.motion.enter 必须是两个数字 [起, 止]")

    bias = vis.get("bias")
    if not isinstance(bias, dict):
        bad.append("visual.bias 必须是 dict")
    else:
        _only(bias, _BIAS_KEYS, "visual.bias", bad)
        _need(bias, _BIAS_KEYS, "visual.bias", bad)
        for k in ("x", "y"):
            if k in bias:
                _int_of(bias[k], "visual.bias." + k, bad, -240, 240)
        if "inset" in bias:
            _int_of(bias["inset"], "visual.bias.inset", bad, 0, 120)

    if "layout" in vis:
        _enum_of(vis["layout"], LAYOUTS, "visual.layout", bad)
    if "hud" in vis:
        _enum_of(vis["hud"], HUDS, "visual.hud", bad)
    if "size" in vis:
        _enum_of(vis["size"], SIZES, "visual.size", bad)
    if "fps" in vis:
        if not _is_int(vis["fps"]):
            bad.append("visual.fps 必须是整数")
        elif vis["fps"] not in FPSES:
            bad.append("visual.fps=%r 不在 %s 里" % (vis["fps"], FPSES))

    # ---- audio ----
    au = design.get("audio")
    if not isinstance(au, dict):
        bad.append("audio 必须是 dict")
    else:
        _only(au, _AUDIO_KEYS, "audio", bad)
        _need(au, ("voice", "music"), "audio", bad)
        if "voice" in au:
            _validate_voice(au["voice"], bad)
        if "music" in au:
            _validate_music(au["music"], bad)
        if "sfx" in au:
            _validate_sfx(au["sfx"], bad)

    # ---- pacing ----
    pa = design.get("pacing")
    if not isinstance(pa, dict):
        bad.append("pacing 必须是 dict")
    else:
        _only(pa, _PACING_KEYS, "pacing", bad)
        _need(pa, _PACING_KEYS, "pacing", bad)
        for k, lo, hi in (("lead", 0.0, 3.0), ("tail", 0.0, 3.0),
                          ("min_dur", 1.0, 12.0), ("hold", 0.0, 5.0),
                          ("item_stagger", 0.05, 1.2)):
            if k in pa:
                _num(pa[k], "pacing." + k, bad, lo, hi)

    # ---- 同源数值门禁：stylekit.visual_lint（临时语言包；门槛只有一份） ----
    try:
        lang = _language_of(design)
    except Exception as e:                                # noqa: BLE001
        bad.append("无法构造视觉语言包（visual_lint 没跑）：%s" % e)
    else:
        for p in _visual_lint_language(lang):
            bad.append("visual: " + p)

    # ---- 同源配色门禁：stylekit.theme_lint（不自己写对比度） ----
    try:
        th = _theme_of_design(design)
        for p in ST.theme_lint(th):
            bad.append("theme: " + p)
    except Exception as e:                                # noqa: BLE001
        bad.append("主题展开失败（theme_lint 没跑）：%s" % e)

    # 去重但保持首次出现的顺序（同一条缺字段会被多个检查提到）
    seen, out = set(), []
    for s in bad:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ============================================================================
# 六、design -> plan（scenes.draw_frame 直接可吃）
# ============================================================================


def _fingerprint_part(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def design_to_plan(design):
    """设计 -> 风格计划（形状对齐 stylekit.resolve_script_style 的返回）。

    theme 必须通过 stylekit.theme_lint；不通过 raise ValueError（带全部问题）。
    渲染侧把返回值挂到 script["_style"]，scenes._plan 会直接用它。
    """
    bad = validate_design(design)
    if bad:
        raise ValueError("设计没过校验（%d 条）：\n  - %s" % (len(bad), "\n  - ".join(bad)))

    th = _theme_of_design(design)
    lint = ST.theme_lint(th)
    if lint:
        raise ValueError("主题没过 stylekit.theme_lint（%d 条）：\n  - %s"
                         % (len(lint), "\n  - ".join(lint)))

    vis = design["visual"]
    au = design["audio"]
    pa = design["pacing"]
    mu = au["music"]
    lang = _language_of(design)

    seed = int(mu["seed"])
    fp = design_fingerprint(design)
    seed_effective = (seed * 1000003 + int(fp, 16)) % (2 ** 31 - 1)

    vo = au["voice"]
    if isinstance(vo, str):
        vid = vo
        vmap = {k: vid for k in VOICE_KINDS}
        v_jitter, v_maxd, v_auto = 0.0, 1, False
    else:
        vid = str(vo["id"])
        raw_map = vo["map"]
        vmap = {}
        for k in VOICE_KINDS:
            if k in raw_map:
                v = raw_map[k]
                vmap[k] = str(v["id"]) if isinstance(v, dict) else str(v)
        if not vmap:
            # 空 map 的语义 = 四个 kind 都用主声线（不是"缺字段回落"，是契约里写明的意思）
            vmap = {k: vid for k in VOICE_KINDS}
        v_jitter = float(vo["jitter"])
        v_maxd = int(vo["max_distinct"])
        v_auto = True
    if "body" in vmap:
        v_default = vmap["body"]
    else:
        v_default = vid

    return {
        "version": 1,
        "generator": "designkit 1.0",
        "style": "design:%s" % design["id"],
        "style_name": str(design["label"]),
        "style_source": "design",
        "style_reason": [str(design["pitch"])],
        "legacy": False,
        "theme_id": th["id"],
        "theme_label": th["label"],
        "theme": th,
        "language": lang,
        "layout_default": vis["layout"],
        "chapter_layout": {},
        "music": {
            "style": mu["style"],
            "scale": mu["scale"],
            "bpm": float(mu["bpm"]),
            "bpm_source": "design",
            "key": mu["key"],
            "key_source": "design",
            "mood": mu["mood"],
            "mood_source": "design",
            "seed": seed,
            "seed_effective": int(seed_effective),
            "seed_from_content": True,
        },
        "voice": {
            "map": vmap,
            "default": v_default,
            "policy": {
                "auto": v_auto,
                "jitter": v_jitter,
                "max_distinct": v_maxd,
                "rate_range": (-15, 12),
                "pitch_range": (-8, 8),
                "cps_band": (3.6, 5.6),
            },
        },
        "pacing": {
            "lead": float(pa["lead"]),
            "tail": float(pa["tail"]),
            "min_dur": float(pa["min_dur"]),
            "hold": float(pa["hold"]),
            "item_stagger": float(pa["item_stagger"]),
        },
        "fingerprint": {
            "version": "design-fp-v1",
            "full": fp,
            "tempo": _fingerprint_part({"structure": design.get("structure"),
                                        "pacing": design.get("pacing")}),
            "style": _fingerprint_part({"visual": design.get("visual")}),
        },
    }


# ============================================================================
# 七、指纹 / 距离
# ============================================================================


def design_fingerprint(design):
    """设计指纹：只看"决定长相"的四个块，忽略 id/label/pitch 这类文案。

    为什么要忽略 id：propose.py 会给候选改写 id（A-1a2b3c4d），若把 id 算进指纹，
    同一个设计在 proposal 与 design.json 里会得到两个指纹，确认状态会"假 stale"。
    """
    if isinstance(design, dict):
        payload = {
            "schema": design.get("schema"),
            "structure": design.get("structure"),
            "visual": design.get("visual"),
            "audio": design.get("audio"),
            "pacing": design.get("pacing"),
        }
    else:
        payload = design
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _dig(obj, keys):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _palette_key(design):
    pal = _dig(design, ("visual", "palette"))
    if not isinstance(pal, dict):
        return ""
    return "bg=%s accent=%s" % (_hex(pal.get("bg")), _hex(pal.get("accent")))


def _voice_key(design):
    vo = _dig(design, ("audio", "voice"))
    if isinstance(vo, str):
        return vo
    if isinstance(vo, dict):
        return str(vo.get("id") or "")
    return ""


# 维度顺序固定（展示与 diff 都按它）。前 7 个是任务要求的底线，后 3 个是额外观测项。
_AXES = (
    ("palette", _palette_key),
    ("motif", lambda d: _dig(d, ("visual", "motif", "kind"))),
    ("transition", lambda d: _dig(d, ("visual", "transition", "kind"))),
    ("ease", lambda d: _dig(d, ("visual", "motion", "ease"))),
    ("number", lambda d: _dig(d, ("visual", "typography", "number"))),
    ("music", lambda d: _dig(d, ("audio", "music", "style"))),
    ("voice", _voice_key),
    ("mood", lambda d: _dig(d, ("audio", "music", "mood"))),
    ("layout", lambda d: _dig(d, ("visual", "layout"))),
    ("scale", lambda d: _dig(d, ("audio", "music", "scale"))),
)


def design_distance(a, b):
    """两个设计在哪些维度上不同（"伪多方案"门禁的判据）。

    返回 {"axes": [维度名...], "n": 不同维度数, "detail": {维度: (a值, b值)}}。
    只看离散签名，不看连续量（ratio/tracking 的细小差异不算另一个人）。
    """
    axes, detail = [], {}
    for name, getter in _AXES:
        va, vb = getter(a), getter(b)
        if va != vb:
            axes.append(name)
            detail[name] = (va, vb)
    return {"axes": axes, "n": len(axes), "detail": detail}


# ============================================================================
# 八、采样器：sha1 派生的确定性采样 + 两两差异自检
# ============================================================================


def _check_brief(brief):
    """brief 的形状校验：topic 必填，其余三个可选；未知键一律报错（fail-closed）。"""
    if not isinstance(brief, dict):
        raise TypeError("brief 必须是 dict，收到 %r" % (type(brief).__name__,))
    extra = [k for k in brief if k not in _BRIEF_KEYS]
    if extra:
        raise ValueError("brief 有未知字段 %s；只认 %s"
                         % (extra, " / ".join(_BRIEF_KEYS)))
    topic = brief.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("brief.topic 必须是非空字符串（至少告诉采样器在给什么题材做方案）")
    dt = brief.get("duration_target")
    if dt is not None:
        if not _is_num(dt):
            raise ValueError("brief.duration_target 必须是数字或 null，收到 %r" % (dt,))
        if not 3.0 <= float(dt) <= 1800.0:
            raise ValueError("brief.duration_target 必须在 3~1800 秒，收到 %r" % (dt,))
    for k in ("must_have", "avoid"):
        v = brief.get(k)
        if v is None:
            continue
        if not isinstance(v, (list, tuple)) or any(not isinstance(x, str) for x in v):
            raise ValueError("brief.%s 必须是字符串数组" % k)


def _brief_canon(brief):
    """brief 的规范形态（只含契约字段；JSON 排序键后作为 sha1 的输入）。"""
    dt = brief.get("duration_target")
    return {
        "topic": str(brief["topic"]),
        "duration_target": None if dt is None else float(dt),
        "must_have": [str(x) for x in brief.get("must_have", [])],
        "avoid": [str(x) for x in brief.get("avoid", [])],
    }

# ---------------------------------------------------------------- 约束（真过滤）
# (类别, 取值, 认得的中英文说法)。只有这五类：它们正好各自对应 design 里一个能直接
# 判定的字段——配色族（由 bg/bg2/surface 反查）、母题、转场、情绪、声线性别。
_CONSTRAINT_WORDS = (
    ("palette", "deep-blue", ("深蓝", "deep-blue")),
    ("palette", "near-black", ("近黑", "near-black")),
    ("palette", "cool-mint", ("薄荷", "cool-mint")),
    ("palette", "sand", ("砂金", "sand")),
    ("palette", "slate", ("石青", "slate")),
    ("palette", "forest", ("森林", "forest")),
    ("palette", "warm-paper", ("暖纸", "warm-paper")),
    ("palette", "ink-paper", ("墨纸", "ink-paper")),
    ("motif", "brackets", ("括角", "brackets")),
    ("motif", "hairlines", ("细线", "hairlines")),
    ("motif", "handdrawn", ("手绘", "handdrawn")),
    ("motif", "slabs", ("色块", "slabs")),
    ("motif", "minimal", ("极简", "minimal")),
    ("motif", "letterbox", ("遮幅", "letterbox")),
    ("motif", "signal", ("信号", "signal")),
    ("motif", "rays", ("放射", "rays")),
    ("transition", "dip", ("叠化", "dip")),
    ("transition", "wash", ("晕染", "wash")),
    ("transition", "paper", ("纸卷", "paper")),
    ("transition", "wipe", ("擦入", "wipe")),
    ("transition", "cut", ("硬切", "cut")),
    ("transition", "iris", ("光圈", "iris")),
    ("mood", "calm", ("平静", "calm")),
    ("mood", "reflective", ("回望", "reflective")),
    ("mood", "hopeful", ("希望", "hopeful")),
    ("mood", "steady", ("稳", "steady")),
    ("mood", "driving", ("推进", "driving")),
    ("mood", "tense", ("紧张", "tense")),
    ("mood", "uplifting", ("上扬", "uplifting")),
    ("voice", "male", ("男声", "male")),
    ("voice", "female", ("女声", "female")),
)

_CATEGORY_VALUES = {
    "palette": tuple(PALETTE_FAMILIES),
    "motif": tuple(MOTIFS),
    "transition": tuple(TRANSITIONS),
    "mood": tuple(MOODS),
    "voice": ("male", "female"),
}
_CATEGORY_WORD = {"palette": "配色", "motif": "母题", "transition": "转场",
                  "mood": "情绪", "voice": "声线"}
# 英文按"整词"认：不加边界的话 "warm-paper" 会顺带命中"纸卷/paper"、"female" 会命中"male"。
_ASCII_WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _alias_spans(text_low, alias):
    """alias 在 text 里的所有起点；英文别名要求前后不紧贴字母数字（整词）。"""
    al = alias.lower()
    ascii_alias = al.isascii()
    out, i = [], text_low.find(al)
    while i != -1:
        j = i + len(al)
        ok = True
        if ascii_alias:
            if i > 0 and text_low[i - 1] in _ASCII_WORD:
                ok = False
            if j < len(text_low) and text_low[j] in _ASCII_WORD:
                ok = False
        if ok:
            out.append(i)
        i = text_low.find(al, i + 1)
    return out


def _constraints_in(text):
    """一句话 -> {类别: (取值, 命中的词)}，每类只留最长的那个词。

    为什么要"每类只留最长"：中文里"稳稳推进"同时命中"稳"(steady) 和"推进"(driving)，
    两者在 must_have 里无法同时成立；取长的那条才符合人的本意（他在说"推进"）。
    先按长度全局排序再吃掉重叠区间，"warm-paper"就整段算配色、不再顺带算一次"纸卷"。
    """
    low = str(text or "").lower()
    hits = []
    for order, (cat, val, aliases) in enumerate(_CONSTRAINT_WORDS):
        for alias in aliases:
            for start in _alias_spans(low, alias):
                hits.append((-len(alias), start, order, cat, val, alias))
    hits.sort()
    taken, got = [], {}
    for neg_len, start, _order, cat, val, alias in hits:
        end = start - neg_len
        if any(not (end <= a or start >= b) for a, b in taken):
            continue                        # 落在更长的命中里：是那个词的一部分
        taken.append((start, end))
        got.setdefault(cat, (val, alias))
    return got


def _vocab(cat):
    """某一类认得的词（只列中文，报错信息要一眼能读完）。"""
    return " / ".join(aliases[0] for c, _v, aliases in _CONSTRAINT_WORDS if c == cat)


def _too_narrow(cat, want, banned):
    """"约束太窄"的统一措辞：必须点名是哪条，否则用户不知道该删哪个。"""
    bits = []
    if want is not None:
        bits.append("must_have「%s」把%s锁成 %s" % (want[2], _CATEGORY_WORD[cat], want[1]))
    for _val, word, src in banned:
        bits.append("avoid「%s」又排除了「%s」" % (src, word))
    return ("brief 的约束把%s的可选范围清空了：%s——没有任何设计能同时满足，请放宽其中一条"
            % (_CATEGORY_WORD[cat], "；".join(bits)))


def _resolve_constraints(brief):
    """brief 的 must_have / avoid -> (must, avoid, domain, unmatched)。

    must   {类别: (取值, 命中的词, 原句)}
    avoid  {类别: [(取值, 命中的词, 原句), ...]}
    domain {类别: 允许的取值元组；None = 这一类没被约束（保持 v1.0 的原采样域）}
    unmatched 一句都没认出来的原句（按出现顺序去重）——交给顶层提示用户"这条没生效"
    """
    must_raw = [str(x) for x in (brief.get("must_have") or [])]
    avoid_raw = [str(x) for x in (brief.get("avoid") or [])]
    must, avoid, unmatched = {}, {}, []

    for src in must_raw:
        hits = _constraints_in(src)
        if not hits:
            unmatched.append(src)
            continue
        for cat, (val, word) in hits.items():
            old = must.get(cat)
            if old is not None and old[0] != val:
                # 同类两条不同的要求 = 无解（一个设计只有一族配色/一种母题），
                # 这不是"再采几次碰运气"能救的，必须马上告诉用户是哪两条在打架。
                raise ValueError(
                    "brief.must_have 的约束互斥：%s既要「%s」又要「%s」"
                    "（一个设计只能选一个，没法同时满足）——去掉一条，或把另一条放进 avoid"
                    % (_CATEGORY_WORD[cat], old[1], word))
            must[cat] = (val, word, src)

    for src in avoid_raw:
        hits = _constraints_in(src)
        if not hits:
            if src not in unmatched:
                unmatched.append(src)
            continue
        for cat, (val, word) in hits.items():
            avoid.setdefault(cat, []).append((val, word, src))

    for cat, (val, word, _src) in sorted(must.items()):
        for aval, aword, _asrc in avoid.get(cat, ()):
            if aval == val:
                raise ValueError(
                    "brief 自相矛盾：「%s」在 must_have 里要 %s=%s，"
                    "「%s」又在 avoid 里排除同一个值——没有任何候选能同时满足"
                    % (word, _CATEGORY_WORD[cat], val, aword))

    domain = {}
    for cat, values in _CATEGORY_VALUES.items():
        want = must.get(cat)
        banned = [v for v, _w, _s in avoid.get(cat, ())]
        allowed = tuple(v for v in values
                        if (want is None or want[0] == v) and v not in banned)
        if not allowed:
            raise ValueError(_too_narrow(cat, want, avoid.get(cat, ())))
        domain[cat] = None if (want is None and not banned) else allowed

    if must_raw and not must:
        # 单条认不出 -> 记录继续；全都认不出 -> 报错。差别在于：后一种情况下用户的
        # 要求被整体忽略却还返回"成功"，比失败更危险（他会以为要求生效了）。
        raise ValueError(
            "brief.must_have 里没有一条能识别：%s\n"
            "  认得的说法：配色 %s / 母题 %s / 转场 %s / 情绪 %s / 声线 %s"
            % ("、".join("「%s」" % s for s in must_raw),
               _vocab("palette"), _vocab("motif"), _vocab("transition"),
               _vocab("mood"), _vocab("voice")))
    return must, avoid, domain, unmatched


def _narrow_hint(must, avoid):
    """采样失败时的附加说明：把收窄了可选域的约束点名（没有约束就返回空，不乱加提示）。"""
    words = [w for _v, w, _s in must.values()]
    for cat in avoid:
        words.extend(w for _v, w, _s in avoid[cat])
    return "" if not words else "；收窄了可选域的约束：%s" % " / ".join(words)


def _voices_for(genders):
    """声线池按性别过滤——"女声"必须落在挑 id 之前，否则只能靠重采样碰运气。

    只从"认得出性别"的声线里挑：响度表以后加了新命名，认不出的那些只是在有性别约束时
    不参与，绝不会因为认不出就被判成违规（那会让整批候选重采样到报错）。
    """
    if not genders:
        return VOICES
    got = tuple(v for v in VOICES if _voice_gender_of(v) in genders)
    if not got:
        raise ValueError("声线池里没有 %s 的声线（VOICES=%s）"
                         % ("/".join(genders), list(VOICES)))
    return got


def _palette_family_of(design):
    """design -> 配色族名（反查 bg/bg2/surface 三个锚点；认不出返回 None）。

    为什么反查而不是在 design 里多存一个 family 字段：design.json 要保持"自足"，
    配色一旦存了族名，改族名就会让老设计漂移；三个底色是采样时原样写进去的，反查无歧义。
    """
    pal = _dig(design, ("visual", "palette"))
    if not isinstance(pal, dict):
        return None
    try:
        got = tuple(tuple(ST._color_of(pal.get(k), "visual.palette." + k))
                    for k in ("bg", "bg2", "surface"))
    except Exception:                                     # noqa: BLE001
        return None
    for name in PALETTE_FAMILIES:
        fam = PALETTE_FAMILIES[name]
        if got == (tuple(fam["bg"]), tuple(fam["bg2"]), tuple(fam["surface"])):
            return name
    return None


def _voice_ids_of(design):
    """design 里用到的全部 edge voice id（主声线 + map 里的每一个）。"""
    vo = _dig(design, ("audio", "voice"))
    if isinstance(vo, str):
        return [vo]
    if not isinstance(vo, dict):
        return []
    ids = []
    if isinstance(vo.get("id"), str):
        ids.append(vo["id"])
    mp = vo.get("map")
    if isinstance(mp, dict):
        for k in sorted(mp, key=str):
            v = mp[k]
            ids.append(v.get("id") if isinstance(v, dict) else v)
    return [str(x) for x in ids if isinstance(x, str) and x]


def _design_value(design, cat):
    """design 在某个约束类别上的取值（声线要求"整条片子的声线都是这一性别"）。"""
    if cat == "palette":
        return _palette_family_of(design)
    if cat == "motif":
        return _dig(design, ("visual", "motif", "kind"))
    if cat == "transition":
        return _dig(design, ("visual", "transition", "kind"))
    if cat == "mood":
        return _dig(design, ("audio", "music", "mood"))
    if cat == "voice":
        kinds = {_voice_gender_of(v) for v in _voice_ids_of(design)}
        return kinds.pop() if len(kinds) == 1 else "mixed"
    return None


def _constraint_problems(design, must, avoid):
    """按 brief 复核一个候选：返回违反项（空 = 过）。采样器用它兜底，不信"域已经限住"。"""
    bad = []
    for cat, (val, word, _src) in sorted(must.items()):
        got = _design_value(design, cat)
        if got != val:
            bad.append("%s=%r 不满足 must_have「%s」（%s 要 %r）"
                       % (_CATEGORY_WORD[cat], got, word, _CATEGORY_WORD[cat], val))
    for cat, rows in sorted(avoid.items()):
        got = _design_value(design, cat)
        for val, word, _src in rows:
            if got == val:
                bad.append("%s=%r 违反了 avoid「%s」" % (_CATEGORY_WORD[cat], got, word))
    return bad



def _beats_for(segments):
    """叙事用途序列：首 hook、尾 outro，中间按固定顺序取（不足就少取）。"""
    mid = ["context", "data", "compare", "process", "quote", "turn"]
    n = max(1, int(segments))
    if n == 1:
        return ["hook"]
    if n == 2:
        return ["hook", "outro"]
    return ["hook"] + mid[:n - 2] + ["outro"]


def _voice_gender_of(vid):
    """edge voice id -> "male"/"female"；认不出返回 None（不猜：猜错等于把"女声"悄悄换成男声）。

    口径取自 edge 中文声线的命名：Xiao*/Hsiao*/Hiu*（晓/曉）是女声，Yun*/Wan*（云/雲）是
    男声；香港粤语的 HiuMaan（女）/ WanLung（男）也在这条命名里。认不出的 id
    （wav 声线、别的引擎）一律"认不出"，由调用方决定怎么处理——不猜。
    """
    stem = str(vid or "").split("-")[-1]
    if stem.startswith(("Xiao", "Hsiao", "Hiu")):
        return "female"
    if stem.startswith(("Yun", "Wan")):
        return "male"
    return None


def _voice_gender(vid):
    """pitch 文案用的单字：女 / 男（认不出的仍按男声写，与 v1.0 的输出一致）。"""
    return "女" if _voice_gender_of(vid) == "female" else "男"


def _make_design(brief, index, seed, attempt, domain=None, unmatched=()):
    """按 sha1(brief 规范 JSON + index + seed + attempt) 派生一个完整 design。

    选择用"哈希 + 下标步进"而不是"每个字段独立随机"：对 n<=8 的候选，
    配色族/母题/转场/编号/声线/情绪等维度按互质步长轮转，天然保证两两至少差 2 维；
    attempt 只在自检失败时参与，仍全是 sha1，没有 random。

    domain 是 brief 约束算出来的"每类允许的取值"（None = 这一类不受限）。步进仍在
    **允许集**上做，所以"两两至少差 2 维"这条保证在过滤之后依然成立，而不是被过滤打散。
    """
    cb = _brief_canon(brief)
    ctext = _canonical(cb)
    tag = "" if seed is None else str(seed)

    def h(salt):
        return _digest(salt, ctext, tag, index, attempt)

    dom = domain or {}
    fam_names = tuple(dom.get("palette") or PALETTE_FAMILIES.keys())
    family = fam_names[(h("palette") + index) % len(fam_names)]
    motifs = tuple(dom.get("motif") or MOTIFS)
    motif = motifs[(h("motif") + 3 * index) % len(motifs)]
    transes = tuple(dom.get("transition") or TRANSITIONS)
    trans = transes[(h("transition") + 5 * index) % len(transes)]
    ease = EASES[(h("ease") + index) % len(EASES)]
    number = NUMBERS[(h("number") + 3 * index) % len(NUMBERS)]
    moods = tuple(dom.get("mood") or MOODS)
    mood = moods[(h("mood") + 3 * index) % len(moods)]
    pool = _voices_for(dom.get("voice"))
    voice_index = (h("voice") + index) % len(pool)
    scale = SCALES[(h("scale") + index) % len(SCALES)]
    layout = LAYOUTS[(h("layout") + index) % len(LAYOUTS)]
    hud = HUDS[(h("hud") + index) % len(HUDS)]
    align = ALIGNS[(h("align") + index) % len(ALIGNS)]
    font_family = FONT_FAMILIES[(h("family") + index) % len(FONT_FAMILIES)]

    hue_shift = ((h("hue") % 161) - 80) / 10.0          # -8.0 .. +8.0 度
    target_shift = ((h("sv") % 9) - 4) / 100.0          # -0.04 .. +0.04
    palette = _solve_palette(family, hue_shift, target_shift)

    density = _DENSITIES[(h("density") + index) % len(_DENSITIES)]
    push = _PUSHES[(h("push") + index) % len(_PUSHES)]
    breathe = _BREATHES[(h("breathe") + index) % len(_BREATHES)]
    enter = list(_ENTERS[(h("enter") + index) % len(_ENTERS)])
    x = _XS[(h("bx") + index) % len(_XS)]
    y = _YS[(h("by") + index) % len(_YS)]
    inset = _INSETS[(h("inset") + index) % len(_INSETS)]

    ratio = round(1.9 + (h("ratio") % 18) * 0.1, 1)     # 1.9 .. 3.6
    tracking = round((h("tracking") % 17) * 0.5, 1)     # 0 .. 8
    line_gap = round(1.15 + (h("gap") % 9) * 0.05, 2)   # 1.15 .. 1.55

    segs = 4 + h("segments") % 5                        # 4 .. 8
    target_sec = None
    dt = brief.get("duration_target")
    if dt is not None:
        segs = max(2, min(40, int(round(float(dt) / 7.0))))
        target_sec = float(dt)
    hold_policy = HOLD_POLICIES[h("hold") % len(HOLD_POLICIES)]

    lead = round(0.45 + (h("lead") % 9) * 0.05, 2)
    tail = round(0.75 + (h("tail") % 9) * 0.05, 2)
    min_dur = round(2.6 + (h("min") % 11) * 0.1, 2)
    hold = round((h("hold_sec") % 5) * 0.1, 2)
    stagger = round(0.24 + (h("stagger") % 15) * 0.02, 2)

    bpm = _MOOD_BPM[mood] + (h("bpm") % 9) - 4
    keys = ST.MINOR_KEYS if scale == "minor" else ST.MAJOR_KEYS
    key = keys[h("key") % len(keys)]
    music_style = _MUSIC_BY_MOOD[mood]
    music_seed = h("mseed") % 100000

    vcount = 1 + h("vcount") % 3
    picks = [pool[(voice_index + j) % len(pool)] for j in range(vcount)]
    vmap = {kind: picks[j % vcount] for j, kind in enumerate(VOICE_KINDS)}
    jitter = round(0.10 + (h("jitter") % 5) * 0.05, 2)

    did = "design-" + hashlib.sha1(
        ("%s|%s|%s|%s" % (ctext, tag, index, attempt)).encode("utf-8")
    ).hexdigest()[:8]
    fam_label = PALETTE_FAMILIES[family]["label"]
    label = "%s·%s" % (fam_label, _MOTIF_WORD[motif])
    pitch = "%s×%s：%s，%s转场，%s声线，%d BPM %s。" % (
        fam_label, _MOTIF_WORD[motif], _MOOD_WORD[mood], _TRANS_WORD[trans],
        _voice_gender(picks[0]), bpm, "小调" if scale == "minor" else "大调")

    return {
        "schema": SCHEMA,
        "id": did,
        "label": label,
        "pitch": pitch,
        "structure": {
            "segments": int(segs),
            "beats": _beats_for(segs),
            "hold_policy": hold_policy,
            "target_sec": target_sec,
        },
        "visual": {
            "palette": {k: list(palette[k]) for k in PALETTE_KEYS},
            "family": font_family,
            "typography": {"tracking": tracking, "line_gap": line_gap,
                           "number": number, "ratio": ratio, "align": align},
            "motif": {"kind": motif, "density": density, "detail": {}},
            "transition": {"kind": trans,
                           "dur": 0.0 if trans == "cut" else round(0.20 + (h("dur") % 5) * 0.05, 2)},
            "motion": {"ease": ease, "enter": enter,
                       "push": push, "breathe_hz": breathe},
            "bias": {"x": x, "y": y, "inset": inset},
            "layout": layout,
            "hud": hud,
            "size": "1920x1080",
            "fps": 30,
        },
        "audio": {
            "voice": {"mode": "edge_id", "id": picks[0], "map": vmap,
                      "jitter": jitter, "max_distinct": vcount},
            "music": {"mode": "params", "style": music_style, "scale": scale,
                      "bpm": int(bpm), "key": key, "mood": mood, "seed": int(music_seed)},
            "sfx": {"enabled": True,
                    "density": SFX_DENSITIES[1 + h("sfx") % 3],
                    "gain_db": -9 + h("sfxgain") % 7},
        },
        "pacing": {"lead": lead, "tail": tail, "min_dur": min_dur,
                   "hold": hold, "item_stagger": stagger},
        # 认不出来的要求：随候选一起走，顶层（propose/summary）才能提示"这条没生效"。
        "_unmatched_constraints": list(unmatched),
    }


def sample_designs(brief, n=3, seed=None):
    """确定性采样 n 个两两可区分的 design（brief 的 must_have/avoid 在这里硬过滤）。

    - 所有选择由 sha1(brief 规范 JSON + index + seed + attempt) 派生，禁 random；
    - must_have/avoid 先解析成"每类允许的取值"，下标步进在**允许集**上轮转：
      约束于是不靠重采样碰运气（声线池只有 3 个女声时，"碰 3 次全中"的概率低到不可用）；
    - 每个候选先过 validate_design，再按 brief 复核一遍约束（不信"域已经限住"）；
    - 与已接受的候选至少 2 个维度不同（design_distance 自检），否则换 attempt；
    - 最多 50 次仍不行 -> raise，并点名是哪条约束把可选域收窄了（绝不静默放宽）。
    """
    _check_brief(brief)
    if not _is_int(n) or n < 1:
        raise ValueError("n 必须是正整数，收到 %r" % (n,))

    must, avoid, domain, unmatched = _resolve_constraints(brief)
    hint = _narrow_hint(must, avoid)

    out = []
    for i in range(n):
        chosen = None
        problems = None
        too_close = None
        violated = None
        for attempt in range(_MAX_RESAMPLE):
            cand = _make_design(brief, i, seed, attempt, domain, unmatched)
            problems = validate_design(cand)
            if problems:
                continue
            violated = _constraint_problems(cand, must, avoid)
            if violated:
                continue
            too_close = [o["id"] for o in out
                         if design_distance(cand, o)["n"] < 2]
            if too_close:
                continue
            # 配色族必须互不相同（族数够时）。为什么单独管这一条：
            # design_distance 会在"母题/转场/缓动/编号"上找到足够的差异，
            # 于是两个候选可能**共用同一套底色**却仍被判为"可区分"。
            # 实测样例：A=近黑金·电影遮幅 / B=近黑金·细线页眉 —— 并排图里第一眼
            # 就是同一个片子的两种排法，用户不会认为那是两个方案。
            # 底色是整屏第一眼信息，必须先分叉。
            if len(out) < len(PALETTE_FAMILIES):
                fam = _palette_family_of(cand)
                if any(_palette_family_of(o) == fam for o in out):
                    continue
            chosen = cand
            break
        if chosen is None:
            if violated:
                raise ValueError("第 %d 个候选重采样 %d 次仍不满足 brief 约束：\n  - %s%s"
                                 % (i + 1, _MAX_RESAMPLE, "\n  - ".join(violated), hint))
            if problems:
                raise ValueError("第 %d 个候选重采样 %d 次仍没过校验：\n  - %s%s"
                                 % (i + 1, _MAX_RESAMPLE, "\n  - ".join(problems), hint))
            raise ValueError("第 %d 个候选重采样 %d 次仍与前序候选不足 2 维差异（最像 %s）%s"
                             % (i + 1, _MAX_RESAMPLE, " / ".join(too_close or []), hint))
        out.append(chosen)
    return out


# ============================================================================
# 九、CLI
# ============================================================================


def _setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                             # noqa: BLE001
            pass


def _palette_rows():
    rows = []
    for name, fam in PALETTE_FAMILIES.items():
        row = {"family": name, "label": fam["label"],
               "bg": list(fam["bg"]), "bg2": list(fam["bg2"]),
               "surface": list(fam["surface"]),
               "accent_hue": fam["accent_hue"], "dark": bool(fam["dark"])}
        try:
            pal = _solve_palette(name)
            row["ink_hex"] = _hex(pal["ink"])
            row["accent_hex"] = _hex(pal["accent"])
        except Exception as e:                        # noqa: BLE001
            row["ink_hex"] = ""
            row["accent_hex"] = ""
            row["error"] = str(e)
        rows.append(row)
    return rows


def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(description="video-studio 设计 DSL：校验 / 采样 / 列配色族")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--sample", metavar="BRIEF", default=None,
                     help="按题材采样 N 个设计")
    grp.add_argument("--lint", metavar="PATH", default=None,
                     help="校验一份 design.json")
    grp.add_argument("--list-palettes", action="store_true", help="列出配色族")
    ap.add_argument("--n", type=int, default=3, help="候选数（默认 3）")
    ap.add_argument("--seed", default=None, help="采样种子（同一 seed 结果可复现）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（stdout）")
    a = ap.parse_args(argv)

    if a.list_palettes:
        rows = _palette_rows()
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print("配色族（%d）：" % len(rows))
            for r in rows:
                print("  %-11s %-6s %s  bg=%s bg2=%s surface=%s  accent_hue=%3d°  "
                      "ink=%s accent=%s"
                      % (r["family"], r["label"], "暗" if r["dark"] else "亮",
                         _hex(r["bg"]), _hex(r["bg2"]), _hex(r["surface"]),
                         int(r["accent_hue"]), r["ink_hex"] or "-", r["accent_hex"] or "-"))
        return 0

    if a.lint:
        try:
            with open(a.lint, encoding="utf-8") as fh:
                design = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print("[designkit] 读不出 %s：%s" % (a.lint, e), file=sys.stderr)
            return 2
        bad = validate_design(design)
        if a.json:
            print(json.dumps({"path": a.lint, "ok": not bad, "problems": bad},
                             ensure_ascii=False, indent=1))
        elif bad:
            print("设计没过校验（%d 条）：" % len(bad))
            for s in bad:
                print("  -", s)
        else:
            print("设计 OK：%s" % design.get("id"))
        return 0 if not bad else 1

    if a.sample is not None:
        try:
            designs = sample_designs({"topic": a.sample}, n=a.n, seed=a.seed)
        except (TypeError, ValueError, RuntimeError) as e:
            print("[designkit] %s" % e, file=sys.stderr)
            return 2
        if a.json:
            print(json.dumps(designs, ensure_ascii=False, indent=1))
            return 0
        print("方案（brief=%r，n=%d）：" % (a.sample, len(designs)))
        for i, d in enumerate(designs):
            print("  [%s] %-14s %s" % ("ABCDEFGH"[i] if i < 8 else i + 1,
                                       d["label"], d["pitch"]))
        print("两两差异（n = 不同的设计维度数）：")
        for i in range(len(designs)):
            for j in range(i + 1, len(designs)):
                dist = design_distance(designs[i], designs[j])
                print("  %s-%s  n=%d  %s"
                      % ("ABCDEFGH"[i], "ABCDEFGH"[j], dist["n"],
                         " / ".join(dist["axes"])))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
