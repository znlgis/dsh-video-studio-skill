# -*- coding: utf-8 -*-
"""presetkit.py — 预设加载器（v1.0 / D3）：把"出厂风格"从代码常量降级为可读可改可删的 JSON。

为什么要有这一层
----------------
D3 之后剧本不再决定长相（那是 design.json 的事），但用户仍然需要一个"起点"：
"以 tech-deep 为底改"比"从白纸开始"快得多。所以 8 套风格不是被删掉，而是**下沉**成
presets/<id>.json —— 数据，不是代码；可复制、可改、可删、可加，不参与自动选型
（auto 一律 false，见每个文件里的 "auto": false）。

与 stylekit 的关系
------------------
stylekit 继续保留**原语**（contrast / theme_lint / visual_lint / 枚举）；
presetkit 只负责"从 JSON 读预设并把它变成合法 design"。
这样 stylekit 里那 8 张表可以整体删掉而不影响预设能力。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PRESET_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "presets"))
SCHEMA = 1


def preset_dir():
    return PRESET_DIR


def list_presets(where=None):
    """返回 [{'id','label','path'}]，按文件名排序（确定性）。"""
    d = str(where or PRESET_DIR)
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        try:
            with open(p, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": str(obj.get("id") or name[:-5]),
                    "label": str(obj.get("label") or ""), "path": p})
    return out


def load_preset(preset_id, where=None):
    """按 id 取一份预设；找不到返回 None（调用方决定是报错还是退化）。"""
    pid = str(preset_id or "").strip()
    if not pid:
        return None
    d = str(where or PRESET_DIR)
    p = os.path.join(d, pid + ".json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
    for row in list_presets(d):
        if row["id"] == pid:
            try:
                with open(row["path"], encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _tuples(obj, depth=0):
    """JSON 的 list 里，长度 3 且全是 0..255 整数的 -> (r,g,b) 元组（theme 要元组）。"""
    if isinstance(obj, list):
        if (len(obj) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in obj)):
            return tuple(obj)
        return [_tuples(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: _tuples(v, depth + 1) for k, v in obj.items()}
    return obj


def _voice_of(preset):
    """预设 voice 段 -> design.audio.voice。

    约定：{"hook": "zh-CN-XiaoyiNeural", ...} 是 kind -> edge id 的映射；
    id 取 body（或第一个有值的位置），map 原样带上。
    """
    raw = preset.get("voice") if isinstance(preset, dict) else None
    vmap = {str(k): str(v) for k, v in (raw or {}).items()
            if isinstance(v, str) and v.endswith("Neural")}
    main = vmap.get("body") or next(iter(vmap.values()), "zh-CN-YunyangNeural")
    return {"mode": "edge_id", "id": main, "map": vmap,
            "jitter": 0.25, "max_distinct": max(1, len(set(vmap.values())))}


def preset_to_design(preset, overrides=None):
    """把预设转成 design.json 的形状（可直接喂 designkit.validate_design）。

    这是"以某套预设为起点改"的实现：预设只提供底色族/母题/转场等结构签名，
    具体颜色仍由 designkit 的对比度求解生成（保证任何预设都不会产出低对比度画面）。
    """
    if not isinstance(preset, dict):
        raise TypeError("preset 必须是 dict")
    th = _tuples(preset.get("theme") or {})
    lang = _tuples(preset.get("language") or {})
    mus = preset.get("music") or {}
    pacing = preset.get("pacing") or {}
    mo = lang.get("motif") or {}
    tr = lang.get("transition") or {}
    mo2 = lang.get("motion") or {}
    ty = lang.get("typography") or {}
    bias = lang.get("bias") or {}
    design = {
        "schema": SCHEMA,
        "id": "preset-%s" % str(preset.get("id") or "custom"),
        "label": str(preset.get("label") or preset.get("id") or "预设"),
        "pitch": "以预设 %s 为起点" % (preset.get("label") or preset.get("id")),
        "structure": {"segments": 4, "beats": [], "hold_policy": "readable",
                      "target_sec": None},
        "visual": {
            # 不给 palette_family：预设的配色是**精确色值**（来自 theme），走 palette 直传。
            # palette_family 只用于"从色相族现场求解一套新配色"，与既定配色是两条路。
            "family": str(th.get("family") or "sans"),
            "typography": {
                "tracking": float(ty.get("tracking") or 0.0),
                "line_gap": float(ty.get("line_gap") or 1.32),
                "number": str(ty.get("number") or "dim"),
                "ratio": float(ty.get("ratio") or 1.5),
                "align": str(ty.get("align") or "left"),
            },
            "motif": {"kind": str(mo.get("kind") or "minimal"),
                      "density": float(mo.get("density") or 0.8), "detail": {}},
            "transition": {"kind": str(tr.get("kind") or "dip"),
                           "dur": float(tr.get("dur") or 0.28)},
            "motion": {"ease": str(mo2.get("ease") or "out3"),
                       "enter": list(mo2.get("enter") or (0.45, 0.85)),
                       "push": float(mo2.get("push") or 0.05),
                       "breathe_hz": float(mo2.get("breathe_hz") or 0.85)},
            "bias": {"x": int(bias.get("x") or 0), "y": int(bias.get("y") or 0),
                     "inset": int(bias.get("inset") or 0)},
            "layout": str(th.get("layout") or "left-stack"),
            "hud": str(th.get("hud") or "chip-left"),
            "size": "1920x1080",
            "fps": 30,
        },
        "audio": {
            # 声线来自预设：presets/*.json 的 voice 段已经是 edge voice id
            # （以前是 persona 名，D3 之后 voicekit 只认 id，所以导出时翻译过一次）。
            # 没有 voice 段才回落单声线——这里不猜"应该像哪把嗓子"。
            "voice": _voice_of(preset),
            "music": {"mode": "params",
                      "style": str(mus.get("style") or "tech-deep"),
                      "scale": "minor" if "m" in str((mus.get("keys") or ["Dm"])[0]).split("b")[0][-1:] else "major",
                      "bpm": float((mus.get("bpm_ladder") or [96])[0]),
                      "key": str((mus.get("keys") or ["Dm"])[0]),
                      "mood": str(mus.get("mood") or "steady"),
                      "seed": 7},
            "sfx": {"enabled": True, "density": "normal"},
        },
        "pacing": {"lead": float(pacing.get("lead") or 0.6),
                   "tail": float(pacing.get("tail") or 0.9),
                   "min_dur": float(pacing.get("min_dur") or 3.0),
                   "hold": float(pacing.get("hold") or 0.0),
                   "item_stagger": float(pacing.get("item_stagger") or 0.3)},
    }
    if isinstance(theme_pal := th, dict) and theme_pal.get("bg"):
        design["visual"]["palette"] = {
            k: list(theme_pal[k]) for k in
            ("bg", "bg2", "surface", "ink", "muted", "dim", "accent", "accent2",
             "good", "bad", "rule") if isinstance(theme_pal.get(k), tuple)}
    if isinstance(overrides, dict):
        design = _deep_merge(design, overrides)
    return design


def _deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    rows = list_presets()
    if not rows:
        print("没有预设：%s" % PRESET_DIR)
        return 1
    print("预设（%d 套，全部 auto=false，不参与自动选型）：" % len(rows))
    for r in rows:
        print("  %-11s %s" % (r["id"], r["label"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
