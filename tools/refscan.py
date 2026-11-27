#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""refscan.py — 参考片逆向：把别人的片子拆成「手法 / 怎么做的 / 要不要抄」。

为什么需要它：想让新片子像某个参考片，最容易走的两条歪路是
  ① 凭记忆描述风格（记错，也描述不出量化差异）；
  ② 直接截图当模板（抄的是画面，学不到节奏与镜头长度）。
这个工具只做**可量化**的部分：切点在哪、每个镜头多长、动得多凶、亮暗与饱和多少，
然后生成一张三列空白表逼你把「手法 | 参考片实现 | 取舍」写清楚。取舍一栏是重点——
抄不动的（拍摄成本、版权素材、真人表演）必须明说放弃，而不是假装设计成那样。

产物（默认 work/refscan/）：
  sheet.png     切点帧联系表（按镜号排序）
  shots.json    每个镜头 {i, start, dur, motion, luma, sat, sample_t}
  REVERSE.md    三列表骨架 + 实测指标表（"手法"/"取舍"两列留给人或 Agent 填）

不联网、不改参考片、不依赖 numpy：亮度/饱和用 PIL ImageStat，动幅度用缩略灰度的
帧间平均绝对差（换算成 0-100 的"动得多凶"，跨片比较够用）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(TOOLS, "..", "skills", "video-studio", "scripts"))
sys.path.insert(0, SCRIPTS)

from PIL import Image, ImageStat   # noqa: E402


def _ffmpeg():
    import filmkit as K
    return K.FFMPEG


def probe(video):
    """ffprobe -> {duration, fps, w, h}；读不出返回 None。"""
    import filmkit as K
    cmd = [K.ffprobe_path(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(video)]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    vs = [s for s in (d.get("streams") or []) if s.get("codec_type") == "video"]
    if not vs:
        return None
    v = vs[0]
    fr = str(v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den or 1)
    except Exception:
        fps = 30.0
    fmt = d.get("format") or {}
    try:
        dur = float(fmt.get("duration") or v.get("duration") or 0.0)
    except Exception:
        dur = 0.0
    return dict(duration=round(dur, 3), fps=round(fps, 3),
                w=int(v.get("width") or 0), h=int(v.get("height") or 0))


def scene_cuts(video, threshold=0.35):
    """ffmpeg scene 检测 -> [秒]（去重、升序）。检测不到返回 []。"""
    sel = "select=" + "gt(scene\\," + str(threshold) + ")" + ",showinfo"
    cmd = [_ffmpeg(), "-v", "info", "-i", str(video), "-an", "-sn",
           "-vf", sel, "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=900)
    except Exception:
        return []
    txt = (p.stderr or b"").decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"pts_time:([0-9]+\.[0-9]+)", txt):
        t = float(m.group(1))
        if t > 0.05:
            out.append(round(t, 3))
    ded = []
    for t in sorted(out):
        if not ded or t - ded[-1] > 0.2:
            ded.append(t)
    return ded


def grab(video, t, out_png, width=480):
    """某秒抽一帧。失败返回 None（调用方按"缺证据帧"处理，不崩）。"""
    cmd = [_ffmpeg(), "-y", "-v", "error", "-ss", "%.4f" % float(t),
           "-i", str(video), "-frames:v", "1",
           "-vf", "scale=%d:-2" % int(width), out_png]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180, check=True)
    except Exception:
        return None
    return out_png if os.path.exists(out_png) else None


def _gray(png, size=(96, 54)):
    im = Image.open(png).convert("L").resize(size, Image.BILINEAR)
    return list(im.getdata())


def motion_score(png_a, png_b):
    """两帧平均绝对差 -> 0-100（缩到 96x54 再比，长片也不慢）。"""
    try:
        a = _gray(png_a)
        b = _gray(png_b)
    except Exception:
        return 0.0
    if not a or len(a) != len(b):
        return 0.0
    diff = sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))
    return round(min(100.0, diff * 100.0 / 25.5), 1)


def look(png):
    """亮度与饱和度 0-100（可量化地说"暗调/高饱和"）。"""
    try:
        im = Image.open(png)
    except Exception:
        return dict(luma=0.0, sat=0.0)
    g = ImageStat.Stat(im.convert("L"))
    h = ImageStat.Stat(im.convert("HSV"))
    return dict(luma=round(float(g.mean[0]) * 100.0 / 255.0, 1),
                sat=round(float(h.mean[1]) * 100.0 / 255.0, 1))


def build_shots(video, cuts, duration, outdir, samples=3, width=480):
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    shots = []
    for i in range(len(bounds) - 1):
        st, en = bounds[i], bounds[i + 1]
        if en - st < 0.08:
            continue
        n = max(2, int(samples))
        times = [st + (en - st) * (k + 0.5) / n for k in range(n)]
        pngs = []
        for k, t in enumerate(times):
            p = os.path.join(outdir, "f%03d_%d.png" % (i + 1, k))
            got = grab(video, t, p, width=width)
            if got:
                pngs.append(got)
        mob = 0.0
        for k in range(len(pngs) - 1):
            mob = max(mob, motion_score(pngs[k], pngs[k + 1]))
        lk = look(pngs[len(pngs) // 2]) if pngs else dict(luma=0.0, sat=0.0)
        shots.append(dict(i=i + 1, start=round(st, 3), end=round(en, 3),
                          dur=round(en - st, 3), motion=mob,
                          luma=lk["luma"], sat=lk["sat"],
                          frames=pngs, sample_t=[round(t, 3) for t in times]))
    return shots


def truncate(shots, cuts, max_shots):
    """按 --max-shots 截断 -> (shots, cuts, 丢掉的镜头数)。

    cuts 必须跟着截：shots.json 里「N 个切点 / M 个镜头」对不上时，读的人会以为
    工具漏检测了切点，而不是「我自己让它只拆前几个」。纯函数，便于自检。
    """
    if not max_shots or len(shots) <= max_shots:
        return shots, cuts, 0
    kept = shots[:max_shots]
    dropped = len(shots) - max_shots
    # n 个镜头之间有 n-1 个切点
    return kept, cuts[:max(0, len(kept) - 1)], dropped

def write_sheet(shots, outdir, cols=4, thumb_w=420):
    import filmkit as K
    picks = []
    for s in shots:
        if s["frames"]:
            picks.append(s["frames"][len(s["frames"]) // 2])
    if not picks:
        return None
    out = os.path.join(outdir, "sheet.png")
    try:
        return K.contact_sheet(picks, out, cols=cols, thumb_w=thumb_w)
    except Exception:
        return None


def _fmt_shot_row(s):
    return "| %02d | %.2f | %.1f | %.1f | %.1f |  |  |  |" % (
        s["i"], s["dur"], s["motion"], s["luma"], s["sat"])


def reverse_markdown(shots, info, sheet, video):
    """三列表骨架：手法 / 参考片实现 / 取舍（后两列留空给人填）。"""
    out = []
    out.append("# 参考片逆向：" + os.path.basename(str(video)))
    out.append("")
    out.append("> 这不是影评。每行必须落成可执行的一句：手法是什么、参考片怎么做的、")
    out.append("> 我们抄不抄（抄不动就写放弃理由）。空着的行等于没拆。")
    out.append("")
    out.append("## 实测指标（机器测的，别凭感觉改）")
    out.append("")
    out.append("| 项 | 值 |")
    out.append("| --- | --- |")
    out.append("| 时长 | %.2fs |" % float(info.get("duration") or 0))
    out.append("| 分辨率 / 帧率 | %dx%d / %.2ffps |" % (int(info.get("w") or 0),
                                                     int(info.get("h") or 0),
                                                     float(info.get("fps") or 0)))
    out.append("| 镜头数 | %d |" % len(shots))
    if shots:
        durs = sorted(s["dur"] for s in shots)
        out.append("| 镜头长度 | 最短 %.2fs / 中位 %.2fs / 最长 %.2fs / 均值 %.2fs |" % (
            durs[0], durs[len(durs) // 2], durs[-1], sum(durs) / float(len(durs))))
        out.append("| 平均动幅度 | %.1f / 100 |"
                   % (sum(s["motion"] for s in shots) / float(len(shots))))
        out.append("| 平均亮度 / 饱和 | %.1f / %.1f |"
                   % (sum(s["luma"] for s in shots) / float(len(shots)),
                      sum(s["sat"] for s in shots) / float(len(shots))))
    if sheet:
        out.append("| 联系表 | %s |" % os.path.basename(sheet))
    if shots:
        half = max(1, len(shots) // 2)
        head = sum(s["dur"] for s in shots[:half]) / float(half)
        tail_list = shots[half:] or shots[:1]
        tail = sum(s["dur"] for s in tail_list) / float(len(tail_list))
        trend = "越剪越快" if head > tail * 1.05 else ("前慢后更慢" if tail > head * 1.05
                                                        else "节奏基本平")
        out.append("| 节奏趋势 | 前段均 %.2fs / 后段均 %.2fs -> %s |" % (head, tail, trend))
    out.append("")
    out.append("## 逐镜拆解（手法 / 参考片实现 / 取舍）")
    out.append("")
    out.append("| # | 时长s | 动幅 | 亮度 | 饱和 | 手法 | 参考片怎么做的 | 我们要不要抄 |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in shots:
        out.append(_fmt_shot_row(s))
    out.append("")
    out.append("## 结构性发现（整片的，不是逐镜的）")
    out.append("")
    out.append("- 转场：本项目默认硬切；参考片若有叠化/推拉/滑动，写明出现在哪几个镜号。")
    out.append("- 可迁移 vs 不可迁移：拍摄、表演、版权素材属于不可迁移，别设计成那样。")
    out.append("- 音画关系：有没有卡点（鼓点切镜头）、留白（打断后静音）、声画对位。")
    out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="参考片逆向：切点/镜头长度/动幅度 + 三列拆解模板")
    ap.add_argument("--video", required=True, help="参考片路径（本地文件，不联网）")
    ap.add_argument("--out", default="", help="输出目录（默认 work/refscan）")
    ap.add_argument("--threshold", type=float, default=0.35,
                    help="scene 检测阈值 0-1（越小越敏感）")
    ap.add_argument("--max-shots", type=int, default=40, help="最多拆多少个镜头")
    ap.add_argument("--samples", type=int, default=3, help="每镜头抽样帧数（>=2）")
    ap.add_argument("--width", type=int, default=480, help="抽帧宽度")
    ap.add_argument("--json", action="store_true", help="机器可读摘要")
    a = ap.parse_args(argv)

    video = os.path.abspath(a.video)
    if not os.path.exists(video):
        print("[缺失] 参考片不存在：%s" % video)
        return 2
    info = probe(video)
    if not info:
        print("[失败] ffprobe 读不出视频流（文件坏了？或不是视频）")
        return 2
    outdir = os.path.abspath(a.out or os.path.join("work", "refscan"))
    os.makedirs(outdir, exist_ok=True)
    cuts = scene_cuts(video, threshold=a.threshold)
    shots = build_shots(video, cuts, float(info["duration"]), outdir,
                        samples=a.samples, width=a.width)
    shots, cuts, truncated = truncate(shots, cuts, a.max_shots)
    sheet = write_sheet(shots, outdir)
    mdp = os.path.join(outdir, "REVERSE.md")
    with open(mdp, "w", encoding="utf-8") as fh:
        fh.write(reverse_markdown(shots, info, sheet, video))
    jp = os.path.join(outdir, "shots.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(dict(video=video, info=info, cuts=cuts, sheet=sheet,
                       truncated_shots=truncated,
                       shots=[{k: v for k, v in s.items() if k != "frames"}
                              for s in shots]), fh, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(dict(ok=True, shots=len(shots), outdir=outdir,
                              md=mdp, sheet=sheet, info=info), ensure_ascii=False))
        return 0
    print("参考片: %s (%.2fs, %dx%d)" % (os.path.basename(video),
                                       float(info["duration"]),
                                       int(info["w"]), int(info["h"])))
    print("切点: %d 个 -> 镜头 %d 个" % (len(cuts), len(shots)))
    if truncated:
        print("[note] 只拆前 %d 个镜头（另有 %d 个未拆；--max-shots 可调大）"
              % (len(shots), truncated))
    if shots:
        durs = sorted(s["dur"] for s in shots)
        print("镜头长度: 短 %.2fs / 中位 %.2fs / 长 %.2fs"
              % (durs[0], durs[len(durs) // 2], durs[-1]))
        print("平均动幅: %.1f / 100" % (sum(s["motion"] for s in shots) / len(shots)))
    print("拆解模板:", mdp)
    print("联系表  :", sheet or "（无）")
    if not cuts:
        print("[note] 没检测到切点（一镜到底或阈值偏高）：已按镜头边界抽样")
    return 0


if __name__ == "__main__":
    sys.exit(main())
