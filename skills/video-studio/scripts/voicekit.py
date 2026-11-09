# -*- coding: utf-8 -*-
"""voicekit.py — 旁白声线（Voice Personas）：让每部片的"谁在说话"也不一样。

v0.5.0 新增。v0.4 的 render.py 把 script.voice / rate / pitch 三个单值一路传给
filmkit.tts_batch，结果是所有片、所有段落都由同一个人用同一种语气念完 ——
用户的原话是「旁白声音都差不多、整体画风也差不多，希望随内容变化」。

本模块只做「策展 + 计划」：给出每句该用哪个声线、什么语气、语速/音高/音量抖多少、
前后留白多少。真正的合成仍在 filmkit.tts（Lead 负责接线），因此本模块
**不 import filmkit**（render.py 会同时 import 两者，互相 import 会成环），
只依赖 stylekit + 标准库。

三层结构
--------
  VOICE_PERSONAS  9 个声线角色（谁在说话）：voice id + 基础语速/音高/音量 + 响度补偿
  VOICE_TONES     6 档语气（怎么说）：相对 deltas + 前后留白 lead/tail
  _rules()        文本规则（说什么）：引号/数字/问叹/长短句/转折词 → 语气与微调

计划优先级（高到低）
--------------------
  lines[].persona / lines[].voice / lines[].tone（显式）
  > style_plan["voice"]["map"][kind]（风格包）
  > 文本规则（引号/数字 → 语气）
  > kind 规则（hook/body/quote/outro 各自的缺省声线）
  > 默认 persona

确定性
------
所有抖动都来自 sha1(title|id|persona)，禁 random / time / hash()（str hash 带盐）。
同一份 film.json 两次 voice_plan() 完全相等；jitter=0 时与「无抖动」逐字段相等。

legacy 兼容
-----------
style_plan["legacy"] 为 True（film.json 没有 style 字段）时不换人、不加抖动、不叠语气：
全部用 script 的 voice/rate/pitch 单值 + script 的 lead/tail，与 v0.4 逐字段一致。
这是硬契约：legacy 路径的任何变化都会让老项目音频变样、缓存全失效。

校准（实测定稿，换 voice id 必须重测）
--------------------------------------
2026-11-09 在本机用 edge-tts 7.3.1 合成同一句校准文本
「这是一句用来校准的中文样本，长度大约二十个字。」（20 个汉字），
用 ffmpeg 解成 24k 单声道后测门限 RMS（20ms 帧，低于 -45 dBFS 的静音帧不计）。
目标响度取 9 个 persona 的中位数 -20.97 dBFS；gain_db = 目标 - 实测，夹在 ±3 dB：
    narrator_male      Yunyang     4.56s 4.61cps -24.39dBFS -> +3.00
    narrator_female    Xiaoxiao    4.63s 4.53cps -18.28dBFS -> -2.69
    warm_female        HsiaoChen   5.33s 3.94cps -17.51dBFS -> -3.00（原始 -3.46，夹到 -3）
    bright_female      Xiaoyi      4.73s 4.44cps -20.40dBFS -> -0.57
    authority_male     Yunjian     5.21s 4.03cps -21.98dBFS -> +1.01
    young_male         Yunxi       4.80s 4.38cps -22.91dBFS -> +1.94
    teen_male          Yunxia      4.85s 4.33cps -21.27dBFS -> +0.30
    dialect_ne_female  Xiaobei     5.47s 3.84cps -20.97dBFS ->  0.00
    dialect_sx_female  Xiaoni      5.09s 4.13cps -20.63dBFS -> -0.34
edge-tts 免费端点只提供上面 8 个 zh-CN 音色（另有 zh-TW/zh-HK，可用但带腔），
故 9 个 persona 复用 8 个 voice id：warm_female 用 zh-TW-HsiaoChenNeural
（微软标注 "Warm, considerate"，实测基频 194Hz 低于 Xiaoxiao 的 238Hz，确实更暖；
若目标观众只要大陆标准音，把它换成 zh-CN-XiaoxiaoNeural 并降速即可，其余字段不用改）。

用法
----
    import voicekit as VK
    plan = VK.voice_plan(script, ST.resolve_script_style(script))
    VK.describe(plan)
    VK.spec_key(dict(plan[0], text=script["lines"][0]["text"]))   # v2 缓存键

    python voicekit.py --list                  # 声线/语气表
    python voicekit.py --plan film.json       # 某份剧本的声线计划（可读表格）
    python voicekit.py --calibrate            # 联网实测 dur/RMS/cps，打印建议 gain_db（失败 SKIP 返回 0）
"""
from __future__ import annotations

import argparse
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

# ============================================================================
# 一、声线（谁在说话）
# ============================================================================
#
# 字段口径：
#   voice     edge-tts 的 voice id（"-" 分隔且以 Neural 结尾）
#   rate/pitch/volume  字符串，直接透传给 edge_tts.Communicate（只接受整数）
#   gain_db  mix 侧给该句加的分贝（正 = 该音色偏轻，需要补），已按校准文本实测
#   desc     人话说明（打印 / --list 用）
#   narrate  能否被"自动"选来念长段叙述（方言/少年音只适合引用与点缀，设 False）
#   home     该声线的"主场"段落类型（hook/body/quote/outro），给 governor 与文档用

PERSONA_IDS = (
    "narrator_male", "narrator_female", "warm_female", "bright_female",
    "authority_male", "young_male", "teen_male",
    "dialect_ne_female", "dialect_sx_female",
)

VOICE_PERSONAS = {
    "narrator_male": {
        "voice": "zh-CN-YunyangNeural", "rate": "+2%", "pitch": "+0Hz", "volume": "+0%",
        "gain_db": 3.0, "narrate": True, "home": "body",
        "desc": "新闻/纪录片男声，稳、清楚，默认叙述（实测最轻，+3dB 补偿）",
    },
    "narrator_female": {
        "voice": "zh-CN-XiaoxiaoNeural", "rate": "+5%", "pitch": "+0Hz", "volume": "+0%",
        "gain_db": -2.7, "narrate": True, "home": "body",
        "desc": "标准女声叙述，清澈不甜，报告/复盘/知识片（实测最响，-2.7dB）",
    },
    "warm_female": {
        "voice": "zh-TW-HsiaoChenNeural", "rate": "-2%", "pitch": "-1Hz", "volume": "+2%",
        "gain_db": -3.0, "narrate": True, "home": "outro",
        "desc": "软糯女声（台湾腔），低基频 194Hz，适合纸感/人文/深夜",
    },
    "bright_female": {
        "voice": "zh-CN-XiaoyiNeural", "rate": "+6%", "pitch": "+2Hz", "volume": "+3%",
        "gain_db": -0.6, "narrate": True, "home": "hook",
        "desc": "活泼女声，高基频 289Hz，适合活力/营销/上新",
    },
    "authority_male": {
        "voice": "zh-CN-YunjianNeural", "rate": "+2%", "pitch": "-2Hz", "volume": "+2%",
        "gain_db": 1.0, "narrate": True, "home": "hook",
        "desc": "有力男声，慢而沉，适合开场/观点/史诗（实测 3.96cps 最慢）",
    },
    "young_male": {
        "voice": "zh-CN-YunxiNeural", "rate": "+5%", "pitch": "+1Hz", "volume": "+1%",
        "gain_db": 1.9, "narrate": True, "home": "quote",
        "desc": "青年男声（v0.4 的缺省音色），适合引用、对话、年轻向内容",
    },
    "teen_male": {
        "voice": "zh-CN-YunxiaNeural", "rate": "+7%", "pitch": "+1Hz", "volume": "+2%",
        "gain_db": 0.3, "narrate": False, "home": "quote",
        "desc": "少年音，基频 282Hz，只适合引用/角色台词，别整片念",
    },
    "dialect_ne_female": {
        "voice": "zh-CN-liaoning-XiaobeiNeural", "rate": "+4%", "pitch": "+0Hz", "volume": "+0%",
        "gain_db": 0.0, "narrate": False, "home": "quote",
        "desc": "东北女声，自带喜感，适合引用/段子/接地气内容",
    },
    "dialect_sx_female": {
        "voice": "zh-CN-shaanxi-XiaoniNeural", "rate": "+3%", "pitch": "+0Hz", "volume": "+0%",
        "gain_db": -0.3, "narrate": False, "home": "quote",
        "desc": "陕西女声，质地厚，适合引用/方言故事/纪录片片段",
    },
}

# 校准日志：--calibrate 会用实测值对比这里，换 voice id 后重跑一次再改表
CALIBRATED_ON = "2026-11-09"
CALIB_TEXT = "这是一句用来校准的中文样本，长度大约二十个字。"
CALIBRATION_LOG = {
    "narrator_male": {"dur": 4.56, "cps": 4.61, "rms_db": -24.39},
    "narrator_female": {"dur": 4.63, "cps": 4.53, "rms_db": -18.28},
    "warm_female": {"dur": 5.33, "cps": 3.94, "rms_db": -17.51},
    "bright_female": {"dur": 4.73, "cps": 4.44, "rms_db": -20.40},
    "authority_male": {"dur": 5.21, "cps": 4.03, "rms_db": -21.98},
    "young_male": {"dur": 4.80, "cps": 4.38, "rms_db": -22.91},
    "teen_male": {"dur": 4.85, "cps": 4.33, "rms_db": -21.27},
    "dialect_ne_female": {"dur": 5.47, "cps": 3.84, "rms_db": -20.97},
    "dialect_sx_female": {"dur": 5.09, "cps": 4.13, "rms_db": -20.63},
}

# ============================================================================
# 二、语气（怎么说）：deltas 叠在 persona 上；lead/tail 是 v0.4 基准附近的偏移量
# ============================================================================
# body 必须等于 v0.4 现状：lead 0.62 / tail 0.92 / 所有 delta = 0，legacy 才可能逐字段一致。
BODY_LEAD = 0.62
BODY_TAIL = 0.92
TONE_IDS = ("body", "hook", "quote", "outro", "emphasis", "calm")

VOICE_TONES = {
    "body": {"rate_delta": 0, "pitch_delta": 0, "volume_delta": 0, "gain_db": 0.0,
             "lead": 0.62, "tail": 0.92, "desc": "正文，平稳（v0.4 基准）"},
    "hook": {"rate_delta": 3, "pitch_delta": 1, "volume_delta": 2, "gain_db": 0.6,
             "lead": 0.48, "tail": 0.78, "desc": "开场，提一点速压一点留白，抓人"},
    "quote": {"rate_delta": -2, "pitch_delta": -1, "volume_delta": 1, "gain_db": 0.4,
              "lead": 0.70, "tail": 1.00, "desc": "引用，降速、留白，像另一个人在说话"},
    "outro": {"rate_delta": -3, "pitch_delta": -2, "volume_delta": 2, "gain_db": 0.8,
              "lead": 0.72, "tail": 1.02, "desc": "收尾，慢下来、沉下去"},
    "emphasis": {"rate_delta": 1, "pitch_delta": 2, "volume_delta": 4, "gain_db": 1.2,
                 "lead": 0.58, "tail": 0.86, "desc": "强调（数字/结论），抬音高加音量"},
    "calm": {"rate_delta": -5, "pitch_delta": -3, "volume_delta": 1, "gain_db": 0.5,
             "lead": 0.88, "tail": 1.25, "desc": "沉静，长留白，适合思辨/深夜"},
}

# kind 规则：风格包没给映射时的缺省声线（风格包优先）
KIND_DEFAULT = {
    "hook": "authority_male", "body": "narrator_male",
    "quote": "narrator_female", "outro": "warm_female",
}

LEGACY_VOICE = "zh-CN-YunxiNeural"       # 与 render.py 的 script.get("voice", ...) 缺省一致
_RATE_LIMIT = (-15, 12)                  # 与 stylekit policy 的缺省一致
_PITCH_LIMIT = (-8, 8)
_VOL_LIMIT = (-10, 10)
_LEAD_LIMIT = (0.20, 1.60)
_TAIL_LIMIT = (0.20, 2.20)
_GAIN_LIMIT = 3.0

try:
    import stylekit as _ST
except Exception:                        # noqa: BLE001 — stylekit 缺失时走兜底解析
    _ST = None


# ============================================================================
# 三、小工具：参数解析/格式化、汉字计数、抖动
# ============================================================================
_PCT_RE = re.compile(r"^\s*([+-]?)(\d+)\s*%\s*$")
_HZ_RE = re.compile(r"^\s*([+-]?)(\d+)\s*Hz\s*$")


def _round_half_away(v):
    """四舍五入而不是 Python 的银行家舍入：抖动量化要可预期、跨版本一致。"""
    v = float(v)
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


def parse_pct(v, default=0):
    """'+4%' -> 4；'-3%' -> -3；数字原样取整；解析失败回落 default。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(_round_half_away(v))
    m = _PCT_RE.match(str(v if v is not None else ""))
    if not m:
        return int(default)
    n = int(m.group(2))
    return -n if m.group(1) == "-" else n


def parse_hz(v, default=0):
    """'+2Hz' -> 2；解析失败回落 default。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(_round_half_away(v))
    m = _HZ_RE.match(str(v if v is not None else ""))
    if not m:
        return int(default)
    n = int(m.group(2))
    return -n if m.group(1) == "-" else n


def fmt_pct(v):
    """4 -> '+4%'；-3 -> '-3%'；edge-tts 只认整数百分比。"""
    return "%+d%%" % _round_half_away(v)


def fmt_hz(v):
    """2 -> '+2Hz'；edge-tts 只认整数 Hz。"""
    return "%+dHz" % _round_half_away(v)


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def hanzi_count(text):
    """数汉字（不含标点/数字/拉丁字母），长短句规则与 cps 都用它。"""
    return len(_CJK_RE.findall(str(text or "")))


def is_edge_voice(name):
    """形如 zh-CN-YunxiNeural 的原始 voice id（不是 persona 名）。"""
    n = str(name or "").strip()
    return ("-" in n) and n.endswith("Neural")


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _jitter(title, lid, who):
    """sha1(title|id|persona) 派生确定性抖动：rate/pitch/lead/tail + 两个量化阈值。

    rate/pitch 是浮点目标值（±1.5% / ±1Hz 再乘 policy.jitter），但 edge-tts 只收整数；
    若直接四舍五入，jitter=0.35 时 ±0.5 的小数会被吃光、抖动形同不存在。
    所以格式化前先用 _quantize() 做「确定性随机进位」，让长期期望值等于浮点目标。
    """
    h = hashlib.sha1(("%s|%s|%s" % (title, lid, who)).encode("utf-8")).hexdigest()

    def u(i, j):
        return int(h[i:j], 16) / float(65535 if j - i == 4 else 4294967295)

    return ((u(0, 8) * 2 - 1) * 1.5, (u(8, 16) * 2 - 1) * 1.0,
            (u(16, 24) * 2 - 1) * 0.04, (u(24, 32) * 2 - 1) * 0.04,
            u(32, 36), u(36, 40))


def _quantize(v, u):
    """确定性随机进位：`floor(v)+1` 的概率 = v 的小数部分，期望值仍是 v。

    u 来自 sha1（同一句永远同一结果），用来把浮点抖动量化成 edge-tts 能收的整数。
    v 本身是整数时（jitter=0）绝不进位，保证与「无抖动」逐字段相等。
    """
    base = int(math.floor(float(v)))
    frac = float(v) - base
    return base + (1 if (frac > 0 and frac > u) else 0)


# ============================================================================
# 四、文本规则（说什么）：全部确定性，命中即返回理由
# ============================================================================
_QUOTE_OPEN = ('"', "\u201c", "\u300c", "\u300e", "\u300a")
_QUOTE_WORDS = ("他说", "她说", "有人问", "所谓", "据称")
_NUM_UNITS = ("%", "万", "亿", "年", "倍")
_TURN_WORDS = ("但是", "然而", "不过", "其实", "问题是")


def _rules(text):
    """文本 -> {tone, rate, volume, tail, reasons}。

    优先级：引号 -> quote；数字/单位 -> emphasis（互斥，先命中先生效）。
    微调可叠加：问叹句 +2% 语速；>45 汉字 -2% 语速、尾巴 +0.10s；<12 汉字 +2% 语速；
    转折词 +2% 音量。所有判断只看字符串，不含任何随机。
    """
    t = str(text or "")
    out = {"tone": "", "rate": 0, "volume": 0, "tail": 0.0, "reasons": []}
    if any(q in t for q in _QUOTE_OPEN) or any(w in t for w in _QUOTE_WORDS):
        out["tone"] = "quote"
        out["reasons"].append("rule:quote")
    elif re.search(r"\d{3,}", t) or any(u in t for u in _NUM_UNITS):
        out["tone"] = "emphasis"
        out["reasons"].append("rule:num")
    if re.search(r"[？?！!][\u201d\u300d\u300f\"']?\s*$", t):
        out["rate"] += 2
        out["reasons"].append("rule:qmark")
    n = hanzi_count(t)
    if n > 45:
        out["rate"] -= 2
        out["tail"] += 0.10
        out["reasons"].append("rule:long")
    elif n < 12:
        out["rate"] += 2
        out["reasons"].append("rule:short")
    if any(w in t for w in _TURN_WORDS):
        out["volume"] += 2
        out["reasons"].append("rule:turn")
    return out


# ============================================================================
# 五、计划：每句用谁、什么语气、多快多响多长留白
# ============================================================================
def _resolve(script, style_plan):
    """拿 stylekit 的计划；没有就调 stylekit 算；stylekit 也没有就兜底。"""
    if isinstance(style_plan, dict) and style_plan:
        return style_plan
    if _ST is not None:
        try:
            return _ST.resolve_script_style(script)
        except Exception:                # noqa: BLE001 — 坏剧本交给 filmkit 报错
            pass
    vis = script.get("visual") if isinstance(script.get("visual"), dict) else {}
    voce = script.get("voice") if isinstance(script.get("voice"), dict) else {}
    legacy = ("style" not in script) and not vis.get("theme")
    return {
        "legacy": bool(legacy),
        "voice": {"map": {}, "default": voce.get("default"), "policy": {
            "auto": not legacy, "jitter": 0.0 if legacy else 0.35,
            "max_distinct": 2 if legacy else 3,
            "rate_range": list(_RATE_LIMIT), "pitch_range": list(_PITCH_LIMIT)}},
        "pacing": {"lead": float(script.get("lead", 0.55)),
                   "tail": float(script.get("tail", 0.85))},
    }


def _fmt_num(v):
    """计划里的 lead/tail 统一保留 3 位（避免 0.6200000000001 这种浮点尾巴）。"""
    return round(float(v) + 0.0, 3)


def _legacy_plan(script, lines, pace):
    """v0.4 路径：单值 voice/rate/pitch + script 的 lead/tail，无抖动无语气。"""
    voice = str(script.get("voice") or LEGACY_VOICE)
    rate = script.get("rate")
    pitch = script.get("pitch")
    rate_s = fmt_pct(parse_pct(rate)) if isinstance(rate, (int, float)) else str(rate or "+0%")
    pitch_s = fmt_hz(parse_hz(pitch)) if isinstance(pitch, (int, float)) else str(pitch or "+0Hz")
    lead = float(script.get("lead", pace.get("lead", 0.55)))
    tail = float(script.get("tail", pace.get("tail", 0.85)))
    rows = []
    for i, ln in enumerate(lines):
        ln = ln if isinstance(ln, dict) else {}
        rows.append({
            "id": str(ln.get("id") or ("l%02d" % (i + 1))),
            "kind": str(ln.get("kind") or "body"),
            "persona": None, "tone": "body", "voice": voice,
            "rate": rate_s, "pitch": pitch_s, "volume": "+0%", "gain_db": 0.0,
            "lead": _fmt_num(lead), "tail": _fmt_num(tail), "reason": "legacy",
        })
    return rows


def _pick_persona(line, kind, vmap, default, reasons):
    """返回 (persona 或 None, 是否显式, 原始 voice id 或 None)。"""
    for field in ("persona", "voice"):
        raw = line.get(field)
        if raw in (None, ""):
            continue
        name = str(raw).strip()
        if name in VOICE_PERSONAS:
            reasons.append("explicit:%s" % field)
            return name, True, None
        if is_edge_voice(name):
            reasons.append("explicit:%s" % field)
            return None, True, name
        reasons.append("unknown_persona:%s" % name)
    mapped = vmap.get(kind, vmap.get("*"))
    if mapped:
        name = str(mapped).strip()
        if name in VOICE_PERSONAS:
            reasons.append("map:%s" % kind)
            return name, False, None
        if is_edge_voice(name):
            reasons.append("map:%s" % kind)
            return None, False, name
        reasons.append("unknown_map:%s" % name)
    if default:
        reasons.append("default")
        return default, False, None
    name = KIND_DEFAULT.get(kind, "narrator_male")
    reasons.append("kind:%s" % kind)
    return name, False, None


def _pick_tone(line, kind, rules, use_rules, reasons):
    """显式 tone > 文本规则 > kind 同名语气 > body。"""
    raw = line.get("tone")
    name = str(raw).strip() if raw not in (None, "") else ""
    if name in VOICE_TONES:
        reasons.append("tone:explicit")
        return name
    if name:
        reasons.append("unknown_tone:%s" % name)
    if use_rules and rules["tone"]:
        reasons.append("tone:%s" % rules["tone"])
        return rules["tone"]
    if kind in VOICE_TONES:
        return kind
    return "body"


def _govern(cand, prev, kind, prev_kind, used, max_distinct, explicit, fallback=None,
            quote_rule=False):
    """换人治理：同段落不换人；换段可换，但要过 max_distinct 预算。

    返回 (最终 persona, 附加 reason)。相邻两句不换人是防"广播剧"的关键：
    同一 kind 的连续句子必须由同一把声音念；只有显式指定、或到了 hook/body/quote/outro
    的段落边界（body->quote 是 spec 明说的允许点），才会换。
    预算用尽时优先回到"正文声线"（fallback），避免收尾句被引用声线一路念到底。
    quote_rule=True 表示"这句文本有引号、该由 quote 声线念"：这是 body->quote 的
    spec 允许点，不受同 kind 限制、也不占 max_distinct 预算（reason 记 governor:quote）。
    """
    if explicit or cand is None or prev is None:
        # 显式指定 / 风格包直接写了原始 voice id：一律放行，不参与换人治理
        return cand, ""
    if cand == prev:
        return cand, ""
    if quote_rule:
        return cand, "governor:quote"
    if kind == prev_kind:
        return prev, "governor:glue"
    if cand not in used and len(used) >= max_distinct:
        # 预算用尽：只有"正文声线已经在用"时才能回落，否则会偷偷多出第 max_distinct+1 个人
        if fallback and fallback in used and fallback != cand:
            return fallback, "governor:cap"
        return prev, "governor:cap"
    return cand, "governor:switch"


def voice_plan(script, style_plan=None):
    """把剧本拆成逐句的旁白计划（纯函数、确定性，两次调用结果完全相等）。

    参数
    ----
    script      film.json 解析后的 dict（只读，不修改）
    style_plan  stylekit.resolve_script_style(script) 的返回值；None 时本模块自己算

    返回
    ----
    list[dict]，每行：id / kind / persona / tone / voice / rate / pitch / volume /
    gain_db / lead / tail / reason。rate/pitch/volume 是可直接透传 edge-tts 的字符串。
    """
    script = script if isinstance(script, dict) else {}
    sp = _resolve(script, style_plan)
    lines = [ln for ln in (script.get("lines") or [])]
    vsec = sp.get("voice") if isinstance(sp.get("voice"), dict) else {}
    policy = vsec.get("policy") if isinstance(vsec.get("policy"), dict) else {}
    pace = sp.get("pacing") if isinstance(sp.get("pacing"), dict) else {}

    if sp.get("legacy"):
        return _legacy_plan(script, lines, pace)

    jitter = max(0.0, float(policy.get("jitter", 0.35)))
    use_rules = _as_bool(policy.get("auto"), True)
    max_distinct = max(1, int(policy.get("max_distinct", 3)))
    rate_lim = tuple(policy.get("rate_range") or _RATE_LIMIT)
    pitch_lim = tuple(policy.get("pitch_range") or _PITCH_LIMIT)
    vmap = vsec.get("map") if isinstance(vsec.get("map"), dict) else {}
    default = str(vsec.get("default") or "").strip()
    default = default if default in VOICE_PERSONAS else ""
    base_lead = float(pace.get("lead", 0.55))
    base_tail = float(pace.get("tail", 0.85))
    title = str(script.get("title") or "")
    # 正文声线锚点：预算用尽时回落用它，让"旁白"始终是正文那把声音
    body_persona = vmap.get("body") if vmap.get("body") in VOICE_PERSONAS else None
    body_persona = body_persona or default or KIND_DEFAULT.get("body", "narrator_male")

    rows, used = [], []
    prev_persona, prev_kind = None, None
    for i, ln in enumerate(lines):
        ln = ln if isinstance(ln, dict) else {}
        lid = str(ln.get("id") or ("l%02d" % (i + 1)))
        kind = str(ln.get("kind") or "body").strip() or "body"
        reasons = []
        rules = _rules(ln.get("text")) if use_rules else {
            "tone": "", "rate": 0, "volume": 0, "tail": 0.0, "reasons": []}

        cand, explicit, raw_voice = _pick_persona(ln, kind, vmap, default, reasons)
        # 文本规则也要能换人：kind 仍标 body 的「他说：『…』」不能还是正文那条嗓子，
        # 否则语气换了、声音没换，听感依旧是同一个人在念。显式 voice/tone 优先级更高。
        quote_rule = False
        if not explicit and rules["tone"] == "quote" and cand is not None and cand == body_persona:
            quote_p = vmap.get("quote") if vmap.get("quote") in VOICE_PERSONAS else None
            quote_p = quote_p or KIND_DEFAULT.get("quote", "narrator_female")
            if quote_p != cand:
                cand = quote_p
                quote_rule = True
                reasons.append("rule:quote:persona")
        final, gov = _govern(cand, prev_persona, kind, prev_kind, used, max_distinct,
                             explicit, body_persona, quote_rule)
        if gov:
            reasons.append(gov)
        if final is not None and final not in used:
            used.append(final)

        tone_name = _pick_tone(ln, kind, rules, use_rules, reasons)
        tone = VOICE_TONES[tone_name]

        if final is not None:
            p = VOICE_PERSONAS[final]
            voice = p["voice"]
            rate_v = parse_pct(p["rate"]) + tone["rate_delta"]
            pitch_v = parse_hz(p["pitch"]) + tone["pitch_delta"]
            vol_v = parse_pct(p["volume"]) + tone["volume_delta"]
            gain = float(p["gain_db"]) + float(tone["gain_db"])
        else:                            # 显式原始 voice id：没有 persona 底子，只叠语气
            voice = raw_voice or LEGACY_VOICE
            rate_v = tone["rate_delta"]
            pitch_v = tone["pitch_delta"]
            vol_v = tone["volume_delta"]
            gain = float(tone["gain_db"])

        rate_v += rules["rate"]
        vol_v += rules["volume"]
        if use_rules:
            reasons.extend(rules["reasons"])

        lead = base_lead + (float(tone["lead"]) - BODY_LEAD)
        tail = base_tail + (float(tone["tail"]) - BODY_TAIL) + rules["tail"]

        u_rate, u_pitch = 0.0, 0.0
        if jitter > 0:
            jr, jp, jl, jt, u_rate, u_pitch = _jitter(title, lid, final or voice)
            rate_v += jr * jitter
            pitch_v += jp * jitter
            lead += jl * jitter
            tail += jt * jitter
            reasons.append("jitter:%.2f" % jitter)

        rate_c = _clamp(rate_v, rate_lim[0], rate_lim[1])
        pitch_c = _clamp(pitch_v, pitch_lim[0], pitch_lim[1])
        vol_c = _clamp(vol_v, _VOL_LIMIT[0], _VOL_LIMIT[1])
        if rate_c != rate_v:
            reasons.append("clamp:rate")
        if pitch_c != pitch_v:
            reasons.append("clamp:pitch")
        if _round_half_away(vol_c) != _round_half_away(vol_v):
            reasons.append("clamp:volume")

        # edge-tts 只收整数：jitter>0 时用确定性随机进位（长期期望=浮点目标），
        # jitter=0 时退化为纯四舍五入，保证与无抖动逐字段相等。
        if jitter > 0:
            rate_s = "%+d%%" % _quantize(rate_c, u_rate)
            pitch_s = "%+dHz" % _quantize(pitch_c, u_pitch)
        else:
            rate_s = fmt_pct(rate_c)
            pitch_s = fmt_hz(pitch_c)

        rows.append({
            "id": lid, "kind": kind, "persona": final, "tone": tone_name,
            "voice": voice, "rate": rate_s, "pitch": pitch_s,
            "volume": fmt_pct(vol_c), "gain_db": round(gain, 2),
            "lead": _fmt_num(_clamp(lead, _LEAD_LIMIT[0], _LEAD_LIMIT[1])),
            "tail": _fmt_num(_clamp(tail, _TAIL_LIMIT[0], _TAIL_LIMIT[1])),
            "reason": ";".join(reasons),
        })
        prev_persona = final if final is not None else prev_persona
        # 生效类型：引号句虽然 kind 标 body，但已经按 quote 声线念了，
        # 下一句普通正文才算"换段"，否则会被同 kind 粘连规则永远锁在引用声线上。
        prev_kind = "quote" if quote_rule else kind
    return rows


# ============================================================================
# 六、缓存键（v2：trim 进 key，修 v0.4 的静默复用 bug）
# ============================================================================
def _norm_pct(v, default="+0%"):
    if v in (None, ""):
        return default
    return fmt_pct(parse_pct(v))


def _norm_hz(v, default="+0Hz"):
    if v in (None, ""):
        return default
    return fmt_hz(parse_hz(v))


def spec_key(spec):
    """v2 合成缓存键：sha1("v2|text|voice|rate|pitch|volume|trim|trim_db")[:16]。

    与 v0.4 的 tts_key（text|voice|rate|pitch）不复用：v0.4 把 trim 漏在 key 外，
    同一句改 trim/trim_db 会静默复用旧音频。spec 传一行计划 + text 即可：
    spec_key(dict(plan[0], text=line["text"]))；trim/trim_db 缺省与 filmkit.tts 一致。
    """
    spec = spec if isinstance(spec, dict) else {}
    try:
        trim_db = float(spec.get("trim_db", -42.0))
    except (TypeError, ValueError):
        trim_db = -42.0
    trim = 1 if _as_bool(spec.get("trim", True), True) else 0
    raw = "v2|%s|%s|%s|%s|%s|%d|%.1f" % (
        str(spec.get("text") or ""), str(spec.get("voice") or ""),
        _norm_pct(spec.get("rate")), _norm_hz(spec.get("pitch")),
        _norm_pct(spec.get("volume")), trim, trim_db)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def plan_stats(plan):
    """计划摘要：用了几个 persona / 哪些语气 / 是否 legacy（测试与日志用）。"""
    rows = [r for r in (plan or []) if isinstance(r, dict)]
    personas = []
    tones = []
    for r in rows:
        p, t = r.get("persona"), r.get("tone")
        if p and p not in personas:
            personas.append(p)
        if t and t not in tones:
            tones.append(t)
    return {"lines": len(rows), "personas": personas, "tones": tones,
            "distinct": len(personas),
            "legacy": bool(rows) and all(r.get("reason") == "legacy" for r in rows)}


# ============================================================================
# 七、人读输出
# ============================================================================
def describe(plan):
    """把计划表打成人可读的多行文本（id/kind/persona/tone/voice/rate/lead/tail）。"""
    rows = [r for r in (plan or []) if isinstance(r, dict)]
    head = "%-8s %-7s %-18s %-9s %-28s %-6s %-6s %-6s" % (
        "id", "kind", "persona", "tone", "voice", "rate", "lead", "tail")
    out = [head, "-" * len(head)]
    for r in rows:
        out.append("%-8s %-7s %-18s %-9s %-28s %-6s %-6.2f %-6.2f" % (
            str(r.get("id", "")), str(r.get("kind", "")),
            str(r.get("persona") or "-"), str(r.get("tone", "")),
            str(r.get("voice", "")), str(r.get("rate", "")),
            float(r.get("lead", 0.0)), float(r.get("tail", 0.0))))
    return "\n".join(out)


# ============================================================================
# 八、校准：真合成、真实测，失败优雅降级（返回 0，不阻塞主链）
# ============================================================================
def _measure_wav(path):
    """读 24k 单声道 wav，返回 dur/cps/rms_db/peak_db（纯标准库，不用 numpy）。"""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    import array
    data = array.array("h")
    data.frombytes(raw)
    if sys.byteorder == "big":
        data.byteswap()
    total = len(data)
    dur = total / float(sr) if sr else 0.0
    peak = max((abs(v) for v in data), default=0) / 32768.0
    fl = max(1, int(0.02 * sr))
    hop = max(1, int(0.01 * sr))
    frames = []
    for i in range(0, max(1, total - fl + 1), hop):
        seg = data[i:i + fl]
        if not seg:
            continue
        acc = 0
        for v in seg:
            acc += v * v
        frames.append(math.sqrt(acc / float(len(seg))) / 32768.0)
    gated = [x for x in frames if x > 10 ** (-45.0 / 20.0)]
    rms = math.sqrt(sum(x * x for x in gated) / len(gated)) if gated else 1e-6
    return {"dur": dur, "cps": hanzi_count(CALIB_TEXT) / dur if dur else 0.0,
            "rms_db": 20.0 * math.log10(rms + 1e-12),
            "peak_db": 20.0 * math.log10(peak + 1e-12)}


def calibrate(personas=None, log=None):
    """逐个 persona 合成校准句，实测 dur/cps/门限 RMS，打印建议 gain_db。

    只在这里 import edge_tts（其余函数纯标准库，方便无网环境单测）。
    没装 edge_tts / 没 ffmpeg / 没网：打印 SKIP 并返回 0，绝不让主链失败。
    """
    log = log or (lambda s: None)
    try:
        import edge_tts
    except Exception as e:              # noqa: BLE001
        log("[SKIP] 没装 edge_tts（%s: %s），跳过校准" % (type(e).__name__, e))
        return 0
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log("[SKIP] 没找到 ffmpeg，无法把 mp3 解成 PCM 测 RMS，跳过校准")
        return 0
    ids = [p for p in (personas or PERSONA_IDS) if p in VOICE_PERSONAS]
    tmp = tempfile.mkdtemp(prefix="voicekit_cal_")
    rows = {}
    try:
        import asyncio
        for pid in ids:
            p = VOICE_PERSONAS[pid]
            mp3 = os.path.join(tmp, pid + ".mp3")
            wav = os.path.join(tmp, pid + ".wav")

            async def _go(_p=p, _mp3=mp3):
                await edge_tts.Communicate(CALIB_TEXT, _p["voice"], rate=_p["rate"],
                                           pitch=_p["pitch"], volume=_p["volume"]).save(_mp3)

            try:
                asyncio.run(_go())
                r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", mp3,
                                    "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", wav],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or "")[-200:])
                m = _measure_wav(wav)
                rows[pid] = m
                log("  OK   %-18s %-28s dur %.2fs  cps %.2f  rms %.2f dBFS"
                    % (pid, p["voice"], m["dur"], m["cps"], m["rms_db"]))
            except Exception as e:      # noqa: BLE001 — 单条失败不该中断整表
                log("  SKIP %-18s %s: %s" % (pid, type(e).__name__, str(e)[:110]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not rows:
        log("[SKIP] 所有音色都合成失败（多半是没网或 edge 端点临时不可用），未产生校准值")
        return 0
    target = sorted(r["rms_db"] for r in rows.values())[len(rows) // 2]
    log("")
    log("目标响度（%d 个 persona 门限 RMS 中位数）= %.2f dBFS" % (len(rows), target))
    log("%-18s %-28s %6s %6s %8s %8s %8s" % (
        "persona", "voice", "dur", "cps", "rms", "建议gain", "当前gain"))
    for pid in rows:
        m = rows[pid]
        want = _clamp(target - m["rms_db"], -_GAIN_LIMIT, _GAIN_LIMIT)
        cur = float(VOICE_PERSONAS[pid]["gain_db"])
        log("%-18s %-28s %6.2f %6.2f %8.2f %+8.2f %+8.2f%s" % (
            pid, VOICE_PERSONAS[pid]["voice"], m["dur"], m["cps"], m["rms_db"],
            want, cur, "" if abs(want - cur) < 0.35 else "  <-- 建议改表"))
    log("")
    log("把上面「建议gain」抄回 VOICE_PERSONAS[pid]['gain_db']，并把 CALIBRATED_ON 改成今天；")
    log("换 voice id 必须重跑本命令 —— 不同音色的响度差 3-7 dB，凭感觉调会一句响一句轻。")
    return 0


# ============================================================================
# 九、CLI
# ============================================================================
def _setup_stdio():
    """Windows 控制台默认 GBK，中文/箭头会炸；能改就改，改不了不致命。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:               # noqa: BLE001
            pass


def _cli_list():
    print("voicekit 声线表（v0.5.0；校准于 %s，edge-tts 免费端点只有 8 个 zh-CN 音色）"
          % CALIBRATED_ON)
    print("%-18s %-28s %6s %7s %7s %8s %-8s %-8s %s" % (
        "persona", "voice", "rate", "pitch", "volume", "gain_db", "narrate", "home", "desc"))
    print("-" * 150)
    for pid in PERSONA_IDS:
        p = VOICE_PERSONAS[pid]
        print("%-18s %-28s %6s %7s %7s %+8.2f %-8s %-8s %s" % (
            pid, p["voice"], p["rate"], p["pitch"], p["volume"], p["gain_db"],
            "yes" if p["narrate"] else "no", p["home"], p["desc"]))
    print("")
    print("语气档（body 必须等于 v0.4 基准：lead 0.62 / tail 0.92 / 所有 delta=0）")
    print("%-10s %8s %9s %9s %8s %6s %6s  %s" % (
        "tone", "rate", "pitch", "volume", "gain_db", "lead", "tail", "desc"))
    print("-" * 120)
    for tid in TONE_IDS:
        t = VOICE_TONES[tid]
        print("%-10s %+8d %+9d %+9d %+8.2f %6.2f %6.2f  %s" % (
            tid, t["rate_delta"], t["pitch_delta"], t["volume_delta"], t["gain_db"],
            t["lead"], t["tail"], t["desc"]))
    print("")
    print("注：warm_female 用 zh-TW-HsiaoChenNeural（台湾腔软糯女声）；要大陆标准音就换成")
    print("    zh-CN-XiaoxiaoNeural 并把 rate 改成 '-3%'，其余字段不用动。")
    return 0


def _cli_plan(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            script = json.load(f)
    except (OSError, ValueError) as e:
        print("[剧本格式错误] %s: %s" % (path, e))
        return 2
    if not isinstance(script, dict) or not script.get("lines"):
        print("[剧本格式错误] %s 顶层必须是对象且含非空 lines" % path)
        return 2
    sp = _resolve(script, None)
    plan = voice_plan(script, sp)
    print("剧本：%s（%d 句）" % (path, len(plan)))
    print("风格：%s  legacy=%s  voice.map=%s"
          % (sp.get("style", "?"), bool(sp.get("legacy")),
             json.dumps(sp.get("voice", {}).get("map", {}), ensure_ascii=False)))
    print("")
    print(describe(plan))
    print("")
    for r, ln in zip(plan, script["lines"]):
        print("  %-8s rate=%-5s pitch=%-6s vol=%-5s gain=%+5.2f lead=%-5.2f tail=%-5.2f %s"
              % (r["id"], r["rate"], r["pitch"], r["volume"], r["gain_db"],
                 r["lead"], r["tail"], r["reason"]))
    st = plan_stats(plan)
    print("")
    print("声线 %d 种：%s" % (st["distinct"], ", ".join(st["personas"]) or "-"))
    print("语气 %d 档：%s" % (len(st["tones"]), ", ".join(st["tones"]) or "-"))
    print("提示：同一份剧本两次 voice_plan() 结果完全相同；legacy 剧本走 v0.4 单值路径。")
    return 0


def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(
        prog="voicekit.py", description="v0.5 旁白声线/语气计划（不合成，只出计划）")
    ap.add_argument("--list", action="store_true", help="列出声线表与语气档")
    ap.add_argument("--plan", metavar="FILM_JSON", help="打印某份 film.json 的声线计划")
    ap.add_argument("--calibrate", action="store_true", help="联网实测各 persona 的 dur/RMS，打印建议 gain_db")
    ap.add_argument("--only", metavar="PERSONA", help="--calibrate 时只测这些 persona（逗号分隔）")
    a = ap.parse_args(argv)
    if a.list:
        return _cli_list()
    if a.calibrate:
        only = [x.strip() for x in (a.only or "").split(",") if x.strip()] or None
        return calibrate(only, log=print)
    if a.plan:
        return _cli_plan(a.plan)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
