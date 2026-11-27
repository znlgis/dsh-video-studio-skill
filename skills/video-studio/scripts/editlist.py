#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""editlist.py — 剪辑交接清单：把成片拆成「哪个镜头、从第几秒到第几秒、配哪句旁白」。

为什么需要它：本技能出的是**最终成片**，但真实工作流里常要交给人在剪映/PR 里再改一轮
（换镜、加花字、调色）。直接给一个 mp4，剪辑师得自己听一遍重新对齐；给一句话说明，
他照样得对一遍。

所以这里导出的是**剪辑师直接能用的清单**：
  work/editlist.json   机器可读（每个镜头的 in/out 点、旁白、字幕、音效提示）
  work/EDITLIST.md     人可读（一张表，照着一刀刀剪）

它只读 work/timeline.json + cues.json + audio 源，不重渲、不改任何东西。

口径：**时间轴真源只有一个**——都是 timeline.json 里的绝对秒。剪辑师按这张表剪出来的
顺序与成片逐帧一致；如果对不上，是表错了而不是成片错了（所以这张表也进交付证据）。
"""
from __future__ import annotations

import json
import os


def _load(work, name):
    p = os.path.join(work, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                  # noqa: BLE001
        return None


def build(timeline, cues=None, out_mp4=""):
    """timeline(+cues) -> 剪辑清单 dict（纯函数）。

    不接 script：清单里的信息（旁白/字幕/音效）全部来自 timeline 与 cues.json，
    剧本只贡献标题（那是 write() 的事）。留一个用不到的参数会让调用方以为它能影响结果。
    """
    tl = timeline or {}
    scenes = tl.get("scenes") or []
    fps = int(tl.get("fps") or 30)
    cue_rows = ((cues or {}).get("cues") or []) if cues else []
    items = []
    for i, s in enumerate(scenes):
        st = float(s.get("start") or 0.0)
        en = float(s.get("end") or 0.0)
        # 每个镜头内属于它的 cue（按时间落入区间；边界归前一段）
        inside = [c for c in cue_rows
                  if st - 1e-6 <= float(c.get("t") or 0.0) < en - 1e-6]
        items.append(dict(
            n=i + 1,
            id=str(s.get("id") or ("s%02d" % (i + 1))),
            kind=str(s.get("kind") or "body"),
            scene_type=str(s.get("type") or ""),
            in_s=round(st, 3),
            out_s=round(en, 3),
            dur=round(en - st, 3),
            in_frame=int(round(st * fps)),
            out_frame=int(round(en * fps)),
            narration=str(s.get("text") or ""),
            subtitle=str(s.get("sub") or ""),
            audio=str(s.get("path") or ""),
            cues=[dict(t=round(float(c.get("t") or 0.0), 3),
                       local=round(float(c.get("t") or 0.0) - st, 3),
                       type=str(c.get("type") or ""),
                       at=str(c.get("at") or "")) for c in inside],
        ))
    return dict(version=1, fps=fps, total=round(float(tl.get("total") or 0.0), 3),
                frames=int(tl.get("frames") or 0), source_mp4=str(out_mp4 or ""),
                shots=items)


def markdown(editlist, title=""):
    """人可读清单：剪辑师照这一张表剪。"""
    el = editlist or {}
    shots = el.get("shots") or []
    out = []
    out.append("# 剪辑交接清单" + (("：" + str(title)) if title else ""))
    out.append("")
    out.append("> 时间都是**成片绝对秒**（与交付 mp4 逐帧一致）。in 含、out 不含：")
    out.append("> 第 n 段的长度 = out - in。旁白列是该段配音原文，音效列是提示点（相对段首）。")
    out.append("")
    out.append("| # | id | 类型 | in | out | 时长 | 帧 | 旁白 | 音效 |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in shots:
        cues = "、".join("%s%.2fs %s" % ("+" if c["local"] >= 0 else "", c["local"], c["type"])
                         for c in s.get("cues") or []) or "—"
        nar = (s.get("narration") or "").replace("|", "｜")
        if len(nar) > 40:
            nar = nar[:40] + "…"
        out.append("| %d | %s | %s | %.2f | %.2f | %.2f | %d–%d | %s | %s |" % (
            s["n"], s["id"], s.get("scene_type") or s.get("kind") or "",
            s["in_s"], s["out_s"], s["dur"], s["in_frame"], s["out_frame"],
            nar or "—", cues))
    out.append("")
    out.append("## 交接注意")
    out.append("")
    out.append("- 总时长 %.2fs / %d 帧 @%dfps" % (float(el.get("total") or 0),
                                                 int(el.get("frames") or 0),
                                                 int(el.get("fps") or 30)))
    out.append("- **不要**在剪映里重排顺序：这张表的顺序就是成片顺序，重排会让字幕与旁白错位。")
    out.append("- 亮度/响度已按交付标准调过（-16 LUFS / 真峰 ≤ -1 dBTP）；在剪辑软件里别再加「音量标准化」。")
    out.append("- 改完任一段长度，字幕与音效都要按本表重新对，不能只拖画面。")
    out.append("")
    return "\n".join(out)


def write(work, out_mp4="", title=""):
    """从 work/ 读时间轴与提示点，写 editlist.json + EDITLIST.md -> (json_path, md_path)。"""
    tl = _load(work, "timeline.json")
    if not tl:
        raise SystemExit("没有 work/timeline.json，先跑 --stage layout")
    cues = _load(work, "cues.json")
    # 标题由调用方给（film.json 在工程目录，不在 work/ 里——早先版本到这里找，永远找不到）。
    el = build(tl, cues, out_mp4=out_mp4)
    os.makedirs(work, exist_ok=True)
    jp = os.path.join(work, "editlist.json")
    mp = os.path.join(work, "EDITLIST.md")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(el, fh, ensure_ascii=False, indent=1)
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write(markdown(el, title))
    return jp, mp
