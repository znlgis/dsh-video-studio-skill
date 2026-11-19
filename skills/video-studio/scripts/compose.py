# -*- coding: utf-8 -*-
"""compose.py — 「素材打底 + 小面积叠加」的合成入口（hybrid 的第三种形态）。

为什么要有它
------------
hybrid（素材 + 自绘结合）在技能里此前只有一种做法：**按段拼接**（素材段走 footage.py、
图文段走 render.py、最后 concat）。实测还有两种更省的形态，其中第三种最好用：

  1. 按段拼接       —— 图文段整段独立成片；风格断层靠交叉淡化/暗场过渡掩盖
  2. 整屏卡片覆盖   —— 在素材上盖满屏卡片；素材被遮掉，等于没用到素材
  3. **素材打底 + 小面积叠加**（本脚本）—— 素材就是画面，只把"必要的一点信息"
     （一行对照、一个标注、一个角标）按时间窗叠上去

本脚本只做第 3 种，并且强制两件事（都是实测踩过的坑）：
  · **帧对齐**：镜头按累计帧切（footageedit.cut_shots 已帧对齐），拼接后视频帧数
    必须等于 round(total*fps)；不等就报错，不允许"差半秒也能出片"。
  · **响度归一化不可绕过**：内部固定走 filmkit.loudnorm_two_pass。
    绕过它手写合成会得到 -25 LUFS 的成片（实测发生过），而且容器/帧级门禁都查不出来。

契约：overlay.json
------------------
{
  "canvas": [1920, 1080],
  "subtitle": "sub.ass",
  "layers": [
    {"id": "s04", "png": "chips/s04.png", "bind": "line:s04",
     "x": "(W-w)/2", "y": 692, "fade": 0.18},
    {"id": "corner", "png": "chips/logo.png", "at": 12.0, "dur": 4.0,
     "x": "W-w-48", "y": "48"}
  ]
}

时间窗解析优先级：bind > (at + dur) > (at，到下一句起点)。
坐标：整数或 ffmpeg 表达式；x/y 支持 (W-w)/2、W-w-48 这类写法（W/w 是主/叠加宽）。

用法
----
    python compose.py --footage footage.json --overlay overlay.json --work work --out out/film.mp4
    python compose.py ... --stage overlay|silent|audio|compose|qa
    python compose.py ... --target-tp -2.2 --crf 22

退出码：0 成功 / 1 缺输入或 ffmpeg 失败 / 2 契约错误（overlay.json 不合法）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import filmkit as K          # noqa: E402

STAGES = ("overlay", "silent", "audio", "compose", "qa")
DEFAULT_TARGET_TP = -2.2     # AAC 会抬高真峰 0.5~1.1 dB，见 filmkit.loudnorm_two_pass


def _utf8_stdout():
    """Windows 控制台默认 GBK：打印中文/子进程回显会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                 # noqa: BLE001 —— 老 Python / 非 tty 直接跳过
            pass


def log(msg):
    try:
        sys.stdout.write(str(msg) + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:            # 兜底：宁可掉字符也不能中断出片
        sys.stdout.write(str(msg).encode("utf-8", "replace").decode("gbk", "replace") + "\n")
        sys.stdout.flush()


def _load_json(path, default=None):
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(str(tmp), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(str(tmp), str(p))


def media_dur(path):
    """读时长；读不到返回 0.0（调用方必须自己判 0，不能当成功）。"""
    try:
        return float(K.media_dur(str(path)) or 0.0)
    except Exception:                     # noqa: BLE001
        return 0.0


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=cwd)
    if p.returncode != 0:
        raise SystemExit("[compose] ffmpeg 失败（exit=%d）：\n%s\n%s"
                         % (p.returncode, " ".join(str(c) for c in cmd[:12]),
                            (p.stderr or "")[-2000:]))
    return p


# ============================================================================
# 一、契约校验
# ============================================================================

def load_overlay(path):
    """读并校验 overlay.json。字段不合法直接 SystemExit（退出码 2 语义）。"""
    spec = _load_json(path)
    if not isinstance(spec, dict):
        raise SystemExit("[compose] overlay.json 读不到或不是对象：%s" % path)
    layers = spec.get("layers") or []
    if not isinstance(layers, list):
        raise SystemExit("[compose] overlay.layers 必须是数组")
    bad = []
    for i, ly in enumerate(layers):
        if not isinstance(ly, dict):
            bad.append("layers[%d] 不是对象" % i); continue
        if not ly.get("png"):
            bad.append("layers[%d] 缺 png（叠加层的透明图路径）" % i)
        if not (ly.get("bind") or ly.get("at")):
            bad.append("layers[%d] 缺时间窗：给 bind（推荐）或 at" % i)
        fade = ly.get("fade")
        if fade is not None and not isinstance(fade, (int, float)):
            bad.append("layers[%d].fade 必须是数字（秒）" % i)
    if bad:
        for b in bad:
            log("  [契约错误] " + b)
        raise SystemExit("[compose] overlay.json 不合法（见上）")
    spec["layers"] = layers
    return spec


def resolve_windows(spec, tl):
    """把每层的时间窗算成 (start, end) 秒。返回 [{id,png,x,y,fade,start,end}]。"""
    rows = {str(r["id"]): r for r in (tl.get("lines") or [])}
    order = [str(r["id"]) for r in (tl.get("lines") or [])]
    total = float(tl.get("total") or 0.0)
    out = []
    for ly in spec.get("layers") or []:
        bind = str(ly.get("bind") or "")
        if bind.startswith("line:"):
            lid = bind.split(":", 1)[1]
            r = rows.get(lid)
            if r is None:
                raise SystemExit("[compose] bind=%r 在 timeline 里找不到该句" % bind)
            start = float(r["start"])
            end = float(r["end"])
            i = order.index(lid) if lid in order else -1
            if ly.get("until") == "next" and 0 <= i < len(order) - 1:
                end = float(rows[order[i + 1]]["start"])
        elif ly.get("at") is not None:
            start = float(ly["at"])
            if ly.get("dur"):
                end = start + float(ly["dur"])
            else:
                nxt = [float(rows[k]["start"]) for k in order
                       if float(rows[k]["start"]) > start + 1e-6]
                end = min(nxt) if nxt else total
        else:
            continue
        start = max(0.0, start + float(ly.get("pad_start") or 0.0))
        end = min(total or end, end + float(ly.get("pad_end") or 0.0))
        if end - start <= 0.05:
            log("  [skip] %s 的时间窗过短（%.2f~%.2f）" % (ly.get("id") or "?", start, end))
            continue
        out.append({"id": str(ly.get("id") or Path(str(ly["png"])).stem),
                    "png": str(ly["png"]),
                    "x": str(ly["x"] if ly.get("x") is not None else "(W-w)/2"),
                    "y": str(ly["y"] if ly.get("y") is not None else "H-h-120"),
                    "fade": float(ly.get("fade") or 0.0),
                    "start": round(start, 3), "end": round(end, 3)})
    return out


# ============================================================================
# 二、各阶段
# ============================================================================

def st_silent(work, footage, force=False):
    """按 cut.json 拼接无声底片（流拷贝），并核对帧数 == round(total*fps)。"""
    work = Path(work)
    cut = _load_json(work / "cut.json")
    tl = _load_json(work / "timeline.json")
    if not cut or not tl:
        raise SystemExit("[compose] 缺 %s/cut.json 或 timeline.json（先跑 footage.py --stage cut）" % work)
    fps = float((footage.get("video") or {}).get("fps") or 30)
    total = float(tl.get("total") or 0.0)
    want_frames = int(round(total * fps))
    have = sum(int(r.get("frames") or 0) for r in cut)
    if not have:
        have = int(round(sum(float(r.get("len") or 0) for r in cut) * fps))
    if want_frames and abs(have - want_frames) > 1:
        raise SystemExit(
            "[compose] 镜头总帧数 %d 与时间轴 %d 帧不符（差 %d 帧 / %.3fs）。\n"
            "  音画会随片长累积错位。修法：重跑 footage.py --stage cut（新实现按累计帧切）；\n"
            "  若 cut.json 是旧版（没有 frames 字段），必须先重切。"
            % (have, want_frames, have - want_frames, (have - want_frames) / fps))
    dst = work / "silent.mp4"
    if dst.exists() and not force:
        d = media_dur(dst)
        if d > 0:
            log("  [silent] 复用 %s（%.3fs）" % (dst.name, d))
            return str(dst)
    lst = work / "concat_compose.txt"
    lst.write_text("\n".join("file '%s'" % Path(r["video"]).resolve().as_posix()
                             for r in cut) + "\n", encoding="utf-8")
    _run([K.FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", str(lst), "-c", "copy", str(dst)])
    d = media_dur(dst)
    if d <= 0:
        raise SystemExit("[compose] 拼接后读不到时长，拼接失败")
    log("  [silent] %d 镜 -> %.3fs（%d 帧，时间轴 %d 帧）" % (len(cut), d, have, want_frames))
    return str(dst)


def st_audio(work, mix_name, target_i, target_tp, force=False):
    """mix.wav -> audio_norm.wav（两遍 loudnorm）。成败都要明确。"""
    work = Path(work)
    mix = work / mix_name
    if not mix.exists():
        raise SystemExit("[compose] 缺 %s（先跑 footage.py --stage mix）" % mix)
    dst = work / "audio_norm.wav"
    if dst.exists() and not force:
        log("  [audio] 复用 %s" % dst.name)
        return str(dst)
    af = K.loudnorm_two_pass(str(mix), target_i=target_i, target_tp=target_tp)
    if not af:
        raise SystemExit("[compose] loudnorm 测量失败（全静音或音频坏）：%s" % mix)
    _run([K.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(mix), "-af", af,
          "-ar", "48000", "-ac", "2", str(dst)])
    log("  [audio] 两遍 loudnorm -> %s（I=%.1f / TP=%.1f）" % (dst.name, target_i, target_tp))
    return str(dst)


def _layer_png(ly, root):
    png = Path(ly["png"])
    return png if png.is_absolute() else Path(root) / ly["png"]


def st_compose(footage, work, out, spec, args):
    """一次编码：底片 -> 按时间窗叠层 -> 烧字幕 -> 合音轨。"""
    work = Path(work); out = Path(out)
    silent = (work / "silent.mp4").resolve()
    audio = (work / "audio_norm.wav").resolve()
    for p in (silent, audio):
        if not p.exists():
            raise SystemExit("[compose] 缺 %s（先跑 --stage silent/audio）" % p)
    tl = _load_json(work / "timeline.json") or {}
    layers = [ly for ly in resolve_windows(spec, tl) if _layer_png(ly, args.root).exists()]
    vd, ad = media_dur(silent), media_dur(audio)
    if vd <= 0 or ad <= 0:
        raise SystemExit("[compose] 读不到底片/音轨时长（%.3f / %.3f）" % (vd, ad))
    fps = float((footage.get("video") or {}).get("fps") or 30)
    pad = max(0.0, ad - vd)
    chain = ["[0:v]fps=%g,format=yuv420p%s[base]"
             % (fps, (",tpad=stop_mode=clone:stop_duration=%.3f" % pad) if pad > 0.01 else "")]
    cur = "base"
    for i, ly in enumerate(layers, start=1):
        span = ly["end"] - ly["start"]
        fade = min(ly["fade"], span / 2.0)
        sub = ["format=rgba", "trim=0:%.3f" % span,
               "setpts=PTS-STARTPTS+%.3f/TB" % ly["start"]]
        if fade > 0:
            sub.append("fade=t=in:st=%.3f:d=%.3f:alpha=1" % (ly["start"], fade))
            sub.append("fade=t=out:st=%.3f:d=%.3f:alpha=1" % (ly["end"] - fade, fade))
        chain.append("[%d:v]" % i + ",".join(sub) + "[ly%d]" % i)
        chain.append("[%s][ly%d]overlay=x=%s:y=%s:eval=init:"
                     "enable='between(t,%.3f,%.3f)'[v%d]"
                     % (cur, i, ly["x"], ly["y"], ly["start"], ly["end"], i))
        cur = "v%d" % i
    sub_ass = spec.get("subtitle")
    ap = (work / sub_ass) if sub_ass else None
    if ap is not None and ap.exists():
        chain.append("[%s]ass=filename='%s',format=yuv420p[vout]" % (cur, ap.name))
        vf_cwd = str(ap.parent)
    else:
        chain.append("[%s]format=yuv420p[vout]" % cur)
        vf_cwd = str(work)
    cmd = [K.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(silent)]
    for ly in layers:
        cmd += ["-loop", "1", "-framerate", "%g" % fps, "-i", str(_layer_png(ly, args.root))]
    cmd += ["-i", str(audio)]
    audio_idx = len(layers) + 1
    out = Path(out).resolve()
    cmd += ["-filter_complex", ";".join(chain),
            "-map", "[vout]", "-map", "%d:a" % audio_idx,
            "-c:v", "libx264", "-crf", str(args.crf), "-preset", args.preset,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-t", "%.3f" % ad,
            "-movflags", "+faststart", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(cmd, cwd=vf_cwd)
    d = media_dur(out)
    if d <= 0:
        raise SystemExit("[compose] 输出读不到时长：%s" % out)
    log("  [compose] %d 层 -> %s（%.3fs / %.1f MB）"
        % (len(layers), out.name, d, out.stat().st_size / 1048576))
    return str(out)


def st_qa(out, work, args):
    """调 qa_frames 做帧级深检；素材线硬切用 --shots 传切点，避免误报闪烁。"""
    work = Path(work)
    tl = _load_json(work / "timeline.json") or {}
    rows = tl.get("lines") or []
    shots = [round(float(r["start"]), 3) for r in rows[1:]]
    shots_json = work / "shot_cuts.json"
    _write_json(shots_json, shots)
    cmd = [sys.executable, os.path.join(HERE, "qa_frames.py"), str(Path(out).resolve()),
           "--shots", str(shots_json), "--out", str(work / "qa" / "deep.json")]
    # 必须显式 utf-8：子进程输出是 UTF-8，Windows 默认按 GBK 解码会出乱码
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (p.stdout or "") + (p.stderr or "")
    log("\n".join(tail.strip().splitlines()[-12:]))
    return p.returncode


# ============================================================================
# 三、CLI
# ============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="compose.py",
        description="素材打底 + 小面积叠加（hybrid 第三种形态）：按时间窗叠层 + 烧字幕 + 强制响度归一")
    ap.add_argument("--footage", default="footage.json", help="素材线契约（取 video.fps 等）")
    ap.add_argument("--overlay", default="overlay.json", help="叠加层契约（可缺省 = 不叠任何层）")
    ap.add_argument("--work", default="work", help="中间产物目录")
    ap.add_argument("--out", default="out/film.mp4", help="成片路径")
    ap.add_argument("--root", default=".", help="工程根（overlay.layers[].png 的相对基准）")
    ap.add_argument("--stage", default="all", choices=("all",) + STAGES)
    ap.add_argument("--mix", default="mix.wav", help="混音结果（默认 mix.wav）")
    ap.add_argument("--target-i", type=float, default=-16.0, help="目标积分响度 LUFS")
    ap.add_argument("--target-tp", type=float, default=DEFAULT_TARGET_TP,
                    help="目标真峰 dBTP（默认 %.1f：AAC 会抬高 0.5~1.1 dB）" % DEFAULT_TARGET_TP)
    ap.add_argument("--crf", type=int, default=20, help="视频 CRF（默认 20）")
    ap.add_argument("--preset", default="medium", help="x264 preset")
    ap.add_argument("--force", action="store_true", help="忽略已有中间产物，强制重跑本阶段")
    args = ap.parse_args(argv)
    _utf8_stdout()

    foot = _load_json(args.footage) or {}
    work = Path(args.work).resolve()
    args.root = str(Path(args.root).resolve())
    args.out = str(Path(args.out).resolve())
    spec = {"layers": []}
    if args.overlay and Path(args.overlay).exists():
        spec = load_overlay(args.overlay)
    elif args.stage in ("all", "compose"):
        log("  [WARN] 没找到 %s：按「不叠任何层」处理" % args.overlay)

    def do(s):
        return args.stage in ("all", s)

    try:
        if do("overlay"):
            tl = _load_json(work / "timeline.json") or {}
            wins = resolve_windows(spec, tl)
            _write_json(work / "overlay_windows.json", wins)
            log("== overlay：%d 层绑定时间窗" % len(wins))
            for w in wins:
                log("   %-10s %.2f ~ %.2f s  y=%s" % (w["id"], w["start"], w["end"], w["y"]))
        if do("silent"):
            log("== silent：拼接底片")
            st_silent(work, foot, force=args.force)
        if do("audio"):
            log("== audio：响度归一")
            st_audio(work, args.mix, args.target_i, args.target_tp, force=args.force)
        if do("compose"):
            log("== compose：叠加 + 字幕 + 封装")
            st_compose(foot, work, args.out, spec, args)
        if args.stage in ("all", "qa"):
            log("== qa：帧级深检")
            rc = st_qa(args.out, work, args)
            if rc != 0:
                log("[compose] 质检未过（见上）")
                return rc
    except SystemExit as e:
        log(str(e))
        return 1
    log("完成：%s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
