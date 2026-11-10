# -*- coding: utf-8 -*-
"""render.py — 一条命令从剧本到成片（v0.5.0）。

    python render.py --script film.json --scenes scenes.py --out out/film.mp4 [--stage all]

阶段（每个 stage 只做自己的事，不顺手重渲全片）：
    style   解析风格包 -> work/style.json（画风/配乐/旁白/节奏一次定死，v0.5）
    tts     逐句合成旁白 + 实测时长（带输入校验的缓存；按声线计划换人换语气）
    layout  由实测时长排时间轴 -> timeline.json / sub.srt / sub.ass（含停留窗口 hold）
    cues    画面提示点 -> work/cues.json（web 引擎从页面导出，pil 引擎从 film.json 生成）
    score   程序化配乐 -> work/music.wav（scorekit；无则跳过）
    sfx     按提示点合成音效轨 -> work/sfx.wav（sfxkit；无则跳过）
    audio   三总线混音（旁白 + 配乐 + 音效，音效响起时配乐让位）-> audio.wav
    frames  逐帧渲染 -> silent.mp4（--engine pil|web；分块并行、断点续跑）
    mux     合成（视频流 copy，两遍 loudnorm）
    qa      质检 + 抽帧 + 联系表 + 帧级深检（闪烁/空窗/音画同步）
    stills  只渲染若干关键帧做母版预览（不生成视频）
    accept  人工签核一道闸（--gate style|stills|facts|release），写 work/signoff.json
    explain 打印"现在哪些 stage 是旧的、为什么、该跑什么"（--explain-stale 的入口）

依赖顺序：style -> tts -> layout -> cues -> score -> sfx -> audio -> frames -> mux -> qa。
--stage all 按顺序跑完；单跑某个 stage 时，若前置产物缺失会明确报错并提示先跑哪个。
--engine 覆盖 film.json 的 engine：pil = Pillow 逐帧（默认，依赖最少）；web = 无头浏览器逐帧。

v0.6.0 起每个 stage 都往「门禁账本」记结论（pass / fail / skipped 三档），并在
--stage qa（或 --stage all 收尾）写出 out/QC-REPORT.md：这是**机器可读的交付结论**，
取代"agent 在对话里复述质检结果"。人工签核走 --stage accept，绑内容指纹——
改了剧本 / scenes.py / 引擎，旧签核自动失效。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import filmkit as K
import gates as GT
import stagewatch as SW

STAGES = ("style", "tts", "layout", "cues", "score", "sfx", "audio", "frames",
          "mux", "qa", "stills", "accept", "explain", "all")

# _render_range 每帧都要主题：按 script 对象缓存一次，避免每帧重新解析（几百毫秒的浪费）
_THEME_CACHE = {}

# 本次运行的门禁账本（main() 里建；stage 函数通过它记账）
_LEDGER = None


def _gate():
    """取当前账本；被单测直接调用 stage 函数时返回 None（不记账，照常打印）。"""
    return _LEDGER


def _setup_stdio():
    """输出被管道/文件重定向时把 stdout/stderr 切到 UTF-8。

    只在"非交互"时切（isatty=False）：Windows 下 python render.py ... | tee out.log
    的 stdout 默认是 GBK 代码页，日志里中文全是乱码；真实控制台保持系统编码，
    交互运行时中文照样正常。与 scorekit / webengine 的入口处理保持同一口径。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


def _file_sha1(path):
    """文件内容指纹（缺文件返回 missing，不抛异常）。"""
    try:
        h = hashlib.sha1()
        with open(str(path), "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()[:16]
    except OSError:
        return "missing"


def _frames_plan_stamp(tl, script, scenes_path, chunk):
    """分块计划的指纹：时长 / scenes.py 内容 / 剧本快照任一变化，旧分块就画出错帧。

    断点续跑只看"分块文件在不在"是危险的：改一句台词 -> 时间轴变了 -> 旧分块
    仍然被跳过复用，最后拼出一部"画面和旁白对不上"的片子却不报错。
    """
    body = json.dumps(dict(script), sort_keys=True, ensure_ascii=False, default=str)
    payload = dict(total=round(float(tl["total"]), 3), frames=int(tl["frames"]),
                   fps=int(tl["fps"]), chunk=int(chunk),
                   scenes=_file_sha1(scenes_path),
                   script=hashlib.sha1(body.encode("utf-8")).hexdigest()[:16])
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

# 可选模块（v0.3.0）：缺哪个就明确报错/跳过，不影响老流程
HERE_SCRIPTS = str(HERE)


def _opt_module(name, hint):
    """按需 import 同目录的可选模块；缺了给一句能照着做的提示。"""
    if HERE_SCRIPTS not in sys.path:
        sys.path.insert(0, HERE_SCRIPTS)
    try:
        return __import__(name)
    except Exception as e:                                  # noqa: BLE001
        raise SystemExit("[缺少 %s.py] %s（%s）" % (name, hint, e)) from e


def _try_module(name):
    """尽力 import 可选模块；缺了返回 None（用于"有就多做一步"的增强，不阻断主流程）。"""
    if HERE_SCRIPTS not in sys.path:
        sys.path.insert(0, HERE_SCRIPTS)
    try:
        return __import__(name)
    except Exception:                                       # noqa: BLE001
        return None


def _engine(script, override=None):
    return (override or script.get("engine") or "pil").lower()


def _web_entry(script):
    """浏览器渲染的入口 HTML：film.json 的 web.entry，或项目目录下 web/index.html。"""
    web = script.get("web") or {}
    entry = web.get("entry") or os.path.join(script.get("_dir", "."), "web", "index.html")
    entry = str(Path(entry).resolve())
    if not os.path.exists(entry):
        raise SystemExit(
            "[缺少 web 页面] %s\n"
            "  把 <SKILL_DIR>/assets/web/ 整个目录拷到项目里（cp -r <SKILL_DIR>/assets/web ./web），"
            "或在 film.json 里写 web.entry 指向你自己的页面。" % entry)
    return entry


def _web_where(entry):
    """报错里告诉用户 DURATION / FPS 到底写在哪：入口 HTML + 同目录脚本 + 一条能照抄的 grep。

    只报入口 HTML 不够用：模板的 window.DURATION 写在 web/film.js 开头（engine.js 另有
    兜底默认值），用户拿到 index.html 还是得全文翻。这里把候选文件一起列出来。
    """
    d = os.path.dirname(entry)
    try:
        scripts = sorted(n for n in os.listdir(d)
                         if n.lower().endswith((".js", ".mjs", ".html")))
    except OSError:
        scripts = []
    tail = ("要改的文件：%s（入口 HTML）" % entry)
    if scripts:
        tail += "；页面脚本在 %s%s" % (
            d, "（%s）" % " / ".join(scripts) if len(scripts) <= 6 else "")
    tail += "\n  查 DURATION / FPS 定义在哪：grep -n \"window.DURATION\\|global.DURATION\\|DURATION =\" %s" % d
    return tail


# ---------------------------------------------------------------- 装载
def load_module(path: str, name="film_scenes"):
    """每次都用独立模块名 + 全新 module 对象，避免跨片污染（历史坑：渲染出上一部片）。"""
    path = str(Path(path).resolve())
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_script(p) -> dict:
    """读剧本并做前置校验：坏 film.json 给一句能照着改的错，而不是 Python traceback。"""
    p = Path(p).resolve()
    if not p.exists():
        raise SystemExit("[缺少剧本] %s（--script 指向 film.json）" % p)
    try:
        s = json.load(open(p, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit("[剧本格式错误] %s 不是合法 JSON：%s" % (p, e)) from e
    if not isinstance(s, dict):
        raise SystemExit("[剧本格式错误] %s 顶层必须是一个 JSON 对象" % p)
    for req in ("title", "lines"):
        if req not in s:
            raise SystemExit("film.json 缺少字段: " + req)
    lines = s["lines"]
    if not isinstance(lines, list) or not lines:
        raise SystemExit("film.json 的 lines 必须是非空数组（当前是 %r）" % (lines,))
    for k in ("music", "sfx", "web"):
        if s.get(k) is not None and not isinstance(s.get(k), dict):
            raise SystemExit("film.json 的 %s 必须是对象（当前是 %s）" % (k, type(s[k]).__name__))
    # v0.5：bpm 允许 "auto"（由 stylekit 按风格梯度 + 内容指纹决定）。
    # 同时允许写在 music.bpm 里（优先级更高）。
    for _k, _v in (("bpm", s.get("bpm")),
                   ("music.bpm", (s.get("music") or {}).get("bpm"))):
        if _v in (None, "") or (isinstance(_v, str) and _v.strip().lower() == "auto"):
            continue
        try:
            if float(_v) <= 0:
                raise ValueError("必须大于 0")
        except (TypeError, ValueError) as e:
            raise SystemExit("film.json 的 %s 必须是正数或 \"auto\"（收到 %r：%s）"
                             % (_k, _v, e)) from e
    seen = set()
    for i, ln in enumerate(lines):
        if not isinstance(ln, dict):
            raise SystemExit("film.json 的 lines[%d] 必须是对象（当前是 %s）"
                             % (i, type(ln).__name__))
        if not str(ln.get("text") or "").strip():
            raise SystemExit("film.json 的 lines[%d] 缺少 text（朗读文本）" % i)
        ln.setdefault("id", "l%02d" % (i + 1))
        ln.setdefault("sub", ln["text"])
        sid = str(ln["id"])
        if sid in seen:
            raise SystemExit("film.json 的 lines id 重复: %r（TTS 文件与 cue 都会串）" % ln["id"])
        seen.add(sid)
    s["_path"] = str(p)
    s["_dir"] = str(p.parent)
    return s


def _need(cond, msg):
    if not cond:
        raise SystemExit("[前置产物缺失] " + msg)


# ---------------------------------------------------------------- 风格解析（v0.5.0）
def _stylekit():
    """惰性 import stylekit；缺文件时给一句能照着做的错，而不是 ModuleNotFoundError。"""
    try:
        import stylekit as ST
        return ST
    except Exception as e:                                  # noqa: BLE001
        raise SystemExit("[缺少 stylekit] v0.5 起 render.py 需要同目录的 stylekit.py"
                         "（skills/video-studio/scripts/stylekit.py）：%s" % e)


def resolve_style(script, work=None, verbose=True):
    """解析风格计划并挂到 script["_style"] 上（同一进程内各 stage 共享）。

    纯函数 + 落盘 work/style.json（审计用：用户能看到"为什么是这套风格"）。
    """
    if isinstance(script.get("_style"), dict):
        return script["_style"]
    ST = _stylekit()
    plan = ST.resolve_script_style(script)
    # 主题体检：坏主题现在拦，不要等渲完 300 帧。
    # v0.7.2 修：以前只体检**基础主题**（ST.THEMES[theme_id]），而 scenes 实际用的是
    # plan["theme"]（= 基础主题 + film.json 的 visual.theme_overrides）。于是用 overrides
    # 把 ink/bg 调成低对比度时，体检照样 PASS，渲完才发现字幕看不清。
    # 实测：overrides ink=[30,30,30] / bg=[10,10,10] -> 基础主题 lint 为空，
    #       最终主题 lint = ['ink 与背景对比度 1.09 < 7.0']。
    bad = ST.theme_lint(plan.get("theme") or ST.THEMES[plan["theme_id"]])
    if bad:
        raise SystemExit("[主题体检失败] %s：%s" % (plan["theme_id"], "；".join(bad)))
    script["_style"] = plan
    if work and verbose:
        path = os.path.join(work, "style.json")
        try:
            os.makedirs(work, exist_ok=True)
            json.dump(plan, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] style.json 落盘失败：%s" % e)
    return plan


def _theme_for(script):
    """返回当前片的主题字典；legacy 返回 None（= v0.4 的硬编码外观，逐字节兼容）。"""
    plan = script.get("_style") if isinstance(script.get("_style"), dict) else None
    if plan is not None and plan.get("legacy"):
        return None
    plan = resolve_style(script)
    if plan.get("legacy"):
        return None
    return _stylekit().THEMES[plan["theme_id"]]


def _print_style(plan):
    m = plan["music"]
    v = plan["voice"]
    print("   风格: %s（%s）%s" % (plan["style"], plan["style_source"],
                                  "" if plan["legacy"] else " / " + plan["style_name"]))
    print("        理由: %s" % "；".join(plan["style_reason"]))
    print("        主题: %s  版式: %s" % (plan["theme_id"],
                                         ",".join(sorted(set(plan["chapter_layout"].values())))
                                         or plan["layout_default"]))
    print("        配乐: %s  BPM %g（%s）  调性 %s（%s）  mood %s"
          % (m["style"], m["bpm"], m["bpm_source"], m["key"], m["key_source"], m["mood"]))
    if v["map"]:
        print("        旁白: %s" % "  ".join("%s=%s" % kv for kv in sorted(v["map"].items())))
    p = plan["pacing"]
    print("        节奏: lead %.2f / tail %.2f / min_dur %.1f / 条目步长 %.2f"
          % (p["lead"], p["tail"], p["min_dur"], p["item_stagger"]))


def st_style(script, work):
    print("== Style")
    plan = resolve_style(script, work)
    _print_style(plan)
    return plan


# ---------------------------------------------------------------- TTS
def _voice_plan(script, plan):
    """声线计划（voicekit）。缺 voicekit 时返回 None -> tts_batch 走 v0.4 单声线路径。"""
    try:
        import voicekit as VK
    except Exception as e:                                  # noqa: BLE001
        print("   [WARN] 没有 voicekit.py，本次旁白单声线（v0.4 行为）：%s" % e)
        return None
    try:
        vp = VK.voice_plan(script, plan)
    except Exception as e:                                  # noqa: BLE001
        print("   [WARN] voicekit 解析失败，退回单声线：%s" % e)
        return None
    return vp


def st_tts(script, work, force=False):
    lines = script["lines"]
    plan = resolve_style(script, work)
    print("== TTS (%d 句)" % len(lines))
    if plan["legacy"]:
        print("   声线: v0.4 兼容（单一声线 %s）" % script.get("voice", "zh-CN-YunxiNeural"))
        vp = None
    else:
        vp = _voice_plan(script, plan)
        if vp:
            import voicekit as VK
            print(VK.describe(vp))
            json.dump({"style": plan["style"], "policy": plan["voice"]["policy"], "lines": vp},
                      open(os.path.join(work, "voice_plan.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
    if force:
        shutil.rmtree(os.path.join(work, "tts"), ignore_errors=True)
    done = K.tts_batch(lines, work,
                       voice=script.get("voice", "zh-CN-YunxiNeural"),
                       rate=script.get("rate", "+0%"),
                       pitch=script.get("pitch", "+0Hz"),
                       workers=int(script.get("tts_workers", 4)),
                       trim=bool(script.get("trim_silence", True)),
                       plan=None if vp is None else
                       [{"voice": r["voice"], "rate": r["rate"], "pitch": r["pitch"],
                         "volume": r.get("volume", "+0%"),
                         "persona": r.get("persona"), "tone": r.get("tone")} for r in vp],
                       trim_db=float(script.get("trim_db", -42.0)))
    json.dump(done, open(os.path.join(work, "tts.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    total = sum(float(x["dur"]) for x in done)
    print("   语音总时长 %.2fs / %d 句" % (total, len(done)))
    return done


# ---------------------------------------------------------------- 排版
def st_layout(script, work, lines=None):
    lines = lines or json.load(open(os.path.join(work, "tts.json"), encoding="utf-8"))
    plan = resolve_style(script, work)
    print("== Layout")
    pace = plan["pacing"]
    # 逐句 lead/tail：voicekit 的 voice_plan 已经算好每句的停顿，这里只负责喂给时间轴。
    # 旧项目（legacy）没有 voice_plan.json，lead_map/tail_map 都是 None -> 全局常数，v0.4 行为。
    vp = None
    vp_path = os.path.join(work, "voice_plan.json")
    if not plan["legacy"] and os.path.exists(vp_path):
        try:
            vp = json.load(open(vp_path, encoding="utf-8"))["lines"]
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] voice_plan.json 读不了，逐句停顿退回全局值：%s" % e)
    lead_map = {r["id"]: float(r["lead"]) for r in vp} if vp else None
    tail_map = {r["id"]: float(r["tail"]) for r in vp} if vp else None
    if vp and script.get("tail_map"):
        tail_extra = script["tail_map"]
        for lid, v in tail_extra.items():
            if lid in tail_map:
                tail_map[lid] = float(v)
    tl = K.build_timeline(lines,
                          lead=script.get("lead", pace["lead"]),
                          tail=script.get("tail", pace["tail"]),
                          min_dur=script.get("min_dur", pace["min_dur"]),
                          tail_map=tail_map or script.get("tail_map"),
                          lead_map=lead_map,
                          hold=script.get("hold", pace["hold"]),
                          max_dur=script.get("max_dur"))
    tl["title"] = script["title"]
    tl["voice"] = script.get("voice", "zh-CN-YunxiNeural")
    tl["crf"] = script.get("crf", 18)
    tl["preset"] = script.get("preset", "medium")
    tl["style"] = {"style": plan["style"], "theme_id": plan["theme_id"],
                   "legacy": plan["legacy"],
                   "chapter_layout": plan["chapter_layout"],
                   "item_stagger": pace["item_stagger"]}
    json.dump(tl, open(os.path.join(work, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_srt = K.write_srt(tl["scenes"], os.path.join(work, "sub.srt"))
    theme = (K._stylekit().THEMES[plan["theme_id"]] if not plan["legacy"] else None)
    n_ass = K.write_ass(tl["scenes"], os.path.join(work, "sub.ass"), theme=theme)
    print("   总时长 %.2fs / %d 帧 / %d 段 / srt %d 条 / ass %d 条"
          % (tl["total"], tl["frames"], len(tl["scenes"]), n_srt, n_ass))
    # 安全区自查：字幕一旦越界，mux 前就能发现
    safe_errs = K.qa_text_safe(tl["scenes"])
    if safe_errs:
        print("   [WARN] 字幕安全区:", safe_errs[:3])
    # 字形自查：缺字符会被画成方块
    glyph_errs = K.check_glyphs(script["lines"] + tl["scenes"])
    if glyph_errs:
        print("   [WARN] 缺字形（会显示为方块）:")
        for lid, fld, chars in glyph_errs[:8]:
            print("     %-6s %-7s %r" % (lid, fld, chars))
    for s in tl["scenes"]:
        print("   %-8s %7.2f-%7.2f  %s" % (s["id"], s["start"], s["end"], s["text"][:40]))
    return tl


# ---------------------------------------------------------------- 提示点
def st_cues(script, work, tl, engine="pil"):
    """画面提示点 -> work/cues.json。

    三个来源按顺序合并（后写的覆盖同类型同时间的项不做去重，音效叠加是合理行为）：
      1. film.json 的 sfx.cues（与引擎无关，手写最直观）
      2. scenes.py 的可选钩子 cues(tl, script)（代码里登记：画面算到哪就响到哪）
      3. web 引擎：页面的 window.getCues()（提示点跟着动画代码走，改动画不用改 JSON）
    """
    print("== Cues")
    bus = K.CueBus(tl["scenes"], tl["total"])
    sfx_cfg = script.get("sfx") or {}
    skipped = []

    def _extend_safe(items, src):
        """逐条登记；某条写错了（场景 id 不存在）只跳过它并记下来，不炸掉整个 stage。

        全部跳过时报错——"提示点一个都没落上"和"少了两个提示点"是两回事，
        前者说明剧本/分段对不上，不能静默出无声片。
        """
        n = 0
        for it in items or []:
            try:
                bus.extend([it])
                n += 1
            except Exception as e:                          # noqa: BLE001
                skipped.append((src, it, str(e)))
        return n

    n_json = 0
    if sfx_cfg.get("enabled", True):
        n_json = _extend_safe(sfx_cfg.get("cues"), "film.json")

    # scenes.py 里的 cues() 钩子：允许直接返回 [{at,type,...}] 或调用 bus.add(...)
    n_hook = 0
    mod = None
    if script.get("_scenes") and os.path.exists(script["_scenes"]):
        try:
            mod = load_module(script["_scenes"])
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] 加载 scenes.py 失败，跳过 cues 钩子:", e)
            mod = None
    if mod is not None and hasattr(mod, "cues"):
        try:
            got = mod.cues(tl, script) or []
        except Exception as e:                              # noqa: BLE001
            raise SystemExit("scenes.py 的 cues() 钩子报错：%s" % e) from e
        n_hook = _extend_safe(got, "scenes.py")
        # v0.7.2：web 引擎下要提醒"两套时间基准混用"。
        # scenes.py 的 cues() 是按 **PIL 时间轴**（timeline.json 的段落与 visual_at）算的，
        # 页面动画则按 **页面自己的时间轴**（window.DURATION）。两者合并进同一份 cues.json 后：
        #   - 音效会按 PIL 时间响（页面画面此刻可能正在另一幕）；
        #   - 音画同步门禁能把这种配对识别为"跨基准"并跳过（qa_frames 的 src 过滤），不报假 FAIL，
        #     但**声音与画面对不上的事实仍然存在**。
        # 不静默：说清现象与两条改法，让用户自己选路线。
        if engine == "web" and n_hook:
            print("   [WARN] web 引擎下仍合并了 scenes.py 的 %d 条 cue —— 它们按 PIL 时间轴生成，"
                  "页面动画按页面时间轴播放，两者基准不同：音效可能响在错误的幕上。" % n_hook)
            print("          两种改法：① 走 PIL 路线（--engine pil）用 scenes.py 这套 cue；"
                  "② 走 web 路线就把 cue 写进页面（window.getCues()）或 film.json 的 sfx.cues，"
                  "并删除 scenes.py 的 cues() 钩子。")

    # web 引擎：从页面导出（需要浏览器）
    n_web = 0
    events = []
    if engine == "web":
        from_web = _st_cues_web(script, work)
        for it in from_web.get("cues") or []:
            row = dict(it)
            row["src"] = "web"
            bus.items.append(row)
            n_web += 1
        events = from_web.get("events") or []
        print("   页面导出 %d 个提示点 / %d 个视觉事件" % (n_web, len(events)))

    if skipped:
        print("   [WARN] %d 条提示点被跳过（场景 id 对不上，通常是改了剧本没同步 film.json）：" % len(skipped))
        for src, it, err in skipped[:6]:
            print("     %-10s %s  ->  %s" % (src, it, err.split("（")[0][:90]))
        if not bus.items and (sfx_cfg.get("cues") or n_hook):
            raise SystemExit("[cues] 所有提示点都落位失败：上面的 id 一个都没在 timeline 里。"
                             "先跑 --stage layout，再核对 film.json 的 sfx.cues 用的是当前场景 id。")

    payload = bus.write(os.path.join(work, "cues.json"), duration=tl["total"], events=events)
    print("   cues.json: %d 条（film.json %d / scenes.py %d / 页面 %d）"
          % (len(payload["cues"]), n_json, n_hook, n_web))
    kinds = {}
    for c in payload["cues"]:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    if kinds:
        print("   类型:", ", ".join("%s×%d" % (k, v) for k, v in sorted(kinds.items())))
    return payload


def _st_cues_web(script, work):
    """调用 webengine.export_cues 导出页面提示点（engine=web 时）。"""
    we = _opt_module("webengine", "浏览器逐帧渲染需要 webengine.py；只用 PIL 路线可以不改 engine")
    entry = _web_entry(script)
    data = we.export_cues(entry)
    return data or {}


# ---------------------------------------------------------------- 配乐 / 音效
def st_score(script, work, tl):
    """程序化配乐 -> work/music.wav（scorekit 不在就跳过，不阻断出片）。

    配乐来源由 K.music_plan 统一判定（与 st_audio 共用同一份语义）：
      - mode=off/none 或 enabled=false：不出配乐，也不删已存在的 music.wav
        （删文件会误删"外部提供的配乐"，而 off 的语义只是"本片不要配乐"）；
      - mode=bed：走 v0.2 和弦床，不生成 scorekit 产物；
      - mode=file：外部 work/music.wav，原样交给 audio 阶段；
      - 缺省：有 bpm 就生成，没有 bpm 沿用已有 music.wav / 和弦床（v0.2 兼容）。
    """
    print("== Score")
    mus = script.get("music") or {}
    music_path = os.path.join(work, "music.wav")
    plan, why = K.music_plan(script, has_music_file=os.path.exists(music_path))
    print("   " + why)
    if plan in ("off", "bed", "file", "none"):
        return None
    if plan in ("missing-file", "missing-bpm", "unknown-mode"):
        raise SystemExit("[score] " + why)
    sk = _try_module("scorekit")
    if sk is None:
        print("   [WARN] 没有 scorekit.py，跳过配乐（audio 阶段没有 music.wav，本片将无配乐）")
        return None
    anchors = K.scenes_anchors(tl["scenes"])
    sections = mus.get("sections")
    if sections:
        # film.json 允许用 id 表述分段起点，换成绝对秒
        fixed = []
        for s in sections:
            s2 = dict(s)
            if "at" in s2:
                s2["start"] = K.cue_time(s2.pop("at"), tl["scenes"])
            fixed.append(s2)
        sections = fixed
    plan_s = resolve_style(script)
    mp = plan_s["music"]
    t0 = time.time()
    style_arg = None if plan_s["legacy"] else str(mp.get("style") or plan_s["style"])
    kwargs = dict(bpm=float(mp["bpm"]), key=str(mp["key"] or "Dm"), anchors=anchors,
                  sections=sections, seed=int(mp["seed_effective"]))
    # v0.7：内容指纹参与配乐结构变奏（和弦替换 / 动机级数 / 鼓型 ghost，见
    # references/audio-variation.md §3.3）。只在非 legacy 时传——不传 fingerprint 的
    # scorekit 认不出这个 kwarg，会走下面的 TypeError 回退链，照样出片只少变奏。
    # legacy 必须不传：v0.4 的逐样本兼容是硬契约（selfcheck 20/26 条守着）。
    fp_hex = ""
    if not plan_s["legacy"]:
        fp_hex = str((plan_s.get("fingerprint") or {}).get("full") or "")
    kw_fp = dict(kwargs, fingerprint=fp_hex) if fp_hex else kwargs
    # scorekit 0.5 起：style/mood + music_plan（一次把 bpm/key/mood/seed 全交出去，
    # 免得两处默认值打架）+ pace（把过碎的段落并进前一段）。
    # 旧 scorekit 没有这些 kwargs -> TypeError -> 自动退回老接口，不阻断出片。
    # 四级回退：带指纹 -> 不带指纹 -> 只有 style/mood -> 最老的签名。
    pace = {"min_dur": float(plan_s["pacing"]["min_dur"]),
            "hold": float(plan_s["pacing"]["hold"])}
    mood_arg = str(mp.get("mood") or "steady")
    samples = report = None
    # v0.7.2：多级签名兼容链以前对**任何** TypeError 都继续换签名试，最后统一报
    # "不接受任何一种已知签名"。scorekit 内部真出 TypeError（例如某个参数是 None 被拿去做
    # 算术）时，用户会照着"签名不兼容"的方向白找半天。这里区分两类：
    #   - 报错文本含 "unexpected keyword argument" -> 确实是不认识这个参数，换签名重试；
    #   - 其它 TypeError -> 是**内部**错误，带上签名与错误信息立刻报，别吞。
    attempts = (
        dict(style=style_arg, mood=mood_arg, music_plan=mp, pace=pace, **kw_fp),
        dict(style=style_arg, mood=mood_arg, music_plan=mp, pace=pace, **kwargs),
        dict(style=style_arg, mood=mood_arg, **kwargs),
        dict(**kwargs),
    )
    for attempt in attempts:
        try:
            samples, report = sk.render(float(tl["total"]), **attempt)
            break
        except TypeError as err:
            if "unexpected keyword argument" in str(err):
                continue
            raise SystemExit(
                "[score] scorekit.render() 内部报错（不是签名不兼容，别去改调用参数）：%s\n"
                "  本次签名：%s\n  要看完整 traceback：set RENDER_DEBUG=1 后重跑"
                % (err, ", ".join(sorted(k for k in attempt if k != "self")))
            ) from err
    if samples is None:
        raise SystemExit("[score] scorekit.render() 不接受任何一种已知签名，无法出配乐"
                         "（试过 %d 种：从最全的参数到最简）" % len(attempts))
    if report and not plan_s["legacy"]:
        print("   配乐风格: %s / mood %s / %s BPM / %s"
              % (report.get("style") or style_arg, mp.get("mood"),
                 report.get("bpm"), report.get("key")))
    out = os.path.join(work, "music.wav")
    sk.save(out, samples)
    if report:
        json.dump(report, open(os.path.join(work, "score.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    samples_read, _sr = K.wav_read(out)
    print("   music.wav %.2fs  峰值 %.3f  %.1fs" % (
        K.wav_dur(out), float(np.max(np.abs(samples_read))), time.time() - t0))
    return out


def _sfx_seed_context(script):
    """v0.7：算音效的内容派生上下文 (style, content_fp, key, enabled)。

    legacy（没写 style 字段）时返回全 None -> CueBus 走旧的 seed_base + i 路径，
    v0.4 及以前的片子逐样本不变。参考 references/audio-variation.md §3.2 / §4。
    """
    plan = resolve_style(script)
    sfx_cfg = script.get("sfx") or {}
    want_content = bool(sfx_cfg.get("content_seed", True))
    if plan.get("legacy") or not want_content:
        return None, None, None
    fp = (plan.get("fingerprint") or {}).get("full")
    mus = plan.get("music") or {}
    return str(plan.get("style")), str(fp) if fp else None, str(mus.get("key") or "Dm")


def _duck_transition_sfx(samples, cues, scenes, sr=K.SR, depth_db=-3.0, window=2.2):
    """段落边界前的转场音效自动让位（v0.7，契约 §4）。

    配乐在段落交界自己已经有一段过门（scorekit 的 swell / riser / sub_boom），
    sfx 轨再在同一时刻放 whoosh / riser，同一频段叠两层就是"配乐和音效听起来像同一套"。
    做法：对位于 [边界-window, 边界] 内的转场类 cue，在它的时长内乘一个 dB 增益包络。
    只压转场类，不碰 impact / 提示音（那是"该响"的语义重音）。
    """
    if not scenes or samples is None or not len(samples):
        return samples
    try:
        import numpy as np
    except Exception:                                       # noqa: BLE001
        return samples
    x = np.array(samples, copy=True, dtype=np.float32)
    n = len(x)
    bounds = [float(s["start"]) for s in scenes if float(s.get("start", 0.0)) > 0.5]
    if not bounds:
        return x
    kinds = 0
    for c in cues or []:
        if str(c.get("type")) not in ("whoosh", "riser", "reverse_swell"):
            continue
        t = float(c.get("t", -1.0))
        if t < 0:
            continue
        near = any(abs(t - b) <= window for b in bounds)
        if not near:
            continue
        dur = float(c.get("dur") or 1.5)
        s = max(0, int(round(t * sr)))
        e = min(n, int(round((t + dur) * sr)))
        if e <= s:
            continue
        g = 10.0 ** (depth_db / 20.0)
        k = min(80, max(8, int(0.01 * sr)))
        ramp = min((e - s) // 2, k)
        seg = x[s:e]
        if ramp > 0:
            env = np.full(e - s, g, dtype=np.float32)
            env[:ramp] = np.linspace(1.0, g, ramp, dtype=np.float32)
            env[-ramp:] = np.linspace(g, 1.0, ramp, dtype=np.float32)
            seg = seg * env[:, None] if seg.ndim == 2 else seg * env
        else:
            seg = seg * g
        x[s:e] = seg
        kinds += 1
    if kinds:
        print("   过门避让: %d 条转场音效在段落边界前 %.1fs 内 -%.0f dB（避免和配乐过门撞车）"
              % (kinds, window, abs(depth_db)))
    return x


def st_sfx(script, work, tl):
    """按提示点合成音效轨 -> work/sfx.wav。没有 cues.json 就跳过。

    v0.7：非 legacy 时走内容派生 seed（跨片同一位置不再逐样本相同），
    并把配乐调性透传给提示音配方（不再硬编码 Dm）；
    段落边界前的转场音效自动让位（不与配乐过门撞车）。
    """
    print("== SFX")
    cues_path = os.path.join(work, "cues.json")
    if not os.path.exists(cues_path):
        print("   没有 cues.json（先跑 --stage cues），本次不出音效轨")
        return None
    bus = K.CueBus.from_json(cues_path)
    bus.total = float(tl["total"])
    if not bus.items:
        print("   cues.json 里没有提示点，跳过")
        return None
    sfx_cfg = script.get("sfx") or {}
    style, content_fp, key = _sfx_seed_context(script)
    try:
        samples = bus.build_audio(total=tl["total"],
                                  base_db=sfx_cfg.get("base_db"),
                                  default_gain_db=float(sfx_cfg.get("gain_db", 0.0)),
                                  style=style, content_fp=content_fp, key=key)
    except Exception as e:                                  # noqa: BLE001
        # 音效轨是"增强"不是"必需"：sfxkit 缺失/配方不全时不要拖死整片，
        # 但必须把原因喊出来（静默降级比报错更危险）。
        print("   [WARN] 音效轨合成失败，本片只有一个旁白+配乐的混音：%s" % e)
        stale = os.path.join(work, "sfx.wav")
        if os.path.exists(stale):
            os.remove(stale)
        return None
    if not sfx_cfg.get("duck_transitions", True):
        pass
    else:
        samples = _duck_transition_sfx(samples, bus.resolved(), tl.get("scenes") or [])
    out = os.path.join(work, "sfx.wav")
    peak = K.wav_write(out, samples)
    print("   sfx.wav %.2fs  峰值 %.3f  声道 %d" % (
        K.wav_dur(out), peak, 2 if samples.ndim == 2 else 1))
    return out


def _voice_track(tl, script):
    """把逐句旁白铺成一条总线，返回 (n,) 单声道。

    v0.5.0 修一个 v0.4 的静默 bug：voice_gain 原来在这里和 mix_film 里**各乘一次**
    （净效果 voice_gain^2，默认 1.0 时看不出来）。现在全局增益只在 mix_film 乘一次，
    这里只负责**逐句**增益（voicekit 的 gain_db，默认 0 dB -> 1.0，与 v0.4 一致）。
    """
    bus = K.AudioBus(tl["total"])
    for s in tl["scenes"]:
        pcm = K.decode_pcm(s["path"])
        gdb = float(s.get("gain_db", 0.0) or 0.0)
        bus.place(pcm, s["voice_start"], gain=10.0 ** (gdb / 20.0),
                  fade_in=0.02, fade_out=0.06)
    return bus.bus[: int(round(float(tl["total"]) * K.SR))].copy()


def _legacy_music(tl, script):
    """v0.2 的和弦床（没有 scorekit / music.wav 时的兜底）。"""
    mus = script.get("music") or {}
    if not (mus.get("enabled", True) and mus.get("chords")):
        return None
    bus = K.AudioBus(tl["total"], channels=2)
    bus.music_bed(mus["chords"], seg=float(mus.get("seg", 4.0)),
                  gain=float(mus.get("gain", 0.9)),
                  amp=float(mus.get("amp", 0.055)),
                  xfade=float(mus.get("xfade", 1.2)))
    return bus.bus[: int(round(float(tl["total"]) * K.SR))].copy()


# ---------------------------------------------------------------- 混音
def st_audio(tl, work, script):
    """三总线混音：旁白 + 配乐 + 音效 -> audio.wav（立体声）。

    v0.3.0 起把三条总线分开处理再合：
      - 旁白：单声道，居中（voice_gain 可调）
      - 配乐：来源由 K.music_plan 判定（score/file/bed/off），口径与 st_score 完全一致——
        music.mode=off 时一条配乐都不混，即使 work/music.wav 还在（外部配乐请用 mode=file）
      - 音效：work/sfx.wav（按 cues.json 合成）；没有 cues 时退回 v0.2 的转场 whoosh
    音效响起时配乐自动让位（duck_db），避免重击被配乐吃掉。
    """
    print("== Audio")
    total = float(tl["total"])
    voice = _voice_track(tl, script)

    mus = script.get("music") or {}
    music = None
    music_path = os.path.join(work, "music.wav")
    plan, why = K.music_plan(script, has_music_file=os.path.exists(music_path))
    if plan == "off":
        # 关键语义：mode=off = 不要配乐。旧实现会无条件读 work/music.wav，
        # 于是"上一轮跑出来的配乐"仍被混进新片（用户改了 mode 却听不出变化）。
        print("   配乐: " + why)
    elif plan == "bed":
        music = _legacy_music(tl, script)
        if music is not None:
            print("   配乐: 和弦床（%s）" % why)
        else:
            print("   配乐: %s，但没有 music.chords，跳过" % why)
    elif plan in ("missing-file", "missing-bpm", "unknown-mode"):
        raise SystemExit("[audio] " + why)
    elif plan in ("file", "score"):
        music, _ = K.wav_read(music_path)
        print("   配乐: music.wav（%s）" % why)
    elif plan == "none":
        print("   配乐: " + why)
    if plan == "score" and music is None:
        # 单独跑 --stage audio 时 score 还没跑过：不混配乐，但必须说清楚为什么
        print("   [WARN] music 用 scorekit 但 %s 不存在（先跑 --stage score）；本次无配乐"
              % music_path)

    sfx = None
    sfx_path = os.path.join(work, "sfx.wav")
    if os.path.exists(sfx_path):
        sfx, _ = K.wav_read(sfx_path)
        print("   音效: sfx.wav（按 cues.json 合成）")
    elif script.get("whoosh", True) and not os.path.exists(os.path.join(work, "cues.json")):
        # v0.2 兼容：没有提示点时才用"每段开头一声 whoosh"的旧行为
        bus = K.AudioBus(total, channels=2)
        for s in tl["scenes"][1:]:
            if float(s["start"]) < 0.2:
                continue
            bus.place(K.whoosh(0.45, amp=float(script.get("whoosh_amp", 0.06))),
                      max(0.0, s["start"] - 0.12), gain=0.7)
        sfx = bus.bus[: int(round(total * K.SR))].copy()
        print("   音效: 旧版转场 whoosh（没有 cues.json）")

    plan_s = resolve_style(script)
    # 风格化的 duck 深度：轻快/极简风格的配乐更"薄"，让位可以浅一点；
    # 电影/史诗要压得更狠。用户显式写 music.duck_db 时永远以用户为准。
    # v0.7：整体 +1 dB —— 旧值（2.0/2.5/3.0/4.5）下配乐只比音效低 1.5 dB、duck 仅 3 dB，
    # 段落过门时两者在同一频段几乎等高，是"配乐和音效听起来像同一套"的混音侧成因。
    duck_default = 4.0
    if not plan_s["legacy"]:
        duck_default = {"mono": 3.0, "lo-fi": 3.5, "magazine": 3.5,
                        "cinema": 5.5, "epic": 5.5}.get(plan_s["style"], 4.0)
    samples = K.mix_film(voice, music, sfx, total=total,
                         voice_gain=float(script.get("voice_gain", 1.0)),
                         # v0.7.1 旁白优先：配乐基线从 -1.5 dB 降到 -12 dB。
                         # 视频以旁白为主，-1.5 dB 时背景与台词响度几乎等高，观众要费力分辨字；
                         # 12 dB 是"人声明显压住背景"的经验线（audio.md §2 的那条设计标准）。
                         music_gain_db=float(mus.get("gain_db", -12.0)),
                         sfx_gain_db=float((script.get("sfx") or {}).get("mix_gain_db", 0.0)),
                         duck_db=float(mus.get("duck_db", duck_default)),
                         # 旁白说话时再压背景（侧链）：配乐 6 dB、音效 3 dB；
                         # 间隙自动恢复，避免整片背景被闷住。显式写 0 可关掉。
                         voice_duck_db=float(mus.get("voice_duck_db", 6.0)),
                         voice_duck_sfx_db=float(mus.get("voice_duck_sfx_db", 3.0)),
                         voice_thresh_db=float(mus.get("voice_thresh_db", -42.0)),
                         voice_floor_db=float(mus.get("voice_floor_db", -60.0)),
                         # SNR 兜底：混音后实测人声活跃区的人声/背景差，不足 12 dB 自动补压背景。
                         # 这是"画面/素材电平再离谱也不许盖住旁白"的最后一道闸；0 = 关。
                         guard_snr_db=float(mus.get("guard_snr_db", 12.0)),
                         guard_cap_db=float(mus.get("guard_cap_db", 30.0)))
    out = os.path.join(work, "audio.wav")
    peak = K.wav_write(out, samples)
    print("   audio.wav %.2fs  峰值 %.3f  声道 %d" % (K.wav_dur(out), peak, 2))
    return out


# ---------------------------------------------------------------- 渲染
def _render_range(tl, mod, script, f0, f1, out_path, crf, preset, progress=True):
    """渲染 [f0, f1) 帧区间到 out_path。纯函数：同一帧号永远画出同一张图。"""
    scenes = tl["scenes"]
    total = tl["total"]
    sink = K.FrameSink(out_path, crf=crf, preset=preset)
    idx = 0
    t_start = time.time()
    last_rep = 0
    for fi in range(f0, f1):
        t = fi / float(tl["fps"])
        while idx + 1 < len(scenes) and t >= scenes[idx + 1]["start"]:
            idx += 1
        s = scenes[idx]
        lt = t - s["start"]
        frame = mod.draw_frame(s, lt, s["dur"], t, script, fi)
        if frame is None:
            raise RuntimeError("场景 %s 在 t=%.2fs 返回 None" % (s.get("id"), t))
        th = _theme_for(script) if _THEME_CACHE.get("k") != id(script) else _THEME_CACHE.get("th")
        if id(script) != _THEME_CACHE.get("k"):
            _THEME_CACHE.update(k=id(script), th=th)
        frame = K.draw_subtitle(frame, K.sub_at(scenes, t, script.get("sub_max_hold")),
                                theme=th)
        fade = 1.0
        if t < 0.5:
            fade *= K.ease_out(t / 0.5)
        if t > total - 0.8:
            fade *= max(0.0, (total - t) / 0.8)
        if fade < 0.999:
            # 浅色主题淡入淡出到纯黑会出现"闪一下黑"，非常廉价。
            # transition=wash 时淡向主题底色，观感是"渐显"而不是"黑场"。
            wipe = (0, 0, 0)
            if th is not None and str(th.get("transition") or "black") == "wash":
                wipe = tuple(th["bg2"])
            frame = K.Image.blend(K.Image.new("RGB", (K.W, K.H), wipe), frame, fade)
        sink.write(frame)
        if progress and fi - last_rep >= 300:
            last_rep = fi
            el = max(1e-6, time.time() - t_start)
            fps = (fi - f0 + 1) / el
            print("   %d/%d  %.1f fps  eta %.0fs" % (fi + 1, tl["frames"], fps,
                                                     (f1 - fi - 1) / max(fps, 1e-6)), flush=True)
    return sink.close()


def _seg_usable(path, frames):
    """已有分块能不能复用：体积够 + 头部声明的帧数与计划一致。

    为什么不能只看 size>1024：Ctrl-C / 杀进程留下的半截 mp4 也 >1024，被当成
    "已存在"跳过后，拼接只在最后报一句"拼接后时长 0.07s != 预期 29.00s"。
    v0.7.2 起分块先写 .part 再 os.replace，正常中断不会留下半截正式文件；这里
    再补一道头部 nb_frames 比对，挡住"文件在/大小对但帧数不对"的异常（例如被
    外部工具截断过）。

    注意：**不做** -count_frames 逐帧核对。实测 390 帧的块要 3.5 s（只读头部
    0.2 s），长片几十个块会让每次 --resume 多花几分钟；被截断的 mp4 头部仍写着
    完整 nb_frames，逐帧核对才是唯一能查出来的手段，代价与收益不成比例——真遇到
    这种情况由拼接后的时长校验兜底，并在报错里指明怎么重来。
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= 1024:
            return False
        info = K.probe(path) or {}
        for st in info.get("streams") or []:
            if st.get("codec_type") == "video" and st.get("nb_frames"):
                return int(st["nb_frames"]) == int(frames)
        # 读不出 nb_frames（老封装 / ffprobe 版本差异）：退回到"体积够就信"
        return True
    except Exception:                                       # noqa: BLE001
        return False


def _render_chunk_worker(args):
    """分块渲染工作单元（供 multiprocessing 调用；函数必须模块级可 pickle）。"""
    (tl_path, scenes_path, script_path, f0, f1, out_path, crf, preset, part_path) = args
    tl = json.load(open(tl_path, encoding="utf-8"))
    script = json.load(open(script_path, encoding="utf-8"))
    script["_total"] = tl["total"]
    # v0.6.0 修：JSON 快照会把元组变成 list（主题色是 (r,g,b) 元组），子进程里
    # scenes._bg() 拿 th["bg"] 当缓存键 -> TypeError: unhashable type: "list"，
    # 于是 --jobs >1 整条并行路径直接崩。计划是**纯函数**，这里按剧本重新解一次，
    # 既拿回元组，也保证子进程的主题与父进程逐字段一致。
    script.pop("_style", None)
    try:
        script["_style"] = resolve_style(script, verbose=False)
    except Exception as e:                                  # noqa: BLE001
        print("   [WARN] 子进程重解风格失败，回退到快照里的 _style：%s" % e)
        script["_style"] = json.load(open(script_path, encoding="utf-8")).get("_style")
    mod = load_module(scenes_path, "film_scenes_chunk_%d" % f0)
    # 先写 .part 再改名：中途被 Ctrl-C / 被杀时，目标文件名对应的永远是一段**完整**
    # 编码结果。否则半截 mp4 只要 >1024 字节就会被 --resume 当成"已存在"跳过，
    # 拼接后时长对不上才报错（实测：截断到 60 KB 的块 -> 拼接后 0.07s vs 预期 29.00s）。
    _render_range(tl, mod, script, f0, f1, part_path, crf, preset, progress=False)
    os.replace(part_path, out_path)
    return out_path


def st_frames(tl, work, scenes_mod, script, jobs=1, resume=True, engine="pil"):
    print("== Frames（engine=%s）" % engine)
    if engine == "web":
        return _st_frames_web(tl, work, script, jobs=jobs, resume=resume)
    script = dict(script)
    script["_total"] = tl["total"]           # 场景算全局进度条要用
    # 分块 worker 是 spawn 出来的新进程：主题必须随快照走，否则子进程算出的
    # _theme_for 与父进程不一致 -> 拼接处出现"半部片换了配色"。
    script.setdefault("_style", resolve_style(script))
    crf = int(script.get("crf", 18))
    preset = script.get("preset", "medium")
    total_frames = tl["frames"]

    if int(jobs) <= 1:
        silent = os.path.join(work, "silent.mp4")
        # 断点续跑：顶层 single 渲染不做帧级续跑（用分块模式才有意义）
        if resume:
            print("   [提示] --jobs 1 是单进程整片渲染，--resume 不生效；要断点续跑请用 --jobs N（N>1）")
        _render_range(tl, scenes_mod, script, 0, total_frames, silent, crf, preset)
        return silent

    # ---- 分块并行：按绝对起始帧命名，便于缺口检测 ----
    seg_dir = os.path.join(work, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    jobs = max(1, int(jobs))
    chunk = int(script.get("chunk_frames") or 0) or \
        int(round(total_frames / float(jobs) / float(tl["fps"]) / 4.0)) * 4 * int(tl["fps"])
    chunk = max(int(tl["fps"]) * 2, int(round(chunk / int(tl["fps"]))) * int(tl["fps"]))
    plan = []
    f = 0
    while f < total_frames:
        f1 = min(total_frames, f + chunk)
        plan.append((f, f1))
        f = f1
    print("   分块: %d 段 x 约 %d 帧，并行 %d" % (len(plan), chunk, jobs))

    script_path = os.path.join(work, "_script_snapshot.json")
    json.dump(script, open(script_path, "w", encoding="utf-8"), ensure_ascii=False)
    tl_path = os.path.join(work, "timeline.json")
    scenes_path = script.get("_scenes") or os.path.join(script["_dir"], "scenes.py")

    # ---- 断点续跑的前置校验：计划变了就不许复用旧分块 ----
    stamp = _frames_plan_stamp(tl, script, scenes_path, chunk)
    stamp_path = os.path.join(seg_dir, "_plan.json")
    old_stamp = None
    if os.path.exists(stamp_path):
        try:
            old_stamp = (json.load(open(stamp_path, encoding="utf-8")) or {}).get("stamp")
        except Exception:                                   # noqa: BLE001
            old_stamp = "unreadable"
    if resume and old_stamp and old_stamp != stamp:
        raise SystemExit(
            "[断点续跑] 分块计划已变化（timeline / scenes.py / 剧本 / 编码参数改过），\n"
            "  旧分块会画出与当前时间轴不符的画面，拒绝拼接。\n"
            "  确认要重渲就删掉分块目录再跑：rm -rf %s" % seg_dir)
    with open(stamp_path, "w", encoding="utf-8") as fh:
        json.dump(dict(stamp=stamp, total=tl["total"], frames=tl["frames"], chunk=chunk),
                  fh, ensure_ascii=False)

    todo = []
    planned = set()
    # 上次被中断留下的半成品（编码中途的残片）：不算分块，直接清掉，别让它占盘。
    # 名字带 _part_ 前缀，既不会被 stale 检测当成"不属于当前计划的旧分块"，
    # 也不会被 _seg_usable 当成"已存在的分块"跳过。
    for n in os.listdir(seg_dir):
        if n.startswith("_part_"):
            try:
                os.remove(os.path.join(seg_dir, n))
            except OSError:
                pass
    for (f0, f1) in plan:
        seg = os.path.join(seg_dir, "seg_%06d_%06d.mp4" % (f0, f1))
        planned.add(os.path.basename(seg))
        if resume and _seg_usable(seg, int(f1) - int(f0)):
            print("   skip 段 %d-%d（已存在）" % (f0, f1))
            continue
        if os.path.exists(seg):
            # 存在但不可用（半截 / 时长不符）：删掉重渲，别让它混进拼接
            print("   [WARN] 段 %d-%d 已存在但帧数/时长对不上，重渲（%d 字节）"
                  % (f0, f1, os.path.getsize(seg)))
            try:
                os.remove(seg)
            except OSError:
                pass
        part = os.path.join(seg_dir, "_part_" + os.path.basename(seg))
        todo.append((tl_path, scenes_path, script_path, f0, f1, seg, crf, preset, part))

    # 旧计划留下的分块不会参与拼接，但会一直占盘：说出来，不静默
    stale = [n for n in os.listdir(seg_dir)
             if n.startswith("seg_") and n.endswith(".mp4") and n not in planned]
    if stale:
        print("   [WARN] 分块目录里有 %d 个不属于当前计划的旧文件（不会拼接，可手动删除）" % len(stale))

    if todo:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        ctx = mp.get_context("spawn")
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as ex:
            futs = {ex.submit(_render_chunk_worker, a): a for a in todo}
            done = 0
            for fu in futs:
                fu.result()
                done += 1
                print("   段完成 %d/%d  %.0fs" % (done, len(todo), time.time() - t0), flush=True)

    # ---- 缺口检测 + 拼接 ----
    parts = []
    missing = []
    for (f0, f1) in plan:
        seg = os.path.join(seg_dir, "seg_%06d_%06d.mp4" % (f0, f1))
        if os.path.exists(seg) and os.path.getsize(seg) > 1024:
            parts.append(seg)
        else:
            missing.append((f0, f1))
    if missing:
        raise RuntimeError("分块缺口，不许拼接：%s" % missing[:5])
    silent = os.path.join(work, "silent.mp4")
    K.concat_segments(parts, silent, workdir=work)
    got = K.media_dur(silent)
    want = total_frames / float(tl["fps"])
    if abs(got - want) > 0.5:
        raise RuntimeError("拼接后时长 %.2fs != 预期 %.2fs" % (got, want))
    print("   silent.mp4 %.2fs（%.2f 帧当量）" % (got, got * float(tl["fps"])))
    return silent


def _st_frames_web(tl, work, script, jobs=1, resume=True):
    """浏览器逐帧渲染：交给 webengine.render_full（分块并行 + 断点续跑 + 缺口检测）。

    页面的 window.DURATION / window.FPS 是切块与校验的唯一真值来源：
    和 timeline.json 对不上就直接报错（十有八九是改了剧本没重新 layout）。
    """
    we = _opt_module("webengine",
                     "浏览器渲染需要 webengine.py（本技能 scripts/ 里自带；还需要 pip install playwright）")
    entry = _web_entry(script)
    silent = os.path.join(work, "silent.mp4")
    web = script.get("web") or {}

    page_dur, page_fps = we.page_meta(entry)
    if abs(float(page_fps) - float(tl["fps"])) > 1e-6:
        raise RuntimeError(
            "页面 FPS=%s 与 timeline fps=%s 不一致。浏览器逐帧渲染的帧号就是 f/FPS，"
            "两边必须同帧率：把页面里的 window.FPS 改成 %s（或重新 --stage layout 生成"
            "与页面一致的 timeline）。\n  %s"
            % (page_fps, tl["fps"], tl["fps"], _web_where(entry)))
    if abs(float(page_dur) - float(tl["total"])) > 0.05:
        raise RuntimeError(
            "页面 DURATION=%.3fs 与 timeline 总时长 %.3fs 不一致（差 %.3fs）。\n"
            "  页面里的时长必须等于 timeline.total；把页面里的 window.DURATION 改成 %.3f"
            "（跑过新的 --stage layout 之后，取 timeline.json 的 total）。\n  %s"
            % (float(page_dur), float(tl["total"]), abs(float(page_dur) - float(tl["total"])),
               float(tl["total"]), _web_where(entry)))

    out = we.render_full(entry, silent, float(tl["total"]),
                         workers=max(1, int(jobs)),
                         chunk_s=float(web.get("chunk_s", 30.0)),
                         resume=bool(resume),
                         crf=int(script.get("crf", 17)),
                         preset=str(script.get("preset", "slow")))
    got = K.media_dur(str(out) if out else silent)
    want = float(tl["frames"]) / float(tl["fps"])
    if abs(got - want) > 0.5:
        raise RuntimeError("浏览器渲染时长 %.2fs != 预期 %.2fs（检查分块缺口/页面 DURATION）"
                           % (got, want))
    print("   silent.mp4 %.2fs（浏览器逐帧，%s→%s 分块）" % (got, page_dur, page_fps))
    return str(out) if out else silent


# ---------------------------------------------------------------- 合成 / 质检
def st_mux(silent, audio, out, script):
    print("== Mux")
    K.mux(silent, audio, out, crf=int(script.get("crf", 18)),
          preset=script.get("preset", "medium"),
          reencode_video=bool(script.get("reencode_video", False)))
    print("   ->", out)
    return out


def _qa_gates(lg, rep, deep, tl, work):
    """把 qa_report / 深检的结论拆成逐条门禁记进账本（pass / fail / skipped）。

    为什么拆：只有一个"质检通过/失败"的总结论，读报告的人看不出"哪几项其实没执行"。
    这里每条都带实测值，skipped 必须写明"为什么没执行"。
    """
    pr = rep.get("probe") or {}
    streams = pr.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = pr.get("format") or {}
    W, H, FPS = K.W, K.H, K.FPS

    # ---- G4 容器：分辨率 / 帧率 / 音轨 / 时长 ----
    try:
        rate = str(v.get("r_frame_rate") or "")
        num, _, den = rate.partition("/")
        got_fps = (float(num) / float(den or 1)) if num and den else 0.0
    except Exception:                                       # noqa: BLE001
        got_fps = 0.0
    dur = float(fmt.get("duration") or 0)
    want_dur = float(tl["frames"]) / float(tl["fps"])
    detail = "%sx%s@%.3g / %s%s / %.2fs" % (
        v.get("width"), v.get("height"), got_fps,
        ("h264" if v.get("codec_name") == "h264" else str(v.get("codec_name") or "?")),
        ("+aac" if a else "（无音轨）"),
        dur)
    ok4 = (v.get("width") == W and v.get("height") == H
           and abs(got_fps - float(FPS)) <= 0.05 and a is not None
           and abs(dur - want_dur) <= 0.5)
    (lg.ok if ok4 else lg.fail)("G4", "容器层（分辨率/帧率/音轨/时长）", detail)

    # ---- G5 响度 / 真峰 ----
    lo = rep.get("loudness") or {}
    if "I" in lo and "TP" in lo:
        i_l, tp = float(lo["I"]), float(lo["TP"])
        ok5 = (-19 <= i_l <= -12) and tp <= -1.0
        (lg.ok if ok5 else lg.fail)("G5", "响度 / 真峰（-16 LUFS / ≤ -1 dBTP）",
                                    "%.1f LUFS · %.1f dBTP" % (i_l, tp))
    else:
        lg.skip("G5", "响度 / 真峰", "ebur128 没测到 %s" % ("I" if "I" not in lo else "TP"))

    # ---- G6 编码锐度比 ----
    q = rep.get("quality")
    if q and "ratio" in q:
        (lg.ok if q["ratio"] >= 0.85 else lg.fail)(
            "G6", "编码锐度比（文字发虚）", "%.3f（阈值 0.85）" % q["ratio"])
    else:
        why = "没装 cv2" if K._cv2() is None else "没有参考帧（scenes.py 缺失 / web 引擎）"
        lg.skip("G6", "编码锐度比（文字发虚）", why + "，本次没查")

    # ---- G7 字幕条数（静默丢字幕的唯一检查点）----
    srt = os.path.join(str(work), "sub.srt")
    got = 0
    if os.path.exists(srt):
        got = len([b for b in open(srt, encoding="utf-8").read().strip().split("\n\n") if b.strip()])
    want = len(tl["scenes"])
    if got:
        (lg.ok if got == want else lg.fail)("G7", "字幕条数 = 剧本句数",
                                            "%d / %d" % (got, want))
    else:
        lg.skip("G7", "字幕条数 = 剧本句数", "没找到 sub.srt")

    # ---- G8 黑帧 ----
    black_msgs = [w for w in (rep.get("warnings") or []) if "黑帧" in w]
    if black_msgs:
        # qa_report 只在"首尾之外有长黑帧"时写 warning，本身是 warning 级
        lg.skip("G8", "首尾之外无长黑帧", "检测到：" + black_msgs[0])
    else:
        lg.ok("G8", "首尾之外无长黑帧", "0 段")

    # ---- G9 帧级深检（闪烁 / 空窗 / 音画同步）----
    if deep is None:
        lg.skip("G9", "帧级深检（闪烁/空窗/同步）", "没有 qa_frames.py 或深检失败")
    else:
        nf = len(deep.get("flicker") or [])
        nb = len(deep.get("black_windows") or [])
        ns = len(deep.get("sync") or [])
        judged = (deep.get("sync_info") or {}).get("judged")
        sync_note = ("同步查了 %s 条" % judged) if judged is not None else "同步未判（没写 visual_at）"
        ok9 = nf == 0 and nb == 0 and ns == 0
        detail9 = "闪烁 %d · 空窗 %d · 同步超差 %d（%s）" % (nf, nb, ns, sync_note)
        if ok9 and not judged and os.environ.get("QA_REQUIRE_SYNC"):
            # 与 SKILL 的 QA_REQUIRE_SYNC 同义：要求"真的查过"，而不是"没报错"
            lg.fail("G9", "帧级深检（闪烁/空窗/同步超差）",
                    detail9 + "；QA_REQUIRE_SYNC=1 要求同步必须被判过")
        else:
            (lg.ok if ok9 else lg.fail)("G9", "帧级深检（闪烁/空窗/同步超差）", detail9)


def st_qa(film, tl, work, scenes_mod=None, script=None):
    print("== QA")
    th = _theme_for(script) if script is not None else None
    shots, refs = [], []             # refs: [(frac 下标, PIL 原图)]
    fracs = (0.08, 0.3, 0.55, 0.78)
    ref_script = dict(script) if script is not None else None
    if ref_script is not None:
        # 参考帧必须和 frames 阶段用同一份 script（含 _total），否则 HUD 进度条等
        # 依赖 _total 的元素画得不一样，锐度比会把"渲染差异"当成"编码损失"。
        ref_script["_total"] = float(tl["total"])
    for k, frac in enumerate(fracs):
        sec = tl["total"] * frac
        p = os.path.join(work, "qa", "snap_%02d.png" % int(frac * 100))
        K.snap(film, sec, p)
        shots.append(p)
        # 参考帧：用场景函数重新渲染同一时刻，而不是拿成片自己抽的帧
        if scenes_mod is not None and ref_script is not None:
            idx = 0
            for i, s in enumerate(tl["scenes"]):
                if sec >= s["start"]:
                    idx = i
            s = tl["scenes"][idx]
            try:
                fr = scenes_mod.draw_frame(s, sec - s["start"], s["dur"], sec, ref_script,
                                           int(round(sec * tl["fps"])))
                fr = K.draw_subtitle(fr, K.sub_at(tl["scenes"], sec, ref_script.get("sub_max_hold")),
                                     theme=th)
                refs.append((k, fr))
            except Exception as e:      # noqa: BLE001
                print("   [WARN] 参考帧渲染失败:", e)

    # 锐度比只跟"同一时刻"的参考帧比：某几张参考帧渲染失败时，不能拿别的时刻顶替
    ref_img, ref_sec = None, None
    if refs:
        k_ref = min(refs, key=lambda kn: abs(kn[0] - 2))[0]
        ref_img = dict(refs)[k_ref]
        ref_sec = tl["total"] * fracs[k_ref]
        print("   锐度参考帧: 场景函数重渲染 t=%.2fs（第 %d 张）" % (ref_sec, k_ref + 1))
    elif scenes_mod is None:
        print("   [WARN] 没有场景函数参考帧（web 引擎或 scenes.py 缺失），锐度比门禁本次跳过")
    rep = K.qa_report(film, expect_frames=tl["frames"], ref_img=ref_img,
                      ref_sec=ref_sec, expected_subtitles=len(tl["scenes"]),
                      notes=os.path.join(work, "sub.srt"), theme=th)
    pr = rep["probe"]
    f = pr.get("format", {})
    print("   时长 %.2fs  大小 %.1f MB  码率 %.2f Mbps" % (
        float(f.get("duration", 0)), int(f.get("size", 0)) / 1048576,
        int(f.get("bit_rate", 0)) / 1e6))
    print("   响度", rep["loudness"])
    q = rep.get("quality")
    if q:
        print("   锐度比 %.2f（参考帧=场景函数重渲染，不是成片抽帧）" % q["ratio"])
    elif ref_img is None and scenes_mod is None:
        print("   [WARN] 锐度比门禁未执行（没有参考帧）")
    safe = K.qa_text_safe(tl["scenes"])
    print("   字幕安全区:", "OK" if not safe else safe[:2])
    # 字幕安全区 / 缺字形以前只 print，不进 qa_report 的 warnings ——
    # 于是 CI、前端、任何只读 JSON 的人**看不到**这两条（"门禁可以保守，但不能静默"）。
    # 现在它们进 warnings（仍是 warning 级，不阻断，但能被程序读到）。
    for _m in (safe or [])[:4]:
        rep["warnings"].append("字幕安全区: %s" % _m)
    _glyph = K.check_glyphs((script or {}).get("lines") or [])
    for _lid, _fld, _chars in (_glyph or [])[:4]:
        rep["warnings"].append("缺字形 %s.%s: %r（会显示为方块，改用矢量线条画）"
                               % (_lid, _fld, _chars))
    sheet = K.contact_sheet(shots, os.path.join(work, "qa", "sheet.png"))
    if sheet:
        print("   联系表:", sheet, "  <- 必须亲眼看")
    # ---- 帧级深检（v0.3.0）：单帧闪烁 / 画面空窗 / 音画同步 / 响度曲线 ----
    deep = _qa_deep(film, work, tl, script)
    if deep:
        for w in deep.get("warnings") or []:
            print("   [WARN][deep]", w)
        for e in deep.get("errors") or []:
            print("   [FAIL][deep]", e)

    for w in rep["warnings"]:
        print("   [WARN]", w)
    errors = list(rep["errors"])
    if deep:
        errors += list(deep.get("errors") or [])
    if errors:
        print("   [FAIL]")
        for e in errors:
            print("     -", e)
    else:
        print("   [PASS] 质检通过")
    lg = _gate()
    if lg is not None:
        _qa_gates(lg, rep, deep, tl, work)
    return errors


def _qa_deep(film, work, tl, script):
    """调用 qa_frames.py 做帧级/时间轴级深检；没有这个模块就跳过（不阻断）。"""
    qf = _try_module("qa_frames")
    if qf is None:
        print("   [deep] 没有 qa_frames.py，跳过帧级深检")
        return None
    qa_dir = os.path.join(work, "qa")
    os.makedirs(qa_dir, exist_ok=True)
    cues_path = os.path.join(work, "cues.json")
    try:
        deep = qf.report(film, cues_json=cues_path if os.path.exists(cues_path) else None,
                         expect_duration=float(tl["total"]),
                         out_json=os.path.join(qa_dir, "deep.json"),
                         out_png=os.path.join(qa_dir, "deep.png"))
    except Exception as e:                                  # noqa: BLE001
        print("   [WARN][deep] 帧级深检失败（不影响成片）:", e)
        return None
    print("   [deep] 闪烁 %d / 空窗 %d / 同步超差 %d  -> %s"
          % (len(deep.get("flicker") or []), len(deep.get("black_windows") or []),
             len(deep.get("sync") or []), os.path.join(qa_dir, "deep.json")))
    return deep


# ---------------------------------------------------------------- 母版预览
def st_stills(tl, work, scenes_mod, script, specs=None):
    """只渲染若干关键帧 -> out/stills/*.png + 联系表。母版阶段必跑。

    specs: ["l01:3.0", ...] 或 [[id, t_local], ...]；缺省为每段中点各一张。
    """
    print("== Stills")
    script = dict(script)
    script["_total"] = tl["total"]
    script.setdefault("_style", resolve_style(script))
    outdir = os.path.join(work, "stills")
    os.makedirs(outdir, exist_ok=True)
    by_id = {s["id"]: s for s in tl["scenes"]}
    if not specs:
        specs = [[s["id"], s["dur"] * 0.55] for s in tl["scenes"]]
    made = []
    for spec in specs:
        if isinstance(spec, str):
            sid, _, lt = spec.partition(":")
            lt = float(lt or 0.0)
        else:
            sid, lt = spec[0], float(spec[1])
        s = by_id.get(sid)
        if not s:
            print("   [skip] 没有这个场景:", sid)
            continue
        tg = s["start"] + float(lt)
        fr = scenes_mod.draw_frame(s, float(lt), s["dur"], tg, script,
                                   int(round(tg * tl["fps"])))
        if fr is None:
            raise RuntimeError("场景 %s 在 t=%.2fs 返回 None（draw_frame 必须返回 PIL.Image）"
                               % (sid, tg))
        if tuple(fr.size) != (K.W, K.H):
            raise RuntimeError("场景 %s 在 t=%.2fs 返回 %dx%d，期望 %dx%d（stills 与 FrameSink 一样严格）"
                               % (sid, tg, fr.size[0], fr.size[1], K.W, K.H))
        fr = K.draw_subtitle(fr, K.sub_at(tl["scenes"], tg, script.get("sub_max_hold")),
                             theme=_theme_for(script))
        p = os.path.join(outdir, "%s_%05.1f.png" % (sid, float(lt)))
        fr.save(p)
        made.append(p)
    if made:
        cols = 6 if len(made) >= 6 else len(made)
        sheet = K.contact_sheet(made, os.path.join(outdir, "_sheet.png"), cols=cols, thumb_w=420)
        print("   %d 张静帧 -> %s" % (len(made), outdir))
        print("   联系表:", sheet, "  <- 必须亲眼看")
    return made


def _reload_ledger_from_disk(work, ledger):
    """把 work/gates.json 里已有的门禁条目搬回内存账本（accept 这类独立进程用）。

    只搬**内容指纹一致**的条目：指纹不同说明剧本/场景/引擎已经变过，那些结论属于
    上一版内容，搬进来等于拿旧结论冒充本版（比丢条目更糟）。搬不回来时明确说一句，
    让用户知道报告里的门禁矩阵为什么是空的（该重跑 --stage all）。
    """
    p = os.path.join(str(work), "gates.json")
    if not os.path.exists(p):
        return 0
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print("   [WARN] 旧账本读不了（%s）：报告里只会有本次的条目" % e)
        return 0
    if not isinstance(d, dict):
        return 0
    if str(d.get("contentHash") or "") != str(getattr(ledger, "content_hash", "") or ""):
        print("   [WARN] 旧账本的内容指纹与当前不一致（剧本/场景/引擎改过），"
              "不搬旧门禁结论：交付前请重跑 --stage qa")
        return 0
    n = 0
    for it in d.get("items") or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        try:
            ledger.add(str(it["id"]), str(it.get("title") or ""),
                       str(it.get("status") or ""), str(it.get("detail") or ""),
                       at=it.get("at"))
            n += 1
        except (ValueError, TypeError) as e:
            print("   [WARN] 旧账本条目 %r 搬不回来：%s" % (it.get("id"), e))
    for b in d.get("bypassed") or []:
        if isinstance(b, dict) and b.get("what"):
            ledger.bypassed.append(dict(what=str(b["what"]), why=str(b.get("why") or ""),
                                        at=float(b.get("at") or time.time())))
    return n


def _report_path(a):
    """QC 报告的唯一落点：**站在成片旁边**（--out 的目录），不猜 work 的兄弟目录。

    v0.7.2 修：accept 分支以前用 dirname(abspath(work))/out/QC-REPORT.md，而 all/mux/qa
    用 dirname(abspath(out))。--out 指到任何非 out/ 的目录（deliver/、中文工作区/成品/…）
    时，签核更新的是另一份没人交付的文件，交付出去的那份仍是"未签核"——正好打穿
    "报告是交付凭据"这条承诺。两处必须走同一个函数。
    """
    return os.path.join(os.path.dirname(os.path.abspath(str(a.out))), "QC-REPORT.md")


# 会写交付账本（work/gates.json + QC-REPORT.md）的阶段。中间产物阶段
# （tts/layout/cues/score/sfx/audio）只做自己的事，不去覆盖上一轮完整跑出来的
# 交付报告——否则 `--stage layout` 一次就会把「全片通过」的报告改写成「未判定」。
_PERSIST_STAGES = ("style", "frames", "mux", "qa", "stills", "all", "accept")


def _finish_gates(a, persist=True):
    """一次运行收尾：落 gates.json + 写 QC-REPORT.md（在成片旁边）+ 统一退出码。

    persist=False：只打印本次门禁汇总与退出码，不动磁盘上的交付账本（中间阶段用）。
    """
    lg = _LEDGER
    if lg is None:
        return 0
    errs = lg.exit_errors(strict=bool(getattr(a, "strict_gates", False)))
    gj = rep = ""
    if persist:
        # 交付账本只在交付阶段写；中间阶段（layout 等）写一次就会把上一轮
        # 「pass 8 / skipped 1」的完整报告换成「只有本阶段一条」的半成品。
        try:
            gj = lg.write_json()
        except Exception as e:                              # noqa: BLE001
            print("[WARN] gates.json 写入失败:", e)
        try:
            rep = GT.write_report(a.work, _report_path(a))
        except Exception as e:                              # noqa: BLE001
            print("[WARN] QC 报告写入失败:", e)
    c = lg.counts()
    print("")
    print("== 门禁汇总：pass %d · fail %d · skipped %d%s"
          % (c[GT.OK], c[GT.FAIL], c[GT.SKIP],
             "（--strict-gates：skipped 也算失败）" if getattr(a, "strict_gates", False) else ""))
    if gj:
        print("   账本:", gj)
    if rep:
        print("   报告:", rep, " <- 交付时把这个文件一起给出")
    if c[GT.SKIP]:
        print("   注意：有 %d 项本次**没有执行**（skipped != 通过）" % c[GT.SKIP])
    if errs:
        print("   [FAIL]")
        for e in errs:
            print("     -", e)
        return 1
    return 0


# ---------------------------------------------------------------- main
def main():
    _setup_stdio()
    ap = argparse.ArgumentParser(description="video-studio 编排器")
    ap.add_argument("--script", default="film.json")
    ap.add_argument("--scenes", default="scenes.py")
    ap.add_argument("--out", default="out/film.mp4")
    ap.add_argument("--work", default="work")
    ap.add_argument("--stage", default="all", choices=STAGES)
    ap.add_argument("--engine", default="", choices=("", "pil", "web"),
                    help="渲染引擎：pil=逐帧 Pillow（默认）；web=无头浏览器（需 webengine.py + 页面）")
    ap.add_argument("--jobs", type=int, default=1, help="分块并行渲染进程数（frames 阶段）")
    ap.add_argument("--resume", action="store_true", help="复用已存在的分块")
    ap.add_argument("--force-tts", action="store_true", help="丢弃 TTS 缓存重新合成")
    ap.add_argument("--force-music", action="store_true", help="丢弃配乐重出（score 阶段）")
    ap.add_argument("--stills", default="", help="stills 阶段用：逗号分隔的 id:秒")
    ap.add_argument("--gate", default="", choices=("",) + GT.GATE_ORDER,
                    help="accept 阶段用：要签核哪一道闸（%s）" % " / ".join(GT.GATE_ORDER))
    ap.add_argument("--verdict", default="approved", choices=("approved", "rejected"),
                    help="accept 阶段用：签核结论（rejected 视为没签）")
    ap.add_argument("--note", default="", help="accept 阶段用：签核备注（写进报告）")
    ap.add_argument("--by", default="", help="accept 阶段用：签核人（缺省 user）")
    ap.add_argument("--strict-gates", action="store_true",
                    help="把 skipped 门禁也算失败（对齐 QA_REQUIRE_SYNC 语义）")
    ap.add_argument("--strict-stale", action="store_true",
                    help="上游产物过时算失败（默认只告警）")
    ap.add_argument("--allow-stale", action="store_true",
                    help="过时也照跑（会在 QC 报告里留痕）")
    ap.add_argument("--no-gate", action="store_true",
                    help="跳过人工签核检查（会在 QC 报告里留痕，不算通过）")
    ap.add_argument("--cost", action="store_true",
                    help="每个 stage 往 work/cost.jsonl 记一行实测耗时")
    a = ap.parse_args()

    # work/ 本来就不存在 = 这一跑自己产出全部中间物，别把"我还没跑到"当成"产物过时"。
    work_pre_exists = os.path.isdir(a.work)
    try:
        os.makedirs(a.work, exist_ok=True)
    except OSError as e:
        raise SystemExit("[工作目录不可用] %s：%s（--work 指到一个能写的目录）" % (a.work, e)) from e
    script = load_script(a.script)
    engine = _engine(script, a.engine or None)
    if engine not in ("pil", "web"):
        raise SystemExit("engine 只能是 pil 或 web，收到 %r" % engine)
    script["_engine"] = engine
    script["_scenes"] = str(Path(a.scenes).resolve())

    # ---- 门禁账本（v0.6.0）----
    global _LEDGER
    # engine=web 时把入口页实际引用的同目录页面脚本一起纳入内容指纹：web 的画面逻辑在
    # web/*.js 里，不在 scenes.py 里——不纳入的话改动画后旧签核仍然"有效"，正好违背
    # v0.6「签核绑内容指纹，改了画面代码旧签核自动失效」的承诺。
    web_entry = ""
    if engine == "web":
        try:
            web_entry = _web_entry(script)   # 入口页不存在时这里会 SystemExit（本来就该早失败）
        except SystemExit:
            web_entry = ""                   # 页面还没建：交给后面的 stage 报错，账本先照建
    _LEDGER = GT.GateLedger(a.work, script, scenes_path=a.scenes,
                            script_path=a.script, engine=engine,
                            web_entry=web_entry)
    if a.no_gate:
        _LEDGER.bypass("人工签核检查（--no-gate）", "本条不算通过，只记录")
        print("[gate] --no-gate：跳过人工签核检查（会在 QC 报告里留痕）")

    def _sources():
        """源头输入：剧本与 scenes.py（改它们等于改了所有下游的依据）。"""
        return {"script": a.script, "scenes": a.scenes}

    # 本次命令行**计划要跑**的 stage 集合：--stage all 展开成 10 个，单 stage 就是它自己。
    # 用途见 stagewatch.check 的 pending：本阶段马上要用新输入重算，那种"输入变了"不是过时。
    _ALL_ORDER = ("style", "tts", "layout", "cues", "score", "sfx", "audio",
                  "frames", "mux", "qa")

    def _planned(stage_name):
        if stage_name == "all":
            return list(_ALL_ORDER)
        return [stage_name] if stage_name in STAGES else []

    def _stage_begin(name):
        """stage 开始时查新鲜度并计时。返回 t0。

        report() 自己负责打印 "[stale] <stage>: <原因>" 与"最小重跑"两行
        （v0.7.2 修：以前只调不接，用户只看到"最小重跑"不知道是哪个阶段、
        为什么过时，得上 --stage explain 才知道）。
        --allow-stale 也要调用：它承诺的是"放行并留痕"（QC 报告里记一条 skipped），
        直接跳过检查连痕都没有——那就成了静默。
        """
        if name:
            errs, _warns = SW.report(a.work, [name], strict=a.strict_stale,
                                      allow_stale=a.allow_stale,
                                      ledger=_LEDGER, sources=_sources(),
                                      self_built=not work_pre_exists,
                                      pending=_planned(stage))
            if errs:
                # --strict-stale 的承诺是"上游产物过时算失败"。stagewatch.report 只把
                # 问题放进返回值（并往账本记一条 fail），它自己不抛异常——所以必须在这里
                # 真的失败。以前丢弃了返回值，于是 --stage layout --strict-stale 报完过时
                # 照跑、退出码 0，help 文本与行为对不上。
                #
                # 选择"立刻退出"而不是"跑完再在收尾处报"：过时的输入会把旧产物掺进
                # 新结果（比如拿旧 cues.json 渲新时间轴的画面），跑完再报既已浪费渲染
                # 时间，产出的东西也不能用。--stage all 同理：一发现就停。
                # 不写 work/gates.json：那是**交付账本**（上一轮 --stage all 的完整
                # 结论）。中间阶段一次失败就把它覆盖成单条 fail，用户手里那份
                # QC-REPORT.md 会变成「未判定」，比留证据代价更大。失败证据在
                # stdout 与退出码里，够定位。
                # 原因行 report() 已经逐条打过了，这里只补"为什么停下 + 怎么办"，
                # 不再把 errs 原样贴一遍。
                raise SystemExit(
                    "[stale][strict] 上面 %d 处上游产物过时 -> 本阶段不跑，直接失败。\n"
                    "  （--strict-stale 下过时=失败；继续跑会把旧产物掺进新结果，"
                    "白渲一遍还是错的。）\n"
                    "  怎么办：\n"
                    "    1) 照着每条下面的『最小重跑』先补上游，再重跑当前阶段\n"
                    "    2) 确认旧产物可以接受：加 --allow-stale 显式放行"
                    "（退出码 0，QC 报告里记 skipped）\n"
                    "    3) 只想看现状不跑：python render.py --stage explain"
                    % len(errs))
        return time.time()

    def _stage_end(name, t0, **extra):
        """stage 结束时落盘：manifest 快照 + 耗时台账。"""
        if not name:
            return
        try:
            SW.snapshot(a.work, name, _sources())
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] manifest 快照失败:", e)
        wall = time.time() - t0
        if a.cost:
            try:
                GT.append_cost(a.work, name, wall, **extra)
            except Exception as e:                          # noqa: BLE001
                print("   [WARN] cost.jsonl 写入失败:", e)
        print("   [time] %s 用时 %.1fs%s" % (name, wall, "（已记 work/cost.jsonl）" if a.cost else ""))

    def _check_signoff(where):
        """交付前的签核检查：缺签只告警，--strict-gates 下算失败。"""
        if a.no_gate:
            return
        state, stale = GT.signoff_state(a.work, _LEDGER.content_hash)
        bad = [g for g, v in state.items() if v["status"] == GT.SKIP]
        rejected = [g for g, v in state.items() if v["status"] == GT.FAIL]
        for g in rejected:
            _LEDGER.fail("signoff:" + g, "人工签核：" + GT.GATES[g], state[g]["detail"])
        if bad:
            _LEDGER.skip("signoff", "人工签核（--stage accept 签核）",
                         "未签/已失效：%s%s"
                         % ("、".join(bad), ("（内容已变：%s）" % "、".join(stale)) if stale else ""))
            print("   [WARN] 人工签核未完成：%s" % "、".join(bad))
            print("          交付前请 --stage accept --gate <名字>；"
                  "--strict-gates 会把它算作失败")
            for g in bad:
                print("              %-8s %s" % (g, GT.GATES[g]))
        elif not rejected:
            _LEDGER.ok("signoff", "人工签核", "%d/4 道闸已签" % len(GT.GATE_ORDER))
    if a.force_music:
        plan, _why = K.music_plan(script, has_music_file=os.path.exists(os.path.join(a.work, "music.wav")))
        if plan in ("off", "bed", "file"):
            print("[force-music] %s：不清 work/music.wav（那是外部配乐 / 本片不用配乐）" % plan)
        else:
            for name in ("music.wav", "score.json"):
                p = os.path.join(a.work, name)
                if os.path.exists(p):
                    os.remove(p)
    tl_p = os.path.join(a.work, "timeline.json")
    tts_p = os.path.join(a.work, "tts.json")

    def ensure_tl():
        if not os.path.exists(tl_p):
            raise SystemExit("[前置] 没有 %s，先跑 --stage layout" % tl_p)
        try:
            return json.load(open(tl_p, encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit("[前置] %s 不是合法 JSON（%s），重新跑 --stage layout" % (tl_p, e)) from e

    def load_scenes(strict=True):
        """加载场景模块：strict=False 时失败只 WARN（qa 缺参考帧也要能跑）。"""
        try:
            mod = load_module(a.scenes)
        except Exception as e:                              # noqa: BLE001
            if strict:
                raise SystemExit("[场景模块加载失败] %s：%s\n"
                                 "  --scenes 要指向定义 draw_frame() 的 scenes.py"
                                 % (a.scenes, e)) from e
            print("   [WARN] 场景模块加载失败（%s），跳过场景函数参考帧: %s" % (a.scenes, e))
            return None
        if not hasattr(mod, "draw_frame"):
            if strict:
                raise SystemExit("[场景模块错误] %s 没有 draw_frame(scene, t_local, dur, t_global, script, frame)"
                                 % a.scenes)
            print("   [WARN] %s 没有 draw_frame()，跳过场景函数参考帧" % a.scenes)
            return None
        return mod

    stage = a.stage

    # ---- explain：只报"哪些阶段过时了"，不跑任何 stage ----
    if stage == "explain":
        rows = SW.explain(a.work, sources=_sources())
        bad = 0
        for r in rows:
            if r["issues"]:
                bad += 1
            print("%s%-8s %s" % ("!! " if r["issues"] else "OK ", r["stage"],
                                 r["at"] or "（无记录）"))
            for it in r["issues"]:
                print("     -", it["reason"])
                if it.get("rerun"):
                    print("       最小重跑:", it["rerun"])
        print()
        if bad:
            print("%d 个阶段过时。默认只告警；--strict-stale 让它失败。" % bad)
            return 1 if a.strict_stale else 0
        print("没有发现过时的阶段（按 work/manifest.json 判断）。")
        return 0

    # ---- accept：人工签核一道闸（写 work/signoff.json，绑内容指纹）----
    if stage == "accept":
        if not a.gate:
            raise SystemExit("[accept] 必须指定要签哪一道闸：--gate %s"
                             % " / ".join(GT.GATE_ORDER))
        if a.verdict == "approved":
            # 签核前必须能证明"真的看过了"：证据产物不在就拒绝签（不然签核是橡皮图章）。
            # 证据可能在 work/（渲染产物），也可能在项目根（FACTS.md 是人写的输入），两处都找。
            proj = os.path.dirname(os.path.abspath(a.script))
            want = {"style": [os.path.join(a.work, "style.json"),
                              os.path.join(a.work, "voice_plan.json")],
                    "stills": [os.path.join(a.work, "qa", "sheet.png")],
                    "facts": [os.path.join(proj, "FACTS.md"), os.path.join(a.work, "FACTS.md")],
                    "release": [a.out]}.get(a.gate, [])
            # 每一组是"多选一"：任一条路径存在就算有证据。
            missing = None
            if want and not any(os.path.exists(str(w)) for w in want):
                missing = "、".join(os.path.relpath(w, proj) if os.path.isabs(w) else w
                                   for w in want)
            if missing:
                raise SystemExit("[accept] 证据不存在，拒绝签核：先跑出要看的东西（缺 %s）"
                                 % missing)
        rec = GT.write_signoff(a.work, a.gate, _LEDGER.content_hash,
                               verdict=a.verdict, by=a.by or "user",
                               note=a.note,
                               artifacts=[os.path.join(a.work, "gates.json")])
        print("[accept] %s = %s（内容指纹 %s，%s）"
              % (a.gate, rec["verdict"], _LEDGER.content_hash, rec["atText"]))
        if a.verdict == "rejected":
            print("[accept] 已记「否决」（不算签核通过）；改了内容后需重新签。")
        # accept 是**独立进程**：本次账本是空的。直接 write_json 会把上一次 all/mux/qa
        # 记的 8 条门禁结论整个覆盖掉（报告变成 "pass 0 · skipped 1"，交付凭据被打穿）。
        # 先把磁盘上的旧账本搬回来（内容指纹一致才搬），再追加签核状态。
        _reload_ledger_from_disk(a.work, _LEDGER)
        # 签核状态进账本：否则报告里的"人工签核"一行仍是上一次 all/mux 收尾时记的
        # skipped，用户签完再打开报告却看不到"已签"——签核就不算兑现。
        try:
            _check_signoff(stage)
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] 签核状态没记进账本:", e)
        try:
            _LEDGER.write_json()
        except Exception as e:                              # noqa: BLE001
            print("   [WARN] gates.json 写入失败:", e)
        p_rep = GT.write_report(a.work, _report_path(a))
        print("[accept] QC 报告已更新:", p_rep)
        return 0

    if stage in ("all", "style"):
        # --stage all 也要把 style 这一行记进 manifest：以前 _nm 在 all 下是 None，
        # 而 _stage_begin/_stage_end 对 None 是空操作 -> work/style.json 写出来了、
        # manifest.json 却从来没有 style 记录。于是之后任何一次 `--stage style` 都会
        # 命中「产物在但没有记录」的判据，报成 no-manifest（--stage all 的用户 100% 踩到）。
        _t0 = _stage_begin("style")
        resolve_style(script, a.work)
        _stage_end("style", _t0, n_tts=None)
        if stage == "style":
            print("== Style")
            _print_style(script["_style"])
            _need(os.path.exists(os.path.join(a.work, "style.json")), "style.json 没写出来")
            _LEDGER.ok("style", "风格包解析", str(script["_style"].get("style")))
            _LEDGER.write_json()
            _finish_gates(a)
            return 0

    if stage in ("all", "tts"):
        _t0 = _stage_begin("tts")
        lines = st_tts(script, a.work, force=a.force_tts)
        _stage_end("tts", _t0, n_tts=len(lines) if isinstance(lines, list) else None)
        # 既有行为：单跑 tts 也会排一次轴（tts.json 的时长信息要用），这里一并计时。
        _t0 = _stage_begin("layout")
        tl = st_layout(script, a.work, lines)
        _stage_end("layout", _t0)
    if stage == "layout":
        _t0 = _stage_begin("layout")
        _need(os.path.exists(tts_p), "没有 tts.json，先跑 --stage tts")
        tl = st_layout(script, a.work)
        _stage_end("layout", _t0)
    if stage in ("all", "cues"):
        tl = tl if stage == "all" else ensure_tl()
        _t0 = _stage_begin("cues")
        cues = st_cues(script, a.work, tl, engine=engine)
        _stage_end("cues", _t0,
                   n_cues=(len((cues or {}).get("cues") or []) if isinstance(cues, dict) else None))
    if stage in ("all", "score"):
        tl = tl if stage == "all" else ensure_tl()
        _t0 = _stage_begin("score")
        st_score(script, a.work, tl)
        _stage_end("score", _t0)
    if stage in ("all", "sfx"):
        tl = tl if stage == "all" else ensure_tl()
        _t0 = _stage_begin("sfx")
        st_sfx(script, a.work, tl)
        _stage_end("sfx", _t0)
    if stage in ("all", "audio"):
        tl = tl if stage == "all" else ensure_tl()
        _need(all(s.get("path") for s in tl["scenes"]), "timeline 里没有旁白路径，先跑 --stage tts")
        missing = [s.get("id") for s in tl["scenes"] if not os.path.exists(str(s.get("path") or ""))]
        _need(not missing, "旁白文件不存在：%s（tts 目录被删/挪过？重新跑 --stage tts）"
              % (missing[:6],))
        _t0 = _stage_begin("audio")
        st_audio(tl, a.work, script)
        _stage_end("audio", _t0)

    if stage == "stills":
        # 母版预览是"上手第一件事"，可它需要 timeline。以前干净目录直接 SystemExit，
        # 而文档三步上手就把它排在第 4 步（照抄必失败）。这里自动补前置：
        # 缺 tts.json 就真合成一次旁白（要联网），缺 timeline 就排一次轴——
        # 但每一步都**明说**自己在补什么，不偷偷跑。
        # 自动补的这几步也要 _stage_begin/_stage_end：否则 manifest 里只有 stills 一条记录，
        # 下次 --stage tts 会看到 tts.json 在、账本里没有 tts 记录 -> 误报「接手旧 work」。
        if not os.path.exists(tts_p):
            print("[stills] 没有 tts.json，自动先跑 --stage tts（需要联网合成旁白）")
            _t0 = _stage_begin("tts")
            lines = st_tts(script, a.work, force=a.force_tts)
            _stage_end("tts", _t0, n_tts=len(lines) if isinstance(lines, list) else None)
            _t0 = _stage_begin("layout")
            tl_tmp = st_layout(script, a.work, lines)
            _stage_end("layout", _t0)
            tl = tl_tmp
        elif not os.path.exists(tl_p):
            print("[stills] 没有 timeline.json，自动先跑 --stage layout")
            _t0 = _stage_begin("layout")
            tl = st_layout(script, a.work)
            _stage_end("layout", _t0)
        else:
            tl = ensure_tl()
        mod = load_scenes()
        specs = [x.strip() for x in a.stills.split(",") if x.strip()] if a.stills else None
        _t0 = _stage_begin("stills")
        made = st_stills(tl, a.work, mod, script, specs)
        _stage_end("stills", _t0)
        _LEDGER.add("stills", "母版静帧已产出", GT.OK if made else GT.FAIL,
                    "%d 张（要人看过才算过：--stage accept --gate stills）" % len(made or []))
        _finish_gates(a)
        return 0

    if stage == "qa":
        # qa 不重渲、也不重新合成：缺成片就直接报错；缺 scenes.py 只丢参考帧、不阻断质检。
        tl = ensure_tl()
        _need(os.path.exists(a.out), "没有成片 %s，先跑 --stage mux" % a.out)
        mod = load_scenes(strict=False) if engine == "pil" else None
        _t0 = _stage_begin("qa")
        errs = st_qa(a.out, tl, a.work, scenes_mod=mod, script=script)
        _stage_end("qa", _t0)
        _check_signoff("qa")
        rc = _finish_gates(a)
        return 1 if (errs or rc) else 0

    if stage in ("all", "frames", "mux"):
        tl = tl if stage == "all" else ensure_tl()
        audio = os.path.join(a.work, "audio.wav")
        silent = os.path.join(a.work, "silent.mp4")
        mod = None
        if stage in ("all", "frames"):
            # 渲染帧本身不需要音频（silent.mp4 是无声的）。只有 --stage all 才要求
            # audio.wav 已经在前面阶段生成——否则 --stage frames 在空 work 目录会白渲全片再挂掉。
            if stage == "all":
                _need(os.path.exists(audio), "混音阶段异常：audio.wav 未生成")
            if engine == "pil":
                mod = load_scenes()
            _t0 = _stage_begin("frames")
            silent = st_frames(tl, a.work, mod, script, jobs=a.jobs, resume=a.resume,
                               engine=engine)
            _stage_end("frames", _t0, frames=int(tl["frames"]))
            if stage == "frames":
                _LEDGER.ok("frames", "逐帧渲染", "%d 帧（%s）" % (int(tl["frames"]), engine))
                _finish_gates(a)
                return 0 if not _LEDGER.fails() else 1
        if stage in ("all", "mux"):
            # mux 只需要 silent.mp4 + audio.wav：不加载 scenes.py（缺场景模块也能合成）
            _need(os.path.exists(silent), "没有 silent.mp4，先跑 --stage frames")
            _need(os.path.exists(audio), "没有 audio.wav，先跑 --stage audio")
            _t0 = _stage_begin("mux")
            film = st_mux(silent, audio, a.out, script)
            _stage_end("mux", _t0)
            _check_signoff("mux")
            if stage == "mux":
                _LEDGER.ok("mux", "合成（视频流 copy + loudnorm）", a.out)
                _finish_gates(a)
                return 0 if not _LEDGER.fails() else 1
            if stage == "all":
                _t0 = _stage_begin("qa")
                errs = st_qa(film, tl, a.work, scenes_mod=mod, script=script)
                _stage_end("qa", _t0)
                rc = _finish_gates(a)
                if errs or rc:
                    print("\n[质检未过] 成片已生成但没有通过门禁，按上面 [FAIL] 逐条修。")
                    return 1
                return 0
    # 所有 stage 统一收尾：落 gates.json + QC-REPORT.md，并按门禁结果给退出码。
    # v0.7.2 修：以前只有 style/frames/mux/qa 这几条分支自己调 _finish_gates，其余
    # （tts/layout/cues/score/sfx/audio/stills）落在这行 return 0——于是 ledger 里
    # 记的 fail 传不到退出码，"门禁失败 -> 非 0"这个承诺按 stage 不同行为不一样。
    # 默认路径不受影响：没有 fail（且没开 --strict-gates）时 _finish_gates 返回 0。
    rc = _finish_gates(a, persist=(stage in _PERSIST_STAGES))
    return 1 if (rc or _LEDGER.fails()) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl-C：给「能不能续跑」的明确答复，而不是甩 traceback 或静默退出 130。
        # 分块渲染先写 _part_ 再改名，所以中断不会留下被 --resume 误认的半截分块；
        # 已完成的段下次加 --resume 直接复用，残留的 _part_ 会被自动清掉。
        _setup_stdio()
        print("\n[中断] 收到 Ctrl-C，本次运行已停止。", file=sys.stderr)
        print("  已完成的产物都留在 work/ 里：分块渲染请加 --resume 续跑"
              "（半成品段不会被当成完成段复用）。", file=sys.stderr)
        sys.exit(130)
    except RuntimeError as e:
        # 领域错误（音视频时长不符 / 分块缺口 / 编码失败 / TTS 重试耗尽 …）给一句话，
        # 不要甩 Python traceback；要完整栈就 set RENDER_DEBUG=1。
        if os.environ.get("RENDER_DEBUG"):
            raise
        _setup_stdio()
        print("\n[ERROR] %s" % e, file=sys.stderr)
        print("  （要看完整 traceback：set RENDER_DEBUG=1 后重跑）", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        if os.environ.get("RENDER_DEBUG"):
            raise
        _setup_stdio()
        print("\n[ERROR] 文件/目录操作失败：%s" % e, file=sys.stderr)
        print("  （要看完整 traceback：set RENDER_DEBUG=1 后重跑）", file=sys.stderr)
        sys.exit(1)
