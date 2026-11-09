# -*- coding: utf-8 -*-
"""estimate.py — 成本/时长预演：跑 --stage all 之前，先算"要渲多久、占多少盘、怎么分块"。

用法
----
    python tools/estimate.py --script film.json [--work work] [--jobs 4]
                             [--engine pil|web] [--rerender 1.4] [--json]

做四件事
--------
  1. 帧数 / 时长 / fps / 分辨率（分辨率 1920x1080 在本项目写死，见 filmkit.W/H）
  2. 各阶段耗时：tts / layout / cues / score / sfx / audio / frames / mux / qa
  3. 磁盘：work 下 raw 管道峰值 + 成片体积估算
  4. 分块建议（与 render.py 的 chunk_frames 规则逐字一致）+ 最大瓶颈项

输入口径
--------
  有 <work>/timeline.json（已排过轴）→ 用真实 total/frames，输出里注明"真实帧数"；
  没有 → 按 lines 文本"等效字数 ÷ 4.2 字/秒 + lead/tail + hold"粗估，
  并在输出里**明确标注这是估算**（排过轴再跑一次就会换成真实数字）。

退出码
------
  0 正常；2 剧本不存在 / 不是合法 JSON（一句话错误，不甩 traceback）。
  --json 时 stdout 只有 JSON（键名全英文），可直接 json.loads；警告走 stderr。

基准来源
--------
  所有速度常量集中在下面"基准常量"一节，出处是 skills/video-studio/SKILL.md §12
  （作者机器 2026-11-07 实测：Ryzen AI 9 HX 370 / 12C24T / 无 NVIDIA）。
  换机后先重测再采信——这些数字决定预演准不准，别凭感觉调。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from pathlib import Path

# ============================================================================
# 基准常量（唯一出处：SKILL.md §12 本机实测；换机请重测）
# ============================================================================

W, H, FPS = 1920, 1080, 30          # filmkit.W/H/FPS：本项目写死，film.json 改不了
PIL_FPS = (40.0, 80.0)              # PIL 逐帧：中等复杂度 40–54 fps / 模板片 68–82 fps
WEB_SEC_PER_FRAME = (0.080, 0.130)  # web 引擎：Edge 无头 PNG 截图 80–130 ms/帧
WEB_STARTUP_SEC = 6.0               # 浏览器冷启动 + CDP 连接（12 s 片 28–30 s 含启动）
X264_FPS = 141.0                    # libx264 -preset medium -crf 17 ≈ 4.7× 实时
TTS_SEC_PER_LINE = 4.0              # edge-tts：单句 4 s 语音约 4 s 合成（联网）
SCORE_REFS = ((60.0, 2.0, 4.0), (389.0, 4.0, 8.0))  # (片长 s, 耗时低, 耗时高)：两个实测点
SFX_SEC_PER_CUE = 0.20              # 单条短音效 合成+落位（24 配方单次 ≤1.5 s 是上限口径）
SFX_MIN_SEC = 0.5
LAYOUT_BASE, LAYOUT_PER_LINE = 0.4, 0.02   # 排轴 + srt/ass + 安全区/字形自查
CUES_BASE, CUES_PER_LINE = 0.2, 0.02       # 提示点落盘 + 排序
AUDIO_BASE, AUDIO_PER_SEC = 0.6, 0.012     # numpy 总线按采样点叠加 + 写 wav
QA_SEC_PER_SEC = 1.0 / 30.0                # 30 s 片全量深检 ≈ 1 s（160×90 流式解码）
QA_MIN_SEC = 0.8
CPS = 4.2                            # 每秒约 4.2 字（voicekit 校准 3.84–4.61 cps 的中位口径）
DEFAULT_LEAD, DEFAULT_TAIL, DEFAULT_MIN_DUR = 0.62, 0.92, 3.0  # v0.5 body 基准
CORES = 12                           # HX 370：12C/24T；jobs > 12 不再线性加速
TTS_BYTES_PER_LINE = 32 * 1024       # 单句 mp3 缓存（4 s 语音约 24 KB，留余量）
WAV_BYTES_PER_SEC = 48000 * 2 * 2    # 48 kHz 立体声 PCM16 = 192 KB/s
VIDEO_MBPS = (1.2, 3.0)              # crf17 @1080p30 图文内容（暗底大字 0.5–1 Mbps 属正常）
AUDIO_MBPS = 0.192                   # AAC 192 kbps
QA_DISK_BYTES = 12 * 1024 * 1024     # 联系表 + 静帧 + deep.png
BASELINE_NOTE = ("区间来自作者机器实测（SKILL.md §12：Ryzen AI 9 HX 370 / 12C24T / "
                 "Radeon 890M 无 CUDA）；换机请先重测再采信")

STAGE_LABELS = {
    "tts": "旁白 edge-tts",
    "layout": "排时间轴",
    "cues": "画面提示点",
    "score": "程序化配乐",
    "sfx": "音效合成",
    "audio": "三总线混音",
    "frames": "逐帧渲染+编码",
    "mux": "合成成片",
    "qa": "质检+抽帧",
}


# ============================================================================
# 小工具
# ============================================================================

def _setup_stdio():
    """输出被管道/文件重定向时把 stdout/stderr 切到 UTF-8（与 render.py 同一口径）。

    Windows 下 python tools/estimate.py ... | tee log 的 stdout 默认是 GBK 代码页，
    中文会乱码；真实控制台保持系统编码，交互运行时中文照样正常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


def _die(msg, as_json=False):
    """一句话错误 + 退出码 2（输入/用法错误，不是片子坏了）。"""
    if as_json:
        sys.stdout.write(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) + "\n")
    else:
        sys.stderr.write("[estimate] %s\n" % msg)
    raise SystemExit(2)


def _warn(msg):
    """预演不阻断：警告一律走 stderr，--json 时 stdout 仍然只有 JSON。"""
    sys.stderr.write("[estimate][WARN] %s\n" % msg)


def load_script(path, as_json=False):
    """读剧本；坏输入给一句话错误（退出码 2），不甩 traceback。"""
    p = Path(path).expanduser()
    if not p.exists():
        _die("找不到剧本 %s（--script 指到 film.json；模板在 skills/video-studio/assets/film.json）"
             % p, as_json)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        _die("读不了剧本 %s：%s" % (p, e), as_json)
    except UnicodeDecodeError as e:
        _die("%s 不是 UTF-8 文本（%s）：用 UTF-8 另存 film.json" % (p, e), as_json)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _die("%s 不是合法 JSON：第 %d 行第 %d 列 %s（先跑 python -m json.tool 看一眼）"
             % (p, e.lineno, e.colno, e.msg), as_json)
    if not isinstance(data, dict):
        _die("%s 顶层必须是 JSON 对象（当前是 %s）" % (p, type(data).__name__), as_json)
    return p, data


def _num(v, default=0.0):
    """宽容取数：film.json 里写成字符串/None 时退回默认值（估算不该被脏字段炸掉）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _weighted_chars(text):
    """等效字数：汉字 1；英文/数字 0.6；标点 0.3（停顿）；空格 0。"""
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


def _dw(s):
    """显示宽度（中文按 2 列算），只用于表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def _pad(s, width):
    s = str(s)
    return s + " " * max(0, width - _dw(s))


def _fmt_bytes(n):
    """十进制单位（1 GB = 10^9 B）：和 SKILL.md §12 的口径一致（1080p30 的 30 s ≈5.6 GB）。"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000.0 or unit == "TB":
            if unit == "B":
                return "%.0f B" % n
            return "%.1f %s" % (n, unit)
        n /= 1000.0


def _fmt_span(lo, hi):
    if abs(float(hi) - float(lo)) < 0.05:
        return "%.1f s" % float(lo)
    return "%.1f–%.1f s" % (float(lo), float(hi))


# ============================================================================
# 估算核心
# ============================================================================

def estimate_timeline(script):
    """没有 timeline.json 时：按"等效字数 ÷ 4.2 字/秒 + lead/tail + hold"粗估。

    与 filmkit.build_timeline 的公式同构：dur = max(min_dur, lead + voice + tail + hold)。
    只能说"量级对"，误差通常 ±10–20%——这正是输出里必须标注"估算"的原因。
    """
    lines = [ln for ln in (script.get("lines") or []) if isinstance(ln, dict)]
    lead = _num(script.get("lead", DEFAULT_LEAD), DEFAULT_LEAD)
    tail = _num(script.get("tail", DEFAULT_TAIL), DEFAULT_TAIL)
    min_dur = _num(script.get("min_dur", DEFAULT_MIN_DUR), DEFAULT_MIN_DUR)
    ghold = _num(script.get("hold", 0.0))
    scenes = []
    for ln in lines:
        voice = _weighted_chars(ln.get("text")) / CPS
        hold = _num(ln.get("hold", ghold))
        scenes.append(dict(id=str(ln.get("id") or ""), voice=voice, hold=hold,
                           dur=max(min_dur, lead + voice + tail + hold)))
    return sum(s["dur"] for s in scenes), scenes


def read_timeline(work):
    """读 <work>/timeline.json（已排轴的真实帧数）。缺失/坏文件返回 (None, 路径)。"""
    p = Path(work) / "timeline.json"
    if not p.exists():
        return None, p
    try:
        tl = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        _warn("忽略 %s（读不了：%s），退回剧本粗估" % (p, e))
        return None, p
    if not isinstance(tl, dict) or "total" not in tl or "frames" not in tl:
        _warn("忽略 %s（没有 total/frames 字段，不像本项目的 timeline），退回剧本粗估" % p)
        return None, p
    return tl, p


def score_seconds(total):
    """配乐耗时：以 60 s / 389 s 两个实测点线性折算（超出范围按最近一段斜率外推）。"""
    pts = [(r[0], r[1], r[2]) for r in SCORE_REFS]
    if total <= pts[0][0]:
        (x0, l0, h0), (x1, l1, h1) = pts[0], pts[1]
    elif total >= pts[-1][0]:
        (x0, l0, h0), (x1, l1, h1) = pts[-2], pts[-1]
    else:
        (x0, l0, h0), (x1, l1, h1) = pts[0], pts[1]
        for i in range(len(pts) - 1):
            if pts[i][0] <= total <= pts[i + 1][0]:
                (x0, l0, h0), (x1, l1, h1) = pts[i], pts[i + 1]
                break
    k = (float(total) - x0) / float(x1 - x0) if x1 != x0 else 0.0
    lo = l0 + (l1 - l0) * k
    hi = h0 + (h1 - h0) * k
    return max(0.3, lo), max(0.5, hi)


def frames_seconds(frames, jobs, engine, rerender):
    """frames 阶段墙钟（只算画面生成，编码单列在 mux 行）。

    PIL：帧数 ÷ 渲染 fps ÷ 并行度；web：启动开销 + 帧数 × 每帧截图耗时 ÷ 并行度。
    render.py 的分块 worker 是"边渲染边编码"，所以真实成本 ≈ 本行 + mux 行。
    """
    jobs_eff = max(1, min(int(jobs), CORES))
    if engine == "web":
        base = [WEB_STARTUP_SEC + frames * spf / jobs_eff for spf in WEB_SEC_PER_FRAME]
    else:
        base = [frames / rfps / jobs_eff for rfps in PIL_FPS]
    base = sorted(base)
    return base[0] * rerender, base[-1] * rerender


def encode_seconds(frames, jobs):
    """x264 编码墙钟：141 fps（medium/crf17），分块并行时按 --jobs 摊到各进程。"""
    jobs_eff = max(1, min(int(jobs), CORES))
    return frames / X264_FPS / jobs_eff


def chunk_plan(frames, fps, jobs, explicit):
    """逐字复刻 render.py 的分块规则（改了那边记得同步这里）。

        chunk = script.chunk_frames or round(frames/jobs/fps/4)*4*fps
        chunk = max(fps*2, round(chunk/fps)*fps)
    """
    fps = int(fps) or FPS
    jobs = max(1, int(jobs))
    if explicit:
        chunk = int(explicit)
    else:
        chunk = int(round(frames / float(jobs) / float(fps) / 4.0)) * 4 * fps
    chunk = max(fps * 2, int(round(chunk / float(fps))) * int(fps))
    nseg = int(math.ceil(frames / float(chunk))) if frames and chunk else 0
    return chunk, nseg


def disk_estimate(frames, total, n_lines, n_cues, crf, music_on):
    """磁盘：raw 管道峰值（不落盘但决定内存/管道带宽）+ work/ + out/。"""
    try:
        crf = int(crf)
    except (TypeError, ValueError):
        crf = 17
    factor = 2.0 ** ((17.0 - crf) / 6.0)          # crf 每降 6 体积约翻倍（x264 经验值）
    vlo = max(0.3, VIDEO_MBPS[0] * factor)
    vhi = max(0.3, VIDEO_MBPS[1] * factor)
    vid_lo = total * vlo * 1e6 / 8.0
    vid_hi = total * vhi * 1e6 / 8.0
    out_lo = total * (vlo + AUDIO_MBPS) * 1e6 / 8.0
    out_hi = total * (vhi + AUDIO_MBPS) * 1e6 / 8.0
    n_wav = 1 + (1 if music_on else 0) + (1 if n_cues else 0)
    wav_bytes = total * WAV_BYTES_PER_SEC
    segments = vid_hi                        # 分块文件合计 ≈ 整片视频体积
    silent = vid_hi
    tts = n_lines * TTS_BYTES_PER_LINE
    qa = QA_DISK_BYTES if frames else 0
    work = tts + segments + silent + n_wav * wav_bytes + qa
    return dict(raw_peak_bytes=W * H * 3 * int(frames),
                work_bytes=work,
                out_bytes_lo=out_lo, out_bytes_hi=out_hi,
                breakdown=dict(tts_bytes=tts, segments_bytes=segments,
                               silent_bytes=silent, wav_bytes=n_wav * wav_bytes,
                               qa_bytes=qa),
                video_mbps=(vlo, vhi))


def bottleneck_advice(stage, engine, jobs_eff):
    if stage == "frames":
        if engine == "web":
            return ("换 engine=pil：同一画面语言下 PIL 比浏览器逐帧快 5–10 倍；"
                    "确实需要 Canvas/WebGL 才留 web（--jobs 提到 %d 也压不动截图往返的瓶颈）"
                    % CORES)
        return ("把 --jobs 从 %d 提到 8–%d（本机 12 核）能把 frames 大致线性压下来；"
                "再想快只能减帧或简化场景——1920x1080 是写死的，降分辨率不是选项"
                % (jobs_eff, CORES))
    if stage == "tts":
        return "TTS 是网络等待：tts_workers 保持 4–6 就够，句子多先压台词，别靠并发硬堆"
    if stage == "qa":
        return "质检已是秒级；它随片长线性涨，是必要成本（不查等于没门禁）"
    if stage == "score":
        return "配乐是秒级纯 CPU 合成；片长再长也不该成为瓶颈"
    return "该阶段占比高但绝对值不大：按上面的区间排期即可"


# ============================================================================
# 主流程
# ============================================================================

def build_report(script_path, script, args):
    as_json = bool(args.as_json)
    lines = [ln for ln in (script.get("lines") or []) if isinstance(ln, dict)]
    if not lines:
        _die("%s 的 lines 是空的：至少写一句（render.py 只接受非空数组）" % script_path, as_json)

    engine = str(args.engine or script.get("engine") or "pil").lower()
    if engine not in ("pil", "web"):
        _warn("engine=%r 不认识（只能是 pil / web），按 pil 预演" % engine)
        engine = "pil"

    tl, tl_path = read_timeline(args.work)
    if tl is not None:
        total = _num(tl.get("total"))
        fps = int(_num(tl.get("fps"), FPS)) or FPS
        frames = int(_num(tl.get("frames"), round(total * fps)))
        source = "timeline"
        source_note = "真实帧数：%s" % tl_path
    else:
        total, _scenes = estimate_timeline(script)
        fps = FPS
        frames = int(round(total * fps))
        source = "script_estimate"
        source_note = ("粗估（等效字数 ÷ %.1f 字/秒 + lead/tail + hold；没有 %s）"
                       % (CPS, tl_path))
        if total <= 0:
            _die("%s 的 lines 里没有可估算的 text：每句都要有朗读文本" % script_path, as_json)

    jobs_req = int(args.jobs)
    if jobs_req < 1:
        _warn("--jobs %d 无效，按 1 算" % jobs_req)
        jobs_req = 1
    jobs_eff = max(1, min(jobs_req, CORES))
    if jobs_req > CORES:
        _warn("--jobs %d 超过本机 12 核：分块并行到 12 之后基本不再线性加速" % jobs_req)

    rerender = max(0.1, float(args.rerender))
    n = len(lines)
    sfx_cfg = script.get("sfx") or {}
    mus = script.get("music") or {}
    if not isinstance(sfx_cfg, dict):
        sfx_cfg = {}
    if not isinstance(mus, dict):
        mus = {}
    n_cues = len(sfx_cfg.get("cues") or [])
    sfx_on = bool(sfx_cfg.get("enabled", True)) and n_cues > 0
    # 配乐口径对齐 filmkit.music_plan：mode=off/none 或 enabled=false 才算关；
    # 只有 mode=score（或没写 mode 但写了 bpm）才会真跑 scorekit。
    mode = str(mus.get("mode") or "").strip().lower()
    has_bpm = "bpm" in mus or "bpm" in script
    music_on = bool(mus.get("enabled", True)) and mode not in ("off", "none")
    score_on = music_on and (mode == "score" or (not mode and has_bpm))
    crf = script.get("crf", 17)
    preset = script.get("preset", "medium")

    # ---- 各阶段 ----
    workers = int(_num(script.get("tts_workers", 4), 4) or 4)
    workers = max(1, min(workers, max(1, n)))
    tts_lo = tts_hi = n * TTS_SEC_PER_LINE / float(workers)
    layout = LAYOUT_BASE + LAYOUT_PER_LINE * n
    cues = CUES_BASE + CUES_PER_LINE * n
    if score_on:
        score_lo, score_hi = score_seconds(total)
    else:
        score_lo = score_hi = 0.0
    sfx = max(SFX_MIN_SEC, n_cues * SFX_SEC_PER_CUE) if sfx_on else 0.0
    audio = AUDIO_BASE + total * AUDIO_PER_SEC
    fr_lo, fr_hi = frames_seconds(frames, jobs_eff, engine, rerender)
    mux = encode_seconds(frames, jobs_eff)
    qa = max(QA_MIN_SEC, total * QA_SEC_PER_SEC)

    stages = [
        dict(stage="tts", lo=tts_lo, hi=tts_hi, measured=True,
             note=("%d 句 × %.1f s ÷ tts_workers=%d（串行口径 %.1f s；需联网，edge-tts 免费端点）"
                   % (n, TTS_SEC_PER_LINE, workers, n * TTS_SEC_PER_LINE))),
        dict(stage="layout", lo=layout, hi=layout, measured=False,
             note="排轴 + sub.srt/ass + 安全区/字形自查（经验值，未单测）"),
        dict(stage="cues", lo=cues, hi=cues, measured=False,
             note="%d 条 sfx.cues 落位 + 排序（经验值，未单测）" % n_cues),
        dict(stage="score", lo=score_lo, hi=score_hi, measured=bool(score_on),
             note=("按 60 s≈2–4 s / 389 s≈4–8 s 线性折算" if score_on
                   else ("mode=%s：不跑 scorekit（%s）"
                         % (mode or "-", "外部配乐/和弦床/关掉配乐")))),
        dict(stage="sfx", lo=sfx, hi=sfx, measured=False,
             note=("%d 条音效 ≈ %.1f s（上限口径：单条 ≤1.5 s；长环境声 wind/rain 另计）"
                   % (n_cues, sfx) if sfx_on else "没有 sfx.cues：跳过（whoosh 兼容路径除外）")),
        dict(stage="audio", lo=audio, hi=audio, measured=False,
             note="旁白+配乐+音效三总线混音 + 写 audio.wav（经验值：%.2f s + %.3f×片长）"
                  % (AUDIO_BASE, AUDIO_PER_SEC)),
        dict(stage="frames", lo=fr_lo, hi=fr_hi, measured=True,
             note=("%s：%s，--jobs %d（本机 12 核）× 返工系数 %.2g；编码成本单列在 mux 行"
                   % (engine, "PIL 40–80 fps" if engine == "pil"
                      else "web 80–130 ms/帧 + 启动 6 s", jobs_eff, rerender))),
        dict(stage="mux", lo=mux, hi=mux, measured=True,
             note=("x264 medium crf17 ≈%d fps，按 --jobs %d 摊（分块 worker 边渲染边编码）；"
                   "render.py 的 mux 本身是 -c:v copy + 两遍 loudnorm，通常 <1 s"
                   % (int(X264_FPS), jobs_eff))),
        dict(stage="qa", lo=qa, hi=qa, measured=True,
             note="30 s 片全量深检 ≈1 s，按片长线性折算 + 联系表/静帧"),
    ]
    total_lo = sum(s["lo"] for s in stages)
    total_hi = sum(s["hi"] for s in stages)
    worst = max(stages, key=lambda s: s["hi"])
    share = worst["hi"] / total_hi if total_hi > 0 else 0.0

    # ---- 磁盘 ----
    disk = disk_estimate(frames, total, n, n_cues, crf, music_on)

    # ---- 分块（render.py 规则）----
    explicit = int(_num(script.get("chunk_frames"), 0))
    chunk, nseg = chunk_plan(frames, fps, jobs_eff, explicit)
    chunk = dict(jobs=jobs_eff, jobs_requested=jobs_req, chunk_frames=chunk,
                 segments=nseg, explicit=bool(explicit),
                 rule="chunk = chunk_frames 或 round(frames/jobs/fps/4)*4*fps，"
                      "再按 fps 取整、最小 2×fps")

    report = {
        "ok": True,
        "file": str(script_path),
        "source": source,
        "source_note": source_note,
        "video": {"title": script.get("title"),
                  "width": W, "height": H, "fps": fps, "frames": frames,
                  "duration_s": round(total, 3), "engine": engine,
                  "crf": crf, "preset": preset, "bpm": mus.get("bpm", script.get("bpm")),
                  "style": script.get("style", None)},
        "counts": {"lines": n, "cues": n_cues, "tts_workers": workers},
        "jobs": {"requested": jobs_req, "effective": jobs_eff, "cores": CORES},
        "rerender": rerender,
        "stages": [dict(stage=s["stage"], label=STAGE_LABELS[s["stage"]],
                        seconds_lo=round(s["lo"], 2), seconds_hi=round(s["hi"], 2),
                        measured=s["measured"], note=s["note"]) for s in stages],
        "total_seconds": {"lo": round(total_lo, 2), "hi": round(total_hi, 2)},
        "disk": {"raw_peak_bytes": disk["raw_peak_bytes"],
                 "raw_peak_human": _fmt_bytes(disk["raw_peak_bytes"]),
                 "work_bytes": int(disk["work_bytes"]),
                 "work_human": _fmt_bytes(disk["work_bytes"]),
                 "out_bytes_lo": int(disk["out_bytes_lo"]),
                 "out_bytes_hi": int(disk["out_bytes_hi"]),
                 "out_human": "%s–%s" % (_fmt_bytes(disk["out_bytes_lo"]),
                                         _fmt_bytes(disk["out_bytes_hi"])),
                 "video_mbps": [round(v, 2) for v in disk["video_mbps"]]},
        "chunk": chunk,
        "bottleneck": {"stage": worst["stage"],
                       "seconds_hi": round(worst["hi"], 2),
                       "share": round(share, 3),
                       "advice": bottleneck_advice(worst["stage"], engine, jobs_eff)},
        "assumptions": [
            "分辨率 1920x1080@%d 由 filmkit.W/H/FPS 写死，film.json 改不了" % FPS,
            "lead=%.2f / tail=%.2f / min_dur=%.1f（v0.5 body 基准；剧本写了就以剧本为准）"
            % (DEFAULT_LEAD, DEFAULT_TAIL, DEFAULT_MIN_DUR),
            "tts 耗时按 %d 句 × %.1f s ÷ workers；实际受网络波动影响" % (n, TTS_SEC_PER_LINE),
            "frames 的 jobs 并行按线性折算且封顶 %d 核（超过不再线性）" % CORES,
            "layout/cues/audio/sfx 的耗时为经验模型（无单阶段实测），量级可信、小数位不可信",
        ],
        "baseline": BASELINE_NOTE,
    }
    return report


def print_report(rep):
    """人读表格（--json 时不走这里，stdout 保持纯 JSON）。"""
    v = rep["video"]
    d = rep["disk"]
    ch = rep["chunk"]
    print("[estimate] %s" % rep["file"])
    if rep["source"] == "timeline":
        print("  来源: %s（已排轴：用真实 total/frames）" % rep["source_note"])
    else:
        print("  来源: %s" % rep["source_note"])
        print("        注意：这是**估算**，不是实测。跑过 --stage layout 后再跑本脚本，"
              "会自动改用真实帧数。")
    style = v.get("style")
    if style is None:
        style = "(没写 style 字段 = v0.4 legacy 行为)"
    print("  《%s》  片长 %.1f s / %d 帧 / %dx%d@%d / engine=%s / crf=%s preset=%s / style=%s"
          % (v.get("title") or "(未写 title)", v["duration_s"], v["frames"], v["width"], v["height"],
             v["fps"], v["engine"], v["crf"], v["preset"], style))
    print("  并行: --jobs %d（请求 %d；本机 12 核，>12 不再线性）  返工系数: %.2g"
          % (ch["jobs"], ch["jobs_requested"], rep["rerender"]))
    print()
    print("  %s %s %s" % (_pad("阶段", 8), _pad("耗时", 16), "说明"))
    for s in rep["stages"]:
        span = _fmt_span(s["seconds_lo"], s["seconds_hi"])
        tag = "" if s["measured"] else "[经验值] "
        print("  %s %s %s"
              % (_pad(s["stage"], 8), _pad(span, 16), tag + s["label"] + "：" + s["note"]))
    total = _fmt_span(rep["total_seconds"]["lo"], rep["total_seconds"]["hi"])
    print("  %s %s %s" % (_pad("合计", 8), _pad(total, 16), "--stage all 顺序跑完的墙钟（frames 并行）"))
    print()
    print("  磁盘")
    print("    raw 管道峰值   %s   %d×%d×3 字节 × %d 帧（原始帧流式喂 x264，不落盘，"
          "但决定内存/管道带宽）" % (_pad(d["raw_peak_human"], 10), W, H, v["frames"]))
    print("    work/          %s   segments + silent.mp4 + wav + tts + qa"
          "（分块并行时 segments 与 silent 会同时存在）" % _pad(d["work_human"], 10))
    print("    out/           %s   成片（视频 %.1f–%.1f Mbps + AAC 192 kbps）"
          % (_pad(d["out_human"], 10), d["video_mbps"][0], d["video_mbps"][1]))
    print()
    print("  分块建议（对齐 render.py：%s）" % ch["rule"])
    print("    --jobs %d" % ch["jobs"])
    print("    chunk_frames: %d   写进 film.json（render.py 没有 --chunk 参数，只从剧本读）"
          % ch["chunk_frames"])
    print("    预计分块 %d 段 × 约 %d 帧" % (ch["segments"], ch["chunk_frames"]))
    if ch["explicit"]:
        print("    （剧本里已显式写了 chunk_frames，这里按它算）")
    print()
    bn = rep["bottleneck"]
    print("  最大瓶颈：%s ≈ %.1f s（占 %.0f%%）—— %s"
          % (bn["stage"], bn["seconds_hi"], bn["share"] * 100, bn["advice"]))
    print()
    print("  基准说明：%s" % rep["baseline"])
    print("  假设：")
    for a in rep["assumptions"]:
        print("    - %s" % a)


def main(argv=None):
    _setup_stdio()
    ap = argparse.ArgumentParser(
        description="video-studio 成本/时长预演：渲多久、占多少盘、怎么分块")
    ap.add_argument("--script", default="film.json", help="剧本路径（默认 film.json）")
    ap.add_argument("--work", default="work", help="工作目录（有 timeline.json 就用真实帧数）")
    ap.add_argument("--jobs", type=int, default=4, help="frames 阶段并行进程数（默认 4，本机上限 12）")
    ap.add_argument("--engine", default="", choices=("", "pil", "web"),
                    help="覆盖 film.json 的 engine（空 = 按剧本，缺省 pil）")
    ap.add_argument("--rerender", type=float, default=1.0,
                    help="返工系数：画面预计重渲几遍（1.0 = 一遍过；1.4 = 预留 40%%）")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="机读输出（stdout 只有 JSON，键名英文）")
    args = ap.parse_args(argv)

    script_path, script = load_script(args.script, bool(args.as_json))
    rep = build_report(script_path, script, args)
    if args.as_json:
        sys.stdout.write(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
