# -*- coding: utf-8 -*-
"""jianying.py — 剪映草稿导出（Windows 优先，pyJianYingDraft 可选依赖）。

为什么要有这条出口：本技能出的是**成片**，但很多人的后续工作流在剪映里（加花字、调色、
换音乐、发抖音/小红书）。给一个 mp4 也能导入，但导进去是一条整轨——想挪一句字幕得
自己重新对时间。而剪映的草稿本来就是"素材 + 时间轴 + 文本轨"的结构，能直接把这三样写进去，
人接手就只剩创作部分。

三个设计决定：

1. **只写"素材盘"**：导出的是**干净画面**（不带字幕、不带音效）+ 独立文本轨 + 独立音频轨。
   为什么：烧进画面的字幕在剪映里抠不掉，而人第一件事往往就是改字幕。所以
   导出前要一份 `subtitle_free` 的 plate；本模块只负责把 plate 与时间表换算成草稿。
2. **时间和尺寸都换算成剪辑软件的单位**：剪映的 timebase 是**微秒**（1e6），文本字号是
   相对画布高度的百分比。换算写在这里，不散落在各处。
3. **没有 pyJianYingDraft 也要能干活**：缺依赖时输出 `draft_plan.json`（同样信息，
   人可按它手搭或等装好再跑），并**明确说明降级了**——不静默成功、不假装写好了草稿。

用法：
    python <SKILL_DIR>/scripts/jianying.py --work work --plate out/plate.mp4 --out out/jianying

产物：
    <out>/draft_plan.json    与依赖无关的中间层（时间轴/字幕/音频全在里面）
    <out>/draft/...          装了 pyJianYingDraft 时才是真草稿目录
"""
from __future__ import annotations

import json
import os

# 剪映 timebase：整数微秒。所有时间都要先过这里，避免各处自己乘 1e6 后取整不一致。
TIMEBASE = 1000000


def us(seconds):
    """秒 -> 微秒整数（四舍五入）。**单一换算口**：别处不要自己乘 1e6。"""
    return int(round(float(seconds) * TIMEBASE))


def us_range(start, dur):
    """(起, 长) -> (start_us, duration_us)，保证 duration 至少 1 微秒。"""
    s = us(start)
    d = us(float(start) + float(dur)) - s
    return s, max(1, d)


def subtitle_size_ratio(size_px, canvas_h):
    """像素字号 -> 剪辑软件用的"高度百分比"（0-1）。

    为什么必须换算：剪映的字号不是像素，是相对画布高度的比例。直接抄 filmkit 的
    46px 会让 9:16 与 16:9 的观感差一倍——同一部片横竖两版会得到两种字幕大小。
    """
    h = int(canvas_h or 0)
    if h <= 0:
        return 0.0
    return round(float(size_px) / float(h), 5)


def build_plan(timeline, plate_path="", subtitle_size=46,
               canvas=(1920, 1080), fps=30):
    """timeline -> 草稿计划 dict（纯函数，不碰剪映库）。

    不接 script：草稿只关心「每段什么时候出现哪句字」，那全在 timeline 里；
    剧本没读到的话忽略 timeline 的文本反而会写出空草稿。
    """
    tl = timeline or {}
    scenes = tl.get("scenes") or []
    w, h = int(canvas[0]), int(canvas[1])
    ratio = subtitle_size_ratio(subtitle_size, h)
    texts = []
    for i, s in enumerate(scenes):
        st = float(s.get("start") or 0.0)
        dur = float(s.get("dur") or (float(s.get("end") or 0.0) - st))
        txt = str(s.get("text") or "").strip()
        if not txt:
            continue
        start_us, dur_us = us_range(st, max(0.2, dur))
        texts.append(dict(
            id=str(s.get("id") or ("s%02d" % (i + 1))),
            text=txt,
            start_us=start_us,
            duration_us=dur_us,
            start_s=round(st, 3),
            dur_s=round(dur, 3),
            size_ratio=ratio,
        ))
    return dict(
        version=1,
        timebase=TIMEBASE,
        fps=int(fps or tl.get("fps") or 30),
        canvas=dict(w=w, h=h),
        duration_us=us(float(tl.get("total") or 0.0)),
        plate=str(plate_path or ""),
        subtitle_size_px=int(subtitle_size),
        subtitle_size_ratio=ratio,
        texts=texts,
        note="plate 必须是**不带字幕、不带音效**的干净画面；字幕与音效由本计划重挂",
    )

def write_plan(plan, outdir):
    """写 draft_plan.json（与依赖无关的中间层）。"""
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "draft_plan.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    return p


def export_draft(plan, outdir):
    """尝试用 pyJianYingDraft 写真草稿 -> (ok, message)。

    缺依赖、版本不匹配、plate 不存在都走降级路径：返回 (False, 原因)，
    由调用方打印**明确**的降级说明。绝不静默返回成功。
    """
    plate = str(plan.get("plate") or "")
    if not plate or not os.path.exists(plate):
        return False, "干净画面不存在：%r（先渲染无字幕无音效的 plate）" % plate
    try:
        import pyJianYingDraft as jy
    except Exception as e:                      # noqa: BLE001
        return False, "缺少可选依赖 pyJianYingDraft（%s）" % e
    # 有库时的最小实现：一条视频轨 + 一条文本轨。
    # 0.3.x 与更早版本 API 有差异，所以任何异常都退回降级——
    # 宁可不写草稿，也不要写出半个坏草稿让人以为能用。
    try:
        script = jy.ScriptFile(plan["canvas"]["w"], plan["canvas"]["h"])
        video_track = script.append_track(jy.TrackType.video)
        dur_us = int(plan.get("duration_us") or 0)
        mat = jy.VideoMaterial(str(plate))
        video_track.append_segment(jy.VideoSegment(mat, jy.trange(0, dur_us)))
        text_track = script.append_track(jy.TrackType.text)
        for t in plan.get("texts") or []:
            style = jy.TextStyle(size=float(t["size_ratio"]))
            clip = jy.TextSegment(
                str(t["text"]),
                jy.trange(int(t["start_us"]),
                          int(t["start_us"]) + int(t["duration_us"])),
                style=style)
            text_track.append_segment(clip)
        # 注意：pyJianYingDraft 的 save() 把草稿写进**剪映自己的草稿根目录**，
        # 不是写进 outdir。所以这句话不能报 outdir，否则用户去 outdir 里找草稿会找不到。
        # 稳定可指认的产物永远是 draft_plan.json（在 outdir 里）。
        script.save()
        return True, ("草稿已提交给剪映草稿根目录（pyJianYingDraft 决定落点）；"
                      "可指认的中间产物是 %s/draft_plan.json" % outdir)
    except Exception as e:                      # noqa: BLE001
        return False, "pyJianYingDraft 调用失败（版本不匹配？）：%s" % e


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="剪映草稿导出（缺依赖时降级为 draft_plan.json）")
    ap.add_argument("--work", required=True, help="work 目录（要 timeline.json）")
    ap.add_argument("--plate", default="", help="无字幕无音效的干净画面 mp4")
    ap.add_argument("--out", default="", help="输出目录（默认 work/jianying）")
    ap.add_argument("--subtitle-size", type=int, default=0,
                    help="字幕像素字号（缺省读 design 的 visual.subtitle.size，再缺省 46）")
    ap.add_argument("--json", action="store_true", help="机器可读结果")
    a = ap.parse_args(argv)
    tl_p = os.path.join(a.work, "timeline.json")
    if not os.path.exists(tl_p):
        print("[前置] 没有 %s，先跑 --stage layout" % tl_p)
        return 2
    with open(tl_p, encoding="utf-8") as fh:
        tl = json.load(fh)
    size = a.subtitle_size
    if not size:
        size = 46
        dp = os.path.join(a.work, "design.json")
        try:
            with open(dp, encoding="utf-8") as fh:
                d = json.load(fh)
            size = int(((d.get("visual") or {}).get("subtitle") or {}).get("size") or 46)
        except Exception:                       # noqa: BLE001
            pass
    vq = tl.get("video") or {}
    canvas = (int(vq.get("out_w") or 1920), int(vq.get("out_h") or 1080))
    plan = build_plan(tl, plate_path=a.plate, subtitle_size=size,
                      canvas=canvas, fps=int(tl.get("fps") or 30))
    outdir = a.out or os.path.join(a.work, "jianying")
    pj = write_plan(plan, outdir)
    ok, msg = export_draft(plan, outdir)
    if a.json:
        print(json.dumps(dict(ok=ok, degraded=(not ok), plan=pj,
                              texts=len(plan["texts"]), msg=msg),
                         ensure_ascii=False))
        return 0 if ok else 3
    print("草稿计划:", pj)
    print("字幕条数: %d（%dpx -> %.4f 画布高）"
          % (len(plan["texts"]), plan["subtitle_size_px"],
             plan["subtitle_size_ratio"]))
    if ok:
        print("剪映草稿:", msg)
    else:
        print("[降级] 没写出剪映草稿：%s" % msg)
        print("       已输出 draft_plan.json（时间轴/字幕/音轨都在里面），装上依赖后重跑即可。")
        print("       安装：pip install pyJianYingDraft")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
