# -*- coding: utf-8 -*-
"""reviewpacket.py — 独立终检评审包（clean-context review packet）的拼装层。

为什么需要它：做片的人对自己的产出有确认偏差。已有的自查（selfcheck / 门禁 / 签核）
全都是同一个上下文里自己查自己，最容易漏的恰恰是「我以为我做了」的那部分。
独立终检的做法是把 成片 + 逐镜关键帧 + 机器门禁摘要 + 一份可逐条打勾的清单 打包出去，
交给一个不参与制作、看不到辩解、只读这个目录的评审者（通常是干净上下文的 subagent）。

本模块只拼数据，不抽帧、不写文件、不依赖 PIL/ffmpeg：
这样「哪几条必查」可以被 ci_check 直接断言，避免清单越改越短却没人发现。
抽帧与落盘属于 I/O，交给 render.py 的 st_review。
"""
from __future__ import annotations

FENCE = chr(96) * 3
CHECK_BOX = chr(45) + chr(32) + chr(91) + chr(32) + chr(93) + chr(32)

# 分组沿用判例式审美准则的编号体系（R/Q/S/C/P），检查项按「能不能拿到证据」重排：
# 拿不到证据的检查项写进包里等于制造假通过，所以每条都标了证据位置。
REVIEW_GROUPS = (
    ("P", "产品与叙事", (
        "P1 每个必须讲清楚的信息，是否真的有一个镜头讲清楚（不是闪一下就过）",
        "P2 全片有没有说两遍的信息（同义 tagline / 重复点位）",
        "P3 有没有夸大或未确认的宣称（数字、年份、第一、唯一）",
    )),
    ("B", "分镜与时长", (
        "B1 剧本每句是否都有对应画面，且没有一段画面既无旁白也无信息",
        "B2 关键信息落定后是否留得住（字标/数字类至少 1 秒，批量动作收尾至少 0.5 秒）",
        "B3 有没有太赶的段：句读落在段外、字幕来不及读",
    )),
    ("V", "视觉", (
        "V1 字体/配色/材质是否符合已冻结的 design.json（不是又回到默认皮肤）",
        "V2 大字与字幕有没有撞在一起、有没有溢出安全区",
        "V3 整片色相数量与明暗族是否守住（不超过 4 个色相，不跨明暗族）",
        "V4 有没有单帧白闪 / 长空窗 / 静止超过 6 秒",
    )),
    ("S", "声音", (
        "S1 配乐有没有盖住旁白（尤其是有音效的段落）",
        "S2 音效是否钉在真实动作上（不要画面已经结束还在响）",
        "S3 有没有太安静的段：该有声音的地方只有底噪",
        "S4 全片响度与真峰是否达标（读 QC-REPORT 的实测值，不看任何口头描述）",
    )),
    ("D", "数据与素材安全", (
        "D1 上屏的每个数字/年份，能否在 FACTS.md 找到出处",
        "D2 素材与截图是否含客户名、个人信息、密钥、内部地址",
        "D3 素材许可是否可商用（来源与许可记在素材清单里）",
    )),
    ("A", "音画同步", (
        "A1 cues 登记的提示点，与画面上的动作是否落在同一拍",
        "A2 若 film.json 写了 mux.audio_offset_ms，这个偏移方向与量是否实测过（不是猜的）",
    )),
    ("Q", "交付与可复现", (
        "Q1 QC-REPORT 的 skipped 项，是否逐条说清了为什么没跑",
        "Q2 签核（--stage accept）是否与当前内容指纹一致（改过剧本就失效）",
        "Q3 复现命令是否写清：重跑哪个 stage 能重现这一版",
    )),
)

# 每条的证据在哪：评审者找不到证据就标「无法验证」，不许猜。
EVIDENCE_HINTS = {
    "P": "剧本 lines[] 与 work/storyboard.json",
    "B": "work/timeline.json（每段 start/end/dur/hold）",
    "V": "本包 frames/ 下的逐镜 enter/settle 帧 + work/qa/deep.json",
    "S": "work/audio.wav 与 QC-REPORT 的响度/真峰实测值",
    "D": "项目根 FACTS.md 与素材清单的 licensing 字段",
    "A": "work/cues.json 与 work/qa/ 下的同步结论",
    "Q": "QC-REPORT.md、work/gates.json、work/signoff.json",
}


def group_ids():
    """分组编号列表（ci_check / selfcheck 断言「清单没被删空」用）。"""
    return [g[0] for g in REVIEW_GROUPS]


def item_count():
    """检查项总数。"""
    return sum(len(g[2]) for g in REVIEW_GROUPS)


def checklist_markdown(extra_notes=None):
    """检查清单 -> markdown（给评审 agent 读，也给人看）。"""
    L = []
    L.append("## 审查清单（逐条给结论，缺证据写「无法验证」）")
    L.append("")
    L.append("> 规则：每条必须写 ✓ / ✗ / 无法验证；✗ 与「无法验证」都要给帧号或文件名做证据。")
    L.append("> 不许写整体感觉不错；不许把没查的写成通过。")
    L.append("")
    for gid, gname, items in REVIEW_GROUPS:
        L.append("### %s — %s" % (gid, gname))
        L.append("")
        L.append("证据位置：%s" % EVIDENCE_HINTS.get(gid, "-"))
        L.append("")
        for it in items:
            L.append("- [ ] %s" % it)
        L.append("")
    if extra_notes:
        L.append("### 本案额外注意")
        L.append("")
        for n in extra_notes:
            L.append("- %s" % n)
        L.append("")
    return "\n".join(L)


def packet_manifest(title, content_hash, duration_s, fps, frames, frame_source, marks,
                    gates_summary=None, artifacts=None, generated_at=""):
    """评审包清单（packet.json）。只拼数据，落盘由调用方做。"""
    marks = list(marks or [])
    return {
        "schema": 1,
        "title": title or "",
        "generatedAt": generated_at,
        "contentHash": content_hash or "",
        "duration": round(float(duration_s or 0.0), 3),
        "fps": int(fps or 30),
        "frames": int(frames or 0),
        "frameSource": frame_source,
        "shotCount": len(marks),
        "reviewFrameCount": len(marks) * 2,
        "marks": marks,
        "gates": gates_summary or {},
        "artifacts": artifacts or [],
    }


def packet_markdown(manifest, report_path="", sheet_path="", frames_dir=""):
    """评审包入口（PACKET.md）：干净上下文评审者只需要读这一个文件。"""
    m = manifest or {}
    L = []
    L.append("# 独立终检包 — %s" % (m.get("title") or "（未命名）"))
    L.append("")
    L.append("> 本包为干净上下文的独立评审准备：评审者不看制作过程、不看辩解，")
    L.append("> 只看这个目录里的东西 + 下面这份清单。缺证据的项写「无法验证」，不许猜。")
    L.append("")
    L.append("## 一、这一版是什么")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 内容指纹 | %s |" % (m.get("contentHash") or "-"))
    L.append("| 时长 | %.2fs / %d 帧 @ %d fps |"
             % (float(m.get("duration") or 0), int(m.get("frames") or 0), int(m.get("fps") or 0)))
    L.append("| 段数 | %d 段 |" % int(m.get("shotCount") or 0))
    L.append("| 逐镜验收帧 | %d 张（每段 enter / settle 各一张） |" % int(m.get("reviewFrameCount") or 0))
    src = m.get("frameSource") or ""
    L.append("| 帧来源 | %s |"
             % ("成片 mp4 抽帧（与交付同一份）" if src == "mp4" else "重渲（非母版：成片缺失时的降级）"))
    if m.get("generatedAt"):
        L.append("| 生成时间 | %s |" % m["generatedAt"])
    L.append("")
    if report_path:
        L.append("- QC 报告（机器交付凭据）：%s" % report_path)
    if sheet_path:
        L.append("- 逐镜联系表（每行 = 一段，左 enter / 右 settle）：%s" % sheet_path)
    if frames_dir:
        L.append("- 逐帧目录：%s" % frames_dir)
    L.append("")
    g = m.get("gates") or {}
    if g:
        L.append("## 二、门禁现状（机器读出来的，不是复述）")
        L.append("")
        L.append("- 合计：pass %s · fail %s · skipped %s"
                 % (g.get("pass", "?"), g.get("fail", "?"), g.get("skipped", "?")))
        fails = g.get("failItems") or []
        skips = g.get("skipItems") or []
        if fails:
            L.append("- fail：" + "；".join(fails[:8]))
        if skips:
            L.append("- skipped（= 没执行，不等于通过）：" + "；".join(skips[:12]))
        L.append("")
    L.append(checklist_markdown())
    L.append("## 三、输出格式（评审者必须按这个格式回）")
    L.append("")
    L.append(FENCE + "text")
    L.append("结论：通过 / 不通过（必须二选一）")
    L.append("阻断项（必须修的）：")
    L.append("  - [组号-条号] 问题一句话 · 证据（帧号/文件名）")
    L.append("建议项（可改可不改的）：")
    L.append("  - [组号-条号] ...")
    L.append("无法验证的项：")
    L.append("  - [组号-条号] 缺什么证据")
    L.append(FENCE)
    L.append("")
    return "\n".join(L)
