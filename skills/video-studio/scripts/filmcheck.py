# -*- coding: utf-8 -*-
"""filmcheck.py — film.json 契约校验器（手写递归，只用标准库）。

为什么需要它
------------
film.json 是唯一内容真源，但 render.py 的 load_script() 只拦四类硬错误
（缺 title/lines、lines 为空、某条没 text、id 重复、bpm 非法）。**未知键会被静默忽略**：
把 hold 写成 holds、kind 拼错、sfx.cues[].at 写成 time，全都"跑得通但效果没了"。
本脚本把这类问题变成"看得见的 WARN + 一句怎么改"。

用法
----
    python filmcheck.py film.json            # 不写路径：先找 cwd/film.json，再找脚本同目录
    python filmcheck.py film.json --strict   # warning 也算失败（CI / 交付门禁用）
    python filmcheck.py film.json --json     # 机读输出（stdout 只有 JSON）

退出码
------
    0  没有 error（--strict 时也没有 warning）
    1  有 error（或 --strict 下有 warning）
    2  用法错误 / 文件不存在 / 不是合法 JSON

三件事 schema（schemas/film.schema.json）做不到，由本脚本补：
    1. 未知键 → WARN + "是不是想写 X"（编辑距离 ≤2 / 前缀匹配 / 常见拼错别名）
    2. 跨字段：sfx.cues[].at 引用的场景 id 必须存在于 lines；hold+语音预估与 timeline 对账
    3. style 枚举**从 stylekit.STYLES 单向读取**（不复制一份），stylekit 不可用时降级为 WARN

注意：这里是"补强"，不是替代。load_script() 仍是权威——两边都通过才说明剧本真的能跑。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ============================================================================
# 与 render.py / stylekit.py / voicekit.py 对齐的硬事实（改了那边要同步这里）
# ============================================================================

KINDS = ("hook", "body", "quote", "outro", "emphasis", "calm")   # 也是 voicekit 的语气 id
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast",
           "medium", "slow", "slower", "veryslow")
MUSIC_MODES = ("score", "bed", "file", "off")
ENGINES = ("pil", "web")

RATE_RE = re.compile(r"^\s*[+-]?\d+\s*%\s*$")
PITCH_RE = re.compile(r"^\s*[+-]?\d+\s*Hz\s*$")
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

CPS = 4.2                                   # 每秒约 4.2 字（voicekit 实测中位口径）
DEFAULT_LEAD, DEFAULT_TAIL, DEFAULT_MIN_DUR = 0.62, 0.92, 3.0
TIMELINE_TOL = 0.5                          # 估算与 timeline 差超过这个秒数就 WARN

# 常见拼错（编辑距离/前缀匹配对 time→at 无效，靠这张表兜底）
KEY_ALIASES = {
    "holds": "hold", "kinds": "kind", "time": "at", "times": "at", "when": "at",
    "start_time": "at", "at_time": "at", "duration": "dur", "length": "dur",
    "delay": "dur", "vol": "gain", "volume": "gain", "db": "gain_db",
    "captions": "sub", "caption": "sub",
    "item": "items", "item_list": "items", "style_id": "style",
    "voices": "voice", "styles": "style", "texts": "text", "lyric": "text",
    "cue": "cues", "sfx_cues": "cues", "sound": "sfx", "bgm": "music",
}

# 不是 film.json 的其他产物（例如 assets/score_demo.json 是 scorekit 报告）——
# 交给本脚本前先认出来，直接 SKIP 而不是按剧本报"缺 title"的错误。
OTHER_ARTIFACT_KEYS = ("generator", "meas", "reproduce", "sections", "beats",
                       "transitions", "sr", "duration", "score")


# ============================================================================
# 报告
# ============================================================================

class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, path, msg, code="invalid"):
        self.errors.append(dict(path=path, code=code, message=msg))

    def warn(self, path, msg, code="warning"):
        self.warnings.append(dict(path=path, code=code, message=msg))


# ============================================================================
# 小工具：类型名、编辑距离、拼错建议
# ============================================================================

_TYPE_TEST = {
    "str": lambda v: isinstance(v, str),
    "num": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
_TYPE_CN = {"str": "字符串", "num": "数字", "int": "整数", "bool": "true/false",
            "list": "数组", "dict": "对象", "null": "null"}


def _tname(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true/false"
    if isinstance(v, (int, float)):
        return "数字"
    if isinstance(v, str):
        return "字符串"
    if isinstance(v, list):
        return "数组"
    if isinstance(v, dict):
        return "对象"
    return type(v).__name__


def _lev(a, b):
    """编辑距离（键名都很短，O(nm) 足够）。"""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(key, known):
    """给未知键找一个"最像"的合法键：别名表 → 编辑距离 ≤2 → 前缀匹配。"""
    low = str(key).lower()
    for cand in known:
        if cand.lower() == low:
            return cand
    alias = KEY_ALIASES.get(low)
    if alias in known:
        return alias
    best, bestd = None, 99
    for cand in known:
        d = _lev(low, cand.lower())
        if d < bestd:
            best, bestd = cand, d
    if best is not None and bestd <= 2:
        return best
    for cand in known:
        cl = cand.lower()
        if min(len(low), len(cl)) >= 2 and (low.startswith(cl) or cl.startswith(low)):
            return cand
    return None


def _f(v, default=0.0):
    """宽容取数：剧本里写脏了（字符串/None/对象）也不让校验器自己崩掉。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _weighted_chars(text):
    """等效字数：汉字 1 / 英文数字 0.6 / 标点 0.3（与工具 estimate.py 同口径）。"""
    n = 0.0
    for ch in str(text or ""):
        o = ord(ch)
        if 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF:
            n += 1.0
        elif ch.isspace():
            continue
        elif ch.isascii() and ch.isalnum():
            n += 0.6
        else:
            n += 0.3
    return n


# ============================================================================
# 契约（JSON Schema 子集的执行版；schema 文件负责文档，这里负责跑）
# ============================================================================

LINE_ITEM_SPEC = {
    "t": "dict", "required": ["text"],
    "props": {
        "text": {"t": "str", "nonempty": True},
        "ok": {"t": "bool"},
    },
}

LINE_SPEC = {
    "t": "dict", "required": ["text"],
    "props": {
        "id": {"t": "str", "pattern": ID_RE, "example": "l01"},
        "kind": {"t": "str", "enum": KINDS},
        "text": {"t": "str", "nonempty": True},
        "sub": {"t": "str"},
        "big": {"t": "str"},
        "subtitle": {"t": "str"},
        "chapter": {"t": "str"},
        "kicker": {"t": "str"},
        "items": {"t": "list", "item": LINE_ITEM_SPEC},
        "hold": {"t": "num", "min": 0.0},
        "lead": {"t": "num", "min": 0.0},
        "tail": {"t": "num", "min": 0.0},
        "persona": {"t": "str"},
        "tone": {"t": "str", "enum": KINDS},
        "voice": {"t": "str"},
    },
}

# sfxkit 配方专属参数（references/sfx-design.md §7 列出的主要参数）：
# 写在这里只为了"不被当成拼错"，一律 {'t': 'any'} 原样透传给配方，不做类型校验。
CUE_RECIPE_PARAMS = ("pitch", "kind", "direction", "tone", "end", "brightness", "metal",
                     "tail", "start_hz", "end_hz", "rate", "density", "accel", "stop",
                     "shift", "depth", "strength", "gustiness", "whistle", "env",
                     "intensity", "surface", "crackle")

CUE_SPEC = {
    "t": "dict", "required": ["at", "type"], "passthrough": True,
    "props": dict({
        "at": {"t": ("num", "str")},
        "type": {"t": "str", "nonempty": True},
        "dur": {"t": "num", "min": 0.0},
        "gain": {"t": "num"},
        "size": {"t": "num"},
        "pan": {"t": "num"},
        "seed": {"t": "int"},
        "visual_at": {"t": ("num", "str")},
        "visual_t": {"t": ("num", "str")},
    }, **{k: {"t": "any"} for k in CUE_RECIPE_PARAMS}),
}

SCRIPT_SPEC = {
    "t": "dict", "required": ["title", "lines"],
    "props": {
        "title": {"t": "str", "nonempty_warn": True},
        "end_text": {"t": "str"},
        "style": {"t": "str", "enum_dynamic": "style"},
        "engine": {"t": "str", "enum": ENGINES},
        "voice": {"t": ("str", "dict"), "open": "dict"},
        "rate": {"t": "str", "pattern": RATE_RE, "example": "+6%"},
        "pitch": {"t": "str", "pattern": PITCH_RE, "example": "+0Hz"},
        "bpm": {"t": ("num", "str"), "auto": True, "auto_only": True, "positive": True},
        "seed": {"t": "int", "min": 0},
        "crf": {"t": "int", "min": 1, "max": 51},
        "preset": {"t": "str", "enum": PRESETS},
        "lines": {"t": "list", "item": LINE_SPEC, "min_items": 1},
        "lead": {"t": "num", "min": 0.0},
        "tail": {"t": "num", "min": 0.0},
        "min_dur": {"t": "num", "min": 0.0},
        "hold": {"t": "num", "min": 0.0},
        "max_dur": {"t": "num", "min": 0.0},
        "bar": {"t": "num", "min": 0.0},
        "sub_max_hold": {"t": ("num", "null")},
        "trim_silence": {"t": "bool"},
        "trim_db": {"t": "num"},
        "tts_workers": {"t": "int", "min": 1},
        "chunk_frames": {"t": "int", "min": 0},
        "reencode_video": {"t": "bool"},
        "voice_gain": {"t": "num"},
        "whoosh": {"t": "bool"},
        "whoosh_amp": {"t": "num", "min": 0.0},
        "tail_map": {"t": "dict", "open": True},
        "pacing": {"t": "dict", "props": {
            "lead": {"t": "num", "min": 0.0}, "tail": {"t": "num", "min": 0.0},
            "min_dur": {"t": "num", "min": 0.0}, "hold": {"t": "num", "min": 0.0},
            "item_stagger": {"t": "num", "min": 0.0}}},
        "visual": {"t": "dict", "props": {
            "theme": {"t": "str"}, "layout": {"t": "str"},
            "theme_overrides": {"t": "dict", "open": True}}},
        "music": {"t": "dict", "props": {
            "enabled": {"t": "bool"},
            "mode": {"t": "str", "enum": MUSIC_MODES},
            "style": {"t": "str"},
            "mood": {"t": "str"},
            "bpm": {"t": ("num", "str"), "auto": True, "auto_only": True, "positive": True},
            "key": {"t": ("num", "str"), "auto": True},
            "seed": {"t": "int", "min": 0},
            "seed_from_content": {"t": "bool"},
            "gain_db": {"t": "num"}, "duck_db": {"t": "num"},
            "seg": {"t": "num", "min": 0.0}, "amp": {"t": "num", "min": 0.0},
            "gain": {"t": "num", "min": 0.0}, "xfade": {"t": "num", "min": 0.0},
            "chords": {"t": "list", "item": {"t": "list", "item": {"t": "num"}}},
            "sections": {"t": "list", "item": {"t": "dict", "open": True}}}},
        "sfx": {"t": "dict", "props": {
            "enabled": {"t": "bool"},
            "gain_db": {"t": "num"},
            "mix_gain_db": {"t": "num"},
            "cues": {"t": "list", "item": CUE_SPEC}}},
        "web": {"t": "dict", "props": {
            "entry": {"t": "str"},
            "chunk_s": {"t": "num", "min": 0.0}}},
    },
}


# ============================================================================
# 递归校验
# ============================================================================

def _types_cn(types):
    return " 或 ".join(_TYPE_CN.get(t, t) for t in types)


def _check(val, rule, path, rep, ctx):
    types = rule.get("t", "any")
    if isinstance(types, str):
        types = (types,)
    if "any" in types:
        return                                  # 配方专属参数：只认键名，不做类型校验
    if val is None:
        if "null" in types:
            return
        rep.err(path, "不能是 null：删掉这个键，或填 %s" % _types_cn(types), "null")
        return
    if isinstance(val, str) and rule.get("auto") and val.strip().lower() in ("auto", ""):
        return                                    # "" 与 "auto" 同义（load_script 也这么认）
    if isinstance(val, str) and rule.get("auto_only") and val.strip():
        rep.err(path, "字符串只能写 \"auto\"（bpm 要么是大于 0 的数字，要么是 \"auto\"；"
                      "收到 %r，render.py 会直接报错）" % val, "bad_auto")
        return
    if not any(_TYPE_TEST[t](val) for t in types if t in _TYPE_TEST):
        rep.err(path, "类型错误：应为 %s，实际是 %s（%r）"
                % (_types_cn(types), _tname(val), val), "bad_type")
        return

    enum = rule.get("enum")
    if rule.get("enum_dynamic") == "style":
        styles = ctx.get("styles")
        if styles is None:
            if not ctx.get("style_warned"):
                ctx["style_warned"] = True
                rep.warn(path, "stylekit.STYLES 不可用（找不到 %s/stylekit.py），"
                               "跳过 style 枚举校验——只提示，不判错" % str(HERE), "stylekit_missing")
        else:
            enum = ["auto"] + list(styles)
    if enum and isinstance(val, str) and val not in enum:
        near = _suggest(val, [e for e in enum if isinstance(e, str)])
        hint = ("；是不是想写 %r？" % near) if near else ""
        rep.err(path, "枚举错误：%r 不是可用值（可用：%s）%s"
                % (val, " / ".join(str(e) for e in enum), hint), "bad_enum")
        return

    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if rule.get("positive") and val <= 0:
            rep.err(path, "必须大于 0（收到 %r；bpm 这类字段也可以写 \"auto\"）" % val, "bad_range")
        if rule.get("min") is not None and val < rule["min"]:
            rep.err(path, "不能小于 %s（收到 %r）" % (rule["min"], val), "bad_range")
        if rule.get("max") is not None and val > rule["max"]:
            rep.err(path, "不能大于 %s（收到 %r）" % (rule["max"], val), "bad_range")

    if isinstance(val, str):
        if rule.get("nonempty") and not val.strip():
            rep.err(path, "不能是空字符串（render.py 会把这条当缺字段）", "empty")
        if rule.get("nonempty_warn") and not val.strip():
            rep.warn(path, "是空字符串：顶栏/片名会是空的（不影响跑通，但成片会露馅）", "empty")
        pat = rule.get("pattern")
        if pat is not None and not pat.match(val):
            rep.err(path, "格式不对：%r（要匹配 %s；例如 %s）"
                    % (val, pat.pattern, rule.get("example", "")), "bad_pattern")

    if isinstance(val, list):
        if rule.get("min_items") and len(val) < rule["min_items"]:
            rep.err(path, "至少要 %d 项（当前 %d 项）" % (rule["min_items"], len(val)), "too_few")
        item = rule.get("item")
        if item:
            for i, it in enumerate(val):
                _check(it, item, "%s[%d]" % (path, i), rep, ctx)

    if isinstance(val, dict):
        if rule.get("open"):
            return                                  # 自由格式字典（tail_map/theme_overrides…）：不查未知键
        props = rule.get("props") or {}
        for req in rule.get("required") or []:
            if req not in val:
                rep.err("%s.%s" % (path, req), "缺少必填键 %r（%s）"
                        % (req, "render.py 会直接拒绝这份剧本"), "missing")
        for k, v in val.items():
            if k in props:
                _check(v, props[k], "%s.%s" % (path, k), rep, ctx)
            elif str(k).startswith("_"):
                continue                                  # _comment 之类的扩展键：不查不报
            else:
                near = _suggest(k, list(props))
                if near:
                    rep.warn("%s.%s" % (path, k),
                             "未知键 %r —— 是不是想写 %r？（render.py 不认识它会静默忽略："
                             "跑得通，但这个设置不生效）" % (k, near), "unknown_key")
                elif rule.get("passthrough"):
                    rep.warn("%s.%s" % (path, k),
                             "未知参数 %r：会原样透传给 sfxkit 配方（拼错则静默无效）；"
                             "常用的是 at/type/dur/gain/size/pan/seed，见 references/sfx-design.md"
                             % k, "unknown_cue_param")
                else:
                    rep.warn("%s.%s" % (path, k),
                             "未知键 %r：render.py 不读它，会被静默忽略"
                             "（确实要放扩展信息就写成 _%s 开头）" % (k, k), "unknown_key")


# ============================================================================
# 跨字段校验
# ============================================================================

def _cue_ref(at):
    """复刻 filmkit.cue_time 的解析：返回 ("abs"|"id"|"bad", 值或错误说明)。"""
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        return "abs", float(at)
    s = str(at or "").strip()
    if not s:
        return "bad", "at 是空字符串（写 12.5 / abs:12.5 / l03 / l03+0.35）"
    if s.startswith("abs:"):
        try:
            return "abs", float(s[4:])
        except ValueError:
            return "bad", "abs: 后面不是数字（写 abs:12.5）"
    try:
        return "abs", float(s)
    except ValueError:
        pass
    cut = len(s)
    for i, ch in enumerate(s):
        if ch in "+-" and i > 0:
            cut = i
            break
    sid, off = s[:cut], s[cut:]
    if off:
        try:
            float(off)
        except ValueError:
            return "bad", "偏移 %r 不是数字（写 l03+0.35 或 l03-0.2）" % off
    return "id", sid


def _find_timeline(script_path):
    cands = [script_path.parent / "work" / "timeline.json",
             Path.cwd() / "work" / "timeline.json",
             script_path.parent / "timeline.json"]
    for c in cands:
        if c.is_file():
            return c
    return None


def check_cross(script, script_path, rep, ctx):
    """schema 表达不了的跨字段约束：id 引用、hold 与 timeline 对账。"""
    lines = [ln for ln in (script.get("lines") or []) if isinstance(ln, dict)]

    # ---- lines id：缺失 / 重复 ----
    ids = []
    for i, ln in enumerate(lines):
        lid = ln.get("id")
        if lid is None or not str(lid).strip():
            rep.warn("lines[%d].id" % i, "没有 id（render.py 会自动生成 l%02d）；"
                                          "要写 sfx.cues 就显式给 id" % (i + 1), "missing_id")
            continue
        lid = str(lid)
        if lid in ids:
            rep.err("lines[%d].id" % i, "id 重复：%r（TTS 缓存与 cue 会串，后一条会覆盖前一条）"
                    % lid, "dup_id")
        ids.append(lid)
    idset = set(ids)

    # ---- sfx.cues[].at 引用的场景 id 必须在 lines 里 ----
    sfx = script.get("sfx") if isinstance(script.get("sfx"), dict) else {}
    cues = sfx.get("cues") if isinstance(sfx.get("cues"), list) else []
    for i, cue in enumerate(cues):
        if not isinstance(cue, dict):
            continue
        at = cue.get("at", cue.get("t"))
        if at is None:
            continue                                      # 缺 at 已由必填检查报过
        kind, val = _cue_ref(at)
        if kind == "bad":
            rep.err("sfx.cues[%d].at" % i, "at 写法不对：%s" % val, "bad_cue_at")
        elif kind == "id" and val not in idset:
            rep.err("sfx.cues[%d].at" % i,
                    "at=%r 引用的场景 id %r 不在 lines 里（现有：%s）。"
                    "改过台词 id 就要同步这里，否则这条音效会被 CueBus 静默跳过"
                    % (at, val, ", ".join(ids[:8]) or "lines 里没有 id"), "cue_id_missing")

    # ---- hold 之和 + 语音预估 vs work/timeline.json（差 > 0.5 s 就 WARN）----
    tl_path = _find_timeline(script_path)
    ctx["timeline"] = str(tl_path) if tl_path else None
    if tl_path is None:
        return
    try:
        tl = json.loads(tl_path.read_text(encoding="utf-8"))
        total = float(tl["total"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        rep.warn(str(tl_path), "读不了 timeline（%s），跳过时长对账" % e, "timeline_unreadable")
        return
    lead = _f(script.get("lead", DEFAULT_LEAD), DEFAULT_LEAD) or DEFAULT_LEAD
    tail = _f(script.get("tail", DEFAULT_TAIL), DEFAULT_TAIL) or DEFAULT_TAIL
    min_dur = _f(script.get("min_dur", DEFAULT_MIN_DUR), DEFAULT_MIN_DUR) or DEFAULT_MIN_DUR
    ghold = _f(script.get("hold", 0.0))
    holds, est, speech = 0.0, 0.0, 0.0
    for ln in lines:
        voice = _weighted_chars(ln.get("text")) / CPS
        hold = _f(ln.get("hold", ghold))
        holds += hold
        speech += voice
        est += max(min_dur, lead + voice + tail + hold)
    if abs(est - total) > TIMELINE_TOL:
        rep.warn(str(tl_path),
                 "剧本 hold 合计 %.2f s + 语音预估 %.2f s（含 lead/tail）≈ 全片 %.2f s，"
                 "与 timeline 的 total=%.2f s 差 %.2f s > %.1f s。"
                 "改过台词 / hold / 节奏参数就重跑 --stage layout（别拿旧时间轴直接渲）"
                 % (holds, speech, est, total, abs(est - total), TIMELINE_TOL), "timeline_drift")
    tl_ids = [str(s.get("id")) for s in (tl.get("scenes") or []) if isinstance(s, dict)]
    if idset and tl_ids and set(tl_ids) != idset:
        miss = sorted(idset - set(tl_ids))
        extra = sorted(set(tl_ids) - idset)
        rep.warn(str(tl_path),
                 "timeline 的场景 id 与 lines 对不上（lines 有但 timeline 没有：%s；"
                 "timeline 有但 lines 没有：%s）：增删过台词就重跑 --stage layout"
                 % (miss[:6] or "无", extra[:6] or "无"), "timeline_ids")


# ============================================================================
# 入口
# ============================================================================

def _setup_stdio():
    """被管道/文件重定向时切 UTF-8（与 render.py 同一口径，避免 Windows 控制台乱码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


def _fail(msg, as_json, code=2):
    if as_json:
        sys.stdout.write(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) + "\n")
    else:
        sys.stderr.write("[filmcheck] %s\n" % msg)
        sys.stderr.write("  用法：python filmcheck.py <film.json> [--strict] [--json]\n")
    raise SystemExit(code)


def _load_styles():
    """从 stylekit.STYLES 单向读取风格 id；拿不到返回 None（调用方降级成只 WARN）。"""
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import stylekit                                  # noqa: F401  （只读枚举，不复制）
        # 优先用公开的 STYLE_IDS（8 套），没有就退回 STYLES 的键（含内部 legacy）
        ids = list(getattr(stylekit, "STYLE_IDS", ()) or getattr(stylekit, "STYLES", {}).keys())
        return ids or None
    except Exception:                                    # noqa: BLE001
        return None


def _classify(data):
    """film = 像剧本；artifact = 其他产物（配乐报告等），跳过；unknown = 判不了。"""
    if not isinstance(data, dict):
        return "unknown"
    if "lines" in data or "title" in data:
        return "film"
    if any(k in data for k in OTHER_ARTIFACT_KEYS):
        return "artifact"
    return "unknown"


def _default_path():
    for c in (Path.cwd() / "film.json", HERE / "film.json"):
        if c.is_file():
            return c
    return None


def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(
        description="film.json 契约校验器（未知键 WARN + 枚举/范围 ERROR + 跨字段对账）")
    ap.add_argument("path", nargs="?", default="", help="film.json 路径（缺省找 cwd/film.json）")
    ap.add_argument("--strict", action="store_true", help="warning 也算失败（退出码 1）")
    ap.add_argument("--json", action="store_true", dest="as_json", help="机读输出（stdout 只有 JSON）")
    args = ap.parse_args(argv)

    path = Path(args.path).expanduser() if args.path else _default_path()
    if path is None:
        _fail("找不到 film.json：先 cd 到项目目录，或显式传路径", args.as_json)
    if not path.exists():
        _fail("文件不存在：%s" % path, args.as_json)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _fail("读不了 %s：%s（用 UTF-8 另存）" % (path, e), args.as_json)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _fail("%s 不是合法 JSON：第 %d 行第 %d 列 %s（先跑 python -m json.tool 看一眼）"
              % (path, e.lineno, e.colno, e.msg), args.as_json)

    kind = _classify(data)
    rep = Report()
    ctx = {"styles": _load_styles(), "style_warned": False, "timeline": None}
    skipped = False
    if kind == "artifact":
        skipped = True
    elif kind == "unknown":
        rep.err("$", "顶层既没有 title 也没有 lines，无法按 film.json 校验"
                     "（render.py 会直接报“缺少字段”）", "not_film")
    else:
        _check(data, SCRIPT_SPEC, "$", rep, ctx)
        check_cross(data, path, rep, ctx)

    failed = bool(rep.errors) or (bool(args.strict) and bool(rep.warnings))
    payload = {
        "ok": not failed,
        "file": str(path),
        "strict": bool(args.strict),
        "skipped": skipped,
        "errors": rep.errors,
        "warnings": rep.warnings,
        "counts": {"errors": len(rep.errors), "warnings": len(rep.warnings)},
        "style_ids": ctx.get("styles"),
        "timeline": ctx.get("timeline"),
    }

    if args.as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        print("[filmcheck] %s" % path)
        if skipped:
            print("  [SKIP] 这不是 film.json（顶层没有 title/lines，像是配乐报告或其他产物）"
                  "——跳过契约校验。")
        for e in rep.errors:
            print("  [ERROR] %s: %s" % (e["path"], e["message"]))
        for w in rep.warnings:
            print("  [WARN]  %s: %s" % (w["path"], w["message"]))
        if skipped:
            print("  结论：跳过（0 error / 0 warning）")
        elif not rep.errors and not rep.warnings:
            print("  结论：契约通过（0 error / 0 warning）")
        else:
            tail = "（--strict：warning 也算失败）" if args.strict else ""
            print("  结论：%d error / %d warning%s%s"
                  % (len(rep.errors), len(rep.warnings), " → 失败" if failed else " → 通过", tail))
            if rep.errors:
                print("  先按上面 [ERROR] 逐条改；[WARN] 里未知键的，正是“改了没生效”的那类坑。")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
